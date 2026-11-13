"""Funções comuns: download com cache e novas tentativas, normalização de texto e registro."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("COLETOR_CACHE", RAIZ / ".cache"))
CACHE.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("coletor")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

SESSAO = requests.Session()
SESSAO.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) preco-teto-ge/1.0 (+coletor de dados publicos)",
    "Accept": "*/*",
})


def baixar(url: str, destino: Path | None = None, *, tentativas: int = 4, timeout: int = 120,
           reusar: bool = True) -> Path | None:
    """Baixa `url` para o cache (ou `destino`). Devolve o caminho, ou None se o recurso não existe (404)."""
    destino = destino or CACHE / re.sub(r"[^A-Za-z0-9_.-]", "_", url.split("://", 1)[-1])[-180:]
    if reusar and destino.exists() and destino.stat().st_size > 0:
        return destino
    ultimo = None
    for i in range(tentativas):
        try:
            with SESSAO.get(url, stream=True, timeout=timeout) as r:
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                tmp = destino.with_suffix(destino.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(destino)
                log.info("baixado %s (%.1f MB)", url, destino.stat().st_size / 1e6)
                return destino
        except requests.RequestException as e:  # noqa: PERF203
            ultimo = e
            espera = 2 ** i * 3
            log.warning("falha ao baixar %s (%s); nova tentativa em %ss", url, e, espera)
            time.sleep(espera)
    raise RuntimeError(f"não foi possível baixar {url}: {ultimo}")


def get_json(url: str, *, tentativas: int = 4, timeout: int = 60, **kw):
    ultimo = None
    for i in range(tentativas):
        try:
            r = SESSAO.get(url, timeout=timeout, **kw)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            ultimo = e
            time.sleep(2 ** i * 2)
    raise RuntimeError(f"falha em {url}: {ultimo}")


def norm(s: str) -> str:
    """Minúsculas, sem acentos e com espaços simples — para comparar descrições de contas e nomes."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def salvar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def ler_json(caminho: Path, padrao=None):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao
