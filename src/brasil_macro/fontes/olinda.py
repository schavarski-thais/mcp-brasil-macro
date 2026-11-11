"""Banco Central — serviços Olinda (OData): expectativas de mercado (Focus) e PTAX."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from ..http import TTL_CURTO, TTL_LONGO, ErroFonte, requisitar

ROTULO_FOCUS = "BCB/Focus — Sistema de Expectativas de Mercado"
ROTULO_PTAX = "BCB/PTAX"
_FOCUS = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/"
_PTAX = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"

# tipo -> (conjunto geral, conjunto Top 5, campo que identifica o período de referência)
CONJUNTOS: dict[str, tuple[str, str, str | None]] = {
    "anual": ("ExpectativasMercadoAnuais", "ExpectativasMercadoTop5Anuais", "DataReferencia"),
    "mensal": ("ExpectativaMercadoMensais", "ExpectativasMercadoTop5Mensais", "DataReferencia"),
    "trimestral": ("ExpectativasMercadoTrimestrais", "ExpectativaMercadoTop5Trimestral", "DataReferencia"),
    "12_meses": ("ExpectativasMercadoInflacao12Meses", "ExpectativasMercadoTop5Inflacao12Meses", None),
    "24_meses": ("ExpectativasMercadoInflacao24Meses", "ExpectativasMercadoTop5Inflacao24Meses", None),
    "selic": ("ExpectativasMercadoSelic", "ExpectativasMercadoTop5Selic", "Reuniao"),
}


def _aspas(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


async def _odata(base: str, recurso: str, opcoes: dict[str, str], ttl: float) -> tuple[list[dict[str, Any]], str, datetime]:
    # A query é montada à mão: o Olinda exige %20 nos espaços de $filter, não "+".
    partes = [f"{chave}={quote(valor, safe='')}" for chave, valor in opcoes.items()]
    partes.append("$format=json")
    r = await requisitar(f"{base}{recurso}?{'&'.join(partes)}", ttl=ttl, aceitar=(200, 400, 404), espera_json=True)
    if r.status != 200:
        raise ErroFonte(f"O serviço Olinda do BCB recusou a consulta (HTTP {r.status}): {r.texto()[:300]} (URL: {r.url})")
    return r.json().get("value", []), r.url, r.consultado_em


async def focus(
    conjunto: str,
    filtros: list[str],
    *,
    topo: int = 1000,
    ordem: str = "Data desc",
    campos: str | None = None,
) -> tuple[list[dict[str, Any]], str, datetime]:
    """Consulta um conjunto de dados do Focus. `filtros` são expressões OData unidas por `and`."""
    opcoes = {"$top": str(topo), "$orderby": ordem}
    if filtros:
        opcoes["$filter"] = " and ".join(filtros)
    if campos:
        opcoes["$select"] = campos
    return await _odata(_FOCUS, conjunto, opcoes, TTL_CURTO)


def filtro_igual(campo: str, valor: str | int) -> str:
    return f"{campo} eq {valor}" if isinstance(valor, int) else f"{campo} eq {_aspas(valor)}"


def filtro_desde(campo: str, d: date) -> str:
    return f"{campo} ge {_aspas(d.isoformat())}"


async def focus_ultima_data(conjunto: str, filtros: list[str]) -> str | None:
    linhas, _, _ = await focus(conjunto, filtros, topo=1, campos="Data")
    return linhas[0]["Data"] if linhas else None


async def focus_indicadores(conjunto: str) -> list[tuple[str, str | None]]:
    """Pares (indicador, detalhe) presentes na data mais recente do conjunto."""
    ultima = await focus_ultima_data(conjunto, [])
    if not ultima:
        return []
    linhas, _, _ = await focus(conjunto, [filtro_igual("Data", ultima)], topo=5000)
    return sorted({(x["Indicador"], x.get("IndicadorDetalhe")) for x in linhas}, key=lambda p: (p[0], p[1] or ""))


# ---------------------------------------------------------------------- PTAX


def _mdy(d: date) -> str:
    return d.strftime("%m-%d-%Y")


async def ptax_moedas() -> list[dict[str, str]]:
    linhas, _, _ = await _odata(_PTAX, "Moedas", {}, TTL_LONGO)
    return [{"simbolo": x["simbolo"], "nome": x["nomeFormatado"]} for x in linhas]


async def ptax(moeda: str, inicio: date, fim: date) -> tuple[list[dict[str, Any]], str, datetime]:
    """Cotações PTAX de fechamento por dia, em ordem cronológica."""
    moeda = moeda.upper()
    if moeda == "USD":
        recurso = "CotacaoDolarPeriodo(dataInicial=@i,dataFinalCotacao=@f)"
        opcoes = {"@i": _aspas(_mdy(inicio)), "@f": _aspas(_mdy(fim))}
    else:
        recurso = "CotacaoMoedaPeriodo(moeda=@m,dataInicial=@i,dataFinalCotacao=@f)"
        opcoes = {"@m": _aspas(moeda), "@i": _aspas(_mdy(inicio)), "@f": _aspas(_mdy(fim))}
    opcoes["$top"] = "10000"
    linhas, url, quando = await _odata(_PTAX, recurso, opcoes, TTL_CURTO)
    saida = []
    for x in linhas:
        if x.get("tipoBoletim", "Fechamento") != "Fechamento":
            continue  # moedas que não o dólar trazem também os boletins de abertura e intermediários
        item = {"data": x["dataHoraCotacao"][:10], "compra": x["cotacaoCompra"], "venda": x["cotacaoVenda"]}
        if "paridadeCompra" in x:
            item["paridade_compra"] = x["paridadeCompra"]
            item["paridade_venda"] = x["paridadeVenda"]
        saida.append(item)
    saida.sort(key=lambda i: i["data"])
    return saida, url, quando
