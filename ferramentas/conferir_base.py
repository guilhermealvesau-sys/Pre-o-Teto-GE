"""Confere a cópia da base gravada pela página principal em publicacao/base e grava o base.json publicado.

Uso:
  python conferir_base.py DOC_JSON SAIDA_JSON

  DOC_JSON    a linha de dados do documento publicacao/base, copiada como veio do banco
              ({"json": "...", "sha256": "...", "n": 44, "criado_em": "..."}; aceita também {"data": {...}})
  SAIDA_JSON  base.json no formato publicado: {"gerado_em", "origem", "ativos", "params"}

Sai com erro (e não grava nada) se o SHA-256 do texto não bater com o gravado pela página,
se o número de papéis divergir ou se faltar consenso, modelo ou grupo em algum papel.
"""
import hashlib
import json
import sys
from pathlib import Path

doc_p, out_p = Path(sys.argv[1]), Path(sys.argv[2])
d = json.loads(doc_p.read_text(encoding="utf-8").strip())
if "json" not in d and isinstance(d.get("data"), dict):
    d = d["data"]
txt = d.get("json") or ""
sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
if sha != d.get("sha256"):
    raise SystemExit(f"SHA-256 diferente: calculado {sha[:12]}…, gravado {str(d.get('sha256'))[:12]}… (cópia com erro)")
b = json.loads(txt)
ativos, params = b.get("ativos") or {}, b.get("params") or {}
if len(ativos) != d.get("n"):
    raise SystemExit(f"{len(ativos)} papéis na cópia, {d.get('n')} gravados pela página")
falta = [tk for tk, a in ativos.items() if a.get("consenso_mi") is None or not a.get("modelo") or not a.get("grupo")]
if falta:
    raise SystemExit("papéis sem consenso, modelo ou grupo: " + ", ".join(sorted(falta)))
if not params.get("dy_min"):
    raise SystemExit("parâmetros sem dy_min")
out = {"gerado_em": b.get("criado_em") or d.get("criado_em"), "origem": "banco da página principal (publicacao/base, conferido por SHA-256)",
       "ativos": ativos, "params": params}
out_p.parent.mkdir(parents=True, exist_ok=True)
out_p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(json.dumps({"ok": True, "papeis": len(ativos), "gerado_em": out["gerado_em"], "sha256": sha}, ensure_ascii=False))
