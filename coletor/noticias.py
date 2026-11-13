"""Manchetes recentes por empresa, do feed RSS público do Google Notícias (edição Brasil).

Guarda só título, veículo, data e link de cada manchete; o texto das matérias fica nos sites de origem.
A busca de cada empresa vem de config/empresas.csv (coluna `noticias`).
"""
from __future__ import annotations

import email.utils
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

from .util import SESSAO, log, norm

URL = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"


def _data(s: str) -> str | None:
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        return None


def ler_rss(xml: str) -> list[dict]:
    out = []
    root = ET.fromstring(xml)
    for it in root.iter("item"):
        titulo = (it.findtext("title") or "").strip()
        src = it.find("source")
        fonte = (src.text or "").strip() if src is not None else ""
        if fonte and titulo.endswith(" - " + fonte):
            titulo = titulo[: -len(fonte) - 3].strip()
        link = (it.findtext("link") or "").strip()
        if titulo and link.startswith("http"):
            out.append({"titulo": titulo[:240], "fonte": fonte[:80], "data": _data(it.findtext("pubDate") or ""),
                        "link": link})
    return out


def _filtrar(itens: list[dict], chaves: list[str], n: int) -> list[dict]:
    """Mantém manchetes que citam a empresa ou o código, sem repetir títulos, das mais novas para as mais antigas."""
    rx = [re.compile(r"\b" + re.escape(norm(k)) + r"\b") for k in chaves if k]
    vistos, out = set(), []
    for i in sorted(itens, key=lambda i: i.get("data") or "", reverse=True):
        t = norm(i["titulo"])
        chave = re.sub(r"[^a-z0-9 ]", "", t)[:80]
        if chave in vistos or (rx and not any(r.search(t) for r in rx)) or "ao vivo" in t:
            continue  # repetida, não cita a empresa no título, ou cobertura "ao vivo" do pregão
        vistos.add(chave)
        out.append(i)
        if len(out) >= n:
            break
    return out


def buscar(consulta: str, chaves: list[str], dias: int = 14, n: int = 8) -> list[dict]:
    r = SESSAO.get(URL.format(q=quote(f"{consulta} when:{dias}d")), timeout=30)
    r.raise_for_status()
    return _filtrar(ler_rss(r.text), chaves, n)


def coletar(empresas: list[dict]) -> dict:
    """`empresas`: [{"emissor", "nome", "noticias" (consulta), "chaves" (termos que o título deve citar)}]."""
    por = {}
    falhas = []
    for e in empresas:
        if not e.get("noticias"):
            continue
        try:
            por[e["emissor"]] = buscar(e["noticias"], e.get("chaves") or [])
        except Exception as ex:  # noqa: BLE001
            falhas.append(e["emissor"])
            log.warning("notícias de %s indisponíveis: %s", e["emissor"], ex)
    log.info("notícias: %s empresas, %s manchetes%s", len(por), sum(map(len, por.values())),
             f" (falhas: {', '.join(falhas)})" if falhas else "")
    return {"atualizado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "fonte": "Google Notícias (RSS, edição Brasil, últimos 14 dias)", "falhas": falhas, "por_emissor": por}
