"""Portais de dados abertos em CKAN (BNDES e ANEEL): busca de conjuntos de dados e de seus recursos."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from ..http import TTL_MEDIO, ErroFonte, requisitar

PORTAIS: dict[str, tuple[str, str]] = {
    "bndes": ("https://dadosabertos.bndes.gov.br", "BNDES/Dados Abertos"),
    "aneel": ("https://dadosabertos.aneel.gov.br", "ANEEL/Dados Abertos"),
}


async def buscar(portal: str, consulta: str, limite: int) -> tuple[int, list[dict[str, Any]], str]:
    """Conjuntos de dados que casam com a consulta: (total, conjuntos, URL da busca)."""
    base, _ = PORTAIS[portal]
    url = f"{base}/api/3/action/package_search?q={quote(consulta)}&rows={limite}"
    r = await requisitar(url, ttl=TTL_MEDIO, tempo_limite=60)
    dados = r.json()
    if not isinstance(dados, dict) or not dados.get("success"):
        raise ErroFonte(f"O portal {portal} recusou a busca (resposta fora do formato CKAN).")
    resultado = dados["result"]
    return int(resultado.get("count", 0)), resultado.get("results", []), url


def descrever(conjunto: dict[str, Any], base: str) -> dict[str, Any]:
    notas = re.sub(r"\s+", " ", conjunto.get("notes") or "").strip()
    recursos = [
        {"nome": r.get("name") or r.get("description") or "", "formato": (r.get("format") or "").upper(), "url": r.get("url")}
        for r in (conjunto.get("resources") or [])[:8]
    ]
    return {
        "titulo": conjunto.get("title") or conjunto.get("name"),
        "id": conjunto.get("name"),
        "organizacao": (conjunto.get("organization") or {}).get("title"),
        "atualizado_em": conjunto.get("metadata_modified"),
        "descricao": notas[:400] + ("…" if len(notas) > 400 else ""),
        "pagina": f"{base}/dataset/{conjunto.get('name')}",
        "recursos": recursos,
        "total_recursos": len(conjunto.get("resources") or []),
    }
