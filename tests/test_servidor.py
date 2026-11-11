"""Testes do servidor MCP em processo: registro das ferramentas e erros legíveis.

Sem marcador `rede`, nenhuma chamada sai para a internet.
"""

from __future__ import annotations

import json

import pytest
from mcp import Client

from brasil_macro.server import criar_servidor

ESPERADAS = {
    "buscar_indicadores",
    "obter_serie",
    "comparar_series",
    "painel",
    "sgs_serie",
    "sgs_buscar",
    "expectativas_focus",
    "ptax",
    "copom",
    "ipca_detalhado",
    "ipca_item_historico",
    "sidra_consulta",
    "sidra_metadados",
    "sidra_buscar_tabelas",
    "calendario_divulgacoes",
    "combustiveis_precos",
    "emendas_pix",
    "fundos_cvm",
    "fundos_informe_diario",
    "indicadores_por_uf",
    "dados_abertos_buscar",
    "ibge_localidades",
}


async def _com_cliente(funcao):
    async with Client(criar_servidor()) as cliente:
        return await funcao(cliente)


def test_ferramentas_registradas(rodar):
    async def f(c):
        return {t.name for t in (await c.list_tools()).tools}

    nomes = rodar(_com_cliente(f))
    assert ESPERADAS <= nomes


def test_todas_somente_leitura_e_com_descricao(rodar):
    async def f(c):
        return (await c.list_tools()).tools

    for t in rodar(_com_cliente(f)):
        assert (t.description or "").strip(), f"{t.name} sem descrição"
        assert t.annotations is not None and t.annotations.read_only_hint is True, t.name


def test_buscar_indicadores_offline(rodar):
    async def f(c):
        return await c.call_tool("buscar_indicadores", {"consulta": "selic meta"})

    r = rodar(_com_cliente(f))
    assert not r.is_error
    texto = r.content[0].text
    dados = json.loads(texto)
    assert "selic" in texto.lower()
    assert dados  # algum resultado


def test_indicador_inexistente_da_mensagem_util(rodar):
    async def f(c):
        return await c.call_tool("obter_serie", {"indicador": "nao_existe_xyz"})

    r = rodar(_com_cliente(f))
    assert r.is_error
    assert "buscar_indicadores" in r.content[0].text


def test_data_invalida_da_mensagem_util(rodar):
    async def f(c):
        return await c.call_tool("obter_serie", {"indicador": "ipca", "inicio": "ontem"})

    r = rodar(_com_cliente(f))
    assert r.is_error
    assert "data" in r.content[0].text.lower()


@pytest.mark.rede
def test_obter_serie_real(rodar):
    async def f(c):
        return await c.call_tool("obter_serie", {"indicador": "selic_meta", "inicio": "2026-01"})

    r = rodar(_com_cliente(f))
    assert not r.is_error
    assert "BCB" in r.content[0].text
