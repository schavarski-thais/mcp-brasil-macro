# mcp-brasil-macro

> **English:** MCP (Model Context Protocol) server with official Brazilian macroeconomic data: Selic and Copom, IPCA inflation, Focus survey, GDP, unemployment, exchange rate (PTAX), public debt, fiscal results, trade, fuel prices and investment funds. Sources: Banco Central (SGS), IBGE (SIDRA), Tesouro Nacional, IPEA, MDIC, ANP, CVM. 476 curated indicators. Works with Claude Code, Claude Desktop and any MCP client over stdio. Docs below are in Portuguese.

Servidor MCP (stdio) com dados macroeconômicos do Brasil, **somente de fontes oficiais**:
Banco Central (SGS, Focus, PTAX, Copom), IBGE (SIDRA), Tesouro Nacional (RTN, dívida, SICONFI),
IPEA (Ipeadata, que redistribui séries de órgãos oficiais), MDIC/Secex (ComexStat), ANP
(levantamento de preços de combustíveis nos postos), TransfereGov (emendas Pix), CVM (cadastro de
fundos), IBGE (localidades) e os portais de dados abertos do BNDES e da ANEEL.

Cobre política monetária, inflação (com as quebras do IPCA), atividade, mercado de trabalho,
política fiscal (resultado primário, NFSP, dívida), crédito, setor externo, comércio exterior,
preços de combustíveis, emendas parlamentares, fundos de investimento e divisão territorial. O catálogo curado tem **476 indicadores oficiais** (mais 12 de produtores
privados, desativados por padrão); o que estiver fora dele se alcança pelas ferramentas
genéricas (`sgs_serie`, `sidra_consulta`, `ipea_serie`).

## Instalação

Requer Python 3.11 ou superior. Instalação direta do GitHub:

```bash
pip install git+https://github.com/schavarski-thais/mcp-brasil-macro.git
```

Isso instala o comando `mcp-brasil-macro`. Para desenvolvimento, clone o repositório:

```bash
git clone https://github.com/schavarski-thais/mcp-brasil-macro.git
cd mcp-brasil-macro
python -m venv .venv
.venv/bin/python -m pip install -e .        # no Windows: .venv\Scripts\python.exe
```

Registro no Claude Code:

```bash
claude mcp add brasil-macro -- mcp-brasil-macro
# ou, se instalou em um venv: claude mcp add brasil-macro -- /caminho/.venv/bin/python -m brasil_macro.server
```

Para outros clientes MCP, o comando é o mesmo (`python -m brasil_macro.server`, ou o script
`mcp-brasil-macro`), em transporte stdio.

## Ferramentas

| Tema | Ferramenta | Para quê |
|---|---|---|
| Catálogo | `buscar_indicadores` | Localiza indicadores por texto, tema ou fonte |
| | `obter_serie` | Série de um indicador, com transformações (variação, acumulado 12m, média móvel, base 100…) |
| | `comparar_series` | Vários indicadores alinhados por período |
| | `painel` | Último valor, anterior e de 12 meses antes de um tema |
| Banco Central | `sgs_serie`, `sgs_buscar` | Qualquer série do SGS pelo código; busca no catálogo de dados abertos |
| | `expectativas_focus` | Focus: anual, mensal, trimestral, 12 e 24 meses, Selic, Top 5, evolução |
| | `ptax` | Cotações PTAX de fechamento |
| | `copom` | Decisões, comunicados e atas; data da próxima reunião |
| IBGE | `ipca_detalhado`, `ipca_item_historico` | Quebras do IPCA, IPCA-15 e INPC por grupo, subgrupo, item e subitem, com peso e contribuição em p.p. |
| | `sidra_consulta`, `sidra_metadados`, `sidra_buscar_tabelas` | Qualquer tabela do SIDRA |
| | `calendario_divulgacoes` | Próximas divulgações do IBGE e do Copom |
| Tesouro | `resultado_tesouro` | RTN do governo central: receitas, despesas e resultado primário |
| | `divida_publica_federal` | Estoque por instrumento e fatores de variação |
| | `tesouro_direto_taxas` | Taxas e preços do Tesouro Direto |
| | `siconfi_entes`, `siconfi_relatorio` | RREO, RGF e DCA de estados e municípios |
| IPEA | `ipea_serie`, `ipea_buscar` | Ipeadata: séries com a fonte original |
| MDIC | `comercio_exterior` | Exportação, importação e saldo por país, bloco, UF, via, seção ou capítulo |
| TransfereGov | `emendas_pix` | Emendas Pix por UF, parlamentar ou beneficiário, com situação e valores dos planos de ação |
| CVM | `fundos_cvm` | Fundos de investimento (RCVM 175) por nome, gestor, administrador, classificação, tipo, público-alvo ou CNPJ; ficha com CNPJs e prestadores, e subclasses |
| | `fundos_informe_diario` | Cota, patrimônio, captação, resgate e cotistas por dia de um FIF (informe diário da CVM), com captação líquida e rentabilidade simples do período. FII, FIDC, FIP e FIAGRO não estão incluídos |
| IBGE | `ibge_localidades` | Estados e municípios com os códigos oficiais |
| | `indicadores_por_uf` | Desocupação, rendimento, massa, ocupados e PIB das 27 UFs, com comparação de período e ano anterior |
| BNDES, ANEEL | `dados_abertos_buscar` | Busca de conjuntos de dados nos portais de dados abertos, com recursos para download |
| ANP | `combustiveis_precos` | Preço nos postos (gasolina, etanol, diesel, GNV, GLP): média nacional e por UF; com UF, municípios; com município, postos mais baratos |

## Convenções de sinal

Os números vêm como as fontes os publicam, e as convenções diferem:

- **NFSP (BCB)**: valor positivo é **déficit**; negativo, superávit.
- **RTN (Tesouro)**: valor positivo é **superávit**.
- **Focus, resultado primário**: negativo é déficit.
- **Balanço de pagamentos**: transações correntes negativas são déficit; na conta financeira, negativo é entrada líquida.
- **Câmbio real efetivo**: alta do índice é depreciação real.

Cada resultado traz fonte, código, unidade, periodicidade e data da consulta.

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `BRASIL_MACRO_CACHE_DIR` | Pasta do cache SQLite (padrão: pasta de cache do usuário) |
| `BRASIL_MACRO_SEM_CACHE=1` | Desliga o cache |
| `BRASIL_MACRO_INCLUIR_PRIVADOS=1` | Habilita séries de produtores privados distribuídas por órgão oficial (IGP-M e IGP-DI da FGV, Confiança do Consumidor da Fecomercio, consumo de energia da Eletrobras). **Desligado por padrão** |

## Cache e limites das fontes

- Respostas ficam em cache (15 min para séries diárias e expectativas, 6 h para mensais e trimestrais, 7 dias para metadados). Se a fonte cair, o servidor entrega o cache vencido e avisa.
- Cada servidor remoto tem limite de concorrência e espaçamento entre chamadas.
- **ComexStat** recusa requisições (HTTP 429) de forma irregular. A ferramenta `comercio_exterior` repete a consulta e devolve erro claro se não passar; vale tentar de novo em instantes. Os indicadores `exportacoes`, `importacoes`, `saldo_comercial` e `corrente_comercio` usam o Ipeadata (mesma fonte, MDIC/Secex, com defasagem) quando o ComexStat não responde.
- A API de séries temporais do Tesouro Transparente está fora do ar (404). RTN e dívida usam as planilhas publicadas no portal de dados abertos, resolvidas a cada consulta.
- **ANP**: cada mês é um CSV de vários MB (um por tipo de combustível). A lista de arquivos é lida da página da série histórica, porque os nomes mudam de um ano para outro. Meses passados ficam em cache por 7 dias e o mês corrente e o anterior por 6 h. Os preços são médias simples das coletas, sem ponderar pelo volume vendido. A série mensal de cada combustível sem período informado cobre os últimos 24 meses publicados.
- **CVM, informe diário**: cada mês é um zip de cerca de 10 MB, então a primeira consulta de um período é lenta. Meses com mais de dois meses de idade ficam em cache por 7 dias; os recentes, por 6 h.
- Não há no SGS decomposição da DBGG, TLP nem série mensal de reservas no conceito liquidez.

## Desenvolvimento

```powershell
.venv\Scripts\python.exe -m pytest                  # testes offline
.venv\Scripts\python.exe -m pytest -m rede          # testes contra as APIs oficiais
.venv\Scripts\python.exe scripts\validar_catalogo.py   # confere todos os códigos SGS na fonte
.venv\Scripts\python.exe scripts\conferir_sidra.py     # confere os indicadores do IBGE
.venv\Scripts\python.exe scripts\chamar.py obter_serie '{"indicador":"selic_meta","ultimos":3}'
```

Estrutura: `catalogo/*.yaml` descreve os indicadores por tema; `fontes/` busca os dados de cada
origem e se registra com `@fonte_de_dados`; `ferramentas/` expõe as ferramentas MCP. Novos
módulos em `fontes/` e `ferramentas/` são descobertos automaticamente. Para acrescentar um
indicador de uma origem existente, basta uma entrada no YAML do tema.
