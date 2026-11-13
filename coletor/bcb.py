"""Câmbio oficial: PTAX de venda do Banco Central (API Olinda)."""
from __future__ import annotations

from datetime import date, timedelta

from .util import get_json

URL = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
       "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
       "?@dataInicial='{ini}'&@dataFinalCotacao='{fim}'&$format=json&$select=cotacaoVenda,dataHoraCotacao")


def ptax(ate: date | None = None) -> tuple[float, str] | None:
    """PTAX de venda do último dia útil até `ate` (inclusive): (valor, data ISO)."""
    ate = ate or date.today()
    ini = ate - timedelta(days=10)
    js = get_json(URL.format(ini=f"{ini:%m-%d-%Y}", fim=f"{ate:%m-%d-%Y}"))
    vals = (js or {}).get("value") or []
    if not vals:
        return None
    ult = vals[-1]
    return float(ult["cotacaoVenda"]), str(ult["dataHoraCotacao"])[:10]
