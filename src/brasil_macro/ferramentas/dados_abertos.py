"""Ferramentas de busca nos portais de dados abertos do BNDES e da ANEEL."""

from __future__ import annotations

from typing import Any

from ..fontes import ckan
from ._comum import carimbo, ferramenta

_LIMITE_MAXIMO = 50


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def dados_abertos_buscar(portal: str, consulta: str, limite: int = 10) -> dict[str, Any]:
        """Busca conjuntos de dados nos portais de dados abertos oficiais: portal 'bndes' (operações, desembolsos e
        financiamentos do BNDES) ou 'aneel' (energia elétrica: geração, tarifas, outorgas). Devolve o título, a
        descrição, a página e os recursos (CSV, XLSX, API) de cada conjunto, para o usuário abrir ou baixar.
        """
        portal = portal.strip().lower()
        if portal not in ckan.PORTAIS:
            raise ValueError(f"portal inválido: {portal!r}. Opções: {', '.join(ckan.PORTAIS)}.")
        if not consulta.strip():
            raise ValueError("Informe um termo de busca.")
        if not 1 <= limite <= _LIMITE_MAXIMO:
            raise ValueError(f"limite deve estar entre 1 e {_LIMITE_MAXIMO}.")
        base, rotulo = ckan.PORTAIS[portal]
        total, conjuntos, url = await ckan.buscar(portal, consulta, limite)
        return {
            "portal": rotulo,
            "consulta": consulta,
            "encontrados": total,
            "conjuntos": [ckan.descrever(c, base) for c in conjuntos],
            "fonte": rotulo,
            "url": url,
            "consultado_em": carimbo(),
        }
