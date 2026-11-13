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

- **Dias úteis, 06h17 (Brasília):** fechamento do pregão anterior e PTAX → `dados/cotacoes.json`
- **Domingos, 05h37:** coleta completa → `dados/base.json`, `dados/relatorio.md`
- Manualmente: aba **Actions → Coleta de dados → Run workflow**

## Definições (janela: 6 últimos anos completos)

- **Lucro 12M:** lucro atribuído aos controladores dos últimos 4 trimestres (acumulado do ano + ano anterior − mesmo período do ano anterior).
- **Nº de ações:** ON + PN integralizadas, menos as em tesouraria, no último formulário entregue à CVM.
- **VPA:** patrimônio líquido dos controladores ÷ nº de ações × ações por papel (units).
- **Payout médio:** soma dos proventos declarados referentes aos lucros da janela ÷ soma desses lucros.
- **DY médio:** média dos DY anuais (proventos com data com no ano ÷ preço médio de fechamento do ano).
- **Dividendo médio (R$):** média anual dos proventos por papel, ajustada para a quantidade de ações de hoje (corrige bonificações e desdobramentos).
- **Proventos considerados:** dividendos, JCP e rendimentos. Devoluções de capital ficam de fora.
- **% em JCP:** parcela dos proventos dos últimos 3 anos completos paga como juros sobre capital próprio.
- **P/L médio:** mediana dos P/L anuais (preço médio do ano ÷ lucro por ação do ano), ignorando anos com prejuízo.

Lucros são **contábeis** (incluem efeitos não recorrentes), como na planilha original. Quando a companhia deixa zerada a linha "atribuído aos controladores" (caso comum sem minoritários), vale o lucro consolidado.

Tickers que mudaram de código usam o histórico do código antigo (coluna `antigos` de `config/ativos.csv`).

## Arquivos

- `config/ativos.csv` — papéis, classe e composição das units
- `config/empresas.csv` — emissores; `cd_cvm` vazio é resolvido pelo cadastro da CVM (resultado em `dados/mapa_cvm.csv`)
- `dados/` — saída das coletas (gerada pelo robô; não editar à mão)

Teste sem internet: `python -m tests.teste_offline`
