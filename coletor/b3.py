"""Dados oficiais da B3: proventos em dinheiro por emissor e série histórica de cotações (COTAHIST)."""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta

from .util import CACHE, baixar, get_json, log, norm

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


def site_ri(code_cvm: str) -> str | None:
    """Site da companhia informado no cadastro da B3 (normalmente a página de relações com investidores)."""
    if not code_cvm:
        return None
    js = get_json(f"{PROXY}/GetDetail/{_b64({'codeCVM': str(code_cvm), 'language': 'pt-br'})}")
    _logar_amostra("GetDetail", js)
    if not isinstance(js, dict):
        return None
    for k, v in js.items():
        if isinstance(v, str) and ("site" in k.lower() or "web" in k.lower()) and "." in v:
            v = v.strip()
            return v if v.lower().startswith("http") else "https://" + v.lstrip("/")
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


def _fator_evento(label: str, fator: float) -> float | None:
    """Quantas ações passa a valer cada ação: desdobramento e bonificação de f% → 1 + f/100; grupamento → f."""
    r = norm(label)
    if "grupamento" in r:
        return fator if fator > 0 else None
    if "desdobramento" in r or "bonificacao" in r:
        return 1 + fator / 100
    return None


def suplemento(emissor: str) -> dict:
    js = get_json(f"{PROXY}/GetListedSupplementCompany/{_b64({'issuingCompany': emissor, 'language': 'pt-br'})}")
    return js[0] if isinstance(js, list) and js else (js or {})


def eventos_acoes(bloco: dict) -> list[dict]:
    """Desdobramentos, grupamentos e bonificações: classe, fator (ações novas por ação antiga) e data com.

    Os valores de proventos da B3 são os declarados na época (não são ajustados depois de um desdobramento),
    então os cálculos convertem cada provento para a base de ações de hoje com estes fatores."""
    out, vistos = [], set()
    for ev in bloco.get("stockDividends") or []:
        classe = _classe(ev.get("isinCode") or ev.get("assetIssued"))
        f, dc = _num(ev.get("factor")), _data(ev.get("lastDatePrior"))
        k = _fator_evento(ev.get("label") or "", f) if f is not None else None
        if not classe or not dc or not k or k == 1:
            continue
        chave = (classe, dc.isoformat(), round(k, 10), norm(ev.get("label")))
        if chave in vistos:
            continue
        vistos.add(chave)
        out.append({"classe": classe, "data_com": dc.isoformat(), "fator": k, "tipo": (ev.get("label") or "").upper()})
    return sorted(out, key=lambda e: e["data_com"])


def acoes_b3(bloco: dict) -> dict:
    """Quantidade de ações no cadastro da B3 (inclui tesouraria) e subscrições recentes (aumentos de capital)."""
    sub = []
    for ev in bloco.get("subscriptions") or []:
        d = _data(ev.get("subscriptionDate")) or _data(ev.get("lastDatePrior"))
        if d:
            sub.append({"data": d.isoformat(), "percentual": _num(ev.get("percentage")), "classe": _classe(ev.get("isinCode"))})
    def inteiro(v):  # a B3 manda "11.196.864.781" (ponto como separador de milhar)
        s = re.sub(r"\D", "", str(v or ""))
        return float(s) if s else None
    return {"on": inteiro(bloco.get("numberCommonShares")), "pn": inteiro(bloco.get("numberPreferredShares")),
            "total": inteiro(bloco.get("totalNumberShares")), "subscricoes": sub}


def proventos(emissor: str, trading_name: str | None = None, bloco: dict | None = None) -> list[dict]:
    """Proventos em dinheiro do emissor: classe, tipo, valor por ação (bruto, como declarado), data com.

    O histórico vem de GetListedCashDividends (todas as datas; um registro por parcela de pagamento) e os
    eventos recentes de GetListedSupplementCompany (com data de pagamento e período de referência)."""
    eventos: list[dict] = []
    for nome in [n for n in (trading_name, (trading_name or "").replace("/", " ").strip(), emissor) if n]:
        try:
            eventos = proventos_historico(nome)
        except Exception as e:  # noqa: BLE001
            log.warning("B3: histórico de proventos de %s (%r) falhou: %s", emissor, nome, e)
        if eventos:
            break
        log.warning("B3: histórico vazio para %s com o nome %r", emissor, nome)
    hist = eventos
    if bloco is None:
        bloco = suplemento(emissor)
    sup = []
    for ev in bloco.get("cashDividends") or []:
        classe = _classe(ev.get("isinCode") or ev.get("assetIssued"))
        valor, dc = _num(ev.get("rate")), _data(ev.get("lastDatePrior"))
        if not classe or valor is None or dc is None:
            continue
        sup.append({"classe": classe, "tipo": _tipo(ev.get("label")), "valor": valor,
                    "data_com": dc.isoformat(), "pagamento": (_data(ev.get("paymentDate")) or dc).isoformat(),
                    "ano_ref": dc.year, "referente": ev.get("relatedTo") or ""})
    # suplemento: o mesmo evento repetido (mesma data com e de pagamento, valor revisado na 4ª casa) conta uma vez;
    # parcelas do mesmo provento têm datas de pagamento diferentes e contam todas
    unicos_sup: list[dict] = []
    for e in sup:
        if not any(m["classe"] == e["classe"] and m["tipo"] == e["tipo"] and m["data_com"] == e["data_com"]
                   and m["pagamento"] == e["pagamento"] and abs(m["valor"] - e["valor"]) <= 0.02 * max(abs(m["valor"]), 1e-9)
                   for m in unicos_sup):
            unicos_sup.append(e)
    # as duas listagens repetem os eventos recentes: cada evento do suplemento substitui UM registro equivalente
    # do histórico (pareamento um a um). Registros iguais no histórico são parcelas (ex.: Cemig e Petrobras pagam
    # em duas parcelas) e não podem ser fundidos.
    usados: set[int] = set()
    for s in unicos_sup:
        for i, h in enumerate(hist):
            if i in usados or h["classe"] != s["classe"] or h["tipo"] != s["tipo"] or h["data_com"] != s["data_com"]:
                continue
            if abs(h["valor"] - s["valor"]) <= 0.02 * max(abs(s["valor"]), 1e-9):
                usados.add(i)
                break
    eventos = [h for i, h in enumerate(hist) if i not in usados] + unicos_sup
    if not eventos:
        chaves = list(bloco.keys()) if isinstance(bloco, dict) else type(bloco).__name__
        log.warning("B3: nenhum provento para %s (resposta: %s)", emissor, chaves)
    return sorted(eventos, key=lambda e: (e["data_com"], e["classe"], e["tipo"]))


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


def fechamentos_ano(ano: int, tickers: set[str]) -> dict[str, list]:
    """Fechamentos diários do ano por ticker: [[AAAA-MM-DD, preço], ...] (cache local, fora do repositório)."""
    cache = CACHE / f"fechamentos_{ano}.json"
    if ano < date.today().year and cache.exists():
        try:
            js = json.loads(cache.read_text())
            if set(js.get("tickers", [])) >= set(tickers):
                return {tk: v for tk, v in js["dados"].items() if tk in tickers}
        except ValueError:
            pass
    p = baixar(f"{SERHIST}/COTAHIST_A{ano}.ZIP", reusar=ano < date.today().year, timeout=600)
    if p is None:
        return {}
    out: dict[str, list] = {}
    for tk, dia, px in _linhas_cotahist(zipfile.ZipFile(p), tickers):
        out.setdefault(tk, []).append([f"{dia[:4]}-{dia[4:6]}-{dia[6:]}", px])
    for v in out.values():
        v.sort()
    if ano < date.today().year:
        cache.write_text(json.dumps({"tickers": sorted(tickers), "dados": out}))
    return out


def precos_ano(ano: int, tickers: set[str], fatores: dict[str, list] | None = None) -> dict[str, dict]:
    """Preço médio de fechamento e último fechamento do ano, por ticker.

    `media` usa os preços como negociados; `media_aj` converte cada dia para a base de ações de hoje com os
    fatores de desdobramento/bonificação/grupamento posteriores a ele ({ticker: [(data_com, fator), ...]})."""
    fatores = fatores or {}
    out = {}
    for tk, serie in fechamentos_ano(ano, tickers).items():
        if not serie:
            continue
        fs = fatores.get(tk, [])
        aj = [px / fator_ate_hoje(fs, dia) for dia, px in serie]
        out[tk] = {"media": round(sum(px for _, px in serie) / len(serie), 4), "fim": serie[-1][1], "dias": len(serie),
                   "media_aj": round(sum(aj) / len(aj), 4), "fim_aj": round(aj[-1], 4)}
    return out


def fator_ate_hoje(fatores: list, data_iso: str) -> float:
    """Produto dos fatores de eventos com data com em `data_iso` ou depois (na data com a ação ainda é a antiga)."""
    g = 1.0
    for d, k in fatores:
        if d >= data_iso:
            g *= k
    return g


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
