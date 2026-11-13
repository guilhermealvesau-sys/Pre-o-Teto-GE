"""Dados contábeis oficiais: Portal de Dados Abertos da CVM (ITR trimestral e DFP anual).

O que sai daqui, por empresa (valores em R$ milhões):
  - lucro líquido atribuído aos controladores: últimos 12 meses e por ano
  - patrimônio líquido dos controladores (para o VPA)
  - nº de ações em circulação por classe (ON/PN), excluindo tesouraria, hoje e no fim de cada ano
  - dívida bruta (empréstimos, financiamentos e arrendamentos), usada para a Petrobras
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .util import baixar, log, norm

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA"
URL_CAD = f"{BASE}/CAD/DADOS/cad_cia_aberta.csv"


# ---------------------------------------------------------------- arquivos
def cadastro() -> pd.DataFrame:
    p = baixar(URL_CAD, reusar=False)
    df = pd.read_csv(p, sep=";", encoding="latin-1", dtype=str)
    df["nome_n"] = df["DENOM_SOCIAL"].map(norm)
    return df


def _zip(tipo: str, ano: int) -> zipfile.ZipFile | None:
    t = tipo.lower()
    p = baixar(f"{BASE}/DOC/{tipo.upper()}/DADOS/{t}_cia_aberta_{ano}.zip", reusar=ano < date.today().year - 1)
    return zipfile.ZipFile(p) if p else None


def _ler(z: zipfile.ZipFile, sufixo: str, cnpjs: set[str]) -> pd.DataFrame:
    nomes = [n for n in z.namelist() if n.lower().endswith(sufixo.lower())]
    if not nomes:
        return pd.DataFrame()
    with z.open(nomes[0]) as f:
        df = pd.read_csv(io.TextIOWrapper(f, encoding="latin-1"), sep=";", dtype=str)
    df = df[df["CNPJ_CIA"].isin(cnpjs)].copy()
    if "VERSAO" in df:
        df["VERSAO"] = pd.to_numeric(df["VERSAO"], errors="coerce")
        ult = df.groupby(["CNPJ_CIA", "DT_REFER"])["VERSAO"].transform("max")
        df = df[df["VERSAO"] == ult]
    return df


def _ler_demo(z, prefixo: str, demo: str, ano: int, cnpjs: set[str]) -> pd.DataFrame:
    """Demonstração consolidada; para quem não consolida, a individual."""
    con = _ler(z, f"{prefixo}_cia_aberta_{demo}_con_{ano}.csv", cnpjs)
    ind = _ler(z, f"{prefixo}_cia_aberta_{demo}_ind_{ano}.csv", cnpjs)
    if not ind.empty:
        # vale a individual quando não há consolidada ou quando a consolidada veio toda zerada (ex.: TIM)
        chave = ["CNPJ_CIA", "DT_REFER"]
        if con.empty:
            usar_ind = set(map(tuple, ind[chave].drop_duplicates().values))
        else:
            soma = con.assign(_v=pd.to_numeric(con["VL_CONTA"], errors="coerce").abs()).groupby(chave)["_v"].sum()
            zerados = {k for k, v in soma.items() if not v}
            com_con = set(map(tuple, con[chave].drop_duplicates().values))
            todos_ind = set(map(tuple, ind[chave].drop_duplicates().values))
            usar_ind = (todos_ind - com_con) | zerados
            if zerados:
                con = con[~con[chave].apply(tuple, axis=1).isin(zerados)]
        if usar_ind:
            sel = ind[ind[chave].apply(tuple, axis=1).isin(usar_ind)]
            con = pd.concat([con, sel], ignore_index=True)
    if con.empty:
        return con
    escala = con["ESCALA_MOEDA"].fillna("MIL").str.upper()
    fator = escala.map(lambda e: 1000.0 if e.startswith("MIL") else 1.0)
    con["vl_mi"] = pd.to_numeric(con["VL_CONTA"], errors="coerce") * fator / 1e6
    con["ds"] = con["DS_CONTA"].map(norm)
    con["nivel"] = con["CD_CONTA"].str.count(r"\.")
    con["origem"] = f"{prefixo.upper()} {ano}"
    return con


# ---------------------------------------------------------------- contas
_CONTROL = ("atribuido a socios da empresa controladora", "atribuido aos socios da empresa controladora",
            "atribuido aos acionistas controladores", "atribuido a acionistas controladores",
            "socios da controladora", "acionistas da controladora")


def _linhas_lucro(dre: pd.DataFrame) -> pd.DataFrame:
    """Linhas do lucro líquido dos controladores (uma por documento e período)."""
    if dre.empty:
        return dre
    out = []
    for (cnpj, ref), g in dre.groupby(["CNPJ_CIA", "DT_REFER"]):
        g3 = g[g["CD_CONTA"].str.startswith("3.")]
        alvo = g3[g3["ds"].apply(lambda s: any(k in s for k in _CONTROL))]
        if not alvo.empty:
            cod = alvo.sort_values("nivel")["CD_CONTA"].iloc[0]
        else:  # sem quebra controlador/minoritário: lucro do período (nível 2, maior código)
            n2 = g3[(g3["nivel"] == 1) & g3["ds"].str.contains("lucro") & g3["ds"].str.contains("periodo")]
            if n2.empty:
                continue
            cod = sorted(n2["CD_CONTA"].unique(), key=lambda c: [int(x) for x in c.split(".")])[-1]
        linhas = g[g["CD_CONTA"] == cod].copy()
        # companhia sem minoritários costuma deixar a linha "atribuído aos controladores" zerada:
        # nesse período vale o lucro consolidado (conta-mãe)
        pai = cod.rsplit(".", 1)[0] if cod.count(".") >= 2 else None
        if pai:
            mae = g[g["CD_CONTA"] == pai].set_index(["ORDEM_EXERC", "DT_INI_EXERC", "DT_FIM_EXERC"])["vl_mi"]
            mae = mae[~mae.index.duplicated()]
            for i, r in linhas.iterrows():
                if not r["vl_mi"]:
                    v = mae.get((r["ORDEM_EXERC"], r["DT_INI_EXERC"], r["DT_FIM_EXERC"]))
                    if v:
                        linhas.at[i, "vl_mi"] = v
                        linhas.at[i, "DS_CONTA"] = f"{r['DS_CONTA']} (zerada; usado {pai})"
        out.append(linhas)
    return pd.concat(out, ignore_index=True) if out else dre.iloc[0:0]


def _pl_controlador(bpp_doc: pd.DataFrame) -> float | None:
    g = bpp_doc[bpp_doc["CD_CONTA"].str.startswith("2.")]
    tot = g[(g["nivel"] == 1) & g["ds"].str.startswith("patrimonio liquido")]
    if tot.empty:
        return None
    cod = tot["CD_CONTA"].iloc[0]
    v = float(tot["vl_mi"].iloc[0])
    nc = g[g["CD_CONTA"].str.startswith(cod + ".") & g["ds"].str.contains("nao controlador")]
    if not nc.empty:
        v -= float(nc.sort_values("nivel")["vl_mi"].iloc[0])
    return v


def _divida_bruta(bpp_doc: pd.DataFrame) -> float | None:
    g = bpp_doc[bpp_doc["CD_CONTA"].str.match(r"^2\.0[12]\.")]
    emp = g[(g["nivel"] == 2) & g["ds"].str.contains("emprestimos e financiamentos")]
    arr = g[g["ds"].str.contains("arrendamento")]
    if not arr.empty:  # só o nível mais alto de cada ramo, para não somar duas vezes
        cods = sorted(arr["CD_CONTA"])
        topo = [c for c in cods if not any(c != o and c.startswith(o + ".") for o in cods)]
        topo = [c for c in topo if not any(c.startswith(e + ".") for e in emp["CD_CONTA"])]
        arr = arr[arr["CD_CONTA"].isin(topo)]
    if emp.empty and arr.empty:
        return None
    return float(emp["vl_mi"].sum() + arr["vl_mi"].sum())


def _acoes(cap: pd.DataFrame) -> pd.DataFrame:
    """Ações em circulação por documento: ON e PN integralizadas menos tesouraria."""
    if cap.empty:
        return cap
    cols = {c: c.upper() for c in cap.columns}

    def col(*partes):
        for c, u in cols.items():
            if all(p in u for p in partes):
                return c
        return None

    on_i, pn_i = col("ORDIN", "INTEGR"), col("PREF", "INTEGR")
    on_t, pn_t = col("ORDIN", "TESOUR"), col("PREF", "TESOUR")
    num = lambda c: pd.to_numeric(cap[c], errors="coerce").fillna(0) if c else 0  # noqa: E731
    out = cap[["CNPJ_CIA", "DT_REFER"]].copy()
    out["on"] = num(on_i) - num(on_t)
    out["pn"] = num(pn_i) - num(pn_t)
    out["total"] = out["on"] + out["pn"]
    return out


# ---------------------------------------------------------------- resultado
@dataclass
class Empresa:
    cnpj: str
    cd_cvm: str
    nome: str
    lucro12m_mi: float | None = None
    lucro12m_ate: str | None = None
    lucro_anual_mi: dict[int, float] = field(default_factory=dict)
    pl_mi: float | None = None
    pl_ref: str | None = None
    acoes_on: float | None = None
    acoes_pn: float | None = None
    acoes_ref: str | None = None
    acoes_ano: dict[int, dict] = field(default_factory=dict)
    divida_bruta_mi: float | None = None
    conta_lucro: str | None = None
    avisos: list[str] = field(default_factory=list)

    def escalar_acoes(self, fator: float) -> None:
        self.acoes_on = (self.acoes_on or 0) * fator
        self.acoes_pn = (self.acoes_pn or 0) * fator
        self.acoes_ano = {y: {"on": v["on"] * fator, "pn": v["pn"] * fator} for y, v in self.acoes_ano.items()}


def resolver(empresas: pd.DataFrame, cad: pd.DataFrame) -> pd.DataFrame:
    """Completa cd_cvm/cnpj de cada emissor pelo cadastro da CVM (companhias ativas)."""
    cad = cad.copy()
    cad["cd"] = cad["CD_CVM"].astype(str).str.strip().str.lstrip("0")
    cad["_ativo"] = cad["SIT"].fillna("").str.upper().str.startswith("ATIVO")
    # o cadastro repete a companhia (uma linha por categoria/situação): uma linha por código, a ativa primeiro
    cad = cad.sort_values("_ativo", ascending=False).drop_duplicates("cd")
    ativos = cad[cad["_ativo"]]
    linhas = []
    for _, e in empresas.iterrows():
        cd = str(e.get("cd_cvm") or "").strip().lstrip("0")
        if cd:
            cand = cad[cad["cd"] == cd]
        else:  # busca_cvm: expressões regulares (sem acento, minúsculas) separadas por "|"
            rx = re.compile(norm(e["busca_cvm"]))
            cand = ativos[ativos["nome_n"].apply(lambda n: bool(rx.search(n)))]
        linhas.append({
            "emissor": e["emissor"], "nome": e["nome"],
            "cnpj": cand["CNPJ_CIA"].iloc[0] if len(cand) == 1 else None,
            "cd_cvm": cand["CD_CVM"].iloc[0] if len(cand) == 1 else None,
            "denom": cand["DENOM_SOCIAL"].iloc[0] if len(cand) == 1 else None,
            "candidatos": ("; ".join(f"{r.CD_CVM}:{r.DENOM_SOCIAL}" for r in cand.itertuples())[:400] or "nenhum candidato")
            if len(cand) != 1 else "",
        })
    return pd.DataFrame(linhas)


def coletar(mapa: pd.DataFrame, anos_hist: int = 7) -> dict[str, Empresa]:
    """`mapa`: colunas emissor, cnpj, cd_cvm, nome (cnpj obrigatório)."""
    hoje = date.today()
    cnpjs = set(mapa["cnpj"].dropna())
    res = {r.emissor: Empresa(r.cnpj, str(r.cd_cvm), r.nome) for r in mapa.itertuples() if r.cnpj}
    por_cnpj = {e.cnpj: k for k, e in res.items()}

    dres, bpps, caps = [], [], []
    for ano in range(hoje.year - anos_hist, hoje.year):
        z = _zip("DFP", ano)
        if z is None:
            log.info("DFP %s ainda não publicado", ano)
            continue
        dres.append(_ler_demo(z, "dfp", "DRE", ano, cnpjs))
        bpps.append(_ler_demo(z, "dfp", "BPP", ano, cnpjs))
        caps.append(_ler(z, f"dfp_cia_aberta_composicao_capital_{ano}.csv", cnpjs))
    for ano in (hoje.year - 1, hoje.year):
        z = _zip("ITR", ano)
        if z is None:
            continue
        dres.append(_ler_demo(z, "itr", "DRE", ano, cnpjs))
        bpps.append(_ler_demo(z, "itr", "BPP", ano, cnpjs))
        caps.append(_ler(z, f"itr_cia_aberta_composicao_capital_{ano}.csv", cnpjs))

    dre = pd.concat([d for d in dres if not d.empty], ignore_index=True)
    bpp = pd.concat([d for d in bpps if not d.empty], ignore_index=True)
    cap = _acoes(pd.concat([d for d in caps if not d.empty], ignore_index=True))

    # ---- lucro: uma série por período (DT_INI, DT_FIM), preferindo o documento mais recente
    luc = _linhas_lucro(dre)
    for cnpj, g in luc.groupby("CNPJ_CIA"):  # conta usada no documento mais recente, para auditoria
        u = g.sort_values("DT_REFER").iloc[-1]
        res[por_cnpj[cnpj]].conta_lucro = f"{u['CD_CONTA']} {u['DS_CONTA']} ({u['origem']})"
    luc = luc.sort_values(["DT_REFER", "VERSAO"]).drop_duplicates(["CNPJ_CIA", "DT_INI_EXERC", "DT_FIM_EXERC"], keep="last")
    for cnpj, g in luc.groupby("CNPJ_CIA"):
        e = res[por_cnpj[cnpj]]
        g = g[g["DT_INI_EXERC"].str[5:] == "01-01"]  # só acumulados do ano (YTD) e anuais
        ser = {(r.DT_INI_EXERC[:4], r.DT_FIM_EXERC[5:7]): r.vl_mi for r in g.itertuples()}
        for (y, m), v in ser.items():
            if m == "12":
                e.lucro_anual_mi[int(y)] = round(float(v), 3)
        if not ser:
            e.avisos.append("lucro não encontrado na CVM")
            continue
        y, m = max(ser, key=lambda k: (k[0], k[1]))
        if m == "12":
            e.lucro12m_mi = float(ser[(y, m)])
        else:
            prev = str(int(y) - 1)
            if (prev, "12") in ser and (prev, m) in ser:
                e.lucro12m_mi = float(ser[(y, m)] + ser[(prev, "12")] - ser[(prev, m)])
            else:
                e.avisos.append(f"sem base para 12 meses até {y}-{m}")
        e.lucro12m_ate = f"{y}-{m}"
        if e.lucro12m_mi is not None:
            e.lucro12m_mi = round(e.lucro12m_mi, 3)

    for e in res.values():  # diagnóstico: lucro ausente ou zerado → mostra as contas da DRE no log
        zerados = [y for y, v in e.lucro_anual_mi.items() if not v]
        if e.lucro12m_mi and not zerados:
            continue
        g = dre[(dre["CNPJ_CIA"] == e.cnpj)]
        if g.empty:
            continue
        ref = f"{zerados[-1]}-12-31" if zerados and (g["DT_REFER"] == f"{zerados[-1]}-12-31").any() else g["DT_REFER"].max()
        log.warning("CVM: %s com lucro zerado nos anos %s", e.nome, zerados)
        g = g[(g["DT_REFER"] == ref) & (g["ORDEM_EXERC"].map(norm) == "ultimo")]
        g = g[g["CD_CONTA"].str.startswith("3.") & (g["nivel"] <= 2)].drop_duplicates("CD_CONTA")
        log.warning("CVM: lucro de %s zerado/ausente (conta usada: %s). Contas da DRE:\n%s", e.nome, e.conta_lucro,
                    "\n".join(f"   {r.CD_CONTA} {r.DS_CONTA} = {r.vl_mi:.1f}" for r in g.itertuples()))

    # ---- balanço mais recente: PL dos controladores e dívida bruta
    for cnpj, g in bpp.groupby("CNPJ_CIA"):
        e = res[por_cnpj[cnpj]]
        ref = g["DT_REFER"].max()
        doc = g[(g["DT_REFER"] == ref) & (g["ORDEM_EXERC"].map(norm) == "ultimo")]
        e.pl_mi, e.pl_ref = _pl_controlador(doc), ref
        e.divida_bruta_mi = _divida_bruta(doc)
        if e.pl_mi is None:
            e.avisos.append("patrimônio líquido não encontrado")

    # ---- ações: atuais e no fim de cada ano
    for cnpj, g in cap.groupby("CNPJ_CIA"):
        e = res[por_cnpj[cnpj]]
        g = g.sort_values("DT_REFER")
        # algumas companhias informam a composição em milhares em parte dos formulários:
        # documento com total 100× menor que o maior da série é reescalado (×1000)
        mx = g["total"].max()
        esc = g["total"].apply(lambda t: 1000.0 if 0 < t < mx / 100 else 1.0)
        g = g.assign(on=g["on"] * esc, pn=g["pn"] * esc, total=g["total"] * esc)
        ult = g.iloc[-1]
        e.acoes_on, e.acoes_pn, e.acoes_ref = float(ult["on"]), float(ult["pn"]), ult["DT_REFER"]
        for r in g[g["DT_REFER"].str[5:] == "12-31"].itertuples():
            e.acoes_ano[int(r.DT_REFER[:4])] = {"on": float(r.on), "pn": float(r.pn)}
        if not e.acoes_on and not e.acoes_pn:
            e.avisos.append("composição do capital vazia")
    return res
