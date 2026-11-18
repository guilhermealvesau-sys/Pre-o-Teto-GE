"""Confere os dados coletados contra referências independentes (Fundamentus, Yahoo, Banco Central, IBGE).

Uso (na raiz do repositório):
  python -m ferramentas.conferir_referencias [SAIDA_JSON]

Lê dados/base.json, dados/cotacoes.json, auditoria/referencias.json (workflow Auditoria) e
config/divergencias_conhecidas.json. Grava o relatório em SAIDA_JSON (padrão auditoria/conferencia.json) e
imprime um resumo em JSON. Uma divergência só vira alerta se não estiver na lista de divergências conhecidas
(papel + campo, com o motivo).

Tolerâncias: preço 0,5%; ações 2%; lucro 12 meses 5%; VPA 3%; macro igual. Lucro e VPA só são comparados
quando o último balanço da referência é o mesmo trimestre do nosso.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TOL = {"preco": 0.005, "acoes": 0.02, "lucro12m": 0.05, "vpa": 0.03}


def br(s):
    if s is None:
        return None
    s = str(s).strip().replace("%", "")
    if s in ("", "-"):
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def data_br(s):
    try:
        return datetime.strptime(str(s).strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def conferir(raiz: Path = RAIZ) -> dict:
    base = json.loads((raiz / "dados/base.json").read_text(encoding="utf-8"))
    cot = json.loads((raiz / "dados/cotacoes.json").read_text(encoding="utf-8"))
    ref = json.loads((raiz / "auditoria/referencias.json").read_text(encoding="utf-8"))
    p_con = raiz / "config/divergencias_conhecidas.json"
    conhecidas = json.loads(p_con.read_text(encoding="utf-8")).get("itens", []) if p_con.exists() else []
    reg = {(c["papel"], c["campo"]): c for c in conhecidas}

    def explicada(papel, campo, nosso, deles):
        """Divergência conhecida só vale enquanto os dois valores seguem perto (±10%) dos registrados com o motivo."""
        c = reg.get((papel, campo))
        if not c:
            return None
        perto = lambda v, r: r is None or abs(v - r) <= 0.10 * max(abs(r), 1e-9)  # noqa: E731
        return c["motivo"] if perto(nosso, c.get("nosso")) and perto(deles, c.get("referencia")) else None

    achados, n = [], 0

    def comparar(papel, campo, nosso, deles, tol, fonte, obs=""):
        nonlocal n
        if nosso is None or deles is None:
            return
        n += 1
        dif = (nosso / deles - 1) if deles else (0 if nosso == deles else 1)
        if abs(dif) > tol:
            achados.append({"papel": papel, "campo": campo, "nosso": round(nosso, 4), "referencia": round(deles, 4),
                            "fonte": fonte, "diferenca_pct": round(dif * 100, 2), "obs": obs,
                            "conhecida": explicada(papel, campo, nosso, deles)})

    pregao = cot.get("ref")
    for tk, a in base["ativos"].items():
        f = ref.get("fundamentus", {}).get(tk) or {}
        g = lambda k, i=0: (f.get(k) or [None])[i] if len(f.get(k) or []) > i else None  # noqa: E731
        px = (cot.get("cotacoes", {}).get(tk) or {}).get("preco")
        if data_br(g("Data últ cot")) == pregao:
            comparar(tk, "preco", px, br(g("Cotação")), TOL["preco"], "Fundamentus")
        yh = (ref.get("yahoo", {}).get(tk) or {}).get("fechamentos", {}).get(pregao)
        comparar(tk, "preco", px, yh, TOL["preco"], "Yahoo")
        n_f = br(g("Nro. Ações"))
        comparar(tk, "acoes", a.get("qtd_mi"), n_f / 1e6 if n_f else None, TOL["acoes"], "Fundamentus")
        bal = data_br(g("Últ balanço processado"))
        if bal and a.get("lucro12m_ate") and bal[:7] == a["lucro12m_ate"][:7]:
            ll = br(g("Lucro Líquido", 0))
            comparar(tk, "lucro12m", a.get("lucro12m_mi"), ll / 1e6 if ll else None, TOL["lucro12m"], "Fundamentus")
            fator = a.get("fator") or 1
            vpa_f = br(g("VPA"))
            if a.get("vpa") is not None and vpa_f:
                # unidades: a referência às vezes traz o VPA por ação, às vezes por unit
                nosso = a["vpa"] if abs(a["vpa"] / vpa_f - 1) <= abs(a["vpa"] / fator / vpa_f - 1) else a["vpa"] / fator
                comparar(tk, "vpa", nosso, vpa_f, TOL["vpa"], "Fundamentus")
        elif bal and a.get("lucro12m_ate"):
            achados.append({"papel": tk, "campo": "trimestre", "nosso": a["lucro12m_ate"], "referencia": bal[:7],
                            "fonte": "Fundamentus", "obs": "balanços de trimestres diferentes (não comparados)",
                            "conhecida": "informativo"})

    mac = cot.get("macro") or {}
    m = ref.get("macro") or {}
    ipca_ibge = {x["codigo"]: br(x["valor"].replace(".", ",")) for x in m.get("ipca12m_ibge", [])}
    ip = mac.get("ipca12m") or {}
    if ip.get("data"):
        comparar("macro", "ipca12m", ip.get("valor"), ipca_ibge.get(ip["data"][:7].replace("-", "")), 0.0001, "IBGE")
    sel = m.get("selic_meta_ultimo") or {}
    comparar("macro", "selic", (mac.get("selic") or {}).get("valor"), br(str(sel.get("valor", "")).replace(".", ",")),
             0.0001, "Banco Central (SGS 432)")
    pt = cot.get("ptax") or {}
    for x in m.get("ptax_bcb", []):
        if str(x.get("dataHoraCotacao", ""))[:10] == pt.get("data"):
            comparar("macro", "ptax", pt.get("valor"), x.get("cotacaoVenda"), 0.0001, "Banco Central (PTAX)")

    novas = [x for x in achados if not x.get("conhecida")]
    return {"verificado_em": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "referencias_de": ref.get("gerado_em"), "coleta_de": base.get("gerado_em"), "pregao": pregao,
            "comparacoes": n, "divergencias_novas": novas,
            "conhecidas": [x for x in achados if x.get("conhecida") and x["conhecida"] != "informativo"],
            "informativo": [x for x in achados if x.get("conhecida") == "informativo"],
            "erros_referencias": ref.get("erros") or []}


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "auditoria/conferencia.json"
    r = conferir()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"pregao": r["pregao"], "comparacoes": r["comparacoes"],
                      "divergencias_novas": [f"{x['papel']} {x['campo']}: {x['nosso']} x {x['referencia']} ({x['fonte']})"
                                             for x in r["divergencias_novas"]],
                      "conhecidas": len(r["conhecidas"]), "trimestres_diferentes": len(r["informativo"]),
                      "erros_referencias": r["erros_referencias"]}, ensure_ascii=False))
