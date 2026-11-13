"""Junta a base da página principal num documento só, para a página de visualização (ranking e notícias).

Uso:
  python espelhar.py LEITURA_DIR SAIDA.json

  LEITURA_DIR: a pasta out_dir das leituras (ArtifactData "list") da página principal, com as subpastas
               ativos/, cotacoes/, config/, status/ e, se houver, resumos/ (um .json por documento).
  SAIDA.json:  documento para gravar na página de visualização em collection "painel", doc_id "base".

A página de visualização combina este documento com os documentos oficiais (oficial/dados e oficial/noticias),
do mesmo jeito que a página principal.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def ler_pasta(p: Path) -> dict:
    out = {}
    if p.is_dir():
        for f in sorted(p.glob("*.json")):
            try:
                out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                pass
    return out


def montar(d: Path) -> dict:
    ativos = ler_pasta(d / "ativos")
    if not ativos:
        raise SystemExit("nenhum documento em ativos/: a leitura da página principal falhou")
    cfg, st = ler_pasta(d / "config"), ler_pasta(d / "status")
    resumos = {k: {c: v.get(c) for c in ("texto", "gerado_em", "fontes", "referencia", "formato") if v.get(c) is not None}
               for k, v in ler_pasta(d / "resumos").items() if v.get("texto")}
    return {"espelhado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "ativos": ativos, "cotacoes": ler_pasta(d / "cotacoes"), "params": cfg.get("params"),
            "status": st.get("geral"), "resumos": resumos}


if __name__ == "__main__":
    doc = montar(Path(sys.argv[1]))
    txt = json.dumps(doc, ensure_ascii=False)
    Path(sys.argv[2]).write_text(txt, encoding="utf-8")
    print(json.dumps({"ativos": len(doc["ativos"]), "cotacoes": len(doc["cotacoes"]), "resumos": len(doc["resumos"]),
                      "bytes": len(txt.encode())}))
