"""Monta o arquivo de dados do dashboard público (dados.json), publicado junto com a página.

Uso:
  python montar_publico.py PAINEL_BASE.json OFICIAL_DADOS.json OFICIAL_NOTICIAS.json SAIDA.json

  PAINEL_BASE.json      saída de espelhar.py (base da página principal: ativos, cotações, parâmetros, resumos)
  OFICIAL_DADOS.json    documento oficial/dados (preparar_sync.py)
  OFICIAL_NOTICIAS.json documento oficial/noticias (preparar_sync.py)

A combinação é a mesma da página principal (dados oficiais por cima da base; commodities mantêm o payout da
política). O consenso não vai em valor bruto: o arquivo leva o dividendo por papel com payout de 100%
("div_base"), do qual a página tira o dividendo projetado com o payout escolhido.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CAMPOS = ("nome", "setor", "grupo", "ordem", "modelo", "fator", "payout", "payout_media", "payout_media_anos",
          "payout_12m", "payout_manual", "vpa", "dy_medio", "pl_medio", "div_medio6", "pct_jcp", "consenso_data",
          "consenso_base", "pl_fixo")
PARAMS = ("dy_min", "ir_jcp", "pl_faixa", "dy_gap", "payout_gap", "consenso_max_dias", "petr_divida_usd_bi",
          "petr_teto_usd_bi", "petr_capex_usd_bi", "petr_anos", "vale_capex_usd_bi", "cambio")


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def montar(base: dict, of: dict, nts: dict) -> dict:
    ofb = (of.get("base") or {}).get("itens") or {}
    oc = of.get("cotacoes") or {}
    p = {**(base.get("params") or {}), **(of.get("params") or {})}
    if num((base.get("params") or {}).get("cambio_manual")):
        p["cambio"] = base["params"]["cambio_manual"]
    ativos = {}
    for tk, raw in (base.get("ativos") or {}).items():
        a = dict(raw)
        o = dict(ofb.get(tk) or {})
        if a.get("modelo") == "POLITICA":
            o.pop("payout", None)
        a.update(o)
        f = num(a.get("fator")) or 1
        qtd, cons = num(a.get("qtd_mi")), num(a.get("consenso_mi"))
        div_base = None
        if qtd and cons is not None:
            if a.get("modelo") == "POLITICA":
                capex = 0.0
                if a.get("capex_ref") == "PETR":
                    capex = (p.get("petr_capex_usd_bi") or 0) * 1000 / (p.get("petr_anos") or 1) * (p.get("cambio") or 0)
                elif a.get("capex_ref") == "VALE":
                    capex = (p.get("vale_capex_usd_bi") or 0) * 1000 * (p.get("cambio") or 0)
                div_base = (cons - capex) / qtd * f
            else:
                div_base = cons / qtd * f
        lucro = num(a.get("lucro12m_mi"))
        item = {k: a.get(k) for k in CAMPOS if a.get(k) is not None}
        item["div_base"] = round(div_base, 6) if div_base is not None else None
        item["lpa"] = round(lucro / qtd * f, 6) if lucro is not None and qtd else None
        ativos[tk] = item
    cot = {tk: {"preco": c.get("preco"), "var": c.get("var"), "ref": c.get("ref")} for tk, c in (base.get("cotacoes") or {}).items()}
    if oc.get("ref"):
        for tk, c in (oc.get("itens") or {}).items():
            cur = cot.get(tk)
            if not cur or not cur.get("ref") or cur["ref"] <= oc["ref"]:
                cot[tk] = {"preco": c.get("preco"), "var": c.get("var"), "ref": oc["ref"]}
    refs = sorted({c["ref"] for c in cot.values() if c.get("ref")})
    por = {}
    for em, v in (nts.get("por_emissor") or {}).items():
        por[em] = {"fatos": (v.get("fatos") or [])[:5], "noticias": (v.get("noticias") or [])[:8], "site": v.get("site")}
    return {
        "publicado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": {"cotacoes_ref": refs[-1] if refs else None, "base_gerado_em": (of.get("base") or {}).get("gerado_em"),
                   "contabil_ate": (of.get("base") or {}).get("contabil_ate"),
                   "fatos_ate": (nts.get("fatos") or {}).get("ultimo_documento"),
                   "noticias_em": (nts.get("noticias") or {}).get("atualizado_em")},
        "params": {k: p.get(k) for k in PARAMS if p.get(k) is not None},
        "ativos": ativos, "cotacoes": cot,
        "avisos": [{k: a.get(k) for k in ("emissor", "empresa", "data", "categoria", "assunto", "link", "detectado_em", "novo")}
                   for a in ((of.get("avisos") or {}).get("itens") or [])[:20]],
        "noticias": por,
        "resumos": {em: {"texto": r.get("texto"), "gerado_em": r.get("gerado_em")}
                    for em, r in (base.get("resumos") or {}).items() if r.get("texto")},
    }


if __name__ == "__main__":
    ler = lambda a: json.loads(Path(a).read_text(encoding="utf-8"))  # noqa: E731
    doc = montar(ler(sys.argv[1]), ler(sys.argv[2]), ler(sys.argv[3]))
    if not doc["ativos"]:
        raise SystemExit("base sem papéis: nada foi gerado")
    txt = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    Path(sys.argv[4]).write_text(txt, encoding="utf-8")
    print(json.dumps({"papeis": len(doc["ativos"]), "pregao": doc["status"]["cotacoes_ref"], "resumos": len(doc["resumos"]),
                      "bytes": len(txt.encode())}))
