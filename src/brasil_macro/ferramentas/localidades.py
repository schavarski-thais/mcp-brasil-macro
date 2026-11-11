"""Ferramenta de divisão territorial do IBGE: estados e municípios com os códigos oficiais."""

from __future__ import annotations

from typing import Any

from ..catalogo import normalizar
from ..fontes import localidades as loc
from ._comum import carimbo, ferramenta

_LIMITE_MAXIMO = 200


def _sigla_uf(municipio: dict[str, Any]) -> str:
    return ((((municipio.get("microrregiao") or {}).get("mesorregiao") or {}).get("UF") or {}).get("sigla")) or ""


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def ibge_localidades(
        nivel: str = "estados",
        uf: str | None = None,
        consulta: str | None = None,
        limite: int = 50,
    ) -> dict[str, Any]:
        """Divisão territorial do IBGE com os códigos oficiais (úteis para cruzar com o SIDRA e outras fontes).

        `nivel` é 'estados' ou 'municipios'. Para municípios, informe a `uf` (sigla) ou um trecho do nome em
        `consulta`; sem nenhum dos dois, a lista seria o país inteiro e é recusada.
        """
        if not 1 <= limite <= _LIMITE_MAXIMO:
            raise ValueError(f"limite deve estar entre 1 e {_LIMITE_MAXIMO}.")
        nivel = nivel.strip().lower()
        base = {"fonte": loc.ROTULO, "consultado_em": carimbo()}

        if nivel == "estados":
            itens = await loc.estados()
            if uf or consulta:
                termo = normalizar(consulta or "")
                sigla = (uf or "").strip().upper()
                itens = [
                    e for e in itens
                    if (not sigla or e.get("sigla") == sigla) and (not termo or termo in normalizar(e.get("nome", "")))
                ]
            linhas = [
                {"id": e["id"], "sigla": e["sigla"], "nome": e["nome"], "regiao": (e.get("regiao") or {}).get("nome")}
                for e in itens
            ]
            return {**base, "nivel": "estados", "total": len(linhas), "itens": linhas[:limite]}

        if nivel != "municipios":
            raise ValueError("nivel deve ser 'estados' ou 'municipios'.")
        if not uf and not consulta:
            raise ValueError("Para municípios, informe a uf (sigla) ou um trecho do nome em consulta.")
        itens = await loc.municipios(uf.strip().upper() if uf else None)
        if consulta:
            termo = normalizar(consulta)
            itens = [m for m in itens if termo in normalizar(m.get("nome", ""))]
        if not itens:
            raise ValueError("Nenhum município encontrado com esses filtros.")
        linhas = [{"id": m["id"], "nome": m["nome"], "uf": _sigla_uf(m)} for m in itens]
        return {**base, "nivel": "municipios", "total": len(linhas), "itens": linhas[:limite]}
