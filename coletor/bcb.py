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
# a série da meta Selic vem preenchida até a próxima reunião do Copom: lê só até hoje, para a data ser a de hoje
SGS_ATE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados?formato=json&dataInicial={ini}&dataFinal={fim}"
SERIES = {"selic": (432, "Meta Selic definida pelo Copom (% a.a.)"), "ipca12m": (13522, "IPCA acumulado em 12 meses (%)")}


def _data_iso(d: str) -> str:
    dia, mes, ano = d.split("/")
    return f"{ano}-{mes}-{dia}"


def macro() -> dict:
    """Último valor da Selic meta e do IPCA 12 meses: {nome: {valor (%), data ISO, descricao, serie}}.

    Selic: `data` é a leitura (hoje) e `vigente_desde`, o primeiro dia do valor atual (efeito da última decisão do
    Copom). IPCA: `data` é o mês de referência."""
    out = {}
    hoje = date.today()
    for nome, (serie, desc) in SERIES.items():
        url = (SGS_ATE.format(serie=serie, ini=f"{hoje - timedelta(days=400):%d/%m/%Y}", fim=f"{hoje:%d/%m/%Y}")
               if nome == "selic" else SGS.format(serie=serie))
        js = get_json(url)
        if isinstance(js, list) and js:
            ult = js[-1]
            v = float(str(ult["valor"]).replace(",", "."))
            out[nome] = {"valor": v, "data": _data_iso(ult["data"]), "descricao": desc, "serie": serie}
            if nome == "selic":
                desde = ult["data"]
                for r in reversed(js):
                    if float(str(r["valor"]).replace(",", ".")) != v:
                        break
                    desde = r["data"]
                out[nome]["vigente_desde"] = _data_iso(desde)
    return out
