"""Banco Central: PTAX de venda (API Olinda), Selic meta e IPCA acumulado em 12 meses (séries do SGS)."""
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


SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/1?formato=json"
SERIES = {"selic": (432, "Meta Selic definida pelo Copom (% a.a.)"), "ipca12m": (13522, "IPCA acumulado em 12 meses (%)")}


def _data_iso(d: str) -> str:
    dia, mes, ano = d.split("/")
    return f"{ano}-{mes}-{dia}"


def macro() -> dict:
    """Último valor da Selic meta e do IPCA 12 meses: {nome: {valor (%), data ISO, descricao, serie}}."""
    out = {}
    for nome, (serie, desc) in SERIES.items():
        js = get_json(SGS.format(serie=serie))
        if isinstance(js, list) and js:
            ult = js[-1]
            out[nome] = {"valor": float(str(ult["valor"]).replace(",", ".")), "data": _data_iso(ult["data"]),
                         "descricao": desc, "serie": serie}
    return out
