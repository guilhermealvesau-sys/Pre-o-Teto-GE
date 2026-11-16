# Preço Teto GE — coletor de dados públicos

Coleta, de fontes oficiais, os dados que alimentam a página **Preço Teto GE** (claude.ai).
O consenso de lucro **não** fica aqui: ele é enviado por planilha na própria página, que é privada.

## Fontes

| Dado | Fonte |
|---|---|
| Lucro dos controladores (12 meses e anual), patrimônio líquido, nº de ações, dívida bruta | CVM – Portal de Dados Abertos (ITR e DFP) |
| Proventos em dinheiro (valor, tipo, data com) | B3 – empresas listadas |
| Cotações (fechamento diário e histórico anual) | B3 – série histórica COTAHIST |
| Câmbio | Banco Central – PTAX de venda |

## Quando roda

- **Dias úteis, 06h17 (Brasília):** coleta completa (CVM, proventos, eventos em ações, preços) e fechamento do pregão anterior.
  Na temporada de balanços a CVM publica ITR novos quase todo dia; por isso a coleta completa é diária.
- **Domingos, 05h37:** coleta completa.
- Manualmente: aba **Actions → Coleta de dados → Run workflow**.

## Definições (janela: 6 últimos anos completos)

- **Base de ações:** a B3 publica cada provento com o valor declarado na época, sem ajustar desdobramentos e
  bonificações posteriores. Todos os cálculos convertem cada provento e cada preço para a base de ações de hoje com
  os fatores de desdobramento, grupamento e bonificação da própria B3 (ex.: Sabesp 1:5 em abr/2026).
- **Parcelas:** a B3 lista cada parcela de pagamento como um registro (ex.: Cemig e Petrobras pagam em duas parcelas
  iguais). Todas contam. Os registros que aparecem nas duas listagens da B3 são pareados um a um.
- **Lucro 12M:** lucro atribuído aos controladores dos últimos 4 trimestres (acumulado do ano + ano anterior − mesmo período do ano anterior).
  Quando a linha "atribuído aos controladores" vem zerada, vale o lucro da individual (a controladora), e só sem ela
  o consolidado menos os não controladores.
- **Bancos** (ABC, Banrisul, Bradesco, Banco do Brasil, BTG, Itaú, Santander): lucro e patrimônio da demonstração
  individual, no padrão do Banco Central (BR GAAP), que é o dos releases e o usado pelo mercado. A consolidada da
  CVM desses bancos é IFRS e difere (ex.: o ágio do Santander deixa o patrimônio IFRS cerca de 30% maior).
- **Nº de ações:** ON + PN integralizadas, menos as em tesouraria, no último formulário entregue à CVM, levadas para hoje:
  desdobramentos e bonificações posteriores multiplicam a quantidade; aumento de capital já registrado na B3 e ainda
  fora do formulário (subscrição recente) entra pela quantidade do cadastro da B3.
- **VPA:** patrimônio líquido dos controladores ÷ nº de ações × ações por papel (units).
- **Payout (usado no modelo):** mediana dos payouts anuais dos 3 últimos anos completos — recente o bastante para refletir a política atual e imune a um ano extraordinário.
  Payout anual = proventos com data com no ano × ações no fim do ano ÷ lucro do ano (critério de caixa, igual em todos os anos).
- **Payout 12M (comparação):** proventos com data com nos últimos 12 meses × ações de hoje ÷ lucro dos últimos 12 meses. Reage rápido a mudanças de política, mas oscila com extraordinários.
- **Payout média (opção):** média simples dos payouts anuais dos 6 últimos anos completos (5, se faltar um ano).
- **DY médio:** média dos DY anuais (proventos com data com no ano ÷ preço médio de fechamento do ano, na base de hoje).
- **Dividendo médio (R$):** média anual dos proventos por papel, na base de ações de hoje.
- **Proventos considerados:** dividendos, JCP (bruto) e rendimentos. Devoluções de capital ficam de fora.
- **% em JCP:** parcela dos proventos dos últimos 3 anos completos paga como juros sobre capital próprio.
- **P/L médio:** mediana dos P/L anuais (preço médio do ano ÷ lucro por ação do ano), ignorando anos com prejuízo.

Lucros são **contábeis** (incluem efeitos não recorrentes), como na planilha original.

Auditoria: `ferramentas/auditoria_referencias.py` e `ferramentas/auditoria_detalhe.py` (workflow **Auditoria**) gravam em
`auditoria/` referências independentes (Fundamentus, Yahoo, IBGE, Banco Central, B3) para conferir a coleta.

Tickers que mudaram de código usam o histórico do código antigo (coluna `antigos` de `config/ativos.csv`).

## Arquivos

- `config/ativos.csv` — papéis, classe e composição das units
- `config/empresas.csv` — emissores; `cd_cvm` vazio é resolvido pelo cadastro da CVM (resultado em `dados/mapa_cvm.csv`)
- `dados/` — saída das coletas (gerada pelo robô; não editar à mão)

Teste sem internet: `python -m tests.teste_offline`
