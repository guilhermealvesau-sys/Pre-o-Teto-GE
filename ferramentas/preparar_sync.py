"""Monta o documento único com os dados oficiais para a base da página Preço Teto GE.

Uso:
  python preparar_sync.py DADOS_DIR SAIDA.json [ANTERIOR.json]

  DADOS_DIR: base.json, cotacoes.json e avisos.json (do repositório)
  SAIDA.json: documento para gravar em collection "oficial", doc_id "dados" (ArtifactData set).
  ANTERIOR.json: o documento hoje gravado na página (opcional), para listar só os avisos novos desde então.

A página combina este documento com os seus próprios dados (consenso e regras das commodities,
que a automação nunca altera).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CAMPOS = ("qtd_mi", "lucro12m_mi", "lucro12m_ate", "vpa", "dy_medio", "pl_medio", "div_medio6", "pct_jcp",
          "payout", "payout_12m", "payout_media6", "payout_anos")


def ler(p: Path, padrao=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def montar(dados: Path) -> dict:
    base = ler(dados / "base.json", {}) or {}
    cot = ler(dados / "cotacoes.json", {}) or {}
    avs = ler(dados / "avisos.json", {}) or {}
    itens = {}
    for tk, v in (base.get("ativos") or {}).items():
        d = {k: v[k] for k in CAMPOS if v.get(k) is not None}
        if v.get("avisos"):
            d["avisos_dados"] = "; ".join(v["avisos"])
        itens[tk] = d
    ates = [v["lucro12m_ate"] for v in itens.values() if v.get("lucro12m_ate")]
    params = {}
    if (cot.get("ptax") or {}).get("valor"):
        params.update(cambio=cot["ptax"]["valor"], cambio_data=cot["ptax"]["data"])
    pe = base.get("petrobras") or {}
    if pe.get("divida_bruta_usd_bi"):
        params.update(petr_divida_usd_bi=pe["divida_bruta_usd_bi"], petr_divida_ref=pe.get("ref"))
    return {
        "cotacoes": {"ref": cot.get("ref"), "fonte": "B3 – COTAHIST (arquivo diário oficial)",
                     "lido_em": cot.get("gerado_em"), "itens": cot.get("cotacoes") or {},
                     "avisos": cot.get("avisos") or []},
        "base": {"gerado_em": base.get("gerado_em"), "contabil_ate": max(ates) if ates else None,
                 "fontes": base.get("fontes"), "itens": itens},
        "params": params,
        "avisos": {"atualizado_em": avs.get("atualizado_em"), "fonte": avs.get("fonte"),
                   "itens": [{k: a.get(k) for k in ("emissor", "empresa", "data", "categoria", "assunto", "link",
                                                    "regra", "detectado_em", "novo")}
                             for a in (avs.get("itens") or [])[:60]]},
        "repositorio": "https://github.com/guilhermealvesau-sys/Pre-o-Teto-GE",
    }


def avisos_desde(doc: dict, anterior: dict | None) -> list[str]:
    """Avisos novos detectados depois da última gravação na página (ou nos últimos 7 dias, se não houver)."""
    from datetime import datetime, timedelta, timezone
    corte = ((anterior or {}).get("avisos") or {}).get("atualizado_em") or \
        (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
    return [f"{a['empresa']} ({a['data']}): {a['assunto'][:120]}" for a in doc["avisos"]["itens"]
            if a.get("novo") and (a.get("detectado_em") or "") > corte]


if __name__ == "__main__":
    # python preparar_sync.py DADOS_DIR SAIDA.json [ANTERIOR.json]
    doc = montar(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    anterior = ler(Path(sys.argv[3])) if len(sys.argv) > 3 else None
    novos = avisos_desde(doc, anterior)
    print(json.dumps({"pregao": doc["cotacoes"]["ref"], "papeis_cotados": len(doc["cotacoes"]["itens"]),
                      "base": doc["base"]["gerado_em"], "avisos_novos": novos,
                      "bytes": len(json.dumps(doc, ensure_ascii=False).encode())}, ensure_ascii=False))
