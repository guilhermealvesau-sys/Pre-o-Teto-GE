"""Ponto de entrada.

  python -m coletor.main diario    → dados/cotacoes.json (fechamento B3 + PTAX)
  python -m coletor.main completo  → dados/base.json e dados/relatorio.md (CVM + B3 + PTAX), mais o diário

Os dois modos também atualizam dados/avisos.json e dados/fatos.json (CVM, documentos eventuais)
e dados/noticias.json (manchetes do Google Notícias).
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import avisos, b3, bcb, cvm, indicadores, noticias
from .util import RAIZ, ler_json, log, salvar_json

CFG = RAIZ / "config"
DADOS = RAIZ / "dados"


def _agora() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ativos() -> pd.DataFrame:
    a = pd.read_csv(CFG / "ativos.csv", dtype={"unit_on": int, "unit_pn": int, "antigos": str, "unit_pn_classe": str}).fillna({"antigos": "", "unit_pn_classe": ""})
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
    try:
        m = bcb.macro()
        if m:
            out["macro"] = {**m, "fonte": "Banco Central – SGS"}
    except Exception as e:  # noqa: BLE001
        out["avisos"].append(f"Selic/IPCA indisponíveis: {e}")
    salvar_json(DADOS / "cotacoes.json", out)
    log.info("cotações: %s papéis, pregão %s", len(out["cotacoes"]), out["ref"])
    return out


def completo() -> dict:
    hoje = date.today()
    a = _ativos()
    emp_cfg = pd.read_csv(CFG / "empresas.csv", dtype=str).fillna("")

    # 1) quem é quem: o cadastro da B3 informa o código CVM e o nome de pregão de cada emissor
    pregao, sites = {}, {}
    for i, e in emp_cfg.iterrows():
        try:
            info = b3.empresa(e["emissor"])
        except Exception as ex:  # noqa: BLE001
            info = None
            log.warning("B3: cadastro de %s indisponível: %s", e["emissor"], ex)
        if info:
            pregao[e["emissor"]] = info.get("tradingName")
            log.info("B3: %s → pregão %r, CVM %s", e["emissor"], info.get("tradingName"), info.get("codeCVM"))
            if not e["cd_cvm"] and info.get("codeCVM"):
                emp_cfg.at[i, "cd_cvm"] = info["codeCVM"]
            try:
                sites[e["emissor"]] = b3.site_ri(emp_cfg.at[i, "cd_cvm"] or info.get("codeCVM"))
            except Exception as ex:  # noqa: BLE001
                log.warning("B3: site de %s indisponível: %s", e["emissor"], ex)
        else:
            log.warning("B3: emissor %s não encontrado no cadastro da bolsa", e["emissor"])
    cad = cvm.cadastro()
    mapa = cvm.resolver(emp_cfg, cad)
    mapa.to_csv(DADOS / "mapa_cvm.csv", index=False)
    pend = mapa[mapa["cnpj"].isna()]
    for r in pend.itertuples():
        log.warning("CVM: %s sem correspondência única (%s)", r.emissor, r.candidatos or "nenhum candidato")

    # 1b) B3: proventos, eventos em ações (desdobramentos, bonificações) e quantidade de ações do cadastro
    prov, fatores, acoes_b3 = {}, {}, {}
    for em in sorted(set(a["emissor"])):
        try:
            bloco = b3.suplemento(em)
        except Exception as e:  # noqa: BLE001
            log.warning("B3: dados complementares de %s falharam: %s", em, e)
            bloco = {}
        fatores[em] = b3.eventos_acoes(bloco)
        acoes_b3[em] = b3.acoes_b3(bloco)
        try:
            prov[em] = b3.proventos(em, pregao.get(em), bloco=bloco)
        except Exception as e:  # noqa: BLE001
            log.warning("B3: proventos de %s falharam: %s", em, e)
            prov[em] = []
    salvar_json(DADOS / "proventos.json", prov)

    # 2) contábeis (bancos: demonstração individual, padrão Banco Central, como nos releases)
    col = emp_cfg.set_index("emissor").get("demonstracao")
    so_ind = set(mapa[mapa["emissor"].map(lambda x: str(col.get(x, "") if col is not None else "").strip() == "individual")]["cnpj"].dropna())
    emps = cvm.coletar(mapa[mapa["cnpj"].notna()], so_ind=so_ind)

    # 3) preços anuais: médias como negociadas e na base de ações de hoje (fatores posteriores a cada dia)
    hist = ler_json(DADOS / "precos_anuais.json", {}) or {}
    hist.pop("_processado", None)
    antigos = {r["ticker"]: [x for x in str(r.get("antigos") or "").split(";") if x and x != "nan"]
               for r in a.to_dict("records")}
    todos = set(a["ticker"]) | {x for v in antigos.values() for x in v}
    emissor_de = dict(zip(a["ticker"], a["emissor"]))
    for tk, olds in antigos.items():
        for old in olds:
            emissor_de[old] = emissor_de[tk]
    fat_tk = {tk: indicadores.fatores_por_data(fatores.get(em, [])) for tk, em in emissor_de.items()}
    for y in range(hoje.year - 6, hoje.year):
        for tk, v in b3.precos_ano(y, todos, fat_tk).items():
            hist.setdefault(tk, {})[str(y)] = v
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

    # 4) ações de hoje: eventos em ações e aumentos de capital posteriores ao formulário da CVM
    for em, e in emps.items():
        _atualizar_acoes(e, fatores.get(em, []), acoes_b3.get(em, {}))

    # 5) indicadores por papel
    base = {"gerado_em": _agora(), "janela_anos": 6, "ativos": {}, "sites": {k: v for k, v in sites.items() if v},
            "fontes": {
        "contabeis": "CVM – Dados Abertos (ITR e DFP)", "proventos": "B3 – proventos em dinheiro",
        "precos": "B3 – COTAHIST", "cambio": "Banco Central – PTAX"}}
    for r in a.to_dict("records"):
        emp = emps.get(r["emissor"])
        precos = {int(y): v for y, v in hist.get(r["ticker"], {}).items()}
        base["ativos"][r["ticker"]] = indicadores.calcular(r, emp, prov.get(r["emissor"], []), precos, hoje.year, hoje=hoje,
                                                           fatores=fatores.get(r["emissor"], []))

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


def _atualizar_acoes(e, fatores: list[dict], b3info: dict) -> None:
    """Leva a quantidade de ações do último formulário da CVM para hoje.

    1. Desdobramentos, grupamentos e bonificações com data com depois do formulário multiplicam as ações.
    2. Aumento de capital ainda fora do formulário (o banco precisa da homologação do Banco Central, por exemplo):
       se o cadastro da B3 tem mais ações que as emitidas na CVM e houve subscrição recente, vale a B3,
       menos as ações em tesouraria do formulário."""
    if not e.acoes_ref:
        return
    k = 1.0
    for d, f in indicadores.fatores_por_data(fatores):
        if d >= e.acoes_ref:
            k *= f
    if abs(k - 1) > 1e-9:
        e.acoes_on = (e.acoes_on or 0) * k
        e.acoes_pn = (e.acoes_pn or 0) * k
        e.tesouraria_on *= k
        e.tesouraria_pn *= k
        e.avisos.append(f"ações do formulário de {e.acoes_ref} multiplicadas por {k:g} (evento em ações posterior)")
    tot_b3 = b3info.get("total") or 0
    emitidas = (e.acoes_on or 0) + (e.acoes_pn or 0) + e.tesouraria_on + e.tesouraria_pn
    desde = (date.fromisoformat(e.acoes_ref) - timedelta(days=120)).isoformat()
    subs = sorted(s["data"] for s in b3info.get("subscricoes", []) if s["data"] >= desde)
    if tot_b3 and emitidas and tot_b3 > emitidas * 1.002 and subs:
        on_b3, pn_b3 = b3info.get("on") or 0, b3info.get("pn") or 0
        if on_b3 + pn_b3 <= 0:
            return
        e.acoes_on = max(on_b3 - e.tesouraria_on, 0)
        e.acoes_pn = max(pn_b3 - e.tesouraria_pn, 0)
        e.avisos.append(f"ações atualizadas pelo cadastro da B3: aumento de capital (subscrição de {subs[-1]}) "
                        f"ainda fora do formulário da CVM de {e.acoes_ref}")


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


def verificar_avisos() -> list[dict]:
    """Varre documentos da CVM atrás de mudanças de política de remuneração (usa o mapa da última coleta)."""
    mapa_p = DADOS / "mapa_cvm.csv"
    if not mapa_p.exists():
        log.warning("avisos: mapa da CVM ainda não existe (rode a coleta completa)")
        return []
    mapa = pd.read_csv(mapa_p, dtype=str).dropna(subset=["cnpj"])
    emp = {r.cnpj: {"emissor": r.emissor, "nome": r.nome} for r in mapa.itertuples()}
    try:
        return avisos.atualizar(emp, DADOS, RAIZ / ".saida")
    except Exception as e:  # noqa: BLE001
        log.warning("avisos: varredura falhou: %s", e)
        return []


def atualizar_noticias() -> None:
    emp = pd.read_csv(CFG / "empresas.csv", dtype=str).fillna("")
    if "noticias" not in emp.columns:
        return
    lst = [{"emissor": r["emissor"], "nome": r["nome"], "noticias": r["noticias"],
            "chaves": [c for c in r.get("chaves", "").split(";") if c]} for r in emp.to_dict("records")]
    try:
        out = noticias.coletar(lst)
        if out["por_emissor"]:
            salvar_json(DADOS / "noticias.json", out)
    except Exception as e:  # noqa: BLE001
        log.warning("notícias: coleta falhou: %s", e)


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "diario"
    DADOS.mkdir(exist_ok=True)
    if modo == "completo":
        completo()
    diario()
    verificar_avisos()
    atualizar_noticias()
