"""Itens compartilhados pelos módulos de ferramentas."""

from __future__ import annotations

import functools
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from ..http import ErroFonte

# Todas as ferramentas só leem dados públicos de fontes externas.
LEITURA = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

# Teto do texto de cada resposta (caracteres). Acima disso o contexto do modelo estoura.
TETO_RESPOSTA = 10_000

_AVISO_CORTE = (
    "Resposta cortada no teto de tamanho. Restrinja com inicio/fim, limite, pagina ou detalhe "
    "para ver o restante."
)


def tabela(colunas: list[str], linhas: list[list[Any]]) -> dict[str, Any]:
    """Lista de registros em formato tabular: as colunas aparecem uma vez, não em cada item."""
    return {"colunas": colunas, "linhas": linhas}


def _serializar(resultado: Any) -> str:
    return json.dumps(resultado, ensure_ascii=False, separators=(",", ":"), default=str)


def encolher(resultado: Any, teto: int = TETO_RESPOSTA) -> str:
    """Serializa o resultado e, se passar do teto, corta a maior lista do nível de cima.

    Corta de forma que o JSON continue válido e a resposta diga quantos itens ficaram de fora.
    Não mexe em texto puro: a ferramenta que devolve texto é responsável pelo próprio tamanho.
    """
    texto = _serializar(resultado)
    if len(texto) <= teto or not isinstance(resultado, dict):
        return texto
    copia = dict(resultado)
    listas = [k for k, v in copia.items() if isinstance(v, list) and len(v) > 1]
    while listas and len(_serializar(copia)) > teto:
        campo = max(listas, key=lambda k: len(_serializar(copia[k])))
        total = len(resultado[campo])
        copia[campo] = copia[campo][: max(1, len(copia[campo]) // 2)]
        if len(copia[campo]) == 1 and len(_serializar(copia)) > teto:
            copia[campo] = []
        listas = [k for k in listas if isinstance(copia[k], list) and len(copia[k]) > 1]
        copia["truncado"] = {"campo": campo, "devolvidos": len(copia[campo]), "total": total, "aviso": _AVISO_CORTE}
    return _serializar(copia)


def ferramenta(servidor, **opcoes: Any) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Registra uma função assíncrona como ferramenta MCP somente leitura.

    Faz três coisas que o SDK não faz sozinho:
    - erros previstos (ValueError, LookupError, ErroFonte) chegam ao modelo com a
      mensagem original; sem isso o SDK devolve só "Error executing tool";
    - o resultado sai como JSON compacto, em vez do JSON indentado padrão;
    - o resultado respeita TETO_RESPOSTA (ver encolher).
    """

    def decorador(funcao: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(funcao)
        async def envolvida(*args: Any, **kwargs: Any) -> CallToolResult:
            try:
                resultado = await funcao(*args, **kwargs)
            except (ValueError, LookupError, ErroFonte) as erro:
                raise ToolError(str(erro)) from erro
            texto = resultado if isinstance(resultado, str) else encolher(resultado)
            return CallToolResult(content=[TextContent(type="text", text=texto)])

        servidor.tool(annotations=LEITURA, structured_output=False, **opcoes)(envolvida)
        return funcao

    return decorador


def arredondar(valor: float | None, casas: int = 4) -> float | None:
    return None if valor is None else round(valor, casas)


def carimbo(momento: datetime | None = None) -> str:
    """Hora da consulta em UTC, no formato usado em todas as respostas."""
    return (momento or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
