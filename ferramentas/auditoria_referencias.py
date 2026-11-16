"""Auditoria: coleta referências independentes para conferir os dados do painel.

Roda no GitHub Actions (os sites da B3, CVM, BCB, IBGE e Fundamentus não são acessíveis de outros ambientes).
Grava, sem tocar em dados/:
  auditoria/referencias.json  Fundamentus (cotação, nº de ações, LPA, VPA, lucro 12M, proventos),
                              Yahoo (fechamentos, desdobramentos/bonificações), IBGE (IPCA 12m),
                              BCB (Selic meta e PTAX), B3 (eventos em ações: desdobramentos, bonificações, grupamentos)
  auditoria/cvm_bruto.json    linhas brutas da CVM (composição do capital e linhas de lucro da DRE) para diagnóstico

Uso: python -m ferramentas.auditoria_referencias
"""
from __future__ import annotations

import html
import io
import json
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from coletor import b3  # noqa: E402
from coletor.util import baixar, get_json  # noqa: E402

SAIDA = RAIZ / "auditoria"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
S = requests.Session()
S.headers.update(UA)


def get(url: str, **kw) -> requests.Response:
    ult = None
    for i in range(3):
        try:
            r = S.get(url, timeout=60, **kw)
            if r.status_code == 200:
                return r
            ult = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ult = str(e)
        time.sleep(2 + 4 * i)
    raise RuntimeError(f"{url}: {ult}")


def limpa(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


# ---------------------------------------------------------------- Fundamentus
def fundamentus(tk: str) -> dict:
    txt = get(f"https://www.fundamentus.com.br/detalhes.php?papel={tk}").content.decode("iso-8859-1", "replace")
    cells = re.findall(r'<td class="(label|data)[^"]*"[^>]*>(.*?)</td>', txt, re.S)
    out: dict[str, list[str]] = {}
    lab = None
    for kind, c in cells:
        v = limpa(c).lstrip("?").strip()
        if kind == "label":
            lab = v
        elif lab:
            out.setdefault(lab, []).append(v)
            lab = None
    return out


def fundamentus_proventos(tk: str) -> list[dict]:
    txt = get(f"https://www.fundamentus.com.br/proventos.php?papel={tk}&tipo=2").content.decode("iso-8859-1", "replace")
    linhas = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S):
        tds = [limpa(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) >= 5 and re.match(r"\d{2}/\d{2}/\d{4}", tds[0]):
            linhas.append({"data_com": tds[0], "valor": tds[1], "tipo": tds[2], "pagamento": tds[3], "por_acoes": tds[4]})
    return linhas


# ---------------------------------------------------------------- Yahoo
def yahoo(tk: str) -> dict:
    out = {}
    js = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}.SA?range=1mo&interval=1d").json()
    r = js["chart"]["result"][0]
    ts, cl = r.get("timestamp") or [], r["indicators"]["quote"][0].get("close") or []
    out["fechamentos"] = {datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"): c for t, c in zip(ts, cl) if c}
    js = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}.SA?range=10y&interval=1mo&events=split").json()
    ev = (js["chart"]["result"][0].get("events") or {}).get("splits") or {}
    out["splits"] = sorted(({"data": datetime.fromtimestamp(v["date"], timezone.utc).strftime("%Y-%m-%d"),
                             "razao": v.get("splitRatio"), "num": v.get("numerator"), "den": v.get("denominator")}
                            for v in ev.values()), key=lambda x: x["data"])
    return out


# ---------------------------------------------------------------- macro
def macro() -> dict:
    out = {}
    try:
        js = get("https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2265/p/last%203").json()
        out["ipca12m_ibge"] = [{"mes": r.get("D3N"), "codigo": r.get("D3C"), "valor": r.get("V")} for r in js[1:]]
    except Exception as e:  # noqa: BLE001
        out["ipca12m_ibge_erro"] = str(e)
    hoje = date.today()
    try:
        url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json&dataInicial="
               f"{hoje - timedelta(days=120):%d/%m/%Y}&dataFinal={hoje + timedelta(days=60):%d/%m/%Y}")
        js = get_json(url)
        mud, ant = [], None
        for r in js or []:
            if r["valor"] != ant:
                mud.append(r)
                ant = r["valor"]
        out["selic_meta_mudancas"] = mud
        out["selic_meta_ultimo"] = (js or [None])[-1]
    except Exception as e:  # noqa: BLE001
        out["selic_erro"] = str(e)
    try:
        js = get_json("https://api.bcb.gov.br/dados/serie/bcdata.sgs.13522/dados/ultimos/3?formato=json")
        out["ipca12m_bcb"] = js
    except Exception as e:  # noqa: BLE001
        out["ipca_bcb_erro"] = str(e)
    try:
        ini, fim = hoje - timedelta(days=12), hoje
        url = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,"
               f"dataFinalCotacao=@dataFinalCotacao)?@dataInicial='{ini:%m-%d-%Y}'&@dataFinalCotacao='{fim:%m-%d-%Y}'"
               "&$format=json&$select=cotacaoCompra,cotacaoVenda,dataHoraCotacao")
        out["ptax_bcb"] = (get_json(url) or {}).get("value")
    except Exception as e:  # noqa: BLE001
        out["ptax_erro"] = str(e)
    try:
        js = get("https://query1.finance.yahoo.com/v8/finance/chart/BRL=X?range=1mo&interval=1d").json()
        r = js["chart"]["result"][0]
        out["usdbrl_yahoo"] = {datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"): c
                               for t, c in zip(r.get("timestamp") or [], r["indicators"]["quote"][0].get("close") or []) if c}
    except Exception as e:  # noqa: BLE001
        out["usdbrl_erro"] = str(e)
    return out


# ---------------------------------------------------------------- B3: eventos em ações
def b3_eventos_acoes(emissor: str) -> dict:
    js = get_json(f"{b3.PROXY}/GetListedSupplementCompany/{b3._b64({'issuingCompany': emissor, 'language': 'pt-br'})}")
    bloco = js[0] if isinstance(js, list) and js else (js or {})
    return {"stockDividends": bloco.get("stockDividends") or [], "subscriptions": bloco.get("subscriptions") or [],
            "chaves": list(bloco.keys()) if isinstance(bloco, dict) else None,
            "numberCommonShares": bloco.get("numberCommonShares"), "numberPreferredShares": bloco.get("numberPreferredShares"),
            "totalNumberShares": bloco.get("totalNumberShares")}


# ---------------------------------------------------------------- CVM bruta
def cvm_bruto(cnpjs: set[str]) -> dict:
    out: dict = {"capital": [], "lucro": []}
    hoje = date.today()
    for tipo, ano in (("ITR", hoje.year), ("DFP", hoje.year - 1), ("ITR", hoje.year - 1)):
        p = baixar(f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{tipo}/DADOS/{tipo.lower()}_cia_aberta_{ano}.zip", reusar=False)
        if p is None:
            continue
        z = zipfile.ZipFile(p)
        for n in z.namelist():
            nl = n.lower()
            if "composicao_capital" in nl:
                with z.open(n) as f:
                    df = pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str)
                df = df[df["CNPJ_CIA"].isin(cnpjs)]
                df["arquivo"] = n
                out["capital"] += df.to_dict("records")
            elif "_dre_" in nl:
                with z.open(n) as f:
                    df = pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str)
                df = df[df["CNPJ_CIA"].isin(cnpjs) & df["CD_CONTA"].str.match(r"^3\.\d\d(\.\d\d)?$")]
                ds = df["DS_CONTA"].str.lower()
                df = df[ds.str.contains("lucro|preju|atribu|controlador", regex=True)]
                df = df[df["ORDEM_EXERC"].str.upper().str.startswith("ÚLTIMO") | df["ORDEM_EXERC"].str.upper().str.startswith("ULTIMO")]
                cols = [c for c in ("CNPJ_CIA", "DENOM_CIA", "DT_REFER", "VERSAO", "DT_INI_EXERC", "DT_FIM_EXERC", "CD_CONTA",
                                    "DS_CONTA", "VL_CONTA", "ESCALA_MOEDA", "MOEDA") if c in df.columns]
                d = df[cols].copy()
                d["arquivo"] = n
                out["lucro"] += d.to_dict("records")
    return out


def main() -> None:
    SAIDA.mkdir(exist_ok=True)
    ativos = pd.read_csv(RAIZ / "config/ativos.csv", dtype=str)
    tickers = list(ativos["ticker"])
    emissores = sorted(set(ativos["emissor"]))
    ref: dict = {"gerado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "fundamentus": {},
                 "fundamentus_proventos": {}, "yahoo": {}, "b3_eventos": {}, "erros": []}
    for tk in tickers:
        try:
            ref["fundamentus"][tk] = fundamentus(tk)
        except Exception as e:  # noqa: BLE001
            ref["erros"].append(f"fundamentus {tk}: {e}")
        try:
            ref["fundamentus_proventos"][tk] = fundamentus_proventos(tk)
        except Exception as e:  # noqa: BLE001
            ref["erros"].append(f"fundamentus proventos {tk}: {e}")
        try:
            ref["yahoo"][tk] = yahoo(tk)
        except Exception as e:  # noqa: BLE001
            ref["erros"].append(f"yahoo {tk}: {e}")
        time.sleep(1.0)
    for em in emissores:
        try:
            ref["b3_eventos"][em] = b3_eventos_acoes(em)
        except Exception as e:  # noqa: BLE001
            ref["erros"].append(f"b3 eventos {em}: {e}")
    ref["macro"] = macro()
    (SAIDA / "referencias.json").write_text(json.dumps(ref, ensure_ascii=False, indent=1), encoding="utf-8")
    print("referências:", len(ref["fundamentus"]), "papéis; erros:", len(ref["erros"]))

    mapa = pd.read_csv(RAIZ / "dados/mapa_cvm.csv", dtype=str)
    bruto = cvm_bruto(set(mapa["cnpj"].dropna()))
    (SAIDA / "cvm_bruto.json").write_text(json.dumps(bruto, ensure_ascii=False), encoding="utf-8")
    print("CVM bruta:", len(bruto["capital"]), "linhas de capital,", len(bruto["lucro"]), "linhas de lucro")


if __name__ == "__main__":
    main()
