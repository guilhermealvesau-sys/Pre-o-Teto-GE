"""Indicadores do modelo a partir dos dados brutos (CVM + B3), com as definições documentadas no README.

Janela: os 6 últimos anos completos (ex.: em 2026, de 2020 a 2025).
  - payout (modelo): mediana dos payouts anuais dos 3 últimos anos completos (proventos referentes ao ano ÷ lucro)
  - payout 12M (comparação): proventos com data com nos últimos 12 meses ÷ lucro dos últimos 12 meses
  - payout média (opção): média simples dos payouts anuais dos 6 últimos anos completos (5, se faltar um ano)
  - payout média 6 anos agregada (referência): soma dos proventos da janela ÷ soma dos lucros
  - DY médio: média dos DY anuais (proventos com data com no ano ÷ preço médio de fechamento do ano)
  - dividendo médio (R$): média anual dos proventos por papel, ajustada à quantidade de ações de hoje
    (bonificações e desdobramentos ficam corrigidos sem precisar dos fatores)
  - % em JCP: parcela dos proventos dos últimos 3 anos completos paga como JCP
  - P/L médio: mediana dos P/L anuais (preço médio do ano ÷ lucro por ação do ano), ignorando anos com prejuízo
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, median

PN_CLASSES = ("PN", "PNA", "PNB", "PNC")
TIPOS = ("DIVIDENDO", "JCP", "RENDIMENTO")


def _cls(classe: str) -> str:
    return "on" if classe == "ON" else "pn"


def _componentes(a: dict) -> list[tuple[str, float]]:
    """Classes que compõem o papel e quantas de cada: unit → [(ON, n), (PN, m)]."""
    if a["classe"] == "UNIT":
        return [("ON", a["unit_on"]), (a.get("unit_pn_classe") or "PN", a["unit_pn"])]
    return [(a["classe"], 1)]


_PREF_PN = ("PN", "PNB", "PNA", "PNC")  # classe preferencial representativa: a mais comum primeiro


def _prov_classe(eventos: list[dict], classe: str) -> list[dict]:
    """Eventos da classe. "PN" genérico (componente da unit ou soma das PN na CVM) escolhe, ano a ano,
    a primeira classe preferencial com eventos naquele ano — a B3 rotula eventos antigos de outra forma."""
    if classe != "PN":
        return [e for e in eventos if e["classe"] == classe]
    por_ano: dict = {}
    for e in eventos:
        if e["classe"] in PN_CLASSES:
            por_ano.setdefault(e["data_com"][:4], set()).add(e["classe"])
    alvo = {y: next(c for c in _PREF_PN if c in cls) for y, cls in por_ano.items()}
    return [e for e in eventos if e["classe"] in PN_CLASSES and alvo.get(e["data_com"][:4]) == e["classe"]]


def calcular(a: dict, emp, eventos: list[dict], precos: dict[int, dict], ano_atual: int,
             anos: int = 6, hoje: date | None = None) -> dict:
    """`a`: linha de config/ativos.csv; `emp`: cvm.Empresa; `precos`: {ano: {media, fim}} do ticker."""
    janela = list(range(ano_atual - anos, ano_atual))
    fator = a["unit_on"] + a["unit_pn"] if a["classe"] == "UNIT" else 1
    out: dict = {"fator": fator, "janela": f"{janela[0]}-{janela[-1]}", "avisos": list(emp.avisos) if emp else []}
    if emp is None:
        out["avisos"].append("empresa sem dados da CVM")
        return out
    # só dividendos, JCP e rendimentos: devolução de capital (REST CAP DIN) não é provento do lucro
    excl = [e for e in eventos if e["tipo"] not in TIPOS]
    if excl:
        out["avisos"].append(f"{len(excl)} devolução(ões) de capital fora das médias")
    eventos = [e for e in eventos if e["tipo"] in TIPOS]

    tot_now = (emp.acoes_on or 0) + (emp.acoes_pn or 0)
    if tot_now > 0:
        out["qtd_mi"] = round(tot_now / 1e6, 4)
        out["acoes_ref"] = emp.acoes_ref
    if emp.lucro12m_mi is not None:
        out["lucro12m_mi"] = emp.lucro12m_mi
        out["lucro12m_ate"] = emp.lucro12m_ate
    if emp.pl_mi is not None and tot_now > 0:
        out["vpa"] = round(emp.pl_mi * 1e6 / tot_now * fator, 4)
        out["vpa_ref"] = emp.pl_ref

    def acoes(cls: str, ano: int | None) -> float | None:
        if ano is not None and ano in emp.acoes_ano:
            v = emp.acoes_ano[ano][_cls(cls)]
            return v if v > 0 else None
        v = emp.acoes_on if cls == "ON" else emp.acoes_pn
        return v if v else None

    comp = _componentes(a)
    por_cls = {c: _prov_classe(eventos, c) for c, _ in comp}

    # ---- proventos por papel e por ano (data com), brutos e ajustados à base de ações de hoje
    prov_ano, prov_adj = {}, {}
    for y in janela:
        bruto = ajustado = 0.0
        for c, n in comp:
            hoje_c = acoes(c, None)
            for e in por_cls[c]:
                if int(e["data_com"][:4]) != y:
                    continue
                bruto += n * e["valor"]
                then_c = acoes(c, y)
                ajustado += n * e["valor"] * ((then_c / hoje_c) if then_c and hoje_c else 1.0)
        prov_ano[y], prov_adj[y] = bruto, ajustado
    tem_hist = any(prov_ano.values())
    if tem_hist:
        out["div_medio6"] = round(mean(prov_adj.values()), 4)
        dys = [prov_ano[y] / precos[y]["media"] for y in janela if precos.get(y, {}).get("media")]
        if len(dys) >= 3:
            out["dy_medio"] = round(mean(dys), 4)
    else:
        out["avisos"].append("sem proventos na B3 para a janela")

    # ---- % em JCP: últimos 3 anos completos
    ult3 = set(janela[-3:])
    tot = jcp = 0.0
    for c, n in comp:
        for e in por_cls[c]:
            if int(e["data_com"][:4]) in ult3:
                tot += n * e["valor"]
                jcp += n * e["valor"] if e["tipo"] == "JCP" else 0.0
    if tot > 0:
        out["pct_jcp"] = round(jcp / tot, 4)

    # ---- payout médio (empresa toda): proventos declarados para o lucro de cada ano ÷ lucro
    dist = 0.0
    luc = 0.0
    anos_ok = []
    payout_ano: dict = {}
    for y in janela:
        if y not in emp.lucro_anual_mi:
            continue
        d = 0.0
        # ON × ações ON + uma classe preferencial representativa × ações PN (a CVM soma PNA e PNB)
        ev_on, ev_pn = _prov_classe(eventos, "ON"), _prov_classe(eventos, "PN")
        v_on = sum(e["valor"] for e in ev_on if e["ano_ref"] == y)
        v_pn = sum(e["valor"] for e in ev_pn if e["ano_ref"] == y)
        if not v_on and v_pn and acoes("ON", y):  # ON não negociada no ano: a B3 só lista a PN
            v_on = v_pn
            if "proventos da ON estimados pelos da PN (ON não negociada)" not in out["avisos"]:
                out["avisos"].append("proventos da ON estimados pelos da PN (ON não negociada)")
        for cls, v in (("ON", v_on), ("PN", v_pn)):
            q = acoes(cls, y)
            if q:
                d += v * q / 1e6
        payout_ano[y] = {"distribuido_mi": round(d, 1), "lucro_mi": emp.lucro_anual_mi[y],
                         "acoes_on": acoes("ON", y), "acoes_pn": acoes("PN", y)}
        dist += d
        luc += emp.lucro_anual_mi[y]
        anos_ok.append(y)
    if luc > 0 and dist > 0 and len(anos_ok) >= 3:
        out["payout_media6"] = round(dist / luc, 4)  # referência: média agregada da janela de 6 anos
    # payout do modelo: mediana dos payouts anuais dos 3 últimos anos completos com lucro positivo
    anuais = [(y, v["distribuido_mi"] / v["lucro_mi"]) for y, v in payout_ano.items() if v["lucro_mi"] and v["lucro_mi"] > 0]
    ult = [p for y, p in anuais if y in janela[-3:]]
    if len(ult) >= 2:
        out["payout"] = round(median(ult), 4)
        out["payout_anos"] = f"{janela[-3]}-{janela[-1]} (mediana)"
    # payout médio (opção): média simples dos payouts anuais da janela de 6 anos, com 6 anos de dados ou, se faltar um, 5
    na_janela = dict((y, p) for y, p in anuais if y in janela)
    if len(na_janela) >= len(janela) - 1 and len(na_janela) >= 5:
        out["payout_media"] = round(sum(na_janela.values()) / len(na_janela), 4)
        falt = [str(y) for y in janela if y not in na_janela]
        out["payout_media_anos"] = (f"{janela[0]}-{janela[-1]} (média de {len(na_janela)} anos"
                                    + (f", sem {', '.join(falt)})" if falt else ")"))

    # ---- payout atual: proventos com data com nos últimos 12 meses ÷ lucro dos últimos 12 meses
    if emp.lucro12m_mi and emp.lucro12m_mi > 0 and hoje is not None:
        ini = (hoje - timedelta(days=365)).isoformat()
        rec = [e for e in eventos if ini < e["data_com"] <= hoje.isoformat()]
        ev_on = [e for e in rec if e["classe"] == "ON"]
        pn_cls = next((c for c in ("PN", "PNB", "PNA", "PNC") if any(e["classe"] == c for e in rec)), None)
        ev_pn = [e for e in rec if e["classe"] == pn_cls]
        v_on, v_pn = sum(e["valor"] for e in ev_on), sum(e["valor"] for e in ev_pn)
        if not v_on and v_pn and emp.acoes_on:
            v_on = v_pn
        d12 = (v_on * (emp.acoes_on or 0) + v_pn * (emp.acoes_pn or 0)) / 1e6
        if d12 > 0:
            out["payout_12m"] = round(d12 / emp.lucro12m_mi, 4)
            out["payout_12m_ate"] = hoje.isoformat()

    # ---- P/L médio
    pls = []
    for y in janela:
        px = precos.get(y, {}).get("media")
        ll = emp.lucro_anual_mi.get(y)
        q = emp.acoes_ano.get(y)
        if not px or ll is None or not q or ll <= 0:
            continue
        lpa = ll * 1e6 / (q["on"] + q["pn"]) * fator
        pls.append(px / lpa)
    if len(pls) >= 3:
        out["pl_medio"] = round(median(pls), 3)

    out["detalhe"] = {
        "proventos_por_ano": {y: round(v, 4) for y, v in prov_ano.items()},
        "lucro_anual_mi": {y: emp.lucro_anual_mi.get(y) for y in janela},
        "preco_medio_ano": {y: precos.get(y, {}).get("media") for y in janela},
        "payout_por_ano": payout_ano,
    }
    return out
