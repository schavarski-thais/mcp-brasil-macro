"""Servidor MCP: dados macroeconômicos oficiais do Brasil."""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer

from . import __version__
from .ferramentas import registrar_todas

INSTRUCOES = """\
Dados macroeconômicos do Brasil só de fontes oficiais: BCB, IBGE, Tesouro, IPEA, MDIC, ANP, TransfereGov, \
CVM e os portais de dados abertos do BNDES e da ANEEL.

Roteiro:
- Indicador do catálogo (Selic, IPCA, PIB, desemprego, fiscal, câmbio, crédito): buscar_indicadores; depois \
obter_serie, comparar_series ou painel.
- Quebras do IPCA: ipca_detalhado. Expectativas: expectativas_focus. Copom: copom.
- Fora do catálogo: sgs_serie (BCB), sidra_consulta (IBGE), ipea_serie (Ipeadata).
- Fiscal: resultado_tesouro, divida_publica_federal, tesouro_direto_taxas; estados e municípios: siconfi_entes \
e siconfi_relatorio.
- Comércio exterior: comercio_exterior. Combustíveis nos postos: combustiveis_precos. Emendas Pix: emendas_pix.
- Fundos (CVM): fundos_cvm (cadastro) e fundos_informe_diario (cotas diárias de FIF).
- Por UF: indicadores_por_uf. Divisão territorial: ibge_localidades. Safra e população estão no catálogo.
- BNDES e ANEEL: dados_abertos_buscar.

Listas vêm em tabela (colunas e linhas). Respostas acima do teto são cortadas e trazem o campo "truncado": \
use os parâmetros indicados para ver o restante.

Cite fonte, código e data de referência em cada resultado. Nas NFSP, valor positivo é déficit.
"""


def criar_servidor() -> MCPServer:
    # O httpx registra cada requisição em INFO; no stderr de um servidor stdio isso é só ruído.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    servidor = MCPServer(
        "brasil-macro",
        title="Brasil Macro — dados oficiais",
        instructions=INSTRUCOES,
        version=__version__,
    )
    registrar_todas(servidor)
    return servidor


def main() -> None:
    criar_servidor().run("stdio")


if __name__ == "__main__":
    main()
