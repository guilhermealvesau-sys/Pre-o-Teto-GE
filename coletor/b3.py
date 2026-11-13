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


_amostra_logada = set()


def _logar_amostra(nome: str, js) -> None:
    if nome in _amostra_logada:
        return
    _amostra_logada.add(nome)
    txt = json.dumps(js, ensure_ascii=False)[:700]
    log.info("B3 %s (amostra): %s", nome, txt)


def empresa(emissor: str) -> dict | None:
    """Cadastro da B3 do emissor: nome de pregão, código CVM e CNPJ."""
    js = get_json(f"{PROXY}/GetInitialCompanies/{_b64({'language': 'pt-br', 'pageNumber': 1, 'pageSize': 20, 'company': emissor})}")
    _logar_amostra("GetInitialCompanies", js)
    res = (js or {}).get("results") or []
    for r in res:
        if str(r.get("issuingCompany", "")).upper() == emissor.upper():
            return {"tradingName": r.get("tradingName"), "codeCVM": str(r.get("codeCVM") or "").strip(),
                    "cnpj": r.get("cnpj"), "companyName": r.get("companyName")}
    return None


def proventos_historico(trading_name: str, paginas: int = 20) -> list[dict]:
    """Histórico completo de proventos em dinheiro (todas as páginas) pelo nome de pregão."""
    eventos, pag, total = [], 1, 1
    while pag <= min(total, paginas):
        q = {"language": "pt-br", "pageNumber": pag, "pageSize": 120, "tradingName": trading_name}
        js = get_json(f"{PROXY}/GetListedCashDividends/{_b64(q)}")
        _logar_amostra("GetListedCashDividends", js)
        if not js:
            break
        total = int(((js.get("page") or {}).get("totalPages")) or 1)
        for ev in js.get("results") or []:
            classe = str(ev.get("typeStock") or "").upper().strip()
            classe = {"ON": "ON", "PN": "PN", "PNA": "PNA", "PNB": "PNB", "PNC": "PNC"}.get(classe)
            valor = _num(ev.get("valueCash"))
            dc = _data(ev.get("lastDatePriorEx"))
            if not classe or valor is None or dc is None:
                continue
            eventos.append({"classe": classe, "tipo": _tipo(ev.get("corporateAction")), "valor": valor,
                            "data_com": dc.isoformat(), "pagamento": None, "ano_ref": dc.year, "referente": ""})
        pag += 1
    return eventos


def proventos(emissor: str, trading_name: str | None = None) -> list[dict]:
    """Proventos em dinheiro do emissor: classe, tipo, valor por ação (bruto), data com, ano de referência.

    O histórico vem de GetListedCashDividends (todas as datas); os eventos recentes de
    GetListedSupplementCompany trazem também o período a que se referem (usado no payout)."""
    eventos: list[dict] = []
    for nome in [n for n in (trading_name, (trading_name or "").replace("/", " ").strip(), emissor) if n]:
        try:
            eventos = proventos_historico(nome)
        except Exception as e:  # noqa: BLE001
            log.warning("B3: histórico de proventos de %s (%r) falhou: %s", emissor, nome, e)
        if eventos:
            break
        log.warning("B3: histórico vazio para %s com o nome %r", emissor, nome)
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
    # as duas listagens repetem eventos, às vezes com o valor revisado na 4ª casa: mesmo (classe, tipo,
    # data com) e valores a menos de 2% um do outro contam uma vez, ficando o que traz o período de referência
    grupos: dict = {}
    for e in eventos:
        grupos.setdefault((e["classe"], e["tipo"], e["data_com"]), []).append(e)
    unicos = []
    for evs in grupos.values():
        evs.sort(key=lambda e: (not e["referente"],))
        mantidos: list[dict] = []
        for e in evs:
            if not any(abs(e["valor"] - m["valor"]) <= 0.02 * max(abs(m["valor"]), 1e-9) for m in mantidos):
                mantidos.append(e)
        unicos += mantidos
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
