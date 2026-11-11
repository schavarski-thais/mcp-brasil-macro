"""TransfereGov — planos de ação das transferências especiais (emendas Pix), pela API PostgREST do Ministério da Gestão."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..http import TTL_LONGO, TTL_MEDIO, ErroFonte, requisitar

ROTULO = "TransfereGov/Transferências Especiais"
BASE = "https://api.transferegov.gestao.gov.br/transferenciasespeciais/plano_acao_especial"
_PAGINA = 1000
_MAXIMO_PAGINAS = 50  # 50 mil planos por ano: folga de sobra, evita laço infinito


async def planos(ano: int) -> tuple[list[dict[str, Any]], str]:
    """Todos os planos de ação de um ano (paginado), com a URL base usada."""
    if not 2019 <= ano <= date.today().year:
        raise ValueError(f"Ano {ano} fora do intervalo com dados (2019 a {date.today().year}).")
    recente = ano >= date.today().year - 1
    todos: list[dict[str, Any]] = []
    for pagina in range(_MAXIMO_PAGINAS):
        url = f"{BASE}?ano_plano_acao=eq.{ano}&order=id_plano_acao.asc&limit={_PAGINA}&offset={pagina * _PAGINA}"
        r = await requisitar(url, ttl=TTL_MEDIO if recente else TTL_LONGO, tempo_limite=120)
        dados = r.json()
        if not isinstance(dados, list):
            raise ErroFonte(f"O TransfereGov devolveu uma resposta fora do formato esperado para {url}.")
        todos.extend(dados)
        if len(dados) < _PAGINA:
            return todos, BASE
    raise ErroFonte(f"O TransfereGov tem mais de {_PAGINA * _MAXIMO_PAGINAS} planos em {ano}; consulta interrompida.")
