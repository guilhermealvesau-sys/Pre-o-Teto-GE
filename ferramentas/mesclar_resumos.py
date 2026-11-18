"""Junta panoramas novos ao resumos.json da página principal.

Uso:
  python mesclar_resumos.py RESUMOS_JSON NOVOS_DIR SAIDA_JSON

  RESUMOS_JSON  resumos.json atual (pode não existir)
  NOVOS_DIR     um arquivo <CÓDIGO>.json por panorama novo, com formato, texto, fontes, referencia, gerado_em, gerado_por
  SAIDA_JSON    resumos.json atualizado

Confere cada panorama novo (quatro seções, fontes com URL, gerado_em) antes de aceitar.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SECOES = ("## Segmento e história", "## Como ganha dinheiro", "## Dados financeiros recentes", "## Possíveis impactos")


def conferir(cod: str, d: dict) -> list[str]:
    erros = []
    if not re.fullmatch(r"[A-Z]{4}", cod):
        erros.append("código inválido")
    t = d.get("texto") or ""
    if [s for s in SECOES if s not in t]:
        erros.append("faltam seções")
    if len(t.split()) > 260:
        erros.append(f"{len(t.split())} palavras")
    fs = d.get("fontes") or []
    if not fs or any(not str(f.get("url", "")).startswith("http") for f in fs):
        erros.append("fontes sem URL")
    if not d.get("gerado_em") or not d.get("referencia"):
        erros.append("sem gerado_em ou referencia")
    return erros


if __name__ == "__main__":
    atual_p, novos_d, saida_p = (Path(a) for a in sys.argv[1:4])
    atual = json.loads(atual_p.read_text(encoding="utf-8")) if atual_p.exists() else {}
    itens = dict(atual.get("itens") or {})
    aceitos, recusados = [], {}
    for f in sorted(novos_d.glob("*.json")):
        cod = f.stem.upper()
        d = json.loads(f.read_text(encoding="utf-8"))
        d.setdefault("formato", 2)
        erros = conferir(cod, d)
        if erros:
            recusados[cod] = erros
            continue
        itens[cod] = d
        aceitos.append(cod)
    out = {"atualizado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
           "itens": dict(sorted(itens.items()))}
    saida_p.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(json.dumps({"aceitos": aceitos, "recusados": recusados, "total": len(itens)}, ensure_ascii=False))
    if recusados and not aceitos:
        sys.exit(1)
