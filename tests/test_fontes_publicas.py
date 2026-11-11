"""Testes de TransfereGov (emendas Pix), CVM (cadastro e informe diário de fundos), CKAN (BNDES/ANEEL) e localidades do IBGE.

Offline: as respostas das fontes são substituídas por dados fabricados. Os testes de rede têm o marcador `rede`.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp import Client

from brasil_macro.fontes import ckan, cvm, ibge, localidades, transferegov
from brasil_macro.server import criar_servidor


def _chamar(rodar, nome: str, args: dict):
    async def executar():
        async with Client(criar_servidor()) as cliente:
            return await cliente.call_tool(nome, args)

    return rodar(executar())


def _json(resultado):
    return json.loads(resultado.content[0].text)


# ---------------------------------------------------------------- TransfereGov


def _plano(uf, parlamentar, custeio, investimento, situacao="CIENTE", beneficiario=None):
    return {
        "uf_beneficiario_plano_acao": uf,
        "nome_parlamentar_emenda_plano_acao": parlamentar,
        "nome_beneficiario_plano_acao": beneficiario or f"MUNICIPIO DE {uf}",
        "valor_custeio_plano_acao": custeio,
        "valor_investimento_plano_acao": investimento,
        "situacao_plano_acao": situacao,
    }


_PLANOS = [
    _plano("MG", "Fulano", 0.0, 1_000_000.0),
    _plano("MG", "Beltrano", 500_000.0, 500_000.0, situacao="IMPEDIDO"),
    _plano("SP", "Fulano", 0.0, 300_000.0),
]


def test_emendas_pix_ranqueia_ufs_por_valor(monkeypatch, rodar):
    async def fake(ano):
        return _PLANOS, transferegov.BASE

    monkeypatch.setattr(transferegov, "planos", fake)
    dados = _json(_chamar(rodar, "emendas_pix", {"ano": 2025, "limite": 5}))
    assert dados["planos"] == 3
    assert dados["valor_total"] == pytest.approx(2_300_000.0)
    assert [r["chave"] for r in dados["ranking"]] == ["MG", "SP"]
    assert dados["ranking"][0]["valor"] == pytest.approx(2_000_000.0)
    assert dados["por_situacao"]["IMPEDIDO"]["planos"] == 1


def test_emendas_pix_filtra_por_parlamentar_e_agrupa(monkeypatch, rodar):
    async def fake(ano):
        return _PLANOS, transferegov.BASE

    monkeypatch.setattr(transferegov, "planos", fake)
    dados = _json(_chamar(rodar, "emendas_pix", {"ano": 2025, "por": "parlamentar", "parlamentar": "fulano"}))
    assert dados["planos"] == 2
    assert dados["agrupado_por"] == "parlamentar"
    assert dados["ranking"] == [
        {"chave": "Fulano", "planos": 2, "valor": pytest.approx(1_300_000.0)},
    ]


def test_emendas_pix_erros(monkeypatch, rodar):
    async def fake(ano):
        return _PLANOS, transferegov.BASE

    monkeypatch.setattr(transferegov, "planos", fake)
    res = _chamar(rodar, "emendas_pix", {"ano": 2025, "uf": "RJ"})
    assert res.is_error and "Nenhum plano" in res.content[0].text
    res = _chamar(rodar, "emendas_pix", {"por": "cidade"})
    assert res.is_error and "por inválido" in res.content[0].text


def test_transferegov_pagina_ate_acabar(monkeypatch, rodar):
    paginas = [[{"id_plano_acao": i} for i in range(1000)], [{"id_plano_acao": 1000}]]
    chamadas = []

    async def fake_requisitar(url, **kwargs):
        chamadas.append(url)
        return SimpleNamespace(json=lambda: paginas[len(chamadas) - 1])

    monkeypatch.setattr(transferegov, "requisitar", fake_requisitar)
    planos, _ = rodar(transferegov.planos(2025))
    assert len(planos) == 1001
    assert "offset=1000" in chamadas[1]
    assert "ano_plano_acao=eq.2025" in chamadas[0]


# ---------------------------------------------------------------- CVM: arquivos fabricados

_CNPJ_FUNDO = "11.111.111/0001-11"
_CNPJ_CLASSE = "33.333.333/0001-33"
_CNPJ_ADMIN = "55.555.555/0001-55"


def _zip_cvm() -> bytes:
    """Cadastro RCVM 175 em miniatura, com as colunas reais, inclusive as de pessoa física (que não devem sair)."""
    fundo = (
        "ID_Registro_Fundo;CNPJ_Fundo;Codigo_CVM;Data_Registro;Data_Constituicao;Tipo_Fundo;Denominacao_Social;"
        "Data_Cancelamento;Situacao;Data_Inicio_Situacao;Data_Adaptacao_RCVM175;Patrimonio_Liquido;"
        "Data_Patrimonio_Liquido;Diretor;CNPJ_Administrador;Administrador;Tipo_Pessoa_Gestor;CPF_CNPJ_Gestor;Gestor\n"
        "1;11.111.111/0001-11;100;2020-01-02;2020-01-02;FI;FUNDO ALFA FI RF;;Em Funcionamento Normal;2020-01-02;"
        "2023-10-02;1234.56;2026-10-30;FULANO DE TAL;55.555.555/0001-55;ADMIN X;PJ;123.456.789-00;GESTORA X\n"
        "2;22.222.222/0001-22;200;2019-05-05;2019-05-05;FI;FUNDO BETA FI AÇÕES;2024-01-01;Cancelado;2024-01-01;"
        ";;;BELTRANO;55.555.555/0001-55;ADMIN X;PJ;;GESTORA Y\n"
    )
    classe = (
        "ID_Registro_Fundo;ID_Registro_Classe;CNPJ_Classe;Codigo_CVM;Data_Registro;Data_Constituicao;Data_Inicio;"
        "Tipo_Classe;Denominacao_Social;Situacao;Data_Inicio_Situacao;Classificacao;Indicador_Desempenho;"
        "Classe_Cotas;Classificacao_Anbima;Tributacao_Longo_Prazo;Entidade_Investimento;"
        "Permitido_Aplicacao_CemPorCento_Exterior;Classe_ESG;Forma_Condominio;Exclusivo;Publico_Alvo;"
        "Patrimonio_Liquido;Data_Patrimonio_Liquido;CNPJ_Auditor;Auditor;CNPJ_Custodiante;Custodiante;"
        "CNPJ_Controlador;Controlador\n"
        "1;10;33.333.333/0001-33;100;2020-01-02;2020-01-02;2020-01-02;Classes FIF;ALFA FI RF CLASSE;"
        "Em Funcionamento Normal;2020-01-02;Renda Fixa;S;Aberto;Renda Fixa Duration Baixa Soberano;N;N;N;N;"
        "Aberto;N;Geral;1234.56;2026-10-30;66.666.666/0001-66;AUDITORIA Z;77.777.777/0001-77;CUST W;;\n"
        "2;20;44.444.444/0001-44;200;2019-05-05;2019-05-05;2019-05-05;Classes FIA;BETA AÇÕES;Cancelado;2024-01-01;"
        "Ações;;;;;;;;;;;;;;;;\n"
    )
    subclasse = (
        "ID_Registro_Classe;ID_Subclasse;Codigo_CVM;Data_Constituicao;Data_Inicio;Denominacao_Social;Situacao;"
        "Data_Inicio_Situacao;Forma_Condominio;Exclusivo;Publico_Alvo;Previdenciario;Exclusivo_INR;"
        "Exclusivo_Previdencia_Complementar\n"
        "10;A;100;2020-01-02;2020-01-02;ALFA FI RF CLASSE - CLASSE A;Em Funcionamento Normal;2020-01-02;"
        "Aberto;N;Geral;N;N;N\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("registro_fundo.csv", fundo.encode("latin-1"))
        zf.writestr("registro_classe.csv", classe.encode("latin-1"))
        zf.writestr("registro_subclasse.csv", subclasse.encode("latin-1"))
    return buffer.getvalue()


_INFORME_2026_10 = (
    "TP_FUNDO_CLASSE;CNPJ_FUNDO_CLASSE;ID_SUBCLASSE;DT_COMPTC;VL_TOTAL;VL_QUOTA;VL_PATRIM_LIQ;CAPTC_DIA;RESG_DIA;NR_COTST\n"
    "Classes FIF;33.333.333/0001-33;;2026-10-01;1000.0;1.00;1000.0;100.0;0.0;10\n"
    "Classes FIF;33.333.333/0001-33;;2026-10-02;1010.0;1.01;1010.0;0.0;30.0;9\n"
    "Classes FIF;33.333.333/0001-33;;2026-10-03;1050.0;1.05;1050.0;50.0;0.0;11\n"
    "Classes FIF;33.333.333/0001-33;A;2026-10-01;500.0;2.00;500.0;0.0;0.0;4\n"
    "Classes FIF;33.333.333/0001-33;A;2026-10-02;510.0;2.20;510.0;0.0;0.0;4\n"
    "Classes FIF;44.444.444/0001-44;;2026-10-01;50.0;5.00;50.0;0.0;0.0;1\n"
)

_INFORME_2022_01 = (  # formato anterior à RCVM 175: coluna CNPJ_FUNDO, sem subclasse
    "TP_FUNDO;CNPJ_FUNDO;DT_COMPTC;VL_TOTAL;VL_QUOTA;VL_PATRIM_LIQ;CAPTC_DIA;RESG_DIA;NR_COTST\n"
    "FI;11.111.111/0001-11;2022-01-03;100.0;2.5;100.0;0.0;0.0;3\n"
)


def _zip_informe(texto: str, nome: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(nome, texto.encode("latin-1"))
    return buffer.getvalue()


def _http_fake(mapa: dict[str, bytes]):
    async def fake(url, **kwargs):
        return SimpleNamespace(corpo=mapa[url], consultado_em=datetime(2026, 11, 10, tzinfo=timezone.utc))

    return fake


def test_cvm_cadastro_junta_classe_fundo_e_subclasses(monkeypatch, rodar):
    monkeypatch.setattr(cvm, "requisitar", _http_fake({cvm.URL: _zip_cvm()}))
    registros, url, _ = rodar(cvm.cadastro())
    alfa = next(r for r in registros if r["nome"] == "ALFA FI RF CLASSE")
    assert alfa["gestor"] == "GESTORA X"
    assert alfa["cnpj_administrador"] == _CNPJ_ADMIN
    assert alfa["administrador"] == "ADMIN X"
    assert alfa["auditor"] == "AUDITORIA Z"
    assert alfa["cnpj_auditor"] == "66.666.666/0001-66"
    assert alfa["custodiante"] == "CUST W"
    assert alfa["patrimonio_liquido"] == pytest.approx(1234.56)
    assert alfa["situacao"] == "Em Funcionamento Normal"
    assert alfa["classe_esg"] == "N"
    assert alfa["subclasses"] == [
        {
            "id": "A",
            "nome": "ALFA FI RF CLASSE - CLASSE A",
            "situacao": "Em Funcionamento Normal",
            "forma_condominio": "Aberto",
            "exclusivo": "N",
            "publico_alvo": "Geral",
            "previdenciario": "N",
        }
    ]
    beta = next(r for r in registros if r["cnpj_classe"] == "44.444.444/0001-44")
    assert beta["subclasses"] == []
    assert "AÇÕES" in beta["nome"]


def test_cvm_cadastro_nao_expoe_diretor_nem_cpf_do_gestor(monkeypatch, rodar):
    monkeypatch.setattr(cvm, "requisitar", _http_fake({cvm.URL: _zip_cvm()}))
    registros, _, _ = rodar(cvm.cadastro())
    texto = json.dumps(registros, ensure_ascii=False)
    assert "FULANO" not in texto
    assert "123.456.789-00" not in texto
    assert all("diretor" not in r and "cpf_cnpj_gestor" not in r for r in registros)


def test_cvm_informe_diario_formato_novo_e_antigo(monkeypatch, rodar):
    mapa = {
        cvm.URL_INFORME.format(ano=2026, mes=10): _zip_informe(_INFORME_2026_10, "inf_diario_fi_202610.csv"),
        cvm.URL_INFORME.format(ano=2022, mes=1): _zip_informe(_INFORME_2022_01, "inf_diario_fi_202201.csv"),
    }
    monkeypatch.setattr(cvm, "requisitar", _http_fake(mapa))

    linhas, urls, _ = rodar(cvm.informe_diario([(2026, 10)], lambda c: c == "33333333000133"))
    assert len(linhas) == 5  # as três linhas do fundo sem subclasse e duas da subclasse A; o outro CNPJ fica fora
    assert {l["subclasse"] for l in linhas} == {None, "A"}
    assert linhas[0]["data"].isoformat() == "2026-10-01"

    linhas, _, _ = rodar(cvm.informe_diario([(2022, 1)], lambda c: c == "11111111000111"))
    assert len(linhas) == 1
    assert linhas[0]["cota"] == pytest.approx(2.5)
    assert linhas[0]["subclasse"] is None
    assert linhas[0]["cotistas"] == 3


def test_cvm_numero_aceita_virgula_decimal():
    assert cvm._numero("1,50") == pytest.approx(1.5)
    assert cvm._numero("1234.56") == pytest.approx(1234.56)
    assert cvm._numero("") is None
    assert cvm._numero("x") is None


def test_fundos_informe_diario_rentabilidade_e_series(monkeypatch, rodar):
    monkeypatch.setattr(cvm, "requisitar", _http_fake({cvm.URL_INFORME.format(ano=2026, mes=10): _zip_informe(_INFORME_2026_10, "x.csv")}))
    dados = _json(_chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-10", "fim": "2026-10"}))
    assert dados["cnpj"] == "33333333000133"
    assert dados["periodo"] == {"inicio": "2026-10-01", "fim": "2026-10-31"}
    sem_subclasse, classe_a = dados["series"]
    assert sem_subclasse["subclasse"] is None
    assert sem_subclasse["dias_com_informe"] == 3
    assert sem_subclasse["rentabilidade_simples_pct"] == pytest.approx(5.0)  # 1,05 / 1,00 - 1
    assert sem_subclasse["captacao_liquida_total"] == pytest.approx(120.0)  # 100 - 30 + 50
    assert sem_subclasse["cotistas_ultimo"] == 11
    # resumo: uma linha por mês, com a captação do mês somada
    assert sem_subclasse["colunas"][0] == "mes"
    assert sem_subclasse["linhas"][0][0] == "2026-10"
    assert sem_subclasse["linhas"][0][4] == pytest.approx(120.0)
    assert classe_a["subclasse"] == "A"
    assert classe_a["rentabilidade_simples_pct"] == pytest.approx(10.0)  # 2,20 / 2,00 - 1


def test_fundos_informe_diario_filtra_subclasse(monkeypatch, rodar):
    monkeypatch.setattr(cvm, "requisitar", _http_fake({cvm.URL_INFORME.format(ano=2026, mes=10): _zip_informe(_INFORME_2026_10, "x.csv")}))
    dados = _json(_chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-10", "fim": "2026-10", "subclasse": "A"}))
    assert [s["subclasse"] for s in dados["series"]] == ["A"]


def test_fundos_informe_diario_erros(monkeypatch, rodar):
    monkeypatch.setattr(cvm, "requisitar", _http_fake({cvm.URL_INFORME.format(ano=2026, mes=10): _zip_informe(_INFORME_2026_10, "x.csv")}))
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "123"})
    assert res.is_error and "14 dígitos" in res.content[0].text
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2025-01", "fim": "2026-01"})
    assert res.is_error and "máximo por chamada é 12" in res.content[0].text
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-11", "fim": "2026-10"})
    assert res.is_error and "posterior" in res.content[0].text
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "99.999.999/0001-99", "inicio": "2026-10", "fim": "2026-10"})
    assert res.is_error and "Nenhum informe" in res.content[0].text
    res = _chamar(rodar, "fundos_informe_diario", {"cnpj": "33.333.333/0001-33", "inicio": "2026-13", "fim": "2026-13"})
    assert res.is_error and "Período inválido" in res.content[0].text


# ---------------------------------------------------------------- CVM: ferramenta de cadastro

_CAMPOS_CADASTRO = [
    "cnpj_fundo", "nome_fundo", "tipo_fundo", "gestor", "cnpj_administrador", "administrador",
    "cnpj_classe", "nome", "tipo", "classificacao", "classificacao_anbima", "situacao", "situacao_desde",
    "patrimonio_liquido", "data_patrimonio", "codigo_cvm", "data_registro", "data_constituicao", "data_inicio",
    "classe_cotas", "indicador_desempenho", "tributacao_longo_prazo", "entidade_investimento", "permite_exterior_100",
    "classe_esg", "forma_condominio", "exclusivo", "publico_alvo", "cnpj_auditor", "auditor", "cnpj_custodiante",
    "custodiante", "cnpj_controlador", "controlador",
]


def _reg(**campos):
    registro = dict.fromkeys(_CAMPOS_CADASTRO)
    registro["subclasses"] = []
    registro.update(campos)
    return registro


_REGISTROS = [
    _reg(
        cnpj_classe=_CNPJ_CLASSE, cnpj_fundo=_CNPJ_FUNDO, nome="ALFA FI RF CLASSE", nome_fundo="FUNDO ALFA FI RF",
        tipo="Classes FIF", classificacao="Renda Fixa", classificacao_anbima="Renda Fixa Duration Baixa Soberano",
        situacao="Em Funcionamento Normal", situacao_desde="2020-01-02", patrimonio_liquido=1234.56,
        data_patrimonio="2026-10-30", gestor="GESTORA X", administrador="ADMIN X", cnpj_administrador=_CNPJ_ADMIN,
        auditor="AUDITORIA Z", cnpj_auditor="66.666.666/0001-66", custodiante="CUST W",
        publico_alvo="Geral", exclusivo="N", codigo_cvm="100", data_registro="2020-01-02",
        subclasses=[{"id": "A", "nome": "ALFA FI RF CLASSE - CLASSE A", "situacao": "Em Funcionamento Normal",
                     "forma_condominio": "Aberto", "exclusivo": "N", "publico_alvo": "Geral", "previdenciario": "N"}],
    ),
    _reg(
        cnpj_classe="44.444.444/0001-44", cnpj_fundo="22.222.222/0001-22", nome="BETA AÇÕES", nome_fundo="FUNDO BETA",
        tipo="Classes FIA", classificacao="Ações", situacao="Cancelado", situacao_desde="2024-01-01",
        gestor="GESTORA Y", administrador="ADMIN X", cnpj_administrador=_CNPJ_ADMIN, codigo_cvm="200",
        data_registro="2019-05-05", publico_alvo="Qualificado",
    ),
]


def _cadastro_pronto(monkeypatch):
    async def cadastro_pronto():
        return _REGISTROS, cvm.URL, datetime(2026, 11, 10, tzinfo=timezone.utc)

    monkeypatch.setattr(cvm, "cadastro", cadastro_pronto)


def test_fundos_cvm_busca_com_situacao_padrao(monkeypatch, rodar):
    _cadastro_pronto(monkeypatch)
    dados = _json(_chamar(rodar, "fundos_cvm", {"consulta": "alfa"}))
    assert dados["encontrados"] == 1
    assert dados["colunas"][4] == "gestor"
    assert dados["linhas"][0][4] == "GESTORA X"
    # a situação padrão exclui o fundo cancelado; com situacao vazia ele aparece
    res = _chamar(rodar, "fundos_cvm", {"classificacao": "Ações"})
    assert res.is_error and "Nenhum fundo" in res.content[0].text
    dados = _json(_chamar(rodar, "fundos_cvm", {"classificacao": "Ações", "situacao": ""}))
    assert dados["encontrados"] == 1


def test_fundos_cvm_filtra_por_administrador_tipo_e_publico(monkeypatch, rodar):
    _cadastro_pronto(monkeypatch)
    dados = _json(_chamar(rodar, "fundos_cvm", {"administrador": _CNPJ_ADMIN, "situacao": ""}))
    assert dados["encontrados"] == 2
    dados = _json(_chamar(rodar, "fundos_cvm", {"administrador": "admin x", "tipo": "fia", "situacao": ""}))
    assert [l[1] for l in dados["linhas"]] == ["BETA AÇÕES"]
    dados = _json(_chamar(rodar, "fundos_cvm", {"publico_alvo": "qualificado", "situacao": ""}))
    assert dados["encontrados"] == 1


def test_fundos_cvm_cnpj_devolve_ficha(monkeypatch, rodar):
    _cadastro_pronto(monkeypatch)
    dados = _json(_chamar(rodar, "fundos_cvm", {"cnpj": _CNPJ_FUNDO}))
    ficha = dados["registros"][0]
    assert ficha["nome"] == "ALFA FI RF CLASSE"
    assert ficha["codigo_cvm"] == "100"
    assert ficha["cnpj_administrador"] == _CNPJ_ADMIN
    assert ficha["auditor"] == "AUDITORIA Z"
    assert ficha["subclasses"][0]["id"] == "A"
    res = _chamar(rodar, "fundos_cvm", {"cnpj": "99.999.999/0001-99"})
    assert res.is_error and "não encontrado" in res.content[0].text


# ---------------------------------------------------------------- CKAN


def test_dados_abertos_buscar_descreve_conjuntos(monkeypatch, rodar):
    async def fake(portal, consulta, limite):
        conjunto = {
            "name": "desembolsos", "title": "Desembolsos", "notes": "Dados  de\n desembolsos",
            "organization": {"title": "BNDES"}, "metadata_modified": "2026-10-24",
            "resources": [{"name": "csv mensal", "format": "csv", "url": "https://x/a.csv"}],
        }
        return 12, [conjunto], "https://x/busca"

    monkeypatch.setattr(ckan, "buscar", fake)
    dados = _json(_chamar(rodar, "dados_abertos_buscar", {"portal": "bndes", "consulta": "desembolsos", "limite": 1}))
    assert dados["encontrados"] == 12
    c = dados["conjuntos"][0]
    assert c["descricao"] == "Dados de desembolsos"
    assert c["pagina"] == "https://dadosabertos.bndes.gov.br/dataset/desembolsos"
    assert c["recursos"][0]["formato"] == "CSV"


def test_dados_abertos_portal_invalido(rodar):
    res = _chamar(rodar, "dados_abertos_buscar", {"portal": "cvm", "consulta": "x"})
    assert res.is_error and "portal inválido" in res.content[0].text


# ---------------------------------------------------------------- localidades do IBGE

_ESTADOS = [
    {"id": 35, "sigla": "SP", "nome": "São Paulo", "regiao": {"nome": "Sudeste"}},
    {"id": 42, "sigla": "SC", "nome": "Santa Catarina", "regiao": {"nome": "Sul"}},
]
_MUNICIPIOS_SC = [
    {"id": 4214201, "nome": "Quilombo", "microrregiao": {"mesorregiao": {"UF": {"sigla": "SC"}}}},
    {"id": 4202008, "nome": "Balneário Camboriú", "microrregiao": {"mesorregiao": {"UF": {"sigla": "SC"}}}},
]


def test_localidades_estados_filtra_por_uf(monkeypatch, rodar):
    async def estados():
        return _ESTADOS

    monkeypatch.setattr(localidades, "estados", estados)
    dados = _json(_chamar(rodar, "ibge_localidades", {"nivel": "estados", "uf": "sc"}))
    assert dados["itens"] == [{"id": 42, "sigla": "SC", "nome": "Santa Catarina", "regiao": "Sul"}]


def test_localidades_municipios_exige_uf_ou_nome(monkeypatch, rodar):
    async def municipios(uf):
        return _MUNICIPIOS_SC

    monkeypatch.setattr(localidades, "municipios", municipios)
    res = _chamar(rodar, "ibge_localidades", {"nivel": "municipios"})
    assert res.is_error and "informe a uf" in res.content[0].text
    dados = _json(_chamar(rodar, "ibge_localidades", {"nivel": "municipios", "uf": "SC", "consulta": "quilombo"}))
    assert dados["itens"] == [{"id": 4214201, "nome": "Quilombo", "uf": "SC"}]


# ---------------------------------------------------------------- PNAD por UF e safra/população (etapa 1)


def _linha_uf(codigo, nome, periodo, periodo_nome, valor):
    return ibge.LinhaSidra(
        periodo=periodo, periodo_nome=periodo_nome, territorio=nome, territorio_codigo=codigo,
        variavel="Taxa de desocupação", variavel_codigo="4099", unidade="%", valor=valor,
    )


def _pnad_fake(monkeypatch):
    trimestres = [
        ("202501", "1º trimestre 2025"), ("202502", "2º trimestre 2025"), ("202503", "3º trimestre 2025"),
        ("202504", "4º trimestre 2025"), ("202601", "1º trimestre 2026"),
    ]
    valores = {"42": [6.0, 5.8, 5.5, 5.4, 5.0], "23": [8.0, 7.8, 7.5, 7.4, 7.2]}
    nomes = {"42": "Santa Catarina", "23": "Ceará"}
    linhas = [
        _linha_uf(codigo, nomes[codigo], periodo, periodo_nome, valores[codigo][i])
        for codigo in valores
        for i, (periodo, periodo_nome) in enumerate(trimestres)
    ]

    async def fake(tabela, variaveis, periodos, classificacoes, nivel, localidades, ttl=None):
        assert (tabela, nivel) == (4099, "n3") and periodos.startswith("last ")
        return ibge.ResultadoSidra(linhas, "Trimestre", "https://apisidra.ibge.gov.br/x", datetime(2026, 11, 10, tzinfo=timezone.utc))

    monkeypatch.setattr(ibge, "consultar", fake)


def _uf(dados, nome):
    return next(l for l in dados["linhas"] if l[0] == nome)


def test_indicadores_por_uf_ranqueia_e_compara(monkeypatch, rodar):
    _pnad_fake(monkeypatch)
    dados = _json(_chamar(rodar, "indicadores_por_uf", {"indicador": "desocupacao"}))
    assert dados["colunas"] == ["uf", "codigo", "valor", "anterior", "ano_anterior", "variacao"]
    assert [l[0] for l in dados["linhas"]] == ["Ceará", "Santa Catarina"]  # maior taxa primeiro
    ce = _uf(dados, "Ceará")
    assert ce[2] == pytest.approx(7.2)
    assert ce[3] == pytest.approx(7.4)  # trimestre anterior
    assert ce[4] == pytest.approx(8.0)  # mesmo trimestre do ano anterior
    assert ce[5] == pytest.approx(-0.8)  # taxa: pontos percentuais
    assert dados["periodos"] == {"atual": "1º trimestre 2026", "anterior": "4º trimestre 2025", "ano_anterior": "1º trimestre 2025"}
    assert dados["variacao_em"] == "pontos percentuais"
    menor = _json(_chamar(rodar, "indicadores_por_uf", {"indicador": "desocupacao", "ordenar": "menor"}))
    assert menor["linhas"][0][0] == "Santa Catarina"
    por_sigla = _json(_chamar(rodar, "indicadores_por_uf", {"indicador": "desocupacao", "uf": "SC"}))
    assert [l[0] for l in por_sigla["linhas"]] == ["Santa Catarina"]
    por_nome = _json(_chamar(rodar, "indicadores_por_uf", {"indicador": "Desocupação", "uf": "santa"}))
    assert [l[0] for l in por_nome["linhas"]] == ["Santa Catarina"]


def test_indicadores_por_uf_passa_classificacao_fixa(monkeypatch, rodar):
    chamadas = []

    async def fake(tabela, variaveis, periodos, classificacoes, nivel, localidades, ttl=None):
        chamadas.append((tabela, variaveis, classificacoes))
        return ibge.ResultadoSidra([], "Trimestre", "https://apisidra.ibge.gov.br/x", datetime(2026, 11, 10, tzinfo=timezone.utc))

    monkeypatch.setattr(ibge, "consultar", fake)
    _chamar(rodar, "indicadores_por_uf", {"indicador": "rendimento"})
    _chamar(rodar, "indicadores_por_uf", {"indicador": "pib"})
    assert chamadas == [(5436, 5932, {2: 6794}), (5938, 37, None)]


def test_indicadores_por_uf_erros(monkeypatch, rodar):
    _pnad_fake(monkeypatch)
    res = _chamar(rodar, "indicadores_por_uf", {"indicador": "inflacao"})
    assert res.is_error and "Opções:" in res.content[0].text
    res = _chamar(rodar, "indicadores_por_uf", {"indicador": "desocupacao", "ordenar": "meio"})
    assert res.is_error and "ordenar deve ser" in res.content[0].text
    res = _chamar(rodar, "indicadores_por_uf", {"indicador": "desocupacao", "periodos": 13})
    assert res.is_error and "periodos deve estar" in res.content[0].text
    res = _chamar(rodar, "indicadores_por_uf", {"indicador": "desocupacao", "uf": "bahia"})
    assert res.is_error and "Nenhuma UF" in res.content[0].text


def test_catalogo_inclui_safra_por_produto_e_populacao():
    from brasil_macro import catalogo

    for id_, produto in [
        ("safra_soja_estimativa", "39443"),
        ("safra_arroz_estimativa", "39432"),
        ("safra_trigo_estimativa", "39445"),
        ("safra_cafe_arabica_estimativa", "39454"), ("safra_cafe_canephora_estimativa", "39455"),
        ("safra_cana_estimativa", "39456"),
        ("safra_algodao_estimativa", "39429"),
    ]:
        ind = catalogo.obter_indicador(id_)
        assert ind.origem == "sidra" and ind.parametros["tabela"] == 6588
        assert ind.parametros["classificacoes"] == {48: int(produto)}
    pop = catalogo.obter_indicador("populacao_residente")
    assert pop.parametros == {"tabela": 6579, "variavel": 9324} and pop.periodicidade == "anual"


# ---------------------------------------------------------------- rede


@pytest.mark.rede
def test_rede_transferegov_e_cvm_e_bndes(rodar):
    async def f():
        planos, _ = await transferegov.planos(2025)
        registros, _, _ = await cvm.cadastro()
        total, conjuntos, _ = await ckan.buscar("bndes", "desembolsos", 3)
        return planos, registros, total

    planos, registros, total = rodar(f())
    assert len(planos) > 5000
    assert any(r["situacao"] == "Em Funcionamento Normal" for r in registros)
    assert any(r["subclasses"] for r in registros)
    assert total > 0


@pytest.mark.rede
def test_rede_cvm_informe_diario_formato_real(rodar):
    # aceita só as primeiras linhas do mês: valida o parser contra o arquivo real sem guardar o mês inteiro
    contagem = [0]

    def primeiras(_cnpj):
        contagem[0] += 1
        return contagem[0] <= 5

    async def f():
        return await cvm.informe_diario([(2025, 1)], primeiras)

    linhas, urls, _ = rodar(f())
    assert len(linhas) == 5
    assert all(l["data"].year == 2025 and l["data"].month == 1 for l in linhas)
    assert all(l["cota"] is not None and l["pl"] is not None for l in linhas)


@pytest.mark.rede
def test_rede_indicadores_por_uf(rodar):
    for indicador in ("desocupacao", "rendimento", "massa_rendimento", "ocupados", "pib"):
        dados = _json(_chamar(rodar, "indicadores_por_uf", {"indicador": indicador}))
        assert len(dados["linhas"]) == 27, indicador
        assert dados["linhas"][0][2] is not None, indicador


def test_rede_pnad_por_uf_e_safra_e_populacao(rodar):
    from brasil_macro import catalogo

    async def f():
        res = await ibge.consultar(4099, 4099, "last 8", None, "n3", "all")
        soja = await catalogo.obter_dados(catalogo.obter_indicador("safra_soja_estimativa"), None, None)
        pop = await catalogo.obter_dados(catalogo.obter_indicador("populacao_residente"), None, None)
        return res, soja, pop

    res, soja, pop = rodar(f())
    assert len({l.territorio_codigo for l in res.linhas}) == 27
    assert soja.observacoes and soja.observacoes[-1][1] > 0
    assert pop.observacoes and pop.observacoes[-1][1] > 200_000_000


@pytest.mark.rede
def test_rede_ibge_localidades(rodar):
    async def f():
        return await localidades.estados(), await localidades.municipios("SC")

    estados, municipios = rodar(f())
    assert len(estados) == 27
    assert len(municipios) == 295
