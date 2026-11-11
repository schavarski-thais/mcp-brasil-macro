"""Ipeadata: parsers e classificação de fontes (offline) e consultas reais (rede)."""

from __future__ import annotations

from datetime import date

import pytest

from brasil_macro import catalogo
from brasil_macro.fontes import ipea

_VALORES = [
    {"SERCODIGO": "X", "VALDATA": "2026-02-01T00:00:00-03:00", "VALVALOR": 20.5, "NIVNOME": "", "TERCODIGO": ""},
    {"SERCODIGO": "X", "VALDATA": "2026-01-01T00:00:00-03:00", "VALVALOR": 10.0, "NIVNOME": "", "TERCODIGO": ""},
    {"SERCODIGO": "X", "VALDATA": "2026-03-01T00:00:00-03:00", "VALVALOR": None, "NIVNOME": "", "TERCODIGO": ""},
]

_REGIONAIS = [
    {"SERCODIGO": "P", "VALDATA": "2023-01-01T00:00:00-03:00", "VALVALOR": 1.0, "NIVNOME": "Brasil", "TERCODIGO": "0"},
    {"SERCODIGO": "P", "VALDATA": "2023-01-01T00:00:00-03:00", "VALVALOR": 2.0, "NIVNOME": "Estados", "TERCODIGO": "35"},
    {"SERCODIGO": "P", "VALDATA": "2023-01-01T00:00:00-03:00", "VALVALOR": 3.0, "NIVNOME": "Regiões", "TERCODIGO": "3"},
]

_METADADOS = [
    {"SERCODIGO": "CAGED12_SALDON12", "SERNOME": "Empregados - saldo - sem ajuste - novo Caged", "SERSTATUS": "A",
     "FNTSIGLA": "Min. Economia/SEPRT/Novo Caged", "FNTNOME": "Ministério da Economia", "TEMCODIGO": 12,
     "UNINOME": "Pessoa", "MULNOME": None, "PERNOME": "Mensal", "SERATUALIZACAO": "2026-09-30T12:08:06-03:00"},
    {"SERCODIGO": "CAGED12_SALDO12", "SERNOME": "Empregados - saldo - INATIVA", "SERSTATUS": "I",
     "FNTSIGLA": "MTE/Caged", "FNTNOME": "Ministério do Trabalho e Emprego", "TEMCODIGO": 12,
     "UNINOME": "Pessoa", "MULNOME": None, "PERNOME": "Mensal", "SERATUALIZACAO": "2020-05-27T00:00:00-03:00"},
    {"SERCODIGO": "IGP12_IGPM12", "SERNOME": "IGP-M - geral - índice", "SERSTATUS": "A",
     "FNTSIGLA": "FGV/Conj. Econ. - IGP", "FNTNOME": "Fundação Getulio Vargas", "TEMCODIGO": 9,
     "UNINOME": "-", "MULNOME": None, "PERNOME": "Mensal", "SERATUALIZACAO": "2026-10-29T00:00:00-03:00"},
    {"SERCODIGO": "SRF12_TOTGER12", "SERNOME": "Arrecadação das receitas federais - receita bruta", "SERSTATUS": "A",
     "FNTSIGLA": "Min. Economia/SRF", "FNTNOME": "Secretaria da Receita Federal", "TEMCODIGO": 6,
     "UNINOME": "R$", "MULNOME": "milhões", "PERNOME": "Mensal", "SERATUALIZACAO": "2026-10-23T00:00:00-03:00"},
]  # fmt: skip
_TEMAS = {12: "Emprego", 9: "Preços", 6: "Finanças públicas"}


def test_interpretar_valores_ordena_e_preserva_nulos():
    grupos = ipea.interpretar_valores(_VALORES)
    assert list(grupos) == [("", "")]
    assert grupos[("", "")] == [(date(2026, 1, 1), 10.0), (date(2026, 2, 1), 20.5), (date(2026, 3, 1), None)]


def test_escolher_territorio():
    grupos = ipea.interpretar_valores(_REGIONAIS)
    assert ipea.escolher_territorio(grupos, None)[0] == ("Brasil", "0")
    assert ipea.escolher_territorio(grupos, "SP") == (("Estados", "35"), [(date(2023, 1, 1), 2.0)])
    assert ipea.escolher_territorio(grupos, "3")[0] == ("Regiões", "3")
    assert ipea.escolher_territorio(grupos, "Brasil")[0] == ("Brasil", "0")
    with pytest.raises(ValueError, match="Estados"):
        ipea.escolher_territorio(grupos, "99")


def test_escolher_territorio_exige_escolha_sem_brasil():
    grupos = ipea.interpretar_valores(_REGIONAIS[1:])
    with pytest.raises(ValueError, match="vários territórios"):
        ipea.escolher_territorio(grupos, None)


@pytest.mark.parametrize(
    ("sigla", "esperado"),
    [
        ("Min. Economia/SEPRT/Novo Caged", "oficial"),
        ("IBGE/PNAD Contínua", "oficial"),
        ("Bacen/Notas Imprensa/F. Púb.", "oficial"),
        ("IPEA / Atlas da Vulnerabilidade Social", "oficial"),
        ("ANP", "oficial"),
        ("FMI/WEO", "oficial"),
        ("FGV/Conj. Econ. - IGP", "privada"),
        ("Funcex", "privada"),
        ("Anfavea", "privada"),
        ("CNI", "privada"),
        ("BM&FBovespa", "privada"),
        ("Fecomercio SP", "privada"),
        ("Eletrobras", "privada"),
        ("Sigla Nova Qualquer", "privada"),
        ("Outras fontes", "indefinida"),
        ("OUTFONTE", "indefinida"),
        (None, "indefinida"),
    ],
)
def test_classificar_fonte(sigla, esperado):
    assert ipea.classificar_fonte(sigla) == esperado
    assert ipea.fonte_oficial(sigla) is (esperado == "oficial")


def test_unidade_e_periodicidade():
    assert ipea.unidade_de({"UNINOME": "R$", "MULNOME": "milhões"}) == "R$ (milhões)"
    assert ipea.unidade_de({"UNINOME": "Pessoa", "MULNOME": None}) == "Pessoa"
    assert ipea.unidade_de({"UNINOME": "-", "MULNOME": " "}) == ""
    assert ipea.periodicidade_de({"PERNOME": "Trimestral"}) == "trimestral"
    assert ipea.periodicidade_de({"PERNOME": "Decenal"}) == "anual"


def test_limpar_texto():
    assert ipea.limpar_texto('Saldo.\r\nMais: <a href="../doc/x.pdf">Nota t&eacute;cnica</a>') == "Saldo. Mais: Nota técnica"


def test_filtrar_series():
    def codigos(**kw):
        return [m["SERCODIGO"] for m in ipea.filtrar_series(_METADADOS, temas=_TEMAS, **kw)]

    assert codigos(consulta="CAGED saldo") == ["CAGED12_SALDON12"]
    assert codigos(consulta="caged saldo", apenas_ativas=False) == ["CAGED12_SALDON12", "CAGED12_SALDO12"]
    assert codigos(consulta="arrecadacao") == ["SRF12_TOTGER12"]  # sem acento casa com "Arrecadação"
    assert codigos(fonte="receita") == ["SRF12_TOTGER12"]
    assert codigos(tema="emprego") == ["CAGED12_SALDON12"]
    assert codigos(tema="6") == ["SRF12_TOTGER12"]
    assert codigos(consulta="igpm12") == ["IGP12_IGPM12"]  # casa pelo código
    assert codigos(consulta="saldo inexistente") == []


def test_codigo_invalido_nao_vai_para_a_rede(rodar):
    with pytest.raises(ValueError, match="inválido"):
        rodar(ipea.metadados_ipea("X' or 1 eq 1"))


def test_catalogo_ipea_bem_formado():
    catalogo.recarregar()
    inds = [i for i in catalogo.todos(com_privados=True) if i.origem == "ipea"]
    ids = {i.id for i in inds}
    assert {"caged_saldo", "caged_admissoes", "caged_desligamentos", "salario_minimo", "arrecadacao_federal"} <= ids
    for ind in inds:
        assert isinstance(ind.parametros, str) and ind.parametros == ind.parametros.strip()
        assert ind.unidade and ind.produtor
    assert not next(i for i in inds if i.id == "consumo_energia_eletrica").oficial


@pytest.mark.rede
def test_rede_caged_saldo(rodar):
    serie = rodar(catalogo.obter_dados(catalogo.obter_indicador("caged_saldo_sem_ajuste"), date(2020, 1, 1), date(2020, 12, 31)))
    valores = dict(serie.observacoes)
    assert len(valores) == 12
    assert valores[date(2020, 4, 1)] < -500_000  # pior mês da pandemia
    assert serie.periodicidade == "mensal" and "CAGED12_SALDON12" in serie.codigo


@pytest.mark.rede
def test_rede_caged_identidade(rodar):
    async def tres():
        return [
            await ipea.serie_ipea(c, date(2025, 1, 1), date(2025, 6, 30))
            for c in ("CAGED12_ADMISN12", "CAGED12_DESLIGN12", "CAGED12_SALDON12")
        ]

    adm, des, saldo = (dict(s.observacoes) for s in rodar(tres()))
    assert len(saldo) == 6
    for mes, valor in saldo.items():
        assert adm[mes] - des[mes] == pytest.approx(valor, abs=1)


@pytest.mark.rede
def test_rede_salario_minimo(rodar):
    serie = rodar(ipea.serie_ipea("MTE12_SALMIN12", date(2024, 1, 1), date(2025, 12, 31)))
    valores = dict(serie.observacoes)
    assert valores[date(2024, 1, 1)] == 1412.0
    assert valores[date(2025, 1, 1)] == 1518.0
    futuras = rodar(ipea.serie_ipea("MTE12_SALMIN12", date.today(), None))
    assert all(d <= date.today() for d, _ in futuras.observacoes)


@pytest.mark.rede
def test_rede_metadados_e_inexistente(rodar):
    meta = rodar(ipea.metadados_ipea("srf12_totger12"))
    assert meta["SERCODIGO"] == "SRF12_TOTGER12"
    assert ipea.fonte_oficial(meta["FNTSIGLA"]) and ipea.unidade_de(meta) == "R$ (milhões)"
    with pytest.raises(LookupError):
        rodar(ipea.metadados_ipea("NAOEXISTE_123"))


@pytest.mark.rede
def test_rede_busca(rodar):
    itens, _ = rodar(ipea.listar_series("macro"))
    assert len(itens) > 2000
    achadas = ipea.filtrar_series(itens, "caged saldo")
    assert {"CAGED12_SALDON12", "CAGED12_SALDONAJU12"} <= {m["SERCODIGO"] for m in achadas}
    igp = ipea.filtrar_series(itens, "igp-m")
    assert igp and not any(ipea.fonte_oficial(m["FNTSIGLA"]) for m in igp)
