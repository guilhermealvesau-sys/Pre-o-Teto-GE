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

from coletor import avisos, b3, bcb, cvm, indicadores, main, noticias  # noqa: E402

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
        ctrl = 0 if (em == "PETR" and fim in (f"{ANO - 2}-12-31", f"{ANO - 4}-12-31")) else mil  # controladores zerada
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


def dre_ind_banco(ref, periodos):
    """Individual do banco (padrão Banco Central): lucro 95% do consolidado IFRS, na conta 3.13."""
    cnpj, cd, nome, *_ = EMP["ITUB"]
    return [f"{cnpj};{ref};1;{nome};{cd};DF Individual - Demonstração do Resultado;REAL;MIL;{o};{i};{f};3.13;Lucro/Prejuízo do Período;{int(v * 950)};S"
            for o, i, f, v in periodos]


def bpp_ind_banco(ref):
    cnpj, cd, nome, *_ = EMP["ITUB"]
    return [f"{cnpj};{ref};1;{nome};{cd};DF Individual - Balanço Patrimonial Passivo;REAL;MIL;ÚLTIMO;{ref};2.05;Patrimônio Líquido;7600000;S"]


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
        # banco: o DFP mais recente não reapresenta o ano anterior (transição da Res. CMN 4.966) → vale o DFP daquele ano
        ind = [HDR] + dre_ind_banco(ref, [("ÚLTIMO", f"{y}-01-01", ref, lucro_ano("ITUB", y)),
                                          ("PENÚLTIMO", f"{y-1}-01-01", f"{y-1}-12-31", 0 if y == ANO - 1 else lucro_ano("ITUB", y - 1))])
        bpp_ind = [HDR_BPP] + bpp_ind_banco(ref)
        if y == ANO - 2:  # PETR: linha dos controladores zerada e minoritários relevantes → vale a individual
            cnpj, cd, nome, *_ = EMP["PETR"]
            for ordem, ini, fim, v in (("ÚLTIMO", f"{y}-01-01", ref, 90000000), ("PENÚLTIMO", f"{y-1}-01-01", f"{y-1}-12-31", 100000000)):
                ind.append(f"{cnpj};{ref};1;{nome};{cd};DF Individual - Demonstração do Resultado;REAL;MIL;{ordem};{ini};{fim};3.11;Lucro/Prejuízo do Período;{v};S")
        if y == ANO - 5:  # PETR entrega a consolidada zerada nesse ano; o lucro real está na individual
            cnpj, cd, nome, *_ = EMP["PETR"]
            dre = [l if not l.startswith(cnpj) else ";".join(l.split(";")[:13] + ["0", "S"]) for l in dre]
            for ordem, ini, fim in (("ÚLTIMO", f"{y}-01-01", ref), ("PENÚLTIMO", f"{y-1}-01-01", f"{y-1}-12-31")):
                ind.append(f"{cnpj};{ref};1;{nome};{cd};DF Individual - Demonstração do Resultado;REAL;MIL;{ordem};{ini};{fim};3.11;Lucro/Prejuízo do Período;77000000;S")
        p = TMP / f"dfp{y}.zip"
        zipar(p, {f"dfp_cia_aberta_DRE_con_{y}.csv": dre, f"dfp_cia_aberta_DRE_ind_{y}.csv": ind,
                  f"dfp_cia_aberta_BPP_con_{y}.csv": bpp, f"dfp_cia_aberta_BPP_ind_{y}.csv": bpp_ind,
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
        ytd_b = (33000, 30000)
        ind = [HDR] + dre_ind_banco(ref, [("ÚLTIMO", f"{y}-01-01", ref, ytd_b[0]), ("PENÚLTIMO", f"{y-1}-01-01", f"{y-1}-09-30", ytd_b[1])])
        p = TMP / f"itr{y}.zip"
        zipar(p, {f"itr_cia_aberta_DRE_con_{y}.csv": dre, f"itr_cia_aberta_DRE_ind_{y}.csv": ind,
                  f"itr_cia_aberta_BPP_con_{y}.csv": bpp, f"itr_cia_aberta_BPP_ind_{y}.csv": [HDR_BPP] + bpp_ind_banco(ref),
                  f"itr_cia_aberta_composicao_capital_{y}.csv": cap})
        arq[f"ITR/DADOS/itr_cia_aberta_{y}.zip"] = p
    ipe_hdr = "CNPJ_Companhia;Nome_Companhia;Codigo_CVM;Data_Referencia;Categoria;Tipo;Especie;Assunto;Data_Entrega;Tipo_Apresentacao;Protocolo_Entrega;Versao;Link_Download"
    cn = EMP["PETR"][0]
    ipe = [ipe_hdr,
           f"{cn};PETROBRAS;9512;{ANO}-03-01;Fato Relevante;;;Revisão da Política de Remuneração aos Acionistas;{ANO}-03-01;AP;111;1;http://x/111",
           f"{cn};PETROBRAS;9512;{ANO}-04-01;Aviso aos Acionistas;;;Pagamento de dividendos e JCP;{ANO}-04-01;AP;112;1;http://x/112",
           f"{EMP['TAEE'][0]};TAESA;20257;{ANO}-05-01;Comunicado ao Mercado;;;Payout de {ANO};{ANO}-05-01;AP;113;1;http://x/113"]
    for y in (ANO - 1, ANO):
        p = TMP / f"ipe{y}.zip"
        zipar(p, {f"ipe_cia_aberta_{y}.csv": ipe if y == ANO else [ipe_hdr]})
        arq[f"IPE/DADOS/ipe_cia_aberta_{y}.zip"] = p
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
    sd = []
    if emissor == "ITUB":  # bonificação de 10% em março de dois anos atrás (as ações da CVM já refletem)
        sd = [{"isinCode": f"BRITUB{c}1", "label": "BONIFICACAO", "factor": "10,00000000000", "lastDatePrior": f"01/03/{ANO - 2}"}
              for c in ("ACNOR", "ACNPR")]
    if emissor == "PETR":  # desdobramento de 1 para 2 depois do formulário de 30/09 (valores antigos não ajustados)
        sd = [{"isinCode": f"BRPETR{c}1", "label": "DESDOBRAMENTO", "factor": "100,00000000000", "lastDatePrior": f"01/10/{ANO}"}
              for c in ("ACNOR", "ACNPR", "CDAM")]
    return [{"code": emissor, "cashDividends": ev, "stockDividends": sd}]


def teste_proventos_parcelas():
    hist_js = {"page": {"totalPages": 1}, "results": [
        {"typeStock": "ON", "valueCash": "0,30", "lastDatePriorEx": "15/03/2025", "corporateAction": "DIVIDENDO"},
        {"typeStock": "ON", "valueCash": "0,30", "lastDatePriorEx": "15/03/2025", "corporateAction": "DIVIDENDO"},
        {"typeStock": "ON", "valueCash": "0,50", "lastDatePriorEx": "15/06/2025", "corporateAction": "DIVIDENDO"}]}
    bloco = {"cashDividends": [
        {"isinCode": "BRXXXXACNOR1", "rate": "0,50", "label": "DIVIDENDO", "lastDatePrior": "15/06/2025", "paymentDate": "30/06/2025"},
        {"isinCode": "BRXXXXACNOR1", "rate": "0,30", "label": "DIVIDENDO", "lastDatePrior": "15/03/2025", "paymentDate": "30/03/2025"}],
        "stockDividends": [
        {"isinCode": "BRXXXXACNOR1", "label": "DESDOBRAMENTO", "factor": "400,00", "lastDatePrior": "28/04/2026"},
        {"isinCode": "BRXXXXCDAM11", "label": "DESDOBRAMENTO", "factor": "400,00", "lastDatePrior": "28/04/2026"},
        {"isinCode": "BRXXXXACNOR1", "label": "BONIFICACAO", "factor": "2,9647", "lastDatePrior": "23/12/2025"},
        {"isinCode": "BRXXXXACNOR1", "label": "GRUPAMENTO", "factor": "0,025", "lastDatePrior": "14/04/2025"},
        {"isinCode": "BRXXXXACNOR1", "label": "RESG TOTAL RV", "factor": "100,00", "lastDatePrior": "14/04/2025"}]}
    orig = b3.get_json
    b3.get_json = lambda url, **kw: hist_js if "GetListedCashDividends" in url else None
    try:
        ev = b3.proventos("XXXX", "NOME", bloco=bloco)
    finally:
        b3.get_json = orig
    got = sorted((e["data_com"], e["valor"]) for e in ev)
    assert got == [("2025-03-15", 0.3), ("2025-03-15", 0.3), ("2025-06-15", 0.5)], got
    print("ok  proventos: parcelas iguais no histórico contam todas; o suplemento substitui um registro cada")
    f = [(x["data_com"], round(x["fator"], 6)) for x in b3.eventos_acoes(bloco)]
    assert f == [("2025-04-14", 0.025), ("2025-12-23", 1.029647), ("2026-04-28", 5.0)], f
    print("ok  eventos em ações: grupamento, bonificação e desdobramento com os fatores da B3")
    vivo = {"stockDividends": [
        {"isinCode": "BRVIVTACNOR1", "label": "DESDOBRAMENTO", "factor": "7.900,00000000000", "lastDatePrior": "14/04/2025"},
        {"isinCode": "BRVIVTACNOR1", "label": "GRUPAMENTO", "factor": "0,02500000000", "lastDatePrior": "14/04/2025"},
        {"isinCode": "BRVIVTACNPR1", "label": "DESDOBRAMENTO", "factor": "7.900,00000000000", "lastDatePrior": "14/04/2025"}]}
    fv = indicadores.fatores_por_data(b3.eventos_acoes(vivo))
    assert len(fv) == 1 and abs(fv[0][1] - 2.0) < 1e-9, fv
    print("ok  eventos em ações: desdobramento e grupamento no mesmo dia se combinam (Vivo ×80 × 0,025 = ×2)")
    info = b3.acoes_b3({"numberCommonShares": "5.606.747.177", "numberPreferredShares": "5.590.117.604",
                        "totalNumberShares": "11.196.864.781"})
    assert info["total"] == 11196864781 and info["on"] == 5606747177, info
    print("ok  cadastro da B3: quantidade de ações com ponto de milhar")


def teste_acoes_hoje():
    e = cvm.Empresa("x", "1", "Banco X", acoes_on=5_585_000_000, acoes_pn=4_985_700_000, acoes_ref="2026-09-30",
                    tesouraria_on=10_000_000, tesouraria_pn=11_300_000)
    info = {"on": 5_606_747_177, "pn": 5_590_117_604, "total": 11_196_864_781,
            "subscricoes": [{"data": "2026-09-04", "percentual": 5.72, "classe": "ON"}]}
    main._atualizar_acoes(e, [], info)
    assert round(e.acoes_on + e.acoes_pn) == 11_196_864_781 - 21_300_000, (e.acoes_on, e.acoes_pn)
    print("ok  ações: aumento de capital no cadastro da B3 e fora do formulário da CVM entra na conta")
    v = cvm.Empresa("y", "2", "Mineradora Y", acoes_on=4_072_366_000, acoes_pn=0, acoes_ref="2026-09-30",
                    tesouraria_on=183_396_000)
    main._atualizar_acoes(v, [], {"on": 4_439_159_752, "pn": 12, "total": 4_439_159_764, "subscricoes": []})
    assert v.acoes_on == 4_072_366_000, v.acoes_on
    print("ok  ações: cadastro da B3 desatualizado (cancelamento) sem subscrição não altera o formulário da CVM")
    s = cvm.Empresa("z", "3", "Saneamento Z", acoes_on=700_000_000, acoes_pn=0, acoes_ref="2026-03-31", tesouraria_on=3_500_000)
    main._atualizar_acoes(s, [{"classe": "ON", "data_com": "2026-04-28", "fator": 5.0, "tipo": "DESDOBRAMENTO"}], {})
    assert s.acoes_on == 3_500_000_000 and s.tesouraria_on == 17_500_000, (s.acoes_on, s.tesouraria_on)
    print("ok  ações: desdobramento depois do formulário multiplica as ações")


def main_teste():
    teste_proventos_parcelas()
    teste_acoes_hoje()
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
        if "bcdata.sgs.432" in url:
            return [{"data": "05/11/2026", "valor": "14.25"}]
        if "bcdata.sgs.13522" in url:
            return [{"data": "01/10/2026", "valor": "4,6"}]
        if "olinda" in url:
            return {"value": [{"cotacaoVenda": 5.0, "dataHoraCotacao": "2026-09-30 13:00:00.000"}]}
        return None

    cvm.baixar = b3.baixar = avisos.baixar = fake_baixar
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
    ok("TAEE payout média 6 anos", t11["payout_media"], 0.7 * 1033 / 1000)
    assert t11["payout_media_anos"].endswith("(média de 6 anos)"), t11["payout_media_anos"]
    ok("TAEE11 P/L médio", t11["pl_medio"], 30 / (1000e6 / 1033e6 * 3))
    ok("ITUB ações corrigidas de milhares", i4["qtd_mi"], 10000.0)
    ok("PETR ações: documento isolado em milhares e desdobramento 1:2 posterior ao formulário", A["PETR4"]["qtd_mi"], 26000.0)
    ok("PETR lucro anual com linha de controladores zerada → individual (não o consolidado com minoritários)",
       A["PETR4"]["detalhe"]["lucro_anual_mi"][ANO - 2], 90000)
    ok("PETR lucro anual com controladores zerados e sem individual → consolidado (com aviso)",
       A["PETR4"]["detalhe"]["lucro_anual_mi"][ANO - 4], 100005, tol=1e-7)
    assert any("usado o consolidado" in x for x in A["PETR4"]["avisos"]), A["PETR4"]["avisos"]
    ok("PETR lucro com consolidada zerada → individual", A["PETR4"]["detalhe"]["lucro_anual_mi"][ANO - 6], 77000)
    ok("ITUB lucro 12M (banco: individual, padrão Banco Central)", i4["lucro12m_mi"], (33000 + 40000 - 30000) * 0.95)
    ok("ITUB lucro de um ano sem comparativo no DFP seguinte → DFP do próprio ano",
       A["ITUB4"]["detalhe"]["lucro_anual_mi"].get(ANO - 2), 40000 * 0.95)
    ok("ITUB4 VPA (banco: patrimônio da individual)", i4["vpa"], 7600e6 / 10000e6)
    ok("PETR4 dividendo médio na base de ações de hoje (÷2)", A["PETR4"]["div_medio6"], 0.35)
    ok("PETR4 DY médio igual antes e depois do desdobramento", A["PETR4"]["dy_medio"], 0.7 / 30)
    ok("PETR payout 12M não muda com o desdobramento", A["PETR4"]["payout_12m"], 0.7 * 13000 / 95000)
    adj = (4 * (1 / 1.1) + 2 * 1.0) / 6 * 0.7
    ok("ITUB4 dividendo médio ajustado pela bonificação", i4["div_medio6"], adj)
    ok("Petrobras dívida bruta (US$ bi)", base["petrobras"]["divida_bruta_usd_bi"], 310000 / 5.0 / 1000)
    assert "empresa sem dados da CVM" in A["VALE3"]["avisos"], A["VALE3"]["avisos"]
    print("ok  VALE3 sem dados é sinalizado")
    # payout 12 meses: um dividendo de junho (0,5) e um JCP de dezembro (0,2) em qualquer janela de 365 dias
    ok("TAEE payout 12M", t11["payout_12m"], 0.7 * 1033 / 1100)
    # avisos: a primeira varredura grava o histórico sem disparar; um documento novo depois dispara
    main.RAIZ = TMP
    n1 = main.verificar_avisos()
    est = json.loads((main.DADOS / "avisos.json").read_text())
    assert n1 == [] and len(est["itens"]) == 2, (n1, est["itens"])
    print("ok  avisos: 2 documentos de política na 1ª varredura (pagamento comum ignorado), sem disparo")
    with zipfile.ZipFile(arq[f"IPE/DADOS/ipe_cia_aberta_{ANO}.zip"], "a") as z:
        pass
    linhas = zipfile.ZipFile(arq[f"IPE/DADOS/ipe_cia_aberta_{ANO}.zip"]).read(f"ipe_cia_aberta_{ANO}.csv").decode("latin-1").splitlines()
    linhas.append(f"{EMP['ITUB'][0]};ITAU;19348;{ANO}-10-01;Fato Relevante;;;Nova política de dividendos;{ANO}-10-01;AP;114;1;http://x/114")
    zipar(arq[f"IPE/DADOS/ipe_cia_aberta_{ANO}.zip"], {f"ipe_cia_aberta_{ANO}.csv": linhas})
    n2 = main.verificar_avisos()
    assert [a["emissor"] for a in n2] == ["ITUB"], n2
    assert (TMP / ".saida" / "avisos_novos.md").exists()
    print("ok  avisos: documento novo do Itaú dispara aviso")
    main.diario()
    cot = json.loads((main.DADOS / "cotacoes.json").read_text())
    assert cot["macro"]["selic"] == {"valor": 14.25, "data": "2026-11-05", "descricao": bcb.SERIES["selic"][1], "serie": 432,
                                     "vigente_desde": "2026-11-05"}, cot.get("macro")
    assert cot["macro"]["ipca12m"]["valor"] == 4.6, cot["macro"]
    print("ok  macro: Selic meta e IPCA 12 meses do SGS")
    fat = json.loads((main.DADOS / "fatos.json").read_text())["por_emissor"]
    assert [f["assunto"] for f in fat["PETR"]] == ["Pagamento de dividendos e JCP", "Revisão da Política de Remuneração aos Acionistas"], fat
    assert fat["ITUB"][0]["categoria"] == "Fato Relevante" and fat["TAEE"][0]["link"] == "http://x/113", fat
    print("ok  fatos: últimos documentos por empresa, do mais novo para o mais antigo")
    rss = """<?xml version="1.0"?><rss><channel>
      <item><title>Petrobras aprova dividendos de R$ 10 bi - Valor</title><link>https://news.google.com/a1</link>
        <pubDate>Thu, 12 Nov 2026 22:10:00 GMT</pubDate><source url="https://valor.globo.com">Valor</source></item>
      <item><title>Petrobras aprova dividendos de R$ 10 bi - InfoMoney</title><link>https://news.google.com/a2</link>
        <pubDate>Thu, 12 Nov 2026 21:00:00 GMT</pubDate><source url="https://infomoney.com.br">InfoMoney</source></item>
      <item><title>Ibovespa hoje ao vivo: PETR4 sobe - Exame</title><link>https://news.google.com/a3</link>
        <pubDate>Fri, 13 Nov 2026 13:00:00 GMT</pubDate><source url="https://exame.com">Exame</source></item>
      <item><title>CRISE NA PETROBRAS | veja - Instagram</title><link>https://news.google.com/a5</link>
        <pubDate>Fri, 13 Nov 2026 21:00:00 GMT</pubDate><source url="https://instagram.com">Instagram</source></item>
      <item><title>Dólar fecha em alta - G1</title><link>https://news.google.com/a4</link>
        <pubDate>Fri, 13 Nov 2026 20:00:00 GMT</pubDate><source url="https://g1.globo.com">G1</source></item>
    </channel></rss>"""
    nts = noticias._filtrar(noticias.ler_rss(rss), ["petrobras", "petr4"], 8)
    assert [(n["titulo"], n["fonte"]) for n in nts] == [("Petrobras aprova dividendos de R$ 10 bi", "Valor")], nts
    assert nts[0]["data"] == "2026-11-12T22:10:00+00:00", nts
    print("ok  notícias: título sem o veículo, repetidas, ao vivo, redes sociais e sem citação descartadas")
    print((main.DADOS / "relatorio.md").read_text()[:600])
    shutil.rmtree(TMP)
    print("\nTODOS OS TESTES PASSARAM")


if __name__ == "__main__":
    main_teste()
