"""Tesouro Nacional: leitura de planilhas e CSVs (offline) e consultas reais (rede)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from brasil_macro import catalogo
from brasil_macro.fontes import tesouro
from brasil_macro.http import ErroFonte

# ------------------------------------------------------------------ amostras RTN

_MENSAL = [
    ("Voltar", None, None, None, None),
    ("Tabela 1.1. Resultado Primário do Governo Central - Brasil - Mensal", None, None, None, None),
    ("R$ Milhões - Valores Correntes", None, None, None, None),
    (None, None, None, None, None),
    ("Discriminação", datetime(2026, 1, 1), datetime(2026, 2, 1), datetime(2026, 3, 1), datetime(2026, 4, 1)),
    ("1. RECEITA TOTAL 1/", 300.0, 200.0, 250.0, None),
    ("1.1 -  Receita Administrada pela RFB", 150.0, 100.0, 120.0, 130.0),
    ("2. TRANSF. POR REPARTIÇÃO DE RECEITA", 50.0, 40.0, 45.0, 60.0),
    ("5. RESULTADO PRIMÁRIO GOVERNO CENTRAL - ACIMA DA LINHA (3 - 4)", -10.0, 20.5, -3.333, 7.0),
    (None, None, None, None, None),
    ("Obs.: Dados sujeitos à alteração.", None, None, None, None),
    ("1/ Apurado pelo conceito de caixa.", None, None, None, None),
]

_ANUAL_PIB = [
    ("Voltar", None, None, None, None),
    ("Tabela 2.1-A Resultado Primário - Anual - Resumido", None, None, None, None),
    ("% do PIB", None, None, None, None),
    (None, None, None, None, None),
    ("Discriminação", 2023, 2024, 2025, None),
    ("5. RESULTADO PRIMÁRIO", -0.02088, -0.003644, -0.004843, None),
    (None, None, None, None, None),
    ("Memorando:", 2023, 2024, 2025, None),
    (" PIB Nominal (R$ Milhões)", 10943344.6, 11779250.5, 12738565.6, None),
]

_COMPARATIVA = [
    ("Voltar", None, None, None, None, None),
    ("Tabela 3.1. Resultado Primário", None, None, None, None, None),
    ("R$ Milhões", None, None, None, None, None),
    (None, None, None, None, None, None),
    ("Discriminação", "Julho", None, "Variação Nominal", None, None),
    (None, 2025, 2026, "R$ Milhões", "Var. %", None),
    (None, 2025, 2026, 2025, 2026, 2025),
    ("1. RECEITA TOTAL", 243998.9, 275883.9, 31885.0, 0.13, 1.0),
]

_TRIMESTRAL = [
    ("Tabela 4.1", None, None, None, None, None),
    ("R$ Milhões", None, None, None, None, None),
    ("Discriminação", "2006-I", "2006-II", "2006-III", "2006-IV", None),
    ("1 Receita", 1.0, 2.0, 3.0, 4.0, None),
]


def test_ler_tabela_mensal():
    t = tesouro.ler_tabela(_MENSAL, "1.1")
    assert t.periodicidade == "mensal"
    assert t.unidade == "R$ Milhões - Valores Correntes"
    assert t.periodos == [date(2026, m, 1) for m in (1, 2, 3, 4)]
    assert [l.codigo for l in t.linhas] == ["1", "1.1", "2", "5"]
    assert t.linhas[0].nome == "RECEITA TOTAL"  # sem a chamada de nota "1/"
    assert t.linhas[0].valores == [300.0, 200.0, 250.0, None]
    assert t.linhas[1].nivel == 2
    assert not any("Obs" in l.nome or "Apurado" in l.nome for l in t.linhas)  # notas de rodapé ficam fora


def test_ler_tabela_trimestral_e_anual_em_pib():
    tri = tesouro.ler_tabela(_TRIMESTRAL, "4.1")
    assert tri.periodicidade == "trimestral"
    assert tri.periodos[:2] == [date(2006, 1, 1), date(2006, 4, 1)]

    pib = tesouro.ler_tabela(_ANUAL_PIB, "2.1-A")
    assert pib.periodicidade == "anual" and pib.periodos == [date(2023, 1, 1), date(2024, 1, 1), date(2025, 1, 1)]
    # Frações viram pontos percentuais; a linha de PIB em R$ não é multiplicada.
    assert pib.linhas[0].valores == pytest.approx([-2.088, -0.3644, -0.4843])
    assert len(pib.linhas) == 2  # "Memorando:" é título de bloco, não dado
    assert pib.linhas[1].nome == "PIB Nominal (R$ Milhões)" and pib.linhas[1].secao == "Memorando"
    assert pib.linhas[1].valores[0] == pytest.approx(10943344.6)


def test_tabela_comparativa_e_rejeitada():
    with pytest.raises(ValueError, match="não é uma série histórica"):
        tesouro.ler_tabela(_COMPARATIVA, "3.1")


def test_localizar_linha_por_codigo_e_texto():
    t = tesouro.ler_tabela(_MENSAL, "1.1")
    assert tesouro.localizar_linha(t, "5", "resultado primario governo central").codigo == "5"
    # Se a numeração mudar, o texto manda; se nada casar, o erro é explícito.
    assert tesouro.localizar_linha(t, "9", "transf. por reparticao").codigo == "2"
    with pytest.raises(ErroFonte, match="não tem uma linha única"):
        tesouro.localizar_linha(t, "9", "linha que nao existe")
    # Número certo mas texto trocado: não devolve outra série em silêncio.
    with pytest.raises(ErroFonte):
        tesouro.localizar_linha(t, "1", "despesa total")


def test_combinar_linhas_e_observacoes():
    t = tesouro.ler_tabela(_MENSAL, "1.1")
    valores, descricao = tesouro.combinar_linhas(
        t, {"linha": "1", "menos": [{"linha": "2", "confere": "repartição"}]}
    )
    assert valores == [250.0, 160.0, 205.0, None]
    assert "linha 1" in descricao and "linha 2" in descricao
    obs = tesouro.observacoes(t, t.linhas[-1].valores, date(2026, 2, 1), date(2026, 4, 1))
    assert obs == [(date(2026, 2, 1), 20.5), (date(2026, 3, 1), -3.33), (date(2026, 4, 1), 7.0)]


def test_catalogo_rtn_declarado():
    ind = catalogo.obter_indicador("rtn_resultado_primario")
    assert ind.origem == "rtn"
    assert "superávit" in ind.notas.lower()  # a convenção de sinal consta nas notas
    ids = [i.id for i in catalogo.todos() if i.id.startswith("rtn_")]
    assert len(ids) >= 20


# ------------------------------------------------------------------ amostras DPF

_ANEXO_ESTOQUE = [
    (None, None, None, None, None),
    ("ANEXO 2.1 - ESTOQUE DA DPF", None, None, None, None),
    ("                    ", None, None, None, "(R$ Bilhões)"),
    (None, None, None, None, None),
    (None, "Dez/00", "Jan/01", "Fev/01", "Mar/01"),
    ("DPF EM PODER DO PÚBLICO", 650.0, 660.0, 670.0, 680.0),
    ("DPMFi", 500.0, 510.0, 520.0, 530.0),
    ("Tesouro Nacional", None, None, None, 99.0),
    ("   LFT ", None, 200.0, 210.0, 220.0),
    ("   LTN", None, 100.0, 110.0, 120.0),
    ("   NTN-B", None, 150.0, 150.0, 150.0),
    ("   NTN-B1", None, None, None, 1.0),
    ("DPFe ¹", 150.0, 150.0, 150.0, 150.0),
    ("DPMFi EM PODER DO BANCO CENTRAL", 130.0, 138.0, 140.0, 144.0),
    ("    LTN", None, 50.0, 60.0, 70.0),
    ("    LFT", None, 80.0, 80.0, 74.0),
    (" NTN-B", None, 10.0, 10.0, 10.0),
    (None, None, None, None, None),
    ("¹ Valores da DPFe convertidos para US$.", None, None, None, None),
    ("Obs.1: Valores apurados pela curva.", None, None, None, None),
]

_ANEXO_FATORES = [
    ("ANEXO 2.9 - FATORES DE VARIAÇÃO DA DPF", None, None, None, None),
    (None, None, None, None, None),
    ("Indicadores", "Jan/07", "Fev/07", "Mar/07", 2007),
    (None, "R$ Milhões", "R$ Milhões", "R$ Milhões", "R$ Milhões"),
    ("Estoque anterior1", 1000.0, 1010.0, 1020.0, 1000.0),
    ("DPMFi", 900.0, 910.0, 920.0, 900.0),
    ("Variação Nominal", 10.0, 20.0, 30.0, 60.0),
    ("DPMFi", 8.0, 15.0, 25.0, 48.0),
    ("I.2 - Juros Apropriados", 5.0, 6.0, 7.0, 18.0),
]

_TD_CSV = (
    "Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;PU Compra Manha;PU Venda Manha;PU Base Manha\n"
    "Tesouro IPCA+;15/05/2035;06/11/2026;7,87;7,99;2515,93;2491,12;2491,12\n"
    "Tesouro IPCA+ com Juros Semestrais;15/05/2035;06/11/2026;7,84;7,96;4402,14;4368,52;4368,52\n"
    "Tesouro Selic;01/03/2027;06/11/2026;0,02;0,03;20251,05;20240,05;20240,05\n"
    "Tesouro IPCA+;15/05/2035;05/11/2026;7,90;8,02;2510,00;2485,00;2485,00\n"
    "Tesouro Selic;01/03/2027;05/11/2026;0,02;0,03;20240,75;20229,76;20229,76\n"
    "Tesouro Selic;01/03/2027;04/11/2026;;;;;\n"
).encode("utf-8")


def test_ler_anexo_estoque():
    t = tesouro.ler_anexo(_ANEXO_ESTOQUE, "2.1")
    assert t.unidade == "R$ Bilhões" and t.periodicidade == "mensal"
    assert t.periodos == [date(2000, 12, 1), date(2001, 1, 1), date(2001, 2, 1), date(2001, 3, 1)]
    nomes = [l.nome for l in t.linhas]
    assert "Tesouro Nacional" not in nomes  # linha de bloco sem série confiável
    assert "DPFe" in nomes  # chamada de rodapé removida
    assert "NTN-B1" in nomes and "DPMFi / NTN-B" in nomes  # "1" de NTN-B1 faz parte do nome
    # Rótulos repetidos em blocos diferentes ficam distintos
    assert "DPMFi / LTN" in nomes and "DPMFi EM PODER DO BANCO CENTRAL / LTN" in nomes
    assert not any("Valores" in n or "Obs" in n for n in nomes)  # rodapé fora dos dados
    ltn = tesouro.localizar_linha(t, confere="DPMFi / LTN")
    assert ltn.valores == [None, 100.0, 110.0, 120.0]
    # Soma de partes: DPMFi (sem NTN-B1) = LFT + LTN + NTN-B no último mês
    soma, _ = tesouro.combinar_linhas(
        t, {"confere": "DPMFi / LFT", "mais": [{"confere": "DPMFi / LTN"}, {"confere": "DPMFi / NTN-B"}]}
    )
    assert soma[-1] == 490.0


def test_ler_anexo_fatores_ignora_colunas_anuais():
    t = tesouro.ler_anexo(_ANEXO_FATORES, "2.9")
    assert t.unidade == "R$ Milhões"
    assert t.periodos == [date(2007, m, 1) for m in (1, 2, 3)]  # a coluna "2007" (ano) fica de fora
    nomes = [l.nome for l in t.linhas]
    assert nomes == [
        "Estoque anterior",
        "Estoque anterior / DPMFi",
        "Variação Nominal",
        "Variação Nominal / DPMFi",
        "I.2 - Juros Apropriados",
    ]
    assert tesouro.localizar_linha(t, confere="Variação Nominal / DPMFi").valores == [8.0, 15.0, 25.0]


def test_ler_anexo_sem_meses_da_erro():
    with pytest.raises(ValueError, match="linha de meses"):
        tesouro.ler_anexo(_MENSAL, "1.1")


def test_ler_cotacoes_td():
    por_titulo, datas = tesouro.ler_cotacoes_td(_TD_CSV)
    assert datas == [date(2026, 11, 5), date(2026, 11, 6)]  # a linha sem valores é descartada
    ipca = por_titulo[("Tesouro IPCA+", date(2035, 5, 15))]
    assert [c[0] for c in ipca] == [date(2026, 11, 5), date(2026, 11, 6)]  # em ordem de data
    assert ipca[-1] == (date(2026, 11, 6), 7.87, 7.99, 2515.93, 2491.12, 2491.12)
    with pytest.raises(ValueError, match="cabeçalho"):
        tesouro.ler_cotacoes_td(b"a;b\n1;2\n")


def test_titulos_td_distingue_ipca_com_juros():
    por_titulo, datas = tesouro.ler_cotacoes_td(_TD_CSV)
    dados = tesouro.DadosTD(por_titulo, datas)
    assert tesouro.titulos_td(dados, "ipca+", "2035-05-15") != []
    assert [t[0] for t in tesouro.titulos_td(dados, "Tesouro IPCA+")] == ["Tesouro IPCA+"]  # exato, sem o "com Juros"
    assert len(tesouro.titulos_td(dados, "ipca")) == 2  # texto parcial: os dois
    assert len(tesouro.titulos_td(dados, None, "2035")) == 2 and len(tesouro.titulos_td(dados, None, "03/2027")) == 1
    assert tesouro.titulos_td(dados, "selic", "2030") == []


def test_catalogo_dpf_declarado():
    ids = {i.id for i in catalogo.todos() if i.origem == "dpf"}
    assert {"dpf_estoque", "dpf_dpmfi", "dpf_dpfe", "dpf_lft", "dpf_juros_apropriados"} <= ids


# ------------------------------------------------------------------------- rede


@pytest.mark.rede
def test_rtn_resultado_primario_real(rodar):
    serie = rodar(catalogo.obter_dados(catalogo.obter_indicador("rtn_resultado_primario"), date(2024, 1, 1), date(2024, 12, 1)))
    assert len(serie.observacoes) == 12
    assert serie.unidade.lower().startswith("r$ milhões")
    total = sum(v for _, v in serie.observacoes)
    # 2024: déficit primário de cerca de R$ 43 bilhões na metodologia acima da linha
    assert -80_000 < total < -10_000


@pytest.mark.rede
def test_rtn_pib_anual_e_derivado(rodar):
    pib = rodar(catalogo.obter_dados(catalogo.obter_indicador("rtn_resultado_primario_pib_anual"), date(2020, 1, 1), date(2022, 1, 1)))
    valores = dict(pib.observacoes)
    assert -12 < valores[date(2020, 1, 1)] < -9  # déficit de 2020 ~ -10% do PIB
    rgps = rodar(catalogo.obter_dados(catalogo.obter_indicador("rtn_resultado_rgps"), date(2024, 1, 1), date(2024, 12, 1)))
    assert -330_000 < sum(v for _, v in rgps.observacoes) < -290_000  # déficit do RGPS em 2024: ~R$ 307 bi


@pytest.mark.rede
def test_dpf_estoque_real(rodar):
    serie = rodar(catalogo.obter_dados(catalogo.obter_indicador("dpf_estoque"), date(2023, 12, 1), date(2023, 12, 31)))
    assert len(serie.observacoes) == 1
    assert 6_000 < serie.observacoes[0][1] < 7_500  # DPF de dez/2023: ~R$ 6,6 tri (em R$ bilhões)
    dpmfi = rodar(catalogo.obter_dados(catalogo.obter_indicador("dpf_dpmfi"), date(2023, 12, 1), date(2023, 12, 31)))
    dpfe = rodar(catalogo.obter_dados(catalogo.obter_indicador("dpf_dpfe"), date(2023, 12, 1), date(2023, 12, 31)))
    assert dpmfi.observacoes[0][1] + dpfe.observacoes[0][1] == pytest.approx(serie.observacoes[0][1], abs=0.05)


@pytest.mark.rede
def test_tesouro_direto_real(rodar):
    dados = rodar(tesouro.dados_tesouro_direto())
    assert dados.datas[0] <= date(2005, 1, 1) and dados.datas[-1] >= date(2026, 1, 1)
    titulos = tesouro.titulos_td(dados, "selic")
    assert titulos
    ultimo = dados.por_titulo[titulos[-1]][-1]
    assert ultimo[1] < 1.0  # no Tesouro Selic a taxa é o ágio sobre a Selic, perto de zero
    assert 5_000 < ultimo[3] < 40_000  # PU do Tesouro Selic ~ R$ 15 a 20 mil


# ---------------------------------------------------------------- SICONFI

_ENTES = [
    {"cod_ibge": 1, "ente": "União", "esfera": "U", "uf": None, "populacao": 8569324},
    {"cod_ibge": 35, "ente": "São Paulo", "esfera": "E", "uf": "BR", "populacao": 46024937},
    {"cod_ibge": 3550308, "ente": "São Paulo", "esfera": "M", "uf": "SP", "populacao": 11904961},
    {"cod_ibge": 3509502, "ente": "Campinas", "esfera": "M", "uf": "SP", "populacao": 1187974},
    {"cod_ibge": 3304557, "ente": "Rio de Janeiro", "esfera": "M", "uf": "RJ", "populacao": 6211223},
    {"cod_ibge": 33, "ente": "Rio de Janeiro", "esfera": "E", "uf": "BR", "populacao": 16000000},
    {"cod_ibge": 2211100, "ente": "União", "esfera": "M", "uf": "PI", "populacao": 40000},
]


def test_buscar_entes():
    codigos = lambda *a: [e["cod_ibge"] for e in tesouro.buscar_entes(_ENTES, *a)]  # noqa: E731
    assert codigos("São Paulo") == [35, 3550308]  # estado e capital: ambíguo, quem chama decide
    assert codigos("sao paulo", None, "E") == [35]
    assert codigos("rio de janeiro", "RJ") == [33, 3304557]  # a UF do estado vem do código, não do campo uf ("BR")
    assert codigos("3509502") == [3509502]
    assert codigos("campinas", "RJ") == []
    with pytest.raises(ValueError, match="UF desconhecida"):
        tesouro.buscar_entes(_ENTES, "x", "ZZ")


_ITENS_ANEXO = [
    {"anexo": "RREO-Anexo 01", "rotulo": "Padrão", "coluna": "PREVISÃO", "cod_conta": "Rc", "conta": "RECEITAS CORRENTES", "valor": 10.456},
    {"anexo": "RREO-Anexo 01", "rotulo": "Padrão", "coluna": "REALIZADA", "cod_conta": "Rc", "conta": "RECEITAS CORRENTES", "valor": 9.0},
    {"anexo": "RREO-Anexo 01", "rotulo": "Padrão", "coluna": "PREVISÃO", "cod_conta": "RcIntra", "conta": "RECEITAS CORRENTES", "valor": 1.0},
    {"anexo": "RREO-Anexo 01", "rotulo": "Padrão", "coluna": "% (b/a)", "cod_conta": "Pessoal", "conta": "Pessoal e Encargos", "valor": 0.5},
]


def test_tabela_siconfi_agrupa_e_filtra():
    from brasil_macro.ferramentas import tesouro as ferr

    saida: dict = {}
    notas: list[str] = []
    ferr._tabela_siconfi(saida, _ITENS_ANEXO, None, None, 10, notas)
    assert saida["colunas"] == ["PREVISÃO", "REALIZADA", "% (b/a)"]
    primeira = saida["linhas"][0]
    assert primeira == {"conta": "RECEITAS CORRENTES", "cod_conta": "Rc", "valores": [10.46, 9.0, None]}
    assert saida["linhas"][1]["cod_conta"] == "RcIntra"  # nome repetido: o código distingue
    assert "cod_conta" not in saida["linhas"][2]

    filtrado: dict = {}
    ferr._tabela_siconfi(filtrado, _ITENS_ANEXO, "pessoal", None, 10, notas)
    assert filtrado["colunas"] == ["% (b/a)"] and filtrado["n_contas"] == 1  # coluna vazia nas linhas exibidas some

    truncado: dict = {}
    avisos: list[str] = []
    ferr._tabela_siconfi(truncado, _ITENS_ANEXO, None, None, 1, avisos)
    assert len(truncado["linhas"]) == 1 and truncado["n_contas"] == 3 and "truncada" in avisos[0]
    with pytest.raises(LookupError, match="Nenhuma conta"):
        ferr._tabela_siconfi({}, _ITENS_ANEXO, "inexistente", None, 10, [])


def test_nome_do_anexo():
    from brasil_macro.ferramentas import tesouro as ferr

    assert ferr._anexo_siconfi("RREO", "1") == "RREO-Anexo 01"
    assert ferr._anexo_siconfi("RGF", "02") == "RGF-Anexo 02"
    assert ferr._anexo_siconfi("DCA", "I-C") == "DCA-Anexo I-C"
    assert ferr._anexo_siconfi("RREO", "RREO-Anexo 10 - RPPS") == "RREO-Anexo 10 - RPPS"


def test_paginacao_do_siconfi(monkeypatch, rodar):
    chamadas = []

    async def falso(url, *, params=None, **_):
        import json
        from datetime import datetime, timezone

        from brasil_macro.http import Resposta

        chamadas.append(params["offset"])
        pagina = params["offset"] // 5000
        corpo = {"items": [{"n": pagina}] * 2, "hasMore": pagina < 2}
        return Resposta(json.dumps(corpo).encode(), url, 200, datetime.now(timezone.utc))

    monkeypatch.setattr(tesouro, "requisitar", falso)
    completa = rodar(tesouro.consultar_siconfi("rreo", {"id_ente": 1}))
    assert chamadas == [0, 5000, 10000] and len(completa.itens) == 6 and not completa.truncada
    chamadas.clear()
    limitada = rodar(tesouro.consultar_siconfi("rreo", {"id_ente": 1}, max_paginas=2))
    assert chamadas == [0, 5000] and limitada.truncada


@pytest.mark.rede
def test_siconfi_real(rodar):
    from brasil_macro.ferramentas import tesouro as ferr

    entes = rodar(tesouro.entes_siconfi())
    assert len(entes) > 5000
    sp = tesouro.buscar_entes(entes, "São Paulo", None, "E")
    assert [e["cod_ibge"] for e in sp] == [35]
    consulta = rodar(
        tesouro.consultar_siconfi(
            "rgf",
            {"an_exercicio": 2024, "id_ente": 35, "in_periodicidade": "Q", "nr_periodo": 3,
             "co_tipo_demonstrativo": "RGF", "co_poder": "E", "no_anexo": "RGF-Anexo 02"},
        )
    )
    dc = [i["valor"] for i in consulta.itens if i["cod_conta"] == "DividaConsolidada" and "3º" in i["coluna"]]
    assert dc and 3e11 < dc[0] < 5e11  # dívida consolidada do estado de SP: ~R$ 370 bi
