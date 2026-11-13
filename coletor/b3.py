"""Dados oficiais da B3: proventos em dinheiro por emissor e série histórica de cotações (COTAHIST)."""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta

from .util import baixar, get_json, log, norm

PROXY = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
SERHIST = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"

_CLASSE_ISIN = {"NOR": "ON", "NPR": "PN", "NPA": "PNA", "NPB": "PNB", "NPC": "PNC"}


def _b64(d: dict) -> str:
    return base64.b64encode(json.dumps(d, separators=(",", ":")).encode()).decode()


def _num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if "," in s:  # formato brasileiro: 1.234,56
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _data(s) -> date | None:
    if not s:
        return None
    s = str(s).strip()[:10]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _classe(isin: str) -> str | None:
    isin = (isin or "").upper()
    if len(isin) < 11 or isin[6:8] != "AC":
        return None  # units (CD) e outros papéis ficam de fora: a unit é somada pelas classes
    return _CLASSE_ISIN.get(isin[8:11])


def _tipo(rotulo: str) -> str:
    r = norm(rotulo)
    if "jrs" in r or "juros" in r or "jcp" in r:
        return "JCP"
    return "DIVIDENDO" if "divid" in r else ("RENDIMENTO" if "rend" in r else r.upper() or "OUTRO")


def _ano_ref(rel: str, data_com: date | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", rel or "")
    return int(m.group(0)) if m else (data_com.year if data_com else None)


def proventos(emissor: str) -> list[dict]:
    """Proventos em dinheiro do emissor: classe, tipo, valor por ação (bruto), data com, ano de referência."""
    eventos: list[dict] = []
    js = get_json(f"{PROXY}/GetListedSupplementCompany/{_b64({'issuingCompany': emissor, 'language': 'pt-br'})}")
    bloco = js[0] if isinstance(js, list) and js else (js or {})
    for ev in bloco.get("cashDividends") or []:
        classe = _classe(ev.get("isinCode") or ev.get("assetIssued"))
        valor, dc = _num(ev.get("rate")), _data(ev.get("lastDatePrior"))
        if not classe or valor is None or dc is None:
            continue
        eventos.append({"classe": classe, "tipo": _tipo(ev.get("label")), "valor": valor,
                        "data_com": dc.isoformat(), "pagamento": (_data(ev.get("paymentDate")) or dc).isoformat(),
                        "ano_ref": _ano_ref(ev.get("relatedTo"), dc), "referente": ev.get("relatedTo") or ""})
    if not eventos:
        chaves = list(bloco.keys()) if isinstance(bloco, dict) else type(js).__name__
        log.warning("B3: nenhum provento para %s (resposta: %s)", emissor, chaves)
    # a B3 repete o mesmo evento em listagens diferentes: um por (classe, tipo, data com, valor)
    vistos, unicos = set(), []
    for e in eventos:
        k = (e["classe"], e["tipo"], e["data_com"], round(e["valor"], 10))
        if k not in vistos:
            vistos.add(k)
            unicos.append(e)
    return sorted(unicos, key=lambda e: e["data_com"])


# ---------------------------------------------------------------- COTAHIST
def _linhas_cotahist(z: zipfile.ZipFile, tickers: set[str]):
    with z.open(z.namelist()[0]) as f:
        for raw in io.TextIOWrapper(f, encoding="latin-1"):
            if raw[:2] != "01" or raw[24:27] != "010":
                continue
            tk = raw[12:24].strip()
            if tk not in tickers:
                continue
            fat = int(raw[210:217] or 1) or 1
            yield tk, raw[2:10], int(raw[108:121]) / 100 / fat


def precos_ano(ano: int, tickers: set[str]) -> dict[str, dict]:
    """Preço médio de fechamento e último fechamento do ano, por ticker."""
    p = baixar(f"{SERHIST}/COTAHIST_A{ano}.ZIP", reusar=ano < date.today().year, timeout=600)
    if p is None:
        return {}
    soma: dict[str, list] = {}
    for tk, dia, px in _linhas_cotahist(zipfile.ZipFile(p), tickers):
        s = soma.setdefault(tk, [0.0, 0, "", 0.0])
        s[0] += px
        s[1] += 1
        if dia >= s[2]:
            s[2], s[3] = dia, px
    return {tk: {"media": round(v[0] / v[1], 4), "fim": v[3], "dias": v[1]} for tk, v in soma.items()}


def ultimos_pregoes(tickers: set[str], n: int = 2, recuo: int = 10) -> list[tuple[str, dict]]:
    """Fechamentos dos `n` últimos pregões disponíveis (arquivos diários da B3)."""
    out = []
    d = date.today()
    for _ in range(recuo):
        if len(out) >= n:
            break
        if d.weekday() < 5:
            p = baixar(f"{SERHIST}/COTAHIST_D{d:%d%m%Y}.ZIP", reusar=d < date.today())
            if p is not None:
                try:
                    px = {tk: v for tk, _, v in _linhas_cotahist(zipfile.ZipFile(p), tickers)}
                    if px:
                        out.append((d.isoformat(), px))
                except zipfile.BadZipFile:
                    log.warning("arquivo diário inválido em %s", d)
        d -= timedelta(days=1)
    return out
