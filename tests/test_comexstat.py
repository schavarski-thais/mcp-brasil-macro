"""ComexStat (MDIC/Secex): montagem de consultas e parsers (offline) e consultas reais (rede)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from brasil_macro import catalogo
from brasil_macro.fontes import comexstat as cx
from brasil_macro.ferramentas.comercio_exterior import agregar
from brasil_macro.http import ErroFonte

_PAISES = [
    {"year": "2026", "monthNumber": "08", "country": "China", "metricFOB": "10000000000", "metricKG": "40000000000"},
    {"year": "2026", "monthNumber": "09", "country": "China", "metricFOB": "12000000000", "metricKG": "50000000000"},
    {"year": "2026", "monthNumber": "09", "country": "Argentina", "metricFOB": "1500000000", "metricKG": "900000000"},
]
_SECAO = {"coNcmSecrom": "V", "year": "2026", "monthNumber": "09", "section": "Produtos minerais", "metricFOB": "10552519531"}

_PAISES_FILTRO = [("160", "China"), ("063", "Argentina"), ("249", "Estados Unidos"), ("573", "Países Baixos (Holanda)")]
_UFS_FILTRO = [("35", "São Paulo"), ("31", "Minas Gerais"), ("51", "Mato Grosso"), ("50", "Mato Grosso do Sul")]


def test_montar_corpo_formato_da_api():
    corpo = cx.montar_corpo(
        "export", date(2025, 1, 1), date(2025, 12, 1), mensal=True, detalhes=("country",),
        filtros={"state": ["35"], "via": []},
    )
    assert corpo == {
        "flow": "export",
        "monthDetail": True,
        "period": {"from": "2025-01", "to": "2025-12"},
        "metrics": ["metricFOB", "metricKG"],
        "details": ["country"],
        "filters": [{"filter": "state", "values": ["35"]}],  # filtro sem valores é descartado
    }


def test_fluxo_api():
    assert cx.fluxo_api("Exportação") == "export"
    assert cx.fluxo_api("importacoes") == "import"
    with pytest.raises(ValueError):
        cx.fluxo_api("reexportacao")


def test_numero_aceita_texto():
    assert cx.numero("33962602370") == 33962602370.0
    assert cx.numero(None) == 0.0
    assert cx.numero("") == 0.0


def test_interpretar_resposta():
    assert cx.interpretar_resposta({"data": {"list": _PAISES}, "success": True}) == _PAISES
    with pytest.raises(ErroFonte, match="excedeu"):
        cx.interpretar_resposta({"error": {"code": 429, "message": "Você excedeu o limite"}})
    with pytest.raises(ErroFonte):
        cx.interpretar_resposta({"data": {}, "success": True})
    with pytest.raises(ErroFonte):
        cx.interpretar_resposta([])


def test_nome_do_detalhe_ignora_codigos():
    assert cx.nome_do_detalhe(_SECAO, "section") == "Produtos minerais"
    # sem a chave com o nome do detalhe, pula ano, mês, métricas e códigos "coXxx"
    assert cx.nome_do_detalhe({**_SECAO, "section": None, "noSecpt": "Produtos minerais"}, "section") == "Produtos minerais"
    assert cx.nome_do_detalhe({"year": "2026", "metricFOB": "1"}, "country") == ""


def test_periodo_da_linha():
    assert cx.periodo_da_linha(_PAISES[0]) == (2026, 8)
    assert cx.periodo_da_linha({"year": "2025", "metricFOB": "1"}) == (2025, None)


@pytest.mark.parametrize(
    ("de", "ate", "esperado"),
    [
        (date(2026, 3, 1), date(2026, 9, 1), [(date(2026, 3, 1), date(2026, 9, 1))]),
        (date(1997, 1, 1), date(2026, 10, 1), [(date(1997, 1, 1), date(2025, 12, 1)), (date(2026, 1, 1), date(2026, 10, 1))]),
        (date(2025, 7, 1), date(2026, 6, 1), [(date(2025, 7, 1), date(2025, 12, 1)), (date(2026, 1, 1), date(2026, 6, 1))]),
        (
            date(2023, 5, 1),
            date(2026, 2, 1),
            [(date(2023, 5, 1), date(2023, 12, 1)), (date(2024, 1, 1), date(2025, 12, 1)), (date(2026, 1, 1), date(2026, 2, 1))],
        ),
        (date(2024, 1, 1), date(2025, 12, 1), [(date(2024, 1, 1), date(2025, 12, 1))]),
        (date(2026, 5, 1), date(2026, 4, 1), []),
    ],
)
def test_retangulos_cobrem_o_intervalo_sem_sobrepor(de, ate, esperado):
    blocos = cx.retangulos(de, ate)
    assert blocos == esperado
    # a API entende cada bloco como [ano inicial, ano final] x [mês inicial, mês final]
    meses_da_api = {
        date(ano, mes, 1)
        for ini, fim in blocos
        for ano in range(ini.year, fim.year + 1)
        for mes in range(ini.month, fim.month + 1)
    }
    esperados = set()
    atual = de
    while atual <= ate:
        esperados.add(atual)
        atual = date(atual.year + (atual.month == 12), atual.month % 12 + 1, 1)
    assert meses_da_api == esperados


def test_escolher_valores_uf_pais_e_ambiguidade():
    assert cx.escolher_valores("state", "SP", _UFS_FILTRO, "UF") == [("35", "São Paulo")]
    assert cx.escolher_valores("state", "mato grosso", _UFS_FILTRO, "UF") == [("51", "Mato Grosso")]  # exato vence o parcial
    assert cx.escolher_valores("country", "china, argentina", _PAISES_FILTRO, "país") == [("160", "China"), ("063", "Argentina")]
    assert cx.escolher_valores("country", "EUA", _PAISES_FILTRO, "país") == [("249", "Estados Unidos")]
    assert cx.escolher_valores("country", "063", _PAISES_FILTRO, "país") == [("063", "Argentina")]
    assert cx.escolher_valores("country", "holanda", _PAISES_FILTRO, "país") == [("573", "Países Baixos (Holanda)")]
    with pytest.raises(ValueError, match="ambíguo"):
        cx.escolher_valores("state", "grosso", _UFS_FILTRO, "UF")
    with pytest.raises(ValueError, match="Não encontrei"):
        cx.escolher_valores("country", "Atlântida", _PAISES_FILTRO, "país")


def test_agregar_por_item_e_por_mes():
    por_item, por_mes = agregar(_PAISES, "country")
    assert por_item["China"] == [22_000_000_000.0, 90_000_000_000.0]
    assert por_item["Argentina"][0] == 1_500_000_000.0
    assert por_mes[date(2026, 9, 1)][0] == 13_500_000_000.0
    assert por_mes[date(2026, 8, 1)][0] == 10_000_000_000.0


def test_combinar_totais():
    x = {date(2026, 1, 1): 100.0, date(2026, 2, 1): 120.0}
    m = {date(2026, 1, 1): 80.0, date(2026, 3, 1): 90.0}
    assert cx.combinar("exportacoes", x, m) == x
    assert cx.combinar("saldo", x, m) == {date(2026, 1, 1): 20.0}  # só meses presentes nos dois fluxos
    assert cx.combinar("corrente", x, m) == {date(2026, 1, 1): 180.0}
    with pytest.raises(ValueError):
        cx.combinar("outro", x, m)


def _linhas_mensais(ano_ini: int, valores: list[float]) -> list[dict]:
    return [
        {"year": str(ano_ini + i // 12), "monthNumber": f"{i % 12 + 1:02d}", "metricFOB": str(int(v * 1e6))}
        for i, v in enumerate(valores)
    ]


def test_serie_totais_saldo_pela_api(rodar, monkeypatch):
    chamadas = []

    async def consulta(fluxo, de, ate, **kw):
        chamadas.append(fluxo)
        base = {"export": [30_000.0, 31_000.0, 32_000.0], "import": [25_000.0, 26_000.0, 27_500.0]}[fluxo]
        return cx.Consulta(_linhas_mensais(2026, base), "https://api/x", datetime(2026, 11, 1, tzinfo=timezone.utc))

    async def ultimo():
        return date(2026, 3, 1)

    monkeypatch.setattr(cx, "consultar_periodo", consulta)
    monkeypatch.setattr(cx, "ultimo_mes_disponivel", ultimo)
    serie = rodar(cx.serie_totais("saldo", date(2026, 2, 1)))
    assert chamadas == ["export", "import"]
    assert serie.observacoes == [(date(2026, 2, 1), 5000.0), (date(2026, 3, 1), 4500.0)]
    assert serie.unidade == "US$ milhões"
    assert serie.fonte == cx.ROTULO


def test_serie_totais_recorre_ao_ipeadata_quando_a_api_recusa(rodar, monkeypatch):
    async def recusa(*a, **kw):
        raise ErroFonte("HTTP 429")

    async def ultimo():
        return date(2026, 3, 1)

    async def ipea(fluxo):
        base = {"export": 100.0, "import": 60.0}[fluxo]
        return {date(2026, 1, 1): base, date(2026, 2, 1): base + 1}, "https://ipea/x", datetime(2026, 11, 1, tzinfo=timezone.utc), False

    monkeypatch.setattr(cx, "consultar_periodo", recusa)
    monkeypatch.setattr(cx, "ultimo_mes_disponivel", ultimo)
    monkeypatch.setattr(cx, "_totais_ipea", ipea)
    serie = rodar(cx.serie_totais("saldo"))
    assert serie.observacoes == [(date(2026, 1, 1), 40.0), (date(2026, 2, 1), 40.0)]
    assert "Ipeadata" in serie.fonte
    assert any("SECEX12_XVTOT12" in n for n in serie.notas)


def test_serie_totais_tipo_invalido(rodar):
    with pytest.raises(ValueError):
        rodar(cx.serie_totais("reservas"))


def test_catalogo_comercio_exterior_bem_formado():
    catalogo.recarregar()
    inds = {i.id: i for i in catalogo.todos(com_privados=True) if i.origem == "comex"}
    assert set(inds) == {"exportacoes", "importacoes", "saldo_comercial", "corrente_comercio"}
    for ind in inds.values():
        assert ind.tema == "comercio_exterior"
        assert ind.parametros in ("exportacoes", "importacoes", "saldo", "corrente")
        assert ind.unidade == "US$ milhões" and ind.produtor == "MDIC/Secex" and ind.oficial


# ---------------------------------------------------------------------------- rede
# A API do ComexStat recusa pedidos de forma irregular (HTTP 429); estes testes só
# falham por isso se o 429 persistir por todas as tentativas.


@pytest.mark.rede
def test_rede_exportacoes_mensais(rodar):
    serie = rodar(catalogo.obter_dados(catalogo.obter_indicador("exportacoes"), date(2024, 10, 1), date(2025, 3, 31)))
    valores = dict(serie.observacoes)
    assert [d.month for d in valores] == [10, 11, 12, 1, 2, 3]  # novembro e dezembro não podem faltar (ver retangulos)
    assert all(15_000 < v < 45_000 for v in valores.values())  # US$ milhões por mês
    assert serie.unidade == "US$ milhões" and serie.url.startswith("http")


@pytest.mark.rede
def test_rede_saldo_e_a_diferenca(rodar):
    inicio, fim = date(2025, 11, 1), date(2026, 2, 28)

    async def tres():
        return [await catalogo.obter_dados(catalogo.obter_indicador(i), inicio, fim) for i in ("exportacoes", "importacoes", "saldo_comercial")]

    x, m, s = (dict(serie.observacoes) for serie in rodar(tres()))
    assert x.keys() == m.keys() == s.keys() and len(s) == 4
    for d in s:
        assert s[d] == pytest.approx(x[d] - m[d], abs=0.01)


@pytest.mark.rede
def test_rede_filtros_do_pais(rodar):
    opcoes = rodar(cx.valores_do_filtro("country"))
    assert ("160", "China") in opcoes
    assert cx.escolher_valores("country", "China", opcoes, "país") == [("160", "China")]
    assert any(id_ == "RJ" or texto == "Rio de Janeiro" for id_, texto in rodar(cx.valores_do_filtro("state")))
