"""Transforma o conteúdo de uma página (paginas/*.html, que começa em <title>) no documento completo publicado como index.html."""
import sys
from pathlib import Path
CAB = ('<!doctype html><html><head><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">'
       '<style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
       'html{scroll-padding-top:env(safe-area-inset-top,0px)}body{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif;background:#fff;color:#000}'
       'img{max-width:100%}[hidden]:not([hidden=until-found i]){display:none!important}</style></head><body>\n')
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
t = src.read_text(encoding="utf-8")
if t.lstrip().lower().startswith("<!doctype"):
    raise SystemExit("já é um documento completo")
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(CAB + t.rstrip("\n") + "\n\n</body></html>", encoding="utf-8")
print(dst, dst.stat().st_size)
