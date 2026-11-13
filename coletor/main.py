"""Ponto de entrada.

  python -m coletor.main diario    → dados/cotacoes.json (fechamento B3 + PTAX)
  python -m coletor.main completo  → dados/base.json e dados/relatorio.md (CVM + B3 + PTAX), mais o diário
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone

import pandas as pd

from . import b3, bcb, cvm, indicadores
from .util import RAIZ, ler_json, log, salvar_json

CFG = RAIZ / "config"
DADOS = RAIZ / "dados"


def _agora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ativos() -> pd.DataFrame:
    a = pd.read_csv(CFG / "ativos.csv", dtype={"unit_on": int, "unit_pn": int, "antigos": str}).fillna({"antigos": ""})
    return a


def diario() -> dict:
    a = _ativos()
    tickers = set(a["ticker"])
    pregoes = b3.ultimos_pregoes(tickers, n=2)
    out = {"gerado_em": _agora(), "fonte": "B3 – COTAHIST (arquivo diário)", "ref": None, "cotacoes": {}, "avisos": []}
    if not pregoes:
        out["avisos"].append("nenhum arquivo diário da B3 nos últimos 10 dias")
    else:
        ref, px = pregoes[0]
        ant = pregoes[1][1] if len(pregoes) > 1 else {}
        out["ref"] = ref
        for tk in sorted(tickers):
            if tk in px:
                v = (px[tk] / ant[tk] - 1) if ant.get(tk) else None
                out["cotacoes"][tk] = {"preco": round(px[tk], 2), "var": round(v, 6) if v is not None else None}
        falt = sorted(tickers - set(out["cotacoes"]))
        if falt:
            out["avisos"].append("sem negócio no pregão: " + ", ".join(falt))
    try:
        p = bcb.ptax()
        if p:
            out["ptax"] = {"valor": p[0], "data": p[1], "fonte": "Banco Central – PTAX venda"}
    except Exception as e:  # noqa: BLE001
        out["avisos"].append(f"PTAX indisponível: {e}")
    salvar_json(DADOS / "cotacoes.json", out)
    log.info("cotações: %s papéis, pregão %s", len(out["cotacoes"]), out["ref"])
    return out


def completo() -> dict:
    hoje = date.today()
    a = _ativos()
    emp_cfg = pd.read_csv(CFG / "empresas.csv", dtype=str).fillna("")

    # 1) quem é quem: o cadastro da B3 informa o código CVM e o nome de pregão de cada emissor
    pregao = {}
    for i, e in emp_cfg.iterrows():
        try:
            info = b3.empresa(e["emissor"])
        except Exception as ex:  # noqa: BLE001
            info = None
            log.warning("B3: cadastro de %s indisponível: %s", e["emissor"], ex)
        if info:
            pregao[e["emissor"]] = info.get("tradingName")
            if not e["cd_cvm"] and info.get("codeCVM"):
                emp_cfg.at[i, "cd_cvm"] = info["codeCVM"]
    cad = cvm.cadastro()
    mapa = cvm.resolver(emp_cfg, cad)
    mapa.to_csv(DADOS / "mapa_cvm.csv", index=False)
    pend = mapa[mapa["cnpj"].isna()]
    for r in pend.itertuples():
        log.warning("CVM: %s sem correspondência única (%s)", r.emissor, r.candidatos or "nenhum candidato")

    # 2) contábeis
    emps = cvm.coletar(mapa[mapa["cnpj"].notna()])

    # 3) preços anuais (anos fechados ficam em cache no repositório)
    hist = ler_json(DADOS / "precos_anuais.json", {}) or {}
    antigos = {r["ticker"]: [x for x in str(r.get("antigos") or "").split(";") if x and x != "nan"]
               for r in a.to_dict("records")}
    todos = set(a["ticker"]) | {x for v in antigos.values() for x in v}
    feitos = hist.setdefault("_processado", {})
    for y in range(hoje.year - 6, hoje.year):
        if sorted(feitos.get(str(y), [])) == sorted(todos):
            continue
        for tk, v in b3.precos_ano(y, todos).items():
            hist.setdefault(tk, {})[str(y)] = v
        feitos[str(y)] = sorted(todos)
    for tk, olds in antigos.items():  # anos sem negócio no código atual vêm do código antigo
        for old in olds:
            for y, v in hist.get(old, {}).items():
                hist.setdefault(tk, {}).setdefault(y, {**v, "codigo": old})
    salvar_json(DADOS / "precos_anuais.json", hist)

    # 3b) composição do capital informada em milhares: confere pelo P/VP com o preço de mercado
    for em, e in emps.items():
        pxs = [v[max(v)]["fim"] for tk in a.loc[a["emissor"] == em, "ticker"] if (v := hist.get(tk))]
        tot = (e.acoes_on or 0) + (e.acoes_pn or 0)
        if pxs and tot and e.pl_mi and e.pl_mi > 0:
            pvp = pxs[0] * tot / (e.pl_mi * 1e6)
            if pvp < 0.05:  # nenhuma companhia real negocia a menos de 0,05× o patrimônio
                e.escalar_acoes(1000)
                e.avisos.append("composição do capital informada em milhares (corrigida)")

    # 4) proventos por emissor
    prov = {}
    for em in sorted(set(a["emissor"])):
        try:
            prov[em] = b3.proventos(em, pregao.get(em))
        except Exception as e:  # noqa: BLE001
            log.warning("B3: proventos de %s falharam: %s", em, e)
            prov[em] = []
    salvar_json(DADOS / "proventos.json", prov)

    # 5) indicadores por papel
    base = {"gerado_em": _agora(), "janela_anos": 6, "ativos": {}, "fontes": {
        "contabeis": "CVM – Dados Abertos (ITR e DFP)", "proventos": "B3 – proventos em dinheiro",
        "precos": "B3 – COTAHIST", "cambio": "Banco Central – PTAX"}}
    for r in a.to_dict("records"):
        emp = emps.get(r["emissor"])
        precos = {int(y): v for y, v in hist.get(r["ticker"], {}).items()}
        base["ativos"][r["ticker"]] = indicadores.calcular(r, emp, prov.get(r["emissor"], []), precos, hoje.year)

    # 6) Petrobras: dívida bruta em US$ pela PTAX da data do balanço
    pe = emps.get("PETR")
    if pe and pe.divida_bruta_mi and pe.pl_ref:
        try:
            px = bcb.ptax(date.fromisoformat(pe.pl_ref))
            if px:
                base["petrobras"] = {"divida_bruta_usd_bi": round(pe.divida_bruta_mi / px[0] / 1000, 2),
                                     "divida_bruta_brl_mi": round(pe.divida_bruta_mi, 1), "ref": pe.pl_ref,
                                     "ptax": px[0], "fonte": "CVM – balanço (empréstimos, financiamentos e arrendamentos)"}
        except Exception as e:  # noqa: BLE001
            log.warning("PTAX da data do balanço indisponível: %s", e)
    salvar_json(DADOS / "base.json", base)
    _relatorio(base, mapa, {k: e.conta_lucro or "" for k, e in emps.items()})
    return base


def _relatorio(base: dict, mapa: pd.DataFrame, contas: dict | None = None) -> None:
    contas = contas or {}
    lin = [f"# Coleta de {base['gerado_em']}", "", "## Empresas na CVM", "",
           "| Emissor | CVM | Razão social | Conta do lucro | Pendência |", "|---|---|---|---|---|"]
    txt = lambda v: "" if v is None or (isinstance(v, float) and v != v) else str(v)  # noqa: E731
    for r in mapa.itertuples():
        lin.append(f"| {r.emissor} | {txt(r.cd_cvm)} | {txt(r.denom)} | {contas.get(r.emissor, '')} | {txt(r.candidatos)} |")
    lin += ["", "## Papéis", "",
            "| Papel | Ações (mi) | Lucro 12M (R$ mi) | Até | VPA | Payout | DY médio | Div. médio | % JCP | P/L médio | Avisos |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda v, d=2: "" if v is None else f"{v:,.{d}f}"  # noqa: E731
    for tk, v in base["ativos"].items():
        lin.append(f"| {tk} | {f(v.get('qtd_mi'))} | {f(v.get('lucro12m_mi'), 1)} | {v.get('lucro12m_ate', '')} | "
                   f"{f(v.get('vpa'))} | {f(v.get('payout'), 3)} | {f(v.get('dy_medio'), 4)} | {f(v.get('div_medio6'), 3)} | "
                   f"{f(v.get('pct_jcp'), 2)} | {f(v.get('pl_medio'))} | {'; '.join(v.get('avisos', []))} |")
    if "petrobras" in base:
        p = base["petrobras"]
        lin += ["", f"Petrobras: dívida bruta US$ {p['divida_bruta_usd_bi']} bi em {p['ref']} (PTAX {p['ptax']})."]
    (DADOS / "relatorio.md").write_text("\n".join(lin) + "\n", encoding="utf-8")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "diario"
    DADOS.mkdir(exist_ok=True)
    if modo == "completo":
        completo()
    diario()
