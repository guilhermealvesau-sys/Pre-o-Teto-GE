"""Monta os documentos com os dados oficiais (coleção "oficial") para a base da página Preço Teto GE.

Uso:
  python preparar_sync.py DADOS_DIR SAIDA_DIR [ANTERIOR]

  DADOS_DIR: base.json, cotacoes.json, avisos.json, fatos.json e noticias.json (do repositório)
  SAIDA_DIR: recebe dados.json, noticias.json e historico.json, um para cada documento da coleção
             "oficial" da página (ArtifactData set, doc_id = nome do arquivo sem .json).
  ANTERIOR:  o oficial/dados hoje gravado na página (arquivo, ou a pasta out_dir de uma leitura da coleção
             "oficial"), opcional, para listar só os avisos novos desde a última gravação.

A página combina estes documentos com os seus próprios dados (consenso e regras das commodities,
que a automação nunca altera).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CAMPOS = ("qtd_mi", "lucro12m_mi", "lucro12m_ate", "vpa", "dy_medio", "pl_medio", "div_medio6", "pct_jcp",
          "payout", "payout_12m", "payout_media6", "payout_anos", "payout_media", "payout_media_anos")


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


def montar_noticias(dados: Path) -> dict:
    fat = ler(dados / "fatos.json", {}) or {}
    nts = ler(dados / "noticias.json", {}) or {}
    sites = (ler(dados / "base.json", {}) or {}).get("sites") or {}
    ems = sorted(set(fat.get("por_emissor") or {}) | set(nts.get("por_emissor") or {}))
    return {"fatos": {"atualizado_em": fat.get("atualizado_em"), "ultimo_documento": fat.get("ultimo_documento"),
                      "fonte": fat.get("fonte")},
            "noticias": {"atualizado_em": nts.get("atualizado_em"), "fonte": nts.get("fonte"), "falhas": nts.get("falhas")},
            "por_emissor": {em: {"fatos": (fat.get("por_emissor") or {}).get(em, [])[:10],
                                 "noticias": (nts.get("por_emissor") or {}).get(em, [])[:8],
                                 "site": sites.get(em)} for em in ems}}


def montar_historico(dados: Path) -> dict:
    base = ler(dados / "base.json", {}) or {}
    itens = {}
    for tk, v in (base.get("ativos") or {}).items():
        d = v.get("detalhe") or {}
        prov, preco, pay = d.get("proventos_por_ano") or {}, d.get("preco_medio_ano") or {}, d.get("payout_por_ano") or {}
        lucro = d.get("lucro_anual_mi") or {}
        anos = sorted(set(prov) | set(lucro))
        if not anos:
            continue
        r = lambda x, n=4: None if x is None else round(x, n)  # noqa: E731
        itens[tk] = {
            "anos": anos,
            "prov": [r(prov.get(y)) for y in anos],
            "preco": [r(preco.get(y), 2) for y in anos],
            "dy": [r(prov[y] / preco[y]) if prov.get(y) is not None and preco.get(y) else None for y in anos],
            "lucro_mi": [r(lucro.get(y), 1) for y in anos],
            "payout": [r(pay[y]["distribuido_mi"] / pay[y]["lucro_mi"], 3)
                       if (pay.get(y) or {}).get("lucro_mi") and pay[y]["lucro_mi"] > 0 and pay[y].get("distribuido_mi") is not None
                       else None for y in anos],
        }
    return {"gerado_em": base.get("gerado_em"), "itens": itens}


def avisos_desde(doc: dict, anterior: dict | None) -> list[str]:
    """Avisos novos detectados depois da última gravação na página (ou nos últimos 7 dias, se não houver)."""
    from datetime import datetime, timedelta, timezone
    corte = ((anterior or {}).get("avisos") or {}).get("atualizado_em") or \
        (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
    return [f"{a['empresa']} ({a['data']}): {a['assunto'][:120]}" for a in doc["avisos"]["itens"]
            if a.get("novo") and (a.get("detectado_em") or "") > corte]


if __name__ == "__main__":
    dados, saida = Path(sys.argv[1]), Path(sys.argv[2])
    saida.mkdir(parents=True, exist_ok=True)
    docs = {"dados": montar(dados), "noticias": montar_noticias(dados), "historico": montar_historico(dados)}
    tam = {}
    for nome, doc in docs.items():
        txt = json.dumps(doc, ensure_ascii=False)
        (saida / f"{nome}.json").write_text(txt, encoding="utf-8")
        tam[nome] = len(txt.encode())
    ant = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    if ant and ant.is_dir():
        ant = ant / "oficial" / "dados.json"
    novos = avisos_desde(docs["dados"], ler(ant) if ant else None)
    d, n = docs["dados"], docs["noticias"]["por_emissor"]
    print(json.dumps({"pregao": d["cotacoes"]["ref"], "papeis_cotados": len(d["cotacoes"]["itens"]),
                      "base": d["base"]["gerado_em"], "avisos_novos": novos,
                      "manchetes": sum(len(v["noticias"]) for v in n.values()),
                      "fatos": sum(len(v["fatos"]) for v in n.values()), "bytes": tam}, ensure_ascii=False))
