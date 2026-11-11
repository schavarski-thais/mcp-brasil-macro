"""Testes da estrutura do catálogo (offline) e da validação ao vivo das séries do SGS.

Os testes sem o marcador `rede` só leem os YAML. O teste marcado com `rede` roda o
validador de `scripts/validar_catalogo.py` contra o SGS e só executa com `pytest -m rede`.

Os testes não supõem que todo indicador seja do SGS: outras origens (`sidra`, `rtn`,
`ipea`, ...) convivem no catálogo, e as regras específicas do SGS olham só as entradas `sgs`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from brasil_macro import catalogo
from brasil_macro.modelos import PERIODICIDADES

SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
NFSP = re.compile(r"^(resultado_primario|resultado_nominal|juros_nominais)(_|$)")
RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def indicadores() -> list[catalogo.Indicador]:
    catalogo.recarregar()
    return catalogo.todos(com_privados=True)


@pytest.fixture(scope="module")
def sgs(indicadores) -> list[catalogo.Indicador]:
    return [i for i in indicadores if i.origem == "sgs"]


# ------------------------------------------------------------------ estrutura


def test_catalogo_carrega_e_tem_os_temas_principais(indicadores):
    assert len(indicadores) > 100
    temas = catalogo.temas()
    for esperado in ("fiscal", "setor_externo", "credito", "politica_monetaria"):
        assert esperado in temas
        assert any(i.tema == esperado for i in indicadores)


def test_ids_unicos(indicadores):
    repetidos = [id_ for id_, n in Counter(i.id for i in indicadores).items() if n > 1]
    assert not repetidos, f"ids repetidos: {repetidos}"


def test_ids_em_snake_case(indicadores):
    fora_do_padrao = [i.id for i in indicadores if not SNAKE_CASE.match(i.id)]
    assert not fora_do_padrao, f"ids fora de snake_case: {fora_do_padrao}"


def test_periodicidades_validas(indicadores):
    invalidos = [(i.id, i.periodicidade) for i in indicadores if i.periodicidade not in PERIODICIDADES]
    assert not invalidos


def test_todo_indicador_tem_nome(indicadores):
    sem_nome = [i.id for i in indicadores if not i.nome.strip()]
    assert not sem_nome


def test_unidade_vazia_so_com_nota_que_justifique(indicadores):
    sem_unidade = [i.id for i in indicadores if not i.unidade.strip() and not i.notas.strip()]
    assert not sem_unidade, f"indicadores sem unidade e sem nota explicando: {sem_unidade}"


def test_cada_indicador_tem_uma_origem_com_parametro(indicadores):
    for i in indicadores:
        assert i.origem and i.parametros not in (None, "", [], {}), i.id


def test_codigos_sgs_inteiros_e_nao_repetidos(sgs):
    nao_inteiros = [i.id for i in sgs if not isinstance(i.parametros, int) or isinstance(i.parametros, bool)]
    assert not nao_inteiros, f"código SGS que não é inteiro: {nao_inteiros}"
    por_codigo: dict[int, list[str]] = {}
    for i in sgs:
        por_codigo.setdefault(i.parametros, []).append(i.id)
    repetidos = {c: ids for c, ids in por_codigo.items() if len(ids) > 1}
    assert not repetidos, f"mesmo código SGS em mais de um indicador: {repetidos}"


def test_painel_geral_so_em_indicadores_oficiais(indicadores):
    assert all(i.oficial for i in indicadores if i.painel_geral)


def test_indicador_privado_declara_o_produtor(indicadores):
    sem_produtor = [i.id for i in indicadores if not i.oficial and not i.produtor]
    assert not sem_produtor, f"privados sem 'produtor': {sem_produtor}"


def test_sinal_das_series_nfsp_esta_documentado(sgs):
    """Toda série de NFSP (primário, juros, nominal) avisa que valor positivo é déficit."""
    nfsp = [i for i in sgs if NFSP.match(i.id) and i.tema == "fiscal"]
    assert len(nfsp) >= 30
    sem_aviso = [i.id for i in nfsp if "déficit" not in i.notas.lower() or "superávit" not in i.notas.lower()]
    assert not sem_aviso, f"NFSP sem a convenção de sinal nas notas: {sem_aviso}"


def test_series_nfsp_dizem_se_sao_fluxo_12_meses_ou_pib(sgs):
    nfsp = [i for i in sgs if NFSP.match(i.id) and i.tema == "fiscal"]
    janela = re.compile(r"fluxo|mensal|12 meses|% do PIB|no ano", re.IGNORECASE)
    sem_janela = [i.id for i in nfsp if not janela.search(i.nome)]
    assert not sem_janela, f"nome sem a janela de acumulação: {sem_janela}"


def test_novos_blocos_do_sgs_nao_estao_vazios(sgs):
    por_tema = Counter(i.tema for i in sgs)
    assert por_tema["fiscal"] >= 50
    assert por_tema["setor_externo"] >= 30
    assert por_tema["credito"] >= 50
    assert por_tema["politica_monetaria"] >= 40


# ---------------------------------------------------------------------- busca


@pytest.mark.parametrize(
    "consulta, esperado",
    [
        ("selic", "selic_meta"),
        ("ipca", "ipca"),
        ("primario", "resultado_primario_12m_pib"),
        ("divida bruta", "dbgg_pib"),
        ("dolar", "cambio_dolar"),
    ],
)
def test_busca_encontra_o_indicador_esperado(consulta, esperado):
    achados = [i.id for i in catalogo.buscar(consulta)]
    assert esperado in achados[:10], f"{consulta!r} -> {achados[:10]}"


def test_busca_sem_acento_e_com_acento_igual():
    assert [i.id for i in catalogo.buscar("dívida bruta")] == [i.id for i in catalogo.buscar("divida bruta")]


@pytest.mark.parametrize("consulta", ["poupanca", "compulsorio", "consignado", "icc", "reservas internacionais", "idp"])
def test_busca_acha_os_temas_novos(consulta):
    assert catalogo.buscar(consulta), consulta


def test_busca_por_tema_filtra():
    achados = catalogo.buscar("", tema="credito")
    assert achados and all(i.tema == "credito" for i in achados)


# ------------------------------------------------------------------- privados


def test_privados_nao_aparecem_por_padrao(monkeypatch, indicadores):
    monkeypatch.delenv("BRASIL_MACRO_INCLUIR_PRIVADOS", raising=False)
    visiveis = catalogo.todos()
    assert all(i.oficial for i in visiveis)
    privados = {i.id for i in indicadores if not i.oficial}
    assert privados, "o catálogo deveria ter ao menos um indicador privado marcado"
    assert not privados & {i.id for i in visiveis}
    assert not privados & {i.id for i in catalogo.buscar("")}


def test_privados_aparecem_quando_habilitados(monkeypatch, indicadores):
    monkeypatch.setenv("BRASIL_MACRO_INCLUIR_PRIVADOS", "1")
    assert {i.id for i in catalogo.todos()} == {i.id for i in indicadores}


def test_pedir_indicador_privado_explica_como_habilitar(monkeypatch, indicadores):
    monkeypatch.delenv("BRASIL_MACRO_INCLUIR_PRIVADOS", raising=False)
    privado = next(i for i in indicadores if not i.oficial)
    with pytest.raises(LookupError, match="BRASIL_MACRO_INCLUIR_PRIVADOS"):
        catalogo.obter_indicador(privado.id)


# ------------------------------------------------------------- validação ao vivo


@pytest.mark.rede
@pytest.mark.parametrize("tema", ["fiscal", "setor_externo", "credito", "politica_monetaria"])
def test_series_sgs_do_tema_existem_atualizadas_e_com_unidade_coerente(tema):
    """Consulta o SGS: nome, unidade e periodicidade oficiais, defasagem e última observação."""
    spec = importlib.util.spec_from_file_location("validar_catalogo", RAIZ / "scripts" / "validar_catalogo.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # o @dataclass do script consulta sys.modules
    spec.loader.exec_module(modulo)
    resultados = asyncio.run(modulo.validar_todos(tema, 5))
    assert resultados
    com_problema = {r.id: r.problemas for r in resultados if r.problemas}
    assert not com_problema, com_problema
