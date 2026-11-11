"""Testes da fonte ANP (preços de combustíveis). Offline, com amostra embutida; o teste de rede tem o marcador `rede`."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from mcp import Client

from brasil_macro.fontes import anp
from brasil_macro.http import ErroFonte
from brasil_macro.server import criar_servidor

_PREFIXO = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc/dsan"
_CABECALHO = (
    "Regiao - Sigla;Estado - Sigla;Municipio;Revenda;CNPJ da Revenda;Nome da Rua;Numero Rua;Complemento;"
    "Bairro;Cep;Produto;Data da Coleta;Valor de Venda;Valor de Compra;Unidade de Medida;Bandeira"
)


def _linha(uf, municipio, revenda, bairro, produto, data, valor, unidade="R$ / litro", bandeira="BRANCA") -> str:
    return f"NE;{uf};{municipio};{revenda};00.000.000/0001-00;RUA X;1;;{bairro};00000-000;{produto};{data};{valor};;{unidade};{bandeira}"


def _c(uf, municipio, valor, revenda="POSTO", bairro="CENTRO", bandeira="BRANCA", dia=1) -> anp.Coleta:
    return anp.Coleta(
        uf=uf,
        municipio=municipio,
        bairro=bairro,
        revenda=revenda,
        bandeira=bandeira,
        produto="GASOLINA",
        data=date(2026, 10, dia),
        valor=valor,
        unidade="R$ / litro",
    )


# ---------------------------------------------------------------- descoberta de arquivos


def test_classificar_links_aceita_os_nomes_publicados_em_cada_ano():
    html = f"""
    <a href="{_PREFIXO}/2025/precos-gasolina-etanol-06.csv">a</a>
    <a href="{_PREFIXO}/2026/10-diesel-gnv.csv">b</a>
    <a href="{_PREFIXO}/2026/02-cados-abertos-preco-gasolina-etanol.csv">c</a>
    <a href="{_PREFIXO}/2026/06-dados-abertos-precos-2026-06-glp.csv">d</a>
    <a href="{_PREFIXO}/2026/readme.pdf">ignorado</a>
    """
    achados = anp.classificar_links(html)
    assert achados[("gasolina-etanol", 2025, 6)].endswith("precos-gasolina-etanol-06.csv")
    assert achados[("diesel-gnv", 2026, 10)].endswith("10-diesel-gnv.csv")
    assert ("gasolina-etanol", 2026, 2) in achados
    assert ("glp", 2026, 6) in achados
    assert len(achados) == 4


def test_classificar_links_prefere_a_versao_csv_do_mesmo_mes():
    html = (
        f'<a href="{_PREFIXO}/2026/04-dados-abertos-precos-glp">sem extensão</a>'
        f'<a href="{_PREFIXO}/2026/04-dados-abertos-precos-glp.csv">csv</a>'
    )
    assert anp.classificar_links(html)[("glp", 2026, 4)].endswith(".csv")


# ---------------------------------------------------------------- leitura do CSV


def test_ler_csv_decodifica_utf8_e_cp1252_no_mesmo_arquivo():
    cabecalho = ("﻿" + _CABECALHO).encode("utf-8")
    # 'm³' em cp1252 (byte 0xB3) e 'SABBÁ' em UTF-8, no mesmo arquivo, como a ANP publica
    linha_cp1252 = _linha("BA", "BARREIRAS", "POSTO", "CENTRO", "GNV", "01/10/2026", "4,10", "R$ / m³", "VIBRA").encode("cp1252")
    linha_utf8 = _linha("SP", "SAO PAULO", "POSTO", "CENTRO", "GASOLINA", "01/10/2026", "6,89", bandeira="SABBÁ").encode("utf-8")
    corpo = b"\n".join([cabecalho, linha_cp1252, linha_utf8])

    linhas = anp.ler_csv(corpo)

    assert linhas[0]["Unidade de Medida"] == "R$ / m³"
    assert linhas[1]["Bandeira"] == "SABBÁ"


def test_ler_csv_sem_colunas_esperadas_levanta_erro():
    with pytest.raises(ErroFonte, match="formato esperado"):
        anp.ler_csv(b"a;b\n1;2\n")


def test_coleta_converte_virgula_decimal_e_descarta_linha_sem_preco():
    corpo = "\n".join(
        [
            _CABECALHO,
            _linha("SP", "SAO PAULO", "POSTO", "CENTRO", "GASOLINA", "01/10/2026", "6,89"),
            _linha("SP", "SAO PAULO", "POSTO", "CENTRO", "GASOLINA", "01/10/2026", ""),
            _linha("SP", "SAO PAULO", "POSTO", "CENTRO", "GASOLINA", "data-ruim", "6,00"),
        ]
    ).encode("utf-8")

    coletas = [c for linha in anp.ler_csv(corpo) if (c := anp._coleta(linha))]

    assert len(coletas) == 1
    assert coletas[0].valor == pytest.approx(6.89)
    assert coletas[0].data == date(2026, 10, 1)


def test_resumir_media_minimo_maximo_e_postos_distintos():
    cs = [_c("SP", "A", 6.0, revenda="X"), _c("SP", "A", 6.0, revenda="X", dia=2), _c("MG", "B", 7.0, revenda="Y")]
    resumo = anp.resumir(cs)
    assert resumo == {"media": 6.333, "minimo": 6.0, "maximo": 7.0, "coletas": 3, "postos": 2}


# ---------------------------------------------------------------- busca de um mês


def test_coletas_de_mes_nao_publicado_explica_os_meses_disponiveis(monkeypatch, rodar):
    async def fake_arquivos():
        return {("gasolina-etanol", 2025, 6): f"{_PREFIXO}/2025/precos-gasolina-etanol-06.csv"}

    monkeypatch.setattr(anp, "arquivos", fake_arquivos)
    with pytest.raises(LookupError, match="Meses publicados: 06/2025 a 06/2025"):
        rodar(anp.coletas("gasolina", 2026, 1))


def test_produto_desconhecido_lista_as_opcoes():
    with pytest.raises(ValueError, match="gasolina, gasolina_aditivada"):
        anp.resolver_produto("querosene")


# ---------------------------------------------------------------- ferramenta combustiveis_precos


def _chamar(rodar, monkeypatch, cs, args):
    async def fake_coletas(produto, ano, mes):
        return cs, "https://exemplo.invalido/arquivo.csv", datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)

    async def fake_ultimo(produto):
        return (2026, 10)

    monkeypatch.setattr(anp, "coletas", fake_coletas)
    monkeypatch.setattr(anp, "ultimo_mes", fake_ultimo)

    async def executar():
        async with Client(criar_servidor()) as cliente:
            return await cliente.call_tool("combustiveis_precos", args)

    return rodar(executar())


_AMOSTRA = [
    _c("SP", "SAO PAULO", 6.0, revenda="POSTO A", bairro="SE"),
    _c("SP", "SAO PAULO", 5.5, revenda="POSTO A", bairro="SE", dia=5),
    _c("SP", "CAMPINAS", 6.4, revenda="POSTO B", bairro="CENTRO"),
    _c("MG", "BELO HORIZONTE", 5.8, revenda="POSTO C", bairro="PRACA", bandeira="IPIRANGA"),
]


def test_combustiveis_precos_brasil_ranqueia_ufs_mais_baratas_primeiro(monkeypatch, rodar):
    res = _chamar(rodar, monkeypatch, _AMOSTRA, {})
    assert not res.is_error
    dados = json.loads(res.content[0].text)
    assert dados["escopo"] == "Brasil"
    assert dados["periodo"] == "10/2026"
    assert dados["resumo"]["coletas"] == 4
    assert [r["uf"] for r in dados["por_uf"]] == ["MG", "SP"]
    assert dados["consultado_em"] == "2026-11-10T12:00Z"


def test_combustiveis_precos_uf_ranqueia_municipios(monkeypatch, rodar):
    res = _chamar(rodar, monkeypatch, _AMOSTRA, {"uf": "sp"})
    dados = json.loads(res.content[0].text)
    assert dados["escopo"] == "UF SP"
    assert dados["municipios"] == 2
    assert dados["por_municipio"][0]["municipio"] == "SAO PAULO"


def test_combustiveis_precos_municipio_lista_postos_sem_repetir_o_mesmo_posto(monkeypatch, rodar):
    res = _chamar(rodar, monkeypatch, _AMOSTRA, {"uf": "SP", "municipio": "são paulo"})
    dados = json.loads(res.content[0].text)
    assert dados["escopo"] == "SAO PAULO/SP"
    assert dados["postos_distintos"] == 1  # POSTO A aparece duas vezes, fica o preço mais baixo
    assert dados["postos_mais_baratos"][0]["valor"] == 5.5


@pytest.mark.parametrize(
    "args, trecho",
    [
        ({"produto": "querosene"}, "inválido"),
        ({"ano": 2026}, "ano e mês juntos"),
        ({"municipio": "Campinas"}, "Informe a UF"),
        ({"uf": "RJ"}, "Sem coletas"),
        ({"limite": 0}, "limite deve estar entre"),
    ],
)
def test_combustiveis_precos_erros_sao_legiveis(monkeypatch, rodar, args, trecho):
    res = _chamar(rodar, monkeypatch, _AMOSTRA, args)
    assert res.is_error
    assert trecho in res.content[0].text


@pytest.mark.rede
def test_rede_anp_media_de_gasolina_em_jun_2025(rodar):
    async def f():
        cs, url, _ = await anp.coletas("gasolina", 2025, 6)
        return anp.resumir(cs), url

    resumo, url = rodar(f())
    assert resumo["coletas"] > 1000
    assert 4 < resumo["media"] < 9
    assert url.endswith(".csv")
