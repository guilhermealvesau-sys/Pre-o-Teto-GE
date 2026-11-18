"""Recalcula o preço teto de cada papel do zero e compara com o que o dados.json publicado leva às páginas.

Uso:
  python recalcular_preco_teto.py BASE_JSON DADOS_DIR DADOS_PUBLICO_JSON

  BASE_JSON           base da página principal: {"ativos": {...}, "params": {...}} (base.json publicado ou
                      painel_base.json de espelhar.py); traz consenso, modelo, fator e regras do usuário
  DADOS_DIR           dados/ do repositório (base.json e cotacoes.json da coleta)
  DADOS_PUBLICO_JSON  dados.json do dashboard público (ou o que montar_publico.py acabou de montar)

O cálculo aqui não usa o código das páginas nem o de montar_publico.py: parte do consenso, da quantidade de
ações e do payout. Confere 4 combinações (base projetada com payout mediano, médio e de 12 meses; base
conservadora com payout mediano). Imprime um resumo em JSON; sai com código 1 se algum preço teto diferir
mais de R$ 0,01.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

COMBOS = (("PROJETADO", "MEDIANO"), ("PROJETADO", "MEDIA"), ("PROJETADO", "ATUAL"), ("CONSERVADOR", "MEDIANO"))


def escolher_payout(a: dict, pay: str):
    if a.get("payout_manual") is not None:
        return a["payout_manual"]
    if pay == "MEDIA":
        return a.get("payout_media") or a.get("payout")
    if pay == "ATUAL":
        return a.get("payout_12m") or a.get("payout")
    return a.get("payout")


def independente(seed: dict, of: dict, P: dict, base: str, pay: str):
    """Dividendo e preço teto a partir do consenso (R$ mi), das ações (mi) e do payout."""
    fator, qtd = seed.get("fator") or 1, of["qtd_mi"]
    if seed["modelo"] == "POLITICA":
        capex = 0.0
        if seed.get("capex_ref") == "PETR":
            capex = P["petr_capex_usd_bi"] * 1000 / P["petr_anos"] * P["cambio"]
        elif seed.get("capex_ref") == "VALE":
            capex = P["vale_capex_usd_bi"] * 1000 * P["cambio"]
        div = (seed.get("payout_manual") if seed.get("payout_manual") is not None else seed["payout"]) * \
            (seed["consenso_mi"] - capex) / qtd * fator
    else:
        div = seed["consenso_mi"] / qtd * fator * escolher_payout({**of, "payout_manual": seed.get("payout_manual")}, pay)
    if base == "CONSERVADOR" and of.get("div_medio6") is not None:
        div = min(div, of["div_medio6"])
    return div / P["dy_min"]


def publicado(a: dict, dy_min: float, base: str, pay: str):
    """Preço teto como as páginas o calculam a partir do dados.json."""
    p = a.get("payout") if a.get("modelo") == "POLITICA" else escolher_payout(a, pay)
    if a.get("modelo") == "POLITICA" and a.get("payout_manual") is not None:
        p = a["payout_manual"]
    div = a["div_base"] * p
    if base == "CONSERVADOR" and a.get("div_medio6") is not None:
        div = min(div, a["div_medio6"])
    return div / dy_min


if __name__ == "__main__":
    seed_doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    dados = Path(sys.argv[2])
    pub = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    of = json.loads((dados / "base.json").read_text(encoding="utf-8"))["ativos"]
    cot = json.loads((dados / "cotacoes.json").read_text(encoding="utf-8"))
    P = {**(seed_doc.get("params") or {}), **(pub.get("params") or {})}
    P["cambio"] = (cot.get("ptax") or {}).get("valor") or P.get("cambio")
    if (seed_doc.get("params") or {}).get("cambio_manual"):
        P["cambio"] = seed_doc["params"]["cambio_manual"]
    linhas, difs, faltam = 0, [], []
    for tk, seed in (seed_doc.get("ativos") or {}).items():
        a = (pub.get("ativos") or {}).get(tk)
        if tk not in of or not a or a.get("div_base") is None:
            faltam.append(tk)
            continue
        for base, pay in COMBOS:
            x, y = independente(seed, of[tk], P, base, pay), publicado(a, P["dy_min"], base, pay)
            linhas += 1
            if abs(x - y) > 0.01:
                difs.append(f"{tk} {base}/{pay}: recalculado R$ {x:.2f} x publicado R$ {y:.2f}")
    ok = not difs and not faltam
    print(json.dumps({"ok": ok, "linhas": linhas, "divergentes": difs[:20], "sem_dado_publicado": faltam,
                      "cambio": P["cambio"], "dy_min": P["dy_min"]}, ensure_ascii=False))
    sys.exit(0 if ok else 1)
