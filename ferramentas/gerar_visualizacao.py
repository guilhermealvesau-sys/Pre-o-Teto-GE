"""Gera a página de visualização (ranking completo, seletores e notícias) a partir do dashboard público."""
import sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
t = src.read_text(encoding="utf-8")

def rep(old, new, n=1):
    global t
    assert t.count(old) == n, (old[:80], t.count(old))
    t = t.replace(old, new)

rep("<title>Dashboard Preço Teto GE</title>", "<title>Ranking Preço Teto GE</title>")
rep("<h1>Dashboard Preço Teto GE</h1>", "<h1>Ranking Preço Teto GE</h1>")
rep("/* Dashboard público: top 10 de cada ranking, painel de notícias do papel escolhido e comparação por setor.",
    "/* Página de visualização: ranking completo com seletores de base e payout, notícias e comparação por setor.")
rep('''    <button role="tab" id="t-setores" aria-controls="p-setores" aria-selected="false" data-tab="setores" tabindex="-1">Comparação por setor</button>
  </nav>''', '''    <button role="tab" id="t-setores" aria-controls="p-setores" aria-selected="false" data-tab="setores" tabindex="-1">Comparação por setor</button>
    <button role="tab" id="t-noticias" aria-controls="p-noticias" aria-selected="false" data-tab="noticias" tabindex="-1">Notícias</button>
  </nav>''')
rep('''    <span class="small"><b>Base projetada</b> · <b>payout dos últimos 12 meses</b> <span class="muted">(commodities: payout da política)</span></span>''',
    '''    <div class="ctl"><span class="muted">Base</span><div class="seg" id="segBase"><button type="button" data-v="PROJETADO">Projetado</button><button type="button" data-v="CONSERVADOR">Conservador</button></div></div>
    <div class="ctl"><span class="muted">Payout</span><div class="seg" id="segPay"><button type="button" data-v="MEDIANO">Mediano 3 anos</button><button type="button" data-v="MEDIA">Média 5–6 anos</button><button type="button" data-v="ATUAL">12 meses</button></div></div>
    <label class="switch"><input type="checkbox" id="uma"> Uma classe por empresa</label>''')
rep('<h2 id="hB">Top 10 BESST</h2><p>Maior desconto ao preço teto · clique numa linha para ver as notícias ao lado</p>',
    '<h2 id="hB">Ranking BESST</h2><p>Do maior para o menor desconto ao preço teto · clique numa linha para ver as notícias ao lado</p>')
# painel de notícias
rep('''  <section role="tabpanel" id="p-setores" aria-labelledby="t-setores" hidden>''',
    '''  <section role="tabpanel" id="p-noticias" aria-labelledby="t-noticias" hidden>
  <section class="panel" aria-labelledby="hN">
    <div class="panel-head"><h2 id="hN">Notícias e documentos</h2><p id="notSub"></p></div>
    <div class="inner"><label class="ctl" for="notEmp"><span class="muted">Empresa</span> <select id="notEmp"></select></label>
      <div class="seg" id="segNot"><button type="button" data-v="noticias">Manchetes</button><button type="button" data-v="fatos">Documentos na CVM</button></div></div>
    <div class="list" id="notLista"><div class="empty">Carregando…</div></div>
  </section>
  </section>

  <section role="tabpanel" id="p-setores" aria-labelledby="t-setores" hidden>''')
rep('''<div><b>Como ler.</b> BESST: dividendo projetado = lucro de consenso por ação × payout dos últimos 12 meses (proventos dos últimos 12 meses ÷ lucro dos últimos 12 meses).''',
    '''<div><b>Como ler.</b> BESST: dividendo projetado = lucro de consenso por ação × payout escolhido (mediano dos 3 últimos anos, média de 5–6 anos ou últimos 12 meses). Base conservadora: o menor entre o dividendo projetado e o dividendo médio de 6 anos.''')
rep('localStorage.getItem("ptgep:" + k)', 'localStorage.getItem("ptgev:" + k)')
rep('localStorage.setItem("ptgep:" + k, JSON.stringify(v))', 'localStorage.setItem("ptgev:" + k, JSON.stringify(v))')
rep('''D: null, ativos: new Map(), base: "PROJETADO", payBase: "ATUAL", uma: false, tab: "dash",''',
    '''D: null, ativos: new Map(), base: store.get("base", "PROJETADO"), payBase: store.get("payBase", "MEDIANO"), uma: store.get("uma", false), tab: "dash",
    notEmp: store.get("notEmp", ""), notTipo: store.get("notTipo", "noticias"),''')
rep('''    const b = ranked("BESST").slice(0, 10), c = ranked("COMMODITIES").slice(0, 10);''',
    '''    syncControls();
    const b = ranked("BESST"), c = ranked("COMMODITIES");''')
rep('''    $("hC").textContent = c.length < 10 ? "Commodities" : "Top 10 Commodities";
    $("subC").textContent = c.length < 10 ? `Maior desconto ao preço teto · todos os ${c.length} papéis` : "Maior desconto ao preço teto";''',
    '''    $("hC").textContent = "Ranking Commodities";
    $("subC").textContent = "Do maior para o menor desconto ao preço teto";''')
rep('''    renderSide();
    renderSetores();
  }
''', '''    renderSide();
    renderSetores();
    renderNoticias();
  }
  function syncControls() {
    for (const [id, v] of [["segBase", S.base], ["segPay", S.payBase]]) $(id).querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v)));
    $("uma").checked = !!S.uma;
    $("segNot").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === S.notTipo)));
  }
  function renderNoticias() {
    if (!S.D) return;
    const N = S.D.noticias || {}, emps = empresas();
    const sel = $("notEmp");
    if (!sel.options.length) sel.innerHTML = '<option value="">Todas as empresas</option>' + emps.map((e) => `<option value="${esc(e.em)}">${esc(e.nome)} (${esc(e.em)})</option>`).join("");
    sel.value = emps.some((e) => e.em === S.notEmp) ? S.notEmp : "";
    const nomeDe = Object.fromEntries(emps.map((e) => [e.em, e.nome]));
    const itens = [];
    for (const [em, v] of Object.entries(N)) {
      if (S.notEmp && em !== S.notEmp) continue;
      for (const x of (S.notTipo === "fatos" ? v.fatos : v.noticias) || []) itens.push({ em, ...x });
    }
    itens.sort((a, b) => String(b.data || "").localeCompare(String(a.data || "")));
    $("notSub").textContent = `${itens.length} ${S.notTipo === "fatos" ? "documentos" : "manchetes"}` + (S.D.status?.noticias_em ? ` · manchetes de ${fDateTime(S.D.status.noticias_em)}` : "");
    $("notLista").innerHTML = itens.length ? itens.slice(0, 80).map((x) => {
      const u = safeUrl(x.link), tit = S.notTipo === "fatos" ? x.assunto : x.titulo, meta = S.notTipo === "fatos" ? x.categoria : x.fonte;
      return `<div class="it"><span class="meta"><span class="num">${fDate(x.data)}</span><b>${esc(nomeDe[x.em] || x.em)}</b><span>${esc(meta || "")}</span></span>${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(tit)}</a>` : esc(tit)}</div>`;
    }).join("") : '<div class="empty">Nada para mostrar.</div>';
  }
''')
rep('''  const TABS = ["dash", "setores"];''', '''  const TABS = ["dash", "setores", "noticias"];''')
rep('''  $("tabs").addEventListener("keydown", (e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); showTab(S.tab === "dash" ? "setores" : "dash", true); } });''',
    '''  $("tabs").addEventListener("keydown", (e) => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); const i = TABS.indexOf(S.tab), d = e.key === "ArrowRight" ? 1 : -1; showTab(TABS[(i + d + TABS.length) % TABS.length], true); } });
  const seg = (id, f) => $(id).addEventListener("click", (e) => { const b = e.target.closest("button[data-v]"); if (b) f(b.dataset.v); });
  seg("segBase", (v) => { S.base = v; store.set("base", v); render(); });
  seg("segPay", (v) => { S.payBase = v; store.set("payBase", v); render(); });
  seg("segNot", (v) => { S.notTipo = v; store.set("notTipo", v); syncControls(); renderNoticias(); });
  $("uma").addEventListener("change", (e) => { S.uma = e.target.checked; store.set("uma", S.uma); render(); });
  $("notEmp").addEventListener("change", (e) => { S.notEmp = e.target.value; store.set("notEmp", S.notEmp); renderNoticias(); });''')
rep('${a.modelo === "POLITICA" ? `Payout: ${payCell(a)}` : `Payout 12 meses <b>${fPct(a.payout_12m, 0)}</b> · mediano 3 anos ${fPct(a.payout, 0)} · média 5–6 anos ${fPct(a.payout_media, 0)}`}',
    '${a.modelo === "POLITICA" ? `Payout: ${payCell(a)}` : `Payout (mediano · média · 12M): ${payCell(a)}`}')
dst.write_text(t, encoding="utf-8")
print("ok", len(t))
