"""Teste sem internet: gera arquivos no formato da CVM, da B3 e do Banco Central e confere os cálculos.

Rodar: python -m tests.teste_offline
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path

ANO = date.today().year
TMP = Path(tempfile.mkdtemp(prefix="coletor_teste_"))

import coletor.util as util  # noqa: E402

util.CACHE = TMP / "cache"
util.CACHE.mkdir()

from coletor import b3, bcb, cvm, main  # noqa: E402

EMP = {  # cnpj, cd_cvm, razão social, ações ON/PN por ano (None = constante)
    "TAEE": ("07.859.971/0001-30", "20257", "TRANSMISSORA ALIANCA DE ENERGIA ELETRICA S.A.", 590e6, 443e6),
    "ITUB": ("60.872.504/0001-23", "19348", "ITAU UNIBANCO HOLDING S.A.", 5e9, 5e9),
    "PETR": ("33.000.167/0001-01", "9512", "PETROLEO BRASILEIRO S.A. PETROBRAS", 7.4e9, 5.6e9),
}
HDR = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA"
HDR_BPP = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;ORDEM_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA"
HDR_CAP = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;ID_DOC;QT_ACAO_ORDIN_CAP_INTEGR;QT_ACAO_PREF_CAP_INTEGR;QT_ACAO_TOTAL_CAP_INTEGR;QT_ACAO_ORDIN_TESOURO;QT_ACAO_PREF_TESOURO;QT_ACAO_TOTAL_TESOURO"


def acoes(em, ano):
    on, pn = EMP[em][3], EMP[em][4]
    if em == "ITUB" and ano < ANO - 2:  # bonificação de 10% dois anos atrás
        on, pn = on / 1.1, pn / 1.1
    return on, pn


def lucro_ano(em, y):
    return {"TAEE": 1000, "ITUB": 40000, "PETR": 100000}[em]  # R$ mi


def dre_rows(em, ref, periodos, banco=False):
    cnpj, cd, nome, *_ = EMP[em]
    out = []
    cod = ("3.09", "3.09.01") if banco else ("3.11", "3.11.01")
    for ordem, ini, fim, v in periodos:
        mil = int(v * 1000)  # MIL
        ctrl = 0 if (em == "PETR" and fim == f"{ANO - 2}-12-31") else mil  # linha de controladores zerada
        out.append(f"{cnpj};{ref};1;{nome};{cd};DF Consolidado - Demonstração do Resultado;REAL;MIL;{ordem};{ini};{fim};{cod[0]};Lucro/Prejuízo Consolidado do Período;{mil + 5000};S")
        out.append(f"{cnpj};{ref};1;{nome};{cd};DF Consolidado - Demonstração do Resultado;REAL;MIL;{ordem};{ini};{fim};{cod[1]};Atribuído a Sócios da Empresa Controladora;{ctrl};S")
    return out


def bpp_rows(em, ref):
    cnpj, cd, nome, *_ = EMP[em]
    base = f"{cnpj};{ref};1;{nome};{cd};DF Consolidado - Balanço Patrimonial Passivo;REAL;MIL;ÚLTIMO;{ref}"
    r = [f"{base};2.03;Patrimônio Líquido Consolidado;8500000;S",
         f"{base};2.03.09;Participação dos Acionistas Não Controladores;500000;S"]
    if em == "PETR":
        r += [f"{base};2.01.04;Empréstimos e Financiamentos;100000000;S",
              f"{base};2.02.01;Empréstimos e Financiamentos;200000000;S",
              f"{base};2.01.05;Outras Obrigações;50000000;S",
              f"{base};2.01.05.02.03;Arrendamentos;10000000;N"]
    return r


def cap_rows(em, ref, ano):
    cnpj, cd, nome, *_ = EMP[em]
    on, pn = acoes(em, ano)
    if em == "ITUB":  # Itaú informa em milhares em todos os documentos (corrigido pelo P/VP)
        on, pn = on / 1000, pn / 1000
    if em == "PETR" and ano == ANO - 3:  # um documento isolado em milhares (corrigido pela série)
        on, pn = on / 1000, pn / 1000
    return [f"{cnpj};{ref};1;{nome};1;{int(on + 1000)};{int(pn)};{int(on + pn + 1000)};1000;0;1000"]


def zipar(caminho, arquivos):
    with zipfile.ZipFile(caminho, "w") as z:
        for nome, linhas in arquivos.items():
            z.writestr(nome, "\n".join(linhas).encode("latin-1"))


def fixtures():
    arq = {}
    for y in range(ANO - 7, ANO):
        ref = f"{y}-12-31"
        dre, bpp, cap = [HDR], [HDR_BPP], [HDR_CAP]
        for em in EMP:
            dre += dre_rows(em, ref, [("ÚLTIMO", f"{y}-01-01", ref, lucro_ano(em, y)),
                                      ("PENÚLTIMO", f"{y-1}-01-01", f"{y-1}-12-31", lucro_ano(em, y - 1))], banco=em == "ITUB")
            bpp += bpp_rows(em, ref)
            cap += cap_rows(em, ref, y)
        p = TMP / f"dfp{y}.zip"
        zipar(p, {f"dfp_cia_aberta_DRE_con_{y}.csv": dre, f"dfp_cia_aberta_BPP_con_{y}.csv": bpp,
                  f"dfp_cia_aberta_composicao_capital_{y}.csv": cap})
        arq[f"DFP/DADOS/dfp_cia_aberta_{y}.zip"] = p
    for y in (ANO - 1, ANO):
        ref = f"{y}-09-30"
        dre, bpp, cap = [HDR], [HDR_BPP], [HDR_CAP]
        for em in EMP:
            ytd = {"TAEE": (800, 700), "ITUB": (33000, 30000), "PETR": (70000, 75000)}[em]
            dre += dre_rows(em, ref, [("ÚLTIMO", f"{y}-01-01", ref, ytd[0]), ("ÚLTIMO", f"{y}-07-01", ref, 1.0),
                                      ("PENÚLTIMO", f"{y-1}-01-01", f"{y-1}-09-30", ytd[1])], banco=em == "ITUB")
            bpp += bpp_rows(em, ref)
            cap += cap_rows(em, ref, y)
        p = TMP / f"itr{y}.zip"
        zipar(p, {f"itr_cia_aberta_DRE_con_{y}.csv": dre, f"itr_cia_aberta_BPP_con_{y}.csv": bpp,
                  f"itr_cia_aberta_composicao_capital_{y}.csv": cap})
        arq[f"ITR/DADOS/itr_cia_aberta_{y}.zip"] = p
    cad = TMP / "cad.csv"
    linhas = ["CNPJ_CIA;DENOM_SOCIAL;SIT;CD_CVM;CATEG_REG"]
    linhas += [f"{c};{n};ATIVO;{cd};Categoria A" for c, cd, n, *_ in EMP.values()]
    linhas += ["00.000.000/0001-00;TRANSMISSORA ALIANCA DE ENERGIA ELETRICA S.A.;CANCELADA;999;Categoria A"]
    cad.write_text("\n".join(linhas), encoding="latin-1")
    arq["CAD/DADOS/cad_cia_aberta.csv"] = cad

    def cot_line(dia, tk, px):
        s = [" "] * 245
        def put(a, b, v):  # posições 1-based inclusivas
            v = str(v)
            s[a - 1:b] = list(v.rjust(b - a + 1, "0") if v.isdigit() else v.ljust(b - a + 1))
        put(1, 2, "01"); put(3, 10, dia); put(11, 12, "02"); put(13, 24, tk); put(25, 27, "010")
        put(109, 121, str(int(round(px * 100)))); put(211, 217, "1")
        return "".join(s)
    precos = {"TAEE3": 10, "TAEE4": 10, "TAEE11": 30, "ITUB4": 30, "ITUB3": 33, "PETR4": 30, "PETR3": 33}
    for y in range(ANO - 6, ANO):
        p = TMP / f"cot{y}.zip"
        zipar(p, {"COTAHIST.TXT": [cot_line(f"{y}0102", tk, v) for tk, v in precos.items()] +
                  [cot_line(f"{y}1230", tk, v) for tk, v in precos.items()]})
        arq[f"COTAHIST_A{y}.ZIP"] = p
    return arq


def proventos_json(emissor):
    ev = []
    for y in range(ANO - 7, ANO + 1):
        for cls in ("ACNOR", "ACNPR"):
            isin = f"BR{emissor}{cls}1"
            ev.append({"isinCode": isin, "rate": "0,50000000000", "label": "DIVIDENDO", "lastDatePrior": f"15/06/{y}",
                       "paymentDate": f"30/06/{y}", "relatedTo": f"1º Semestre/{y}"})
            ev.append({"isinCode": isin, "rate": "0,20000000000", "label": "JRS CAP PROPRIO", "lastDatePrior": f"15/12/{y}",
                       "paymentDate": f"30/12/{y}", "relatedTo": f"Anual/{y}"})
        ev.append({"isinCode": f"BR{emissor}CDAM1", "rate": "9,9", "label": "DIVIDENDO", "lastDatePrior": f"15/06/{y}"})
    ev.append(dict(ev[0]))  # duplicado proposital
    quase = dict(ev[0]); quase["rate"] = "0,50040000000"; quase["relatedTo"] = ""  # revisão na 4ª casa
    ev.append(quase)
    return [{"code": emissor, "cashDividends": ev}]


def main_teste():
    arq = fixtures()

    def fake_baixar(url, destino=None, **kw):
        for k, p in arq.items():
            if url.endswith(k):
                return p
        if "COTAHIST_D" in url:
            return None
        return None

    def fake_get_json(url, **kw):
        if "GetListedSupplementCompany" in url:
            import base64
            d = json.loads(base64.b64decode(url.rsplit("/", 1)[1]))
            return proventos_json(d["issuingCompany"]) if d["issuingCompany"] in EMP else [{"cashDividends": []}]
        if "olinda" in url:
            return {"value": [{"cotacaoVenda": 5.0, "dataHoraCotacao": "2026-09-30 13:00:00.000"}]}
        return None

    cvm.baixar = b3.baixar = fake_baixar
    b3.get_json = bcb.get_json = fake_get_json
    main.DADOS = TMP / "dados"
    main.DADOS.mkdir()
    base = main.completo()
    A = base["ativos"]

    def ok(nome, v, esperado, tol=1e-3):
        assert v is not None and abs(v - esperado) <= tol * max(1, abs(esperado)), f"{nome}: {v} ≠ {esperado}"
        print(f"ok  {nome} = {v}")

    t11, t4, i4 = A["TAEE11"], A["TAEE4"], A["ITUB4"]
    ok("TAEE lucro 12M", t11["lucro12m_mi"], 800 + 1000 - 700)
    ok("TAEE qtd (mi, sem tesouraria)", t11["qtd_mi"], 1033.0)
    ok("TAEE11 VPA", t11["vpa"], 8000e6 / 1033e6 * 3)
    ok("TAEE11 DY médio", t11["dy_medio"], 2.1 / 30)
    ok("TAEE4 DY médio", t4["dy_medio"], 0.7 / 10)
    ok("TAEE11 dividendo médio", t11["div_medio6"], 2.1)
    ok("TAEE11 % JCP", t11["pct_jcp"], 0.2 / 0.7)
    ok("TAEE payout", t11["payout"], 0.7 * 1033 / 1000)
    ok("TAEE11 P/L médio", t11["pl_medio"], 30 / (1000e6 / 1033e6 * 3))
    ok("ITUB ações corrigidas de milhares", i4["qtd_mi"], 10000.0)
    ok("PETR ações com documento isolado em milhares", A["PETR4"]["qtd_mi"], 13000.0)
    ok("PETR lucro anual com linha de controladores zerada → consolidado",
       A["PETR4"]["detalhe"]["lucro_anual_mi"][ANO - 2], 100005)
    ok("ITUB lucro 12M (layout de banco)", i4["lucro12m_mi"], 33000 + 40000 - 30000)
    adj = (4 * (1 / 1.1) + 2 * 1.0) / 6 * 0.7
    ok("ITUB4 dividendo médio ajustado pela bonificação", i4["div_medio6"], adj)
    ok("Petrobras dívida bruta (US$ bi)", base["petrobras"]["divida_bruta_usd_bi"], 310000 / 5.0 / 1000)
    assert "empresa sem dados da CVM" in A["VALE3"]["avisos"], A["VALE3"]["avisos"]
    print("ok  VALE3 sem dados é sinalizado")
    print((main.DADOS / "relatorio.md").read_text()[:600])
    shutil.rmtree(TMP)
    print("\nTODOS OS TESTES PASSARAM")


if __name__ == "__main__":
    main_teste()
