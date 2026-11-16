"""Auditoria, parte 2: registros brutos para diagnóstico (roda no GitHub Actions).

Grava auditoria/detalhe.json com:
  b3_proventos_brutos   registros crus da B3 (GetListedCashDividends e GetListedSupplementCompany) por emissor
  yahoo_dividendos      dividendos por papel no Yahoo (valores ajustados a desdobramentos), 10 anos
  cvm_pl                linhas de patrimônio líquido do BPP (consolidado e individual) da CVM, 2025-2026
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from coletor import b3  # noqa: E402
from coletor.util import baixar, get_json  # noqa: E402
from ferramentas.auditoria_referencias import get  # noqa: E402


def b3_brutos(emissor: str, trading: str | None) -> dict:
    out = {"historico": [], "suplemento": None}
    for nome in [n for n in (trading, emissor) if n]:
        pag, total = 1, 1
        while pag <= min(total, 20):
            q = {"language": "pt-br", "pageNumber": pag, "pageSize": 120, "tradingName": nome}
            js = get_json(f"{b3.PROXY}/GetListedCashDividends/{b3._b64(q)}")
            if not js:
                break
            total = int(((js.get("page") or {}).get("totalPages")) or 1)
            out["historico"] += js.get("results") or []
            pag += 1
        if out["historico"]:
            out["nome_usado"] = nome
            break
    js = get_json(f"{b3.PROXY}/GetListedSupplementCompany/{b3._b64({'issuingCompany': emissor, 'language': 'pt-br'})}")
    bloco = js[0] if isinstance(js, list) and js else (js or {})
    out["suplemento"] = bloco.get("cashDividends") or []
    return out


def yahoo_div(tk: str) -> list[dict]:
    js = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}.SA?range=10y&interval=1mo&events=div").json()
    ev = (js["chart"]["result"][0].get("events") or {}).get("dividends") or {}
    return sorted(({"data": datetime.fromtimestamp(v["date"], timezone.utc).strftime("%Y-%m-%d"), "valor": v.get("amount")}
                   for v in ev.values()), key=lambda x: x["data"])


def cvm_pl(cnpjs: set[str]) -> list[dict]:
    out = []
    hoje = date.today()
    for tipo, ano in (("ITR", hoje.year), ("DFP", hoje.year - 1)):
        p = baixar(f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{tipo}/DADOS/{tipo.lower()}_cia_aberta_{ano}.zip", reusar=False)
        if p is None:
            continue
        z = zipfile.ZipFile(p)
        for n in z.namelist():
            if "_bpp_" not in n.lower():
                continue
            with z.open(n) as f:
                df = pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str)
            df = df[df["CNPJ_CIA"].isin(cnpjs) & df["CD_CONTA"].str.match(r"^2\.\d\d(\.\d\d)?$")]
            ds = df["DS_CONTA"].str.lower()
            df = df[ds.str.contains("patrim|controlador", regex=True)]
            df = df[df["ORDEM_EXERC"].str.upper().str.contains("LTIMO") & ~df["ORDEM_EXERC"].str.upper().str.contains("PEN")]
            cols = [c for c in ("CNPJ_CIA", "DT_REFER", "VERSAO", "CD_CONTA", "DS_CONTA", "VL_CONTA", "ESCALA_MOEDA") if c in df.columns]
            d = df[cols].copy()
            d["arquivo"] = n
            out += d.to_dict("records")
    return out


def main() -> None:
    ativos = pd.read_csv(RAIZ / "config/ativos.csv", dtype=str)
    emp = pd.read_csv(RAIZ / "config/empresas.csv", dtype=str).fillna("")
    det: dict = {"gerado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "b3_proventos_brutos": {},
                 "yahoo_dividendos": {}, "erros": []}
    for em in sorted(set(ativos["emissor"])):
        try:
            info = b3.empresa(em)
            det["b3_proventos_brutos"][em] = b3_brutos(em, (info or {}).get("tradingName"))
        except Exception as e:  # noqa: BLE001
            det["erros"].append(f"b3 {em}: {e}")
    for tk in ativos["ticker"]:
        try:
            det["yahoo_dividendos"][tk] = yahoo_div(tk)
        except Exception as e:  # noqa: BLE001
            det["erros"].append(f"yahoo {tk}: {e}")
        time.sleep(0.5)
    mapa = pd.read_csv(RAIZ / "dados/mapa_cvm.csv", dtype=str)
    det["cvm_pl"] = cvm_pl(set(mapa["cnpj"].dropna()))
    (RAIZ / "auditoria").mkdir(exist_ok=True)
    (RAIZ / "auditoria/detalhe.json").write_text(json.dumps(det, ensure_ascii=False), encoding="utf-8")
    print("detalhe:", len(det["b3_proventos_brutos"]), "emissores;", len(det["cvm_pl"]), "linhas de PL; erros:", det["erros"])


if __name__ == "__main__":
    main()
