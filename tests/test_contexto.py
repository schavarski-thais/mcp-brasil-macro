"""Controle de contexto: teto de resposta, formato tabular e orçamento das definições de ferramentas.

Sem marcador `rede`: tudo roda offline.
"""

from __future__ import annotations

import asyncio
import json

from mcp import Client

from brasil_macro.ferramentas._comum import TETO_RESPOSTA, encolher, tabela
from brasil_macro.server import INSTRUCOES, criar_servidor

# Orçamento do custo fixo por sessão (definições das ferramentas + instruções).
# Medido em 36,6 mil caracteres após a otimização; o teto dá folga pequena para novas ferramentas.
ORCAMENTO_FIXO = 38_000


def test_encolher_mantem_resposta_pequena_intacta():
    dados = {"a": 1, "lista": [1, 2, 3]}
    assert json.loads(encolher(dados)) == dados


def test_encolher_corta_maior_lista_e_avisa():
    dados = {"fonte": "x", "dados": [["2026-01", i, "texto " * 20] for i in range(2000)]}
    texto = encolher(dados)
    assert len(texto) <= TETO_RESPOSTA
    saida = json.loads(texto)  # continua JSON válido
    aviso = saida["truncado"]
    assert aviso["campo"] == "dados"
    assert aviso["total"] == 2000
    assert aviso["devolvidos"] == len(saida["dados"]) < 2000
    assert saida["fonte"] == "x"


def test_tabela_separa_colunas_das_linhas():
    assert tabela(["a", "b"], [[1, 2]]) == {"colunas": ["a", "b"], "linhas": [[1, 2]]}


def _definicoes() -> int:
    async def f():
        async with Client(criar_servidor()) as c:
            return (await c.list_tools()).tools

    total = 0
    for t in asyncio.run(f()):
        total += len(json.dumps({"name": t.name, "description": t.description, "inputSchema": t.input_schema}, ensure_ascii=False))
    return total


def test_orcamento_do_custo_fixo_das_ferramentas(rodar):
    total = _definicoes() + len(INSTRUCOES)
    assert total <= ORCAMENTO_FIXO, f"custo fixo {total} acima do orçamento {ORCAMENTO_FIXO}; enxugue descrições"


def test_informe_diario_detalhe_diario_pagina(monkeypatch, rodar):
    # Reaproveita o fixture do informe diário de outubro/2026 (3 dias) de test_fontes_publicas.
    from tests.test_fontes_publicas import _INFORME_2026_10, _http_fake, _json, _chamar, _zip_informe
    from brasil_macro.fontes import cvm

    monkeypatch.setattr(cvm, "requisitar", _http_fake({cvm.URL_INFORME.format(ano=2026, mes=10): _zip_informe(_INFORME_2026_10, "x.csv")}))
    dados = _json(_chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-10", "fim": "2026-10", "detalhe": "diario"}))
    primeiro = dados["series"][0]
    assert primeiro["colunas"][0] == "data"
    assert primeiro["pagina"] == 1 and primeiro["paginas"] == 1
    assert len(primeiro["linhas"]) == primeiro["dias_com_informe"] == 3
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-10", "fim": "2026-10", "detalhe": "diario", "pagina": 2})
    assert res.is_error and "além do fim" in res.content[0].text
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-10", "fim": "2026-10", "detalhe": "tudo"})
    assert res.is_error and "detalhe deve ser" in res.content[0].text
