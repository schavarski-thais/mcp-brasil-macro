"""IBGE — divisão territorial (regiões, estados e municípios) com os códigos usados pelo SIDRA e pelas demais fontes."""

from __future__ import annotations

from typing import Any

from ..http import TTL_LONGO, ErroFonte, requisitar

ROTULO = "IBGE/Localidades"
_BASE = "https://servicodados.ibge.gov.br/api/v1/localidades"


async def _json(url: str) -> Any:
    r = await requisitar(url, ttl=TTL_LONGO, tempo_limite=60)
    dados = r.json()
    if not isinstance(dados, list):
        raise ErroFonte(f"O IBGE devolveu uma resposta fora do formato esperado para {url}.")
    return dados


async def estados() -> list[dict[str, Any]]:
    return await _json(f"{_BASE}/estados?orderBy=nome")


async def municipios(uf: str | None) -> list[dict[str, Any]]:
    """Municípios de uma UF (sigla ou código), ou de todo o país quando uf é None."""
    if uf is None:
        return await _json(f"{_BASE}/municipios?orderBy=nome")
    return await _json(f"{_BASE}/estados/{uf}/municipios?orderBy=nome")
