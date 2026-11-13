"""Indicadores do modelo a partir dos dados brutos (CVM + B3), com as definições documentadas no README.

Janela: os 6 últimos anos completos (ex.: em 2026, de 2020 a 2025).
  - payout médio: soma dos proventos declarados para os lucros da janela ÷ soma do lucro dos controladores
  - DY médio: média dos DY anuais (proventos com data com no ano ÷ preço médio de fechamento do ano)
  - dividendo médio (R$): média anual dos proventos por papel, ajustada à quantidade de ações de hoje
    (bonificações e desdobramentos ficam corrigidos sem precisar dos fatores)
  - % em JCP: parcela dos proventos dos últimos 3 anos completos paga como JCP
  - P/L médio: mediana dos P/L anuais (preço médio do ano ÷ lucro por ação do ano), ignorando anos com prejuízo
"""
from __future__ import annotations

from statistics import mean, median

PN_CLASSES = ("PN", "PNA", "PNB", "PNC")


def _cls(classe: str) -> str:
    return "on" if classe == "ON" else "pn"


def _componentes(a: dict) -> list[tuple[str, float]]:
    """Classes que compõem o papel e quantas de cada: unit → [(ON, n), (PN, m)]."""
    if a["classe"] == "UNIT":
        return [("ON", a["unit_on"]), ("PN", a["unit_pn"])]
    return [(a["classe"], 1)]


def _prov_classe(eventos: list[dict], classe: str) -> list[dict]:
    if classe == "PN":  # na unit, o componente preferencial pode ser PN, PNA ou PNB
        tem = {e["classe"] for e in eventos if e["classe"] in PN_CLASSES}
        alvo = "PN" if "PN" in tem else (sorted(tem)[0] if tem else "PN")
        return [e for e in eventos if e["classe"] == alvo]
    return [e for e in eventos if e["classe"] == classe]


def calcular(a: dict, emp, eventos: list[dict], precos: dict[int, dict], ano_atual: int,
             anos: int = 6) -> dict:
    """`a`: linha de config/ativos.csv; `emp`: cvm.Empresa; `precos`: {ano: {media, fim}} do ticker."""
    janela = list(range(ano_atual - anos, ano_atual))
    fator = a["unit_on"] + a["unit_pn"] if a["classe"] == "UNIT" else 1
    out: dict = {"fator": fator, "janela": f"{janela[0]}-{janela[-1]}", "avisos": list(emp.avisos) if emp else []}
    if emp is None:
        out["avisos"].append("empresa sem dados da CVM")
        return out

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
        if not ev_on and v_pn and acoes("ON", y):  # ON não negociada: B3 só lista a PN
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
        out["payout"] = round(dist / luc, 4)
        out["payout_anos"] = f"{anos_ok[0]}-{anos_ok[-1]}"

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
