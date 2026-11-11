"""Testes offline do núcleo: datas, transformações, séries e cache HTTP."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from brasil_macro import http, transformacoes
from brasil_macro.modelos import (
    Serie,
    data_opcional,
    deslocar_meses,
    interpretar_data,
    rotulo_periodo,
)


# ------------------------------------------------------------------ datas


@pytest.mark.parametrize(
    "texto, fim, esperado",
    [
        ("2026", False, date(2026, 1, 1)),
        ("2026", True, date(2026, 12, 31)),
        ("2026-09", False, date(2026, 9, 1)),
        ("2026-02", True, date(2026, 2, 28)),
        ("2024-02", True, date(2024, 2, 29)),
        ("2026-T2", False, date(2026, 4, 1)),
        ("2026-T2", True, date(2026, 6, 30)),
        ("2026-09-15", False, date(2026, 9, 15)),
        ("09/2026", True, date(2026, 9, 30)),
        ("15/09/2026", False, date(2026, 9, 15)),
    ],
)
def test_interpretar_data(texto, fim, esperado):
    assert interpretar_data(texto, fim=fim) == esperado


@pytest.mark.parametrize("texto", ["ontem", "2026-13", "2026-02-30", ""])
def test_interpretar_data_invalida(texto):
    with pytest.raises(ValueError):
        interpretar_data(texto)


def test_data_opcional():
    assert data_opcional(None) is None
    assert data_opcional("  ") is None
    assert data_opcional("2026-01") == date(2026, 1, 1)


def test_deslocar_meses_ajusta_fim_de_mes():
    assert deslocar_meses(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert deslocar_meses(date(2026, 1, 15), -13) == date(2024, 12, 15)
    assert deslocar_meses(date(2026, 11, 1), 3) == date(2027, 2, 1)


def test_rotulo_periodo():
    d = date(2026, 5, 6)
    assert rotulo_periodo(d, "diaria") == "2026-05-06"
    assert rotulo_periodo(d, "mensal") == "2026-05"
    assert rotulo_periodo(d, "trimestral") == "2026-T2"
    assert rotulo_periodo(d, "anual") == "2026"


# ---------------------------------------------------------- transformações


def _mensal(valores, inicio=(2025, 1)):
    ano, mes = inicio
    return [(deslocar_meses(date(ano, mes, 1), i), v) for i, v in enumerate(valores)]


def test_variacao_periodo_e_anual():
    obs = _mensal([100.0, 110.0, 121.0] + [0.0] * 9 + [132.0])
    saida, _ = transformacoes.aplicar("variacao_periodo", obs, "mensal", "índice")
    assert saida[0] == (date(2025, 2, 1), pytest.approx(10.0))
    assert saida[1] == (date(2025, 3, 1), pytest.approx(10.0))
    anual, _ = transformacoes.aplicar("variacao_anual", obs, "mensal", "índice")
    assert anual == [(date(2026, 1, 1), pytest.approx(32.0))]


def test_acumulado_12m_composto():
    obs = _mensal([1.0] * 12)
    saida, unidade = transformacoes.aplicar("acumulado_12m", obs, "mensal", "% a.m.")
    assert len(saida) == 1
    assert saida[0][1] == pytest.approx((1.01**12 - 1) * 100)
    assert "12" in unidade or "%" in unidade


def test_acumulado_ano_reinicia_em_janeiro():
    obs = _mensal([1.0, 1.0, 1.0, 2.0, 2.0], inicio=(2025, 11))  # nov, dez, jan, fev, mar
    saida, _ = transformacoes.aplicar("acumulado_ano", obs, "mensal", "% a.m.")
    mapa = dict(saida)
    assert date(2025, 12, 1) not in mapa  # ano de 2025 incompleto na amostra
    assert mapa[date(2026, 1, 1)] == pytest.approx(1.0)
    assert mapa[date(2026, 3, 1)] == pytest.approx((1.01 * 1.02 * 1.02 - 1) * 100)


def test_soma_12m_e_media_movel():
    obs = _mensal([1.0] * 11 + [13.0])
    soma, _ = transformacoes.aplicar("soma_12m", obs, "mensal", "R$")
    assert soma == [(date(2025, 12, 1), pytest.approx(24.0))]
    mm, _ = transformacoes.aplicar("media_movel_3m", _mensal([3.0, 6.0, 9.0]), "mensal", "R$")
    assert mm == [(date(2025, 3, 1), pytest.approx(6.0))]


def test_diferenca_e_anualizado():
    saida, _ = transformacoes.aplicar("diferenca", _mensal([10.0, 12.5]), "mensal", "p.p.")
    assert saida == [(date(2025, 2, 1), pytest.approx(2.5))]
    ano, _ = transformacoes.aplicar("anualizado", _mensal([1.0]), "mensal", "% a.m.")
    assert ano[0][1] == pytest.approx((1.01**12 - 1) * 100)


def test_base_100():
    saida, unidade = transformacoes.aplicar("base_100", _mensal([50.0, 75.0]), "mensal", "índice")
    assert [v for _, v in saida] == [pytest.approx(100.0), pytest.approx(150.0)]
    assert "100" in unidade


def test_base_100_exige_primeira_observacao_nao_nula():
    with pytest.raises(ValueError):
        transformacoes.aplicar("base_100", _mensal([0.0, 1.0]), "mensal", "índice")


def test_transformacao_desconhecida():
    with pytest.raises(ValueError, match="desconhecida"):
        transformacoes.aplicar("magica", _mensal([1.0]), "mensal", "")


def test_media_movel_3m_so_para_mensal():
    obs = [(date(2025, 1, 1), 1.0), (date(2025, 4, 1), 2.0)]
    with pytest.raises(ValueError):
        transformacoes.aplicar("media_movel_3m", obs, "trimestral", "")


def test_valores_nulos_sao_ignorados():
    obs = [(date(2025, 1, 1), 100.0), (date(2025, 2, 1), None), (date(2025, 3, 1), 110.0)]
    saida, _ = transformacoes.aplicar("variacao_periodo", obs, "mensal", "índice")
    assert saida == []  # fevereiro ausente: março não tem mês anterior


def test_agregar_diaria_para_mensal():
    obs = [(date(2026, 1, 2), 1.0), (date(2026, 1, 30), 3.0), (date(2026, 2, 3), 5.0)]
    assert transformacoes.agregar(obs, "diaria", "mensal", "ultimo") == [
        (date(2026, 1, 1), 3.0),
        (date(2026, 2, 1), 5.0),
    ]
    assert transformacoes.agregar(obs, "diaria", "mensal", "media")[0] == (date(2026, 1, 1), 2.0)
    assert transformacoes.agregar(obs, "diaria", "mensal", "soma")[0] == (date(2026, 1, 1), 4.0)


def test_agregar_rejeita_frequencia_mais_alta():
    with pytest.raises(ValueError):
        transformacoes.agregar([], "mensal", "diaria", "ultimo")
    with pytest.raises(ValueError):
        transformacoes.agregar([], "diaria", "mensal", "mediana")


def test_mais_baixa():
    assert transformacoes.mais_baixa(["diaria", "trimestral", "mensal"]) == "trimestral"


# ------------------------------------------------------------------ série


def test_serie_recortar_e_truncar():
    obs = [(date(2026, m, 1), float(m)) for m in range(1, 13)]
    s = Serie("x", "fonte", "SGS 1", "%", "mensal", obs)
    d = s.para_dict(limite=5)
    assert len(d["observacoes"]) == 5 if "observacoes" in d else True
    assert any("truncada" in n for n in d["notas"])
    s.recortar(date(2026, 3, 1), date(2026, 5, 31))
    assert [v for _, v in s.observacoes] == [3.0, 4.0, 5.0]


# ---------------------------------------------------------------- HTTP/cache


_CLIENTE_REAL = httpx.AsyncClient


def _servidor_falso(monkeypatch, respostas):
    """Troca o transporte do httpx por um que devolve `respostas` em sequência."""
    chamadas: list[str] = []

    def tratador(request: httpx.Request) -> httpx.Response:
        chamadas.append(str(request.url))
        item = respostas[min(len(chamadas) - 1, len(respostas) - 1)]
        return item if isinstance(item, httpx.Response) else httpx.Response(**item)

    original = _CLIENTE_REAL

    def cliente(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(tratador)
        return original(*args, **kwargs)

    monkeypatch.setattr(http.httpx, "AsyncClient", cliente)
    return chamadas


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    async def nada(_):
        return None

    monkeypatch.setattr(http.asyncio, "sleep", nada)


def test_cache_evita_segunda_chamada(monkeypatch, rodar):
    chamadas = _servidor_falso(monkeypatch, [{"status_code": 200, "json": [{"a": 1}]}])

    async def fluxo():
        a = await http.requisitar("https://exemplo.gov.br/x", ttl=60)
        b = await http.requisitar("https://exemplo.gov.br/x", ttl=60)
        await http.fechar()
        return a, b

    a, b = rodar(fluxo())
    assert len(chamadas) == 1
    assert not a.do_cache and b.do_cache
    assert b.json() == [{"a": 1}]


def test_repete_em_erro_passageiro(monkeypatch, rodar):
    chamadas = _servidor_falso(
        monkeypatch, [{"status_code": 503}, {"status_code": 503}, {"status_code": 200, "json": {"ok": True}}]
    )

    async def fluxo():
        r = await http.requisitar("https://exemplo.gov.br/y", ttl=0)
        await http.fechar()
        return r

    assert rodar(fluxo()).json() == {"ok": True}
    assert len(chamadas) == 3


def test_erro_definitivo_vira_erro_fonte(monkeypatch, rodar):
    _servidor_falso(monkeypatch, [{"status_code": 400, "text": "parâmetro inválido"}])

    async def fluxo():
        try:
            await http.requisitar("https://exemplo.gov.br/z", ttl=0)
        finally:
            await http.fechar()

    with pytest.raises(http.ErroFonte, match="400"):
        rodar(fluxo())


def test_status_aceito_nao_e_erro_nem_entra_no_cache(monkeypatch, rodar):
    chamadas = _servidor_falso(monkeypatch, [{"status_code": 404, "text": "Value(s) not found"}])

    async def fluxo():
        a = await http.requisitar("https://exemplo.gov.br/w", ttl=60, aceitar=(200, 404))
        b = await http.requisitar("https://exemplo.gov.br/w", ttl=60, aceitar=(200, 404))
        await http.fechar()
        return a, b

    a, b = rodar(fluxo())
    assert a.status == 404 and b.status == 404
    assert len(chamadas) == 2


def test_cache_velho_serve_quando_a_fonte_cai(monkeypatch, rodar):
    _servidor_falso(monkeypatch, [{"status_code": 200, "json": [1]}])

    async def preparar():
        await http.requisitar("https://exemplo.gov.br/v", ttl=60)
        await http.fechar()

    rodar(preparar())

    # expira o cache e derruba a fonte
    con = http._cache._conexao()
    con.execute("UPDATE respostas SET expira_em = 0")
    con.commit()
    _servidor_falso(monkeypatch, [{"status_code": 503}])

    async def depois():
        r = await http.requisitar("https://exemplo.gov.br/v", ttl=60, tentativas=2)
        await http.fechar()
        return r

    r = rodar(depois())
    assert r.desatualizado and r.json() == [1]
