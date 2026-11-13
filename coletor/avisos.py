"""Avisos de mudança na política de remuneração aos acionistas.

Fonte: documentos periódicos e eventuais entregues à CVM (conjunto IPE, atualizado semanalmente).
Filtra, para as companhias acompanhadas, fatos relevantes, comunicados e avisos cujo assunto fale de
política de dividendos/remuneração ou de payout. Avisos de pagamento comuns ("declarou JCP") não entram.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .util import baixar, ler_json, log, norm, salvar_json

URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"

# padrões sobre o texto normalizado (minúsculas, sem acento) de categoria + tipo + espécie + assunto
PADROES = [
    (r"politica de (dividendos|remuneracao|distribuicao|destinacao|pagamento de dividendos|proventos)", "política"),
    (r"remuneracao (aos|dos|ao) acionistas?", "remuneração aos acionistas"),
    (r"\bpay ?out\b", "payout"),
    (r"dividendos? minimos?", "dividendo mínimo"),
    (r"(alteracao|revisao|nova|atualizacao)\b.{0,60}\b(politica|payout|remuneracao)", "alteração de política"),
]


def _col(df: pd.DataFrame, *opcoes: str) -> str | None:
    low = {c.lower(): c for c in df.columns}
    for o in opcoes:
        if o in low:
            return low[o]
    for o in opcoes:
        for lc, c in low.items():
            if o in lc:
                return c
    return None


def _ler_ano(ano: int) -> pd.DataFrame:
    p = baixar(URL.format(ano=ano), reusar=False)
    if p is None:
        return pd.DataFrame()
    z = zipfile.ZipFile(p)
    nome = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
    if not nome:
        return pd.DataFrame()
    with z.open(nome) as f:
        return pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str).fillna("")


def varrer(empresas: dict[str, dict], anos: list[int]) -> list[dict]:
    """`empresas`: {cnpj: {"emissor":..., "nome":...}}. Devolve documentos que casam com os padrões."""
    achados = []
    for ano in anos:
        df = _ler_ano(ano)
        if df.empty:
            continue
        c = {k: _col(df, *v) for k, v in {
            "cnpj": ("cnpj_companhia", "cnpj"), "cat": ("categoria",), "tipo": ("tipo",), "esp": ("especie",),
            "ass": ("assunto",), "data": ("data_entrega",), "ref": ("data_referencia",),
            "link": ("link_download", "link"), "prot": ("protocolo_entrega", "protocolo"), "nome": ("nome_companhia",)}.items()}
        if not c["cnpj"] or not c["ass"]:
            log.warning("IPE %s: colunas inesperadas %s", ano, list(df.columns))
            continue
        df = df[df[c["cnpj"]].isin(empresas)]
        for r in df.to_dict("records"):
            partes = [r.get(c[k]) or "" for k in ("cat", "tipo", "esp", "ass") if c[k]]
            txt = norm(" | ".join(partes))
            regra = next((rot for rx, rot in PADROES if re.search(rx, txt)), None)
            if not regra:
                continue
            cat = norm(r.get(c["cat"]) or "")
            if "aviso aos acionistas" in cat and regra not in ("política", "alteração de política"):
                continue  # aviso de pagamento comum
            e = empresas[r[c["cnpj"]]]
            achados.append({
                "protocolo": str(r.get(c["prot"]) or f"{r[c['cnpj']]}-{r.get(c['data'])}-{r.get(c['ass'])}")[:120],
                "emissor": e["emissor"], "empresa": e["nome"],
                "data": (r.get(c["data"]) or r.get(c["ref"]) or "")[:10],
                "categoria": r.get(c["cat"]) or "", "tipo": r.get(c["tipo"]) or "",
                "assunto": (r.get(c["ass"]) or r.get(c["esp"]) or "").strip()[:300],
                "link": r.get(c["link"]) or "", "regra": regra,
            })
    return achados


def atualizar(empresas: dict[str, dict], dados: Path, saida: Path) -> list[dict]:
    """Atualiza dados/avisos.json; grava em `saida` os avisos novos (para abrir issue). Devolve os novos."""
    hoje = date.today()
    estado = ler_json(dados / "avisos.json", None)
    primeira = estado is None
    estado = estado or {"vistos": [], "itens": []}
    vistos = set(estado.get("vistos", []))
    achados = varrer(empresas, [hoje.year - 1, hoje.year])
    novos = [a for a in achados if a["protocolo"] not in vistos]
    agora = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for a in novos:
        a["detectado_em"] = agora
        a["novo"] = not primeira  # na primeira varredura o histórico entra sem disparar aviso
    itens = {i["protocolo"]: i for i in estado.get("itens", [])}
    for a in novos:
        itens[a["protocolo"]] = a
    lista = sorted(itens.values(), key=lambda i: i.get("data", ""), reverse=True)[:150]
    salvar_json(dados / "avisos.json", {"atualizado_em": agora, "fonte": "CVM – documentos eventuais (IPE)",
                                        "vistos": sorted(vistos | {a["protocolo"] for a in achados}), "itens": lista})
    disparar = [] if primeira else novos
    saida.mkdir(parents=True, exist_ok=True)
    salvar_json(saida / "avisos_novos.json", disparar)
    if disparar:
        md = ["Documentos entregues à CVM que tratam de política de remuneração aos acionistas:", ""]
        for a in disparar:
            md.append(f"- **{a['empresa']} ({a['emissor']})** — {a['data']} — {a['categoria']}: {a['assunto']} "
                      f"[documento]({a['link']})")
        md += ["", "Confira se o payout usado no modelo continua adequado."]
        (saida / "avisos_novos.md").write_text("\n".join(md), encoding="utf-8")
    log.info("avisos de política: %s encontrados, %s novos%s", len(achados), len(novos),
             " (primeira varredura, sem disparo)" if primeira else "")
    return disparar
