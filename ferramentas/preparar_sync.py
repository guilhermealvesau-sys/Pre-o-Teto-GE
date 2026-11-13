"""Prepara as gravações que levam os dados coletados para a base da página Preço Teto GE.

Uso:
  python preparar_sync.py DADOS_DIR DUMP_DIR SAIDA_DIR

  DADOS_DIR: base.json, cotacoes.json e avisos.json (do repositório)
  DUMP_DIR:  leitura atual da base da página (ArtifactData list com out_dir), com as pastas
             ativos/, cotacoes/, avisos/, config/, status/
  SAIDA_DIR: recebe docs/*.json e os lotes writes_*.json (até 50 gravações cada), prontos para
             ArtifactData action "batch". O consenso e as regras das commodities nunca são alterados.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CAMPOS = ("qtd_mi", "lucro12m_mi", "lucro12m_ate", "vpa", "dy_medio", "pl_medio", "div_medio6", "pct_jcp",
          "payout_12m", "payout_media6", "payout_anos")


def ler(p: Path, padrao=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def dump(pasta: Path) -> dict[str, tuple[dict, int | None]]:
    """{doc_id: (dados, versão)} a partir dos arquivos que o ArtifactData grava com out_dir."""
    out = {}
    if not pasta.exists():
        return out
    for f in pasta.glob("*.json"):
        d = ler(f, {})
        if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict):
            out[d.get("id") or f.stem] = (d["data"], d.get("version"))
        else:
            out[f.stem] = (d, d.get("version") if isinstance(d, dict) else None)
    return out


def main(dados: Path, dmp: Path, saida: Path) -> None:
    base = ler(dados / "base.json", {}) or {}
    cot = ler(dados / "cotacoes.json", {}) or {}
    avs = ler(dados / "avisos.json", {}) or {}
    db_at, db_cot, db_av = dump(dmp / "ativos"), dump(dmp / "cotacoes"), dump(dmp / "avisos")
    db_cfg, db_st = dump(dmp / "config"), dump(dmp / "status")
    docs = saida / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    lotes: dict[str, list] = {"cotacoes": [], "ativos": [], "avisos": []}

    def gravar(lote, colecao, doc_id, dados_doc, versao, op="update"):
        f = docs / f"{colecao}__{doc_id}.json"
        f.write_text(json.dumps(dados_doc, ensure_ascii=False), encoding="utf-8")
        e = {"op": op if versao else "set", "collection": colecao, "doc_id": doc_id, "file_path": str(f.resolve())}
        if versao:
            e["if_version"] = versao
        lotes[lote].append(e)

    # ---- cotações do último pregão
    ref = cot.get("ref")
    for tk, v in (cot.get("cotacoes") or {}).items():
        atual, ver = db_cot.get(tk, ({}, None))
        if atual.get("ref") == ref and atual.get("preco") == v.get("preco"):
            continue
        gravar("cotacoes", "cotacoes", tk, {"preco": v["preco"], "var": v.get("var"), "ref": ref,
                                            "fonte": "B3 – COTAHIST", "lido_em": cot.get("gerado_em")}, ver)

    # ---- dados contábeis e de proventos (nunca mexe em consenso_* nem nas regras das commodities)
    for tk, v in (base.get("ativos") or {}).items():
        atual, ver = db_at.get(tk, (None, None))
        if atual is None:
            continue  # papel que a página não acompanha
        upd = {k: v[k] for k in CAMPOS if v.get(k) is not None and atual.get(k) != v[k]}
        if atual.get("modelo") == "POLITICA":
            if v.get("payout") is not None and atual.get("payout_hist") != v["payout"]:
                upd["payout_hist"] = v["payout"]  # só referência: o modelo usa o payout da política
            if "pl_fixo" in atual:
                upd["pl_fixo"] = {"__delete__": True}
        elif v.get("payout") is not None and atual.get("payout") != v["payout"]:
            upd["payout"] = v["payout"]
        avisos_txt = "; ".join(v.get("avisos") or [])
        if atual.get("avisos_dados", "") != avisos_txt:
            upd["avisos_dados"] = avisos_txt
        if upd:
            upd["dados_fonte"] = "CVM + B3"
            upd["dados_coleta"] = base.get("gerado_em")
            gravar("ativos", "ativos", tk, upd, ver)

    # ---- avisos de política (só os que a página ainda não tem)
    for a in avs.get("itens") or []:
        doc_id = re.sub(r"[^A-Za-z0-9_.~:@+-]", "_", str(a["protocolo"]))[:180]
        if doc_id in db_av:
            continue
        gravar("avisos", "avisos", doc_id, {k: a.get(k) for k in (
            "emissor", "empresa", "data", "categoria", "tipo", "assunto", "link", "regra", "detectado_em", "novo")}, None)

    # ---- parâmetros e situação
    cfg, ver_cfg = db_cfg.get("params", ({}, None))
    upd_cfg = {}
    if (cot.get("ptax") or {}).get("valor") and cfg.get("cambio") != cot["ptax"]["valor"]:
        upd_cfg.update(cambio=cot["ptax"]["valor"], cambio_data=cot["ptax"]["data"])
    pe = base.get("petrobras") or {}
    if pe.get("divida_bruta_usd_bi") and cfg.get("petr_divida_usd_bi") != pe["divida_bruta_usd_bi"]:
        upd_cfg.update(petr_divida_usd_bi=pe["divida_bruta_usd_bi"], petr_divida_ref=pe.get("ref"))
    if upd_cfg and ver_cfg:
        gravar("ativos", "config", "params", upd_cfg, ver_cfg)
    st, ver_st = db_st.get("geral", ({}, None))
    ates = [v.get("lucro12m_ate") for v in (base.get("ativos") or {}).values() if v.get("lucro12m_ate")]
    novos = [a for a in (avs.get("itens") or []) if a.get("novo") and re.sub(r"[^A-Za-z0-9_.~:@+-]", "_", str(a["protocolo"]))[:180] not in db_av]
    upd_st = {"cotacoes_ref": ref, "cotacoes_fonte": "B3 – COTAHIST (arquivo diário oficial)",
              "cotacoes_lido_em": cot.get("gerado_em"), "cotacoes_n": len(cot.get("cotacoes") or {}),
              "cotacoes_erro": None if cot.get("cotacoes") else "; ".join(cot.get("avisos") or ["sem cotações"]),
              "base_gerado_em": base.get("gerado_em"), "contabil_ate": max(ates) if ates else None,
              "avisos_atualizado_em": avs.get("atualizado_em"), "avisos_novos_n": len(novos)}
    upd_st = {k: v for k, v in upd_st.items() if st.get(k) != v}
    if upd_st and ver_st:
        gravar("cotacoes", "status", "geral", upd_st, ver_st)

    resumo = {}
    for nome, l in lotes.items():
        for i in range(0, len(l), 50):
            arq = saida / f"writes_{nome}_{i // 50 + 1}.json"
            arq.write_text(json.dumps(l[i:i + 50], ensure_ascii=False, indent=0), encoding="utf-8")
            resumo[arq.name] = len(l[i:i + 50])
    novos_txt = [f"{a['empresa']}: {a['assunto']}" for a in novos]
    (saida / "resumo.json").write_text(json.dumps({"lotes": resumo, "pregao": ref, "avisos_novos": novos_txt},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"lotes": resumo, "pregao": ref, "avisos_novos": novos_txt}, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
