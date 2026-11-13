"""Avisos de mudança na política de remuneração aos acionistas.

Fonte: documentos periódicos e eventuais entregues à CVM (conjunto IPE, atualizado semanalmente).
Filtra, para as companhias acompanhadas, fatos relevantes, comunicados e avisos cujo assunto fale de
política de dividendos/remuneração ou de payout. Avisos de pagamento comuns ("declarou JCP") não entram.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .util import baixar, ler_json, log, norm, salvar_json

URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"

# padrões sobre o texto normalizado (minúsculas, sem acento) de tipo + espécie + assunto.
# Cada um vale para todas as categorias (None) ou só para as listadas.
_POL = (r"politica (de|da|para) (dividendos|distribuicao|destinacao|pagamento de (dividendos|proventos)"
        r"|proventos|remuneracao (aos|dos|ao) acionistas?)")
FORTES = {"fato relevante", "comunicado ao mercado"}
PADROES = [
    (_POL, "política de remuneração", None),
    (r"\bpay ?out\b", "payout", FORTES),
    (r"(reducao|aumento|alteracao|revisao|mudanca)\b.{0,40}\b(dividendos?|proventos|distribuicao de resultados)",
     "mudança na distribuição", FORTES),
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


def carregar(empresas: dict[str, dict], anos: list[int]) -> list[dict]:
    """Documentos eventuais (IPE) das companhias acompanhadas. `empresas`: {cnpj: {"emissor":..., "nome":...}}."""
    regs = []
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
            e = empresas[r[c["cnpj"]]]
            g = lambda k: (r.get(c[k]) or "") if c[k] else ""  # noqa: E731
            regs.append({
                "protocolo": str(g("prot") or f"{r[c['cnpj']]}-{g('data')}-{g('ass')}")[:120],
                "emissor": e["emissor"], "empresa": e["nome"],
                "data": (g("data") or g("ref"))[:10], "categoria": g("cat"), "tipo": g("tipo"), "especie": g("esp"),
                "assunto": (g("ass") or g("esp")).strip()[:300], "link": g("link"),
            })
    return regs


def varrer(regs: list[dict]) -> list[dict]:
    """Documentos que tratam de política de remuneração aos acionistas (ou de payout)."""
    achados = []
    for r in regs:
        txt = norm(" | ".join((r["tipo"], r["especie"], r["assunto"])))
        cat = norm(r["categoria"])
        regra = next((rot for rx, rot, cats in PADROES
                      if re.search(rx, txt) and (cats is None or cat in cats)), None)
        if not regra:
            continue
        if cat == "aviso aos acionistas" and not re.search(r"(alteracao|revisao|nova|aprovacao)", txt):
            continue  # aviso de pagamento que só cita a política vigente
        achados.append({k: r[k] for k in ("protocolo", "emissor", "empresa", "data", "categoria", "tipo", "assunto",
                                          "link")} | {"regra": regra})
    return achados


CATS_FATOS = {"fato relevante", "comunicado ao mercado", "aviso aos acionistas"}


def recentes(regs: list[dict], por_emissor: int = 10, dias: int = 400) -> dict[str, list[dict]]:
    """Últimos fatos relevantes, comunicados ao mercado e avisos aos acionistas de cada emissor."""
    corte = (date.today() - timedelta(days=dias)).isoformat()
    vistos, out = set(), {}
    for r in sorted(regs, key=lambda r: r["data"], reverse=True):
        if norm(r["categoria"]) not in CATS_FATOS or r["data"] < corte:
            continue
        chave = (r["emissor"], r["data"], norm(r["assunto"]))
        if chave in vistos:  # reapresentações do mesmo documento
            continue
        vistos.add(chave)
        lst = out.setdefault(r["emissor"], [])
        if len(lst) < por_emissor:
            lst.append({k: r[k] for k in ("data", "categoria", "assunto", "link")})
    return out


def atualizar(empresas: dict[str, dict], dados: Path, saida: Path) -> list[dict]:
    """Atualiza dados/avisos.json; grava em `saida` os avisos novos (para abrir issue). Devolve os novos."""
    hoje = date.today()
    estado = ler_json(dados / "avisos.json", None)
    primeira = estado is None
    estado = estado or {"vistos": [], "itens": []}
    vistos = set(estado.get("vistos", []))
    regs = carregar(empresas, [hoje.year - 1, hoje.year])
    achados = varrer(regs)
    fatos = recentes(regs)
    if regs:
        salvar_json(dados / "fatos.json", {"atualizado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                                           "fonte": "CVM – documentos eventuais (IPE)",
                                           "ultimo_documento": max(r["data"] for r in regs), "por_emissor": fatos})
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
