"""Monta os arquivos publicados junto com as páginas, sem passar pelo banco delas.

Uso:
  python montar_arquivos.py DADOS_DIR BASE_JSON RESUMOS_JSON SAIDA_DIR [OFICIAL_ANTERIOR]

  DADOS_DIR         dados/ do repositório (base.json, cotacoes.json, avisos.json, fatos.json, noticias.json)
  BASE_JSON         base.json publicado na página principal: {"ativos": {ticker: {...}}, "params": {...}}
                    (consenso e regras do usuário; a página principal o exporta pelo botão de publicação)
  RESUMOS_JSON      resumos.json publicado na página principal: {"itens": {código: panorama}}
  SAIDA_DIR         recebe oficial/dados.json, oficial/noticias.json e oficial/historico.json (página principal)
                    e dados.json (dashboard público e página de visualização)
  OFICIAL_ANTERIOR  oficial/dados.json publicado antes (opcional), para listar só os avisos novos

Imprime um resumo em JSON (pregão, papéis, resumos, avisos novos).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import montar_publico as mp  # noqa: E402
import preparar_sync as ps  # noqa: E402


def escrever(p: Path, doc: dict) -> int:
    p.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    p.write_text(txt, encoding="utf-8")
    return len(txt.encode())


def main(argv: list[str]) -> dict:
    dados, base_p, res_p, saida = (Path(a) for a in argv[:4])
    ant = Path(argv[4]) if len(argv) > 4 else None
    base = json.loads(base_p.read_text(encoding="utf-8"))
    if not base.get("ativos"):
        raise SystemExit("base.json sem ativos: nada foi gerado")
    res = json.loads(res_p.read_text(encoding="utf-8")) if res_p.exists() else {}
    docs = {"dados": ps.montar(dados), "noticias": ps.montar_noticias(dados), "historico": ps.montar_historico(dados)}
    if not docs["dados"]["base"].get("itens"):
        raise SystemExit("dados do repositório sem papéis: nada foi gerado")
    tam = {f"oficial/{n}.json": escrever(saida / "oficial" / f"{n}.json", d) for n, d in docs.items()}
    painel = {"ativos": base["ativos"], "cotacoes": {}, "params": base.get("params"), "status": None,
              "resumos": res.get("itens") or {}}
    pub = mp.montar(painel, docs["dados"], docs["noticias"])
    tam["dados.json"] = escrever(saida / "dados.json", pub)
    novos = ps.avisos_desde(docs["dados"], ps.ler(ant) if ant else None)
    return {"pregao": docs["dados"]["cotacoes"]["ref"], "papeis": len(pub["ativos"]),
            "cotados": len(docs["dados"]["cotacoes"]["itens"]), "resumos": len(pub["resumos"]),
            "avisos_novos": novos, "bytes": tam}


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:]), ensure_ascii=False))
