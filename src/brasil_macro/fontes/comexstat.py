"""MDIC/Secex — ComexStat (estatísticas de comércio exterior).

A API (https://api-comexstat.mdic.gov.br, documentação em /docs) responde HTTP 429
de forma intermitente nas consultas POST, mesmo com intervalos de 10 a 60 s entre
elas; em teste, cerca de uma tentativa em três passava. Por isso as consultas
tentam de novo com pausas longas, guardam em cache tudo o que dá certo e, para os
totais mensais do catálogo, recorrem às mesmas séries da Secex publicadas pelo
Ipeadata quando a API não cede.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..catalogo import Indicador, fonte_de_dados, normalizar
from ..http import TTL_LONGO, TTL_MEDIO, ErroFonte, requisitar
from ..modelos import Observacao, Serie, deslocar_meses

ROTULO = "MDIC/Secex (ComexStat)"
BASE = "https://api-comexstat.mdic.gov.br"

PRIMEIRO_ANO = 1997

# Pausa entre tentativas depois de um 429 e número de tentativas por consulta.
# O limite não é regular; ver a docstring do módulo.
_ESPERA_429 = 20.0
_TENTATIVAS = 4

# Nome usado nas ferramentas -> nome do detalhamento/filtro na API.
DETALHES = {
    "pais": "country",
    "bloco": "economicBlock",
    "uf": "state",
    "via": "via",
    "secao": "section",
    "capitulo": "chapter",
    "sh4": "heading",
    "cuci_secao": "SITCSection",
    "cuci_capitulo": "SITCDivision",
    "isic_secao": "ISICSection",
    "isic_divisao": "ISICDivision",
    "cgce": "BECLevel1",
}

NOMES_UF = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina", "SP": "São Paulo",
    "SE": "Sergipe", "TO": "Tocantins",
}  # fmt: skip

_APELIDOS_PAIS = {"eua": "Estados Unidos", "usa": "Estados Unidos", "holanda": "Países Baixos", "uk": "Reino Unido"}

_FLUXOS = {"export": "export", "exportacao": "export", "exportacoes": "export",
           "import": "import", "importacao": "import", "importacoes": "import"}  # fmt: skip


@dataclass
class Consulta:
    linhas: list[dict[str, Any]]
    url: str
    consultado_em: datetime
    desatualizado: bool = False


def fluxo_api(fluxo: str) -> str:
    try:
        return _FLUXOS[normalizar(fluxo)]
    except KeyError:
        raise ValueError(f"Fluxo inválido: {fluxo!r}. Use exportacao, importacao ou saldo.") from None


def numero(valor: Any) -> float:
    """A API devolve as métricas como texto ("33962602370")."""
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def montar_corpo(
    fluxo: str,
    de: date,
    ate: date,
    *,
    mensal: bool,
    detalhes: tuple[str, ...] = (),
    filtros: dict[str, list[str]] | None = None,
    metricas: tuple[str, ...] = ("metricFOB", "metricKG"),
) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "flow": fluxo,
        "monthDetail": mensal,
        "period": {"from": f"{de.year}-{de.month:02d}", "to": f"{ate.year}-{ate.month:02d}"},
        "metrics": list(metricas),
    }
    if detalhes:
        corpo["details"] = list(detalhes)
    if filtros:
        corpo["filters"] = [{"filter": nome, "values": valores} for nome, valores in filtros.items() if valores]
    return corpo


def interpretar_resposta(dados: Any) -> list[dict[str, Any]]:
    """Linhas da resposta de /general; levanta ErroFonte se a API sinalizou falha."""
    if not isinstance(dados, dict):
        raise ErroFonte("Resposta inesperada do ComexStat (não é um objeto JSON).")
    if dados.get("error"):
        raise ErroFonte(f"O ComexStat recusou a consulta: {dados['error'].get('message', dados['error'])}")
    if dados.get("success") is False:
        raise ErroFonte(f"O ComexStat recusou a consulta: {dados.get('message') or 'sem detalhe'}")
    lista = (dados.get("data") or {}).get("list")
    if not isinstance(lista, list):
        raise ErroFonte("Resposta do ComexStat sem a lista de dados esperada.")
    return lista


def nome_do_detalhe(linha: dict[str, Any], detalhe: str) -> str:
    """Texto do item de um detalhamento. Se a chave não tiver o nome do detalhe, usa a primeira que não for código nem métrica."""
    if isinstance(linha.get(detalhe), str):
        return linha[detalhe]
    for chave, valor in linha.items():
        if chave in ("year", "monthNumber") or chave.startswith("metric"):
            continue
        if chave.startswith("co") and len(chave) > 2 and chave[2].isupper():
            continue  # coNcmSecrom, coPais...: códigos
        if isinstance(valor, str):
            return valor
    return ""


def periodo_da_linha(linha: dict[str, Any]) -> tuple[int, int | None]:
    ano = int(linha["year"])
    mes = linha.get("monthNumber")
    return ano, int(mes) if mes not in (None, "") else None


async def consultar_geral(corpo: dict[str, Any], *, ttl: float = TTL_MEDIO, tentativas: int = _TENTATIVAS) -> Consulta:
    """POST /general com novas tentativas espaçadas e cache; ErroFonte se o 429 persistir."""
    ultimo = ""
    for tentativa in range(1, tentativas + 1):
        r = await requisitar(
            f"{BASE}/general",
            params={"language": "pt"},
            metodo="POST",
            json_corpo=corpo,
            ttl=ttl,
            tempo_limite=90,
            tentativas=2,
            aceitar=(200, 400, 429),
        )
        if r.status == 429:
            ultimo = "HTTP 429 (limite de requisições)"
            if tentativa < tentativas:
                await asyncio.sleep(_ESPERA_429)
            continue
        try:
            dados = r.json()
        except ValueError:
            raise ErroFonte(f"O ComexStat devolveu uma resposta que não é JSON (HTTP {r.status}).") from None
        if r.status != 200:
            mensagem = (dados.get("error") or {}).get("message", "") if isinstance(dados, dict) else ""
            raise ErroFonte(
                f"O ComexStat recusou a consulta (HTTP {r.status}): {mensagem or 'sem detalhe'}. "
                "Se o pedido for muito grande, reduza o período ou use filtros."
            )
        return Consulta(interpretar_resposta(dados), r.url, r.consultado_em, r.desatualizado)
    raise ErroFonte(
        f"O ComexStat recusou a consulta {tentativas} vezes seguidas ({ultimo}). A API da Secex limita as "
        "requisições de forma irregular; tente de novo em um ou dois minutos (o que já foi consultado fica em cache)."
    )


def retangulos(de: date, ate: date) -> list[tuple[date, date]]:
    """Parte um período em blocos que a API entende.

    O ComexStat trata `period` como ano em [ano inicial, ano final] E mês em
    [mês inicial, mês final], e não como um intervalo contínuo: de 2025-07 a
    2026-06 não devolve nada, e de 1997-01 a 2026-10 devolve só janeiro a
    outubro de todos os anos. Por isso o período vira, no máximo, três blocos
    em que a regra coincide com o intervalo real: o primeiro ano parcial, os
    anos cheios e o último ano parcial.
    """
    if de > ate:
        return []
    if de.year == ate.year:
        return [(de, ate)]
    blocos: list[tuple[date, date]] = []
    primeiro_cheio = de.year if de.month == 1 else de.year + 1
    ultimo_cheio = ate.year if ate.month == 12 else ate.year - 1
    if de.month != 1:
        blocos.append((de, date(de.year, 12, 1)))
    if primeiro_cheio <= ultimo_cheio:
        blocos.append((date(primeiro_cheio, 1, 1), date(ultimo_cheio, 12, 1)))
    if ate.month != 12:
        blocos.append((date(ate.year, 1, 1), ate))
    return blocos


async def consultar_periodo(
    fluxo: str,
    de: date,
    ate: date,
    *,
    detalhes: tuple[str, ...] = (),
    filtros: dict[str, list[str]] | None = None,
    metricas: tuple[str, ...] = ("metricFOB", "metricKG"),
    tentativas: int = _TENTATIVAS,
    ttl: float = TTL_MEDIO,
) -> Consulta:
    """Consulta mensal de um período qualquer (ver `retangulos`), juntando os blocos."""
    linhas: list[dict[str, Any]] = []
    primeira: Consulta | None = None
    desatualizado = False
    momento: datetime | None = None
    for inicio, fim in retangulos(de, ate):
        corpo = montar_corpo(fluxo, inicio, fim, mensal=True, detalhes=detalhes, filtros=filtros, metricas=metricas)
        consulta = await consultar_geral(corpo, ttl=ttl, tentativas=tentativas)
        linhas.extend(consulta.linhas)
        primeira = primeira or consulta
        desatualizado = desatualizado or consulta.desatualizado
        momento = consulta.consultado_em if momento is None else min(momento, consulta.consultado_em)
    if primeira is None or momento is None:
        raise ValueError("Período vazio.")
    return Consulta(linhas, primeira.url, momento, desatualizado)


async def ultimo_mes_disponivel() -> date:
    """Primeiro dia do último mês com dados fechados."""
    try:
        r = await requisitar(f"{BASE}/general/dates/updated", ttl=TTL_MEDIO, espera_json=True, tentativas=3)
        dados = r.json()["data"]
        return date(int(dados["year"]), int(dados["monthNumber"]), 1)
    except (ErroFonte, KeyError, ValueError, TypeError):
        hoje = date.today().replace(day=1)
        return deslocar_meses(hoje, -2)


def _lista_de_filtro(dados: Any) -> list[dict[str, Any]]:
    lista = (dados or {}).get("data") if isinstance(dados, dict) else None
    if isinstance(lista, dict):
        lista = lista.get("list")
    if isinstance(lista, list) and lista and isinstance(lista[0], list):
        lista = lista[0]  # a API embrulha a lista numa lista
    return lista if isinstance(lista, list) else []


async def valores_do_filtro(filtro: str) -> list[tuple[str, str]]:
    """Pares (id, texto) dos valores de um filtro, sem repetições."""
    r = await requisitar(
        f"{BASE}/general/filters/{filtro}",
        params={"language": "pt"},
        ttl=TTL_LONGO,
        espera_json=True,
        tentativas=3,
    )
    vistos: dict[str, str] = {}
    for item in _lista_de_filtro(r.json()):
        if isinstance(item, dict) and item.get("id") is not None:
            vistos.setdefault(str(item["id"]), str(item.get("text", "")))
    return list(vistos.items())


def escolher_valores(
    filtro_nome: str, pedido: str, opcoes: list[tuple[str, str]], rotulo: str
) -> list[tuple[str, str]]:
    """Resolve nomes, códigos ou siglas de UF para valores de filtro; erro com sugestões se ambíguo."""
    escolhidos: list[tuple[str, str]] = []
    for parte in (p.strip() for p in pedido.split(",")):
        if not parte:
            continue
        alvo = normalizar(parte)
        if filtro_nome == "state" and parte.upper() in NOMES_UF:
            alvo = normalizar(NOMES_UF[parte.upper()])
        elif filtro_nome == "country":
            alvo = normalizar(_APELIDOS_PAIS.get(alvo, parte))
        exatos = [o for o in opcoes if o[0].lower() == parte.lower() or normalizar(o[1]) == alvo]
        parciais = [o for o in opcoes if alvo and alvo in normalizar(o[1])]
        achados = exatos or parciais
        if len(achados) == 1 or (achados and exatos):
            escolhidos.append(achados[0])
            continue
        if not achados:
            raise ValueError(f"Não encontrei {rotulo} {parte!r} no ComexStat. Use um nome ou código válido.")
        sugestoes = "; ".join(f"{texto} ({id_})" for id_, texto in achados[:6])
        raise ValueError(f"{rotulo.capitalize()} {parte!r} é ambíguo no ComexStat. Candidatos: {sugestoes}.")
    return escolhidos


# ------------------------------------------------------ totais mensais do Brasil


def _serie_mensal(linhas: list[dict[str, Any]], metrica: str = "metricFOB", divisor: float = 1e6) -> dict[date, float]:
    saida: dict[date, float] = {}
    for linha in linhas:
        ano, mes = periodo_da_linha(linha)
        if mes is None:
            continue
        d = date(ano, mes, 1)
        saida[d] = saida.get(d, 0.0) + numero(linha.get(metrica)) / divisor
    return saida


async def _totais_api(fluxo: str, ate: date, tentativas: int) -> tuple[dict[date, float], Consulta]:
    # Sempre o histórico inteiro (dois blocos por fluxo, ver `retangulos`): o cache reaproveita
    # as consultas para qualquer janela pedida depois (cerca de 350 linhas).
    consulta = await consultar_periodo(
        fluxo, date(PRIMEIRO_ANO, 1, 1), ate, metricas=("metricFOB",), tentativas=tentativas
    )
    return _serie_mensal(consulta.linhas), consulta


async def _totais_ipea(fluxo: str) -> tuple[dict[date, float], str, datetime, bool]:
    from .ipea import serie_ipea

    codigo = "SECEX12_XVTOT12" if fluxo == "export" else "SECEX12_MVTOT12"
    s = await serie_ipea(codigo)
    velha = any("cópia anterior" in nota for nota in s.notas)
    valores = {d: v for d, v in s.observacoes if v is not None}
    return valores, s.url, s.consultado_em, velha  # type: ignore[return-value]


_NOMES_SERIE = {
    "exportacoes": "Exportações brasileiras — valor FOB mensal",
    "importacoes": "Importações brasileiras — valor FOB mensal",
    "saldo": "Balança comercial (MDIC/Secex) — saldo mensal, exportações menos importações FOB",
    "corrente": "Corrente de comércio (MDIC/Secex) — exportações mais importações FOB mensais",
}

def combinar(tipo: str, exportacoes: dict[date, float], importacoes: dict[date, float]) -> dict[date, float]:
    if tipo == "exportacoes":
        return dict(exportacoes)
    if tipo == "importacoes":
        return dict(importacoes)
    comuns = exportacoes.keys() & importacoes.keys()
    if tipo == "saldo":
        return {d: exportacoes[d] - importacoes[d] for d in comuns}
    if tipo == "corrente":
        return {d: exportacoes[d] + importacoes[d] for d in comuns}
    raise ValueError(f"Tipo de total desconhecido: {tipo!r}. Use {', '.join(_NOMES_SERIE)}.")


async def serie_totais(tipo: str, inicio: date | None = None, fim: date | None = None, tentativas: int = 3) -> Serie:
    """Totais mensais do Brasil em US$ milhões FOB (exportacoes, importacoes, saldo ou corrente)."""
    if tipo not in _NOMES_SERIE:
        raise ValueError(f"Tipo de total desconhecido: {tipo!r}. Use {', '.join(_NOMES_SERIE)}.")
    necessarios = ["export", "import"] if tipo in ("saldo", "corrente") else [("export" if tipo == "exportacoes" else "import")]
    notas: list[str] = []
    series: dict[str, dict[date, float]] = {}
    url, consultado, defasada = "", None, False
    try:
        ultimo = await ultimo_mes_disponivel()
        for fluxo in necessarios:
            series[fluxo], consulta = await _totais_api(fluxo, ultimo, tentativas)
            url, consultado = url or consulta.url, consultado or consulta.consultado_em
            defasada = defasada or consulta.desatualizado
        rotulo = ROTULO
    except ErroFonte as erro:
        # A API oficial não cedeu: usa as séries SECEX do Ipeadata (mesmo órgão, defasagem de semanas).
        series.clear()
        for fluxo in necessarios:
            series[fluxo], url_ipea, quando, velha = await _totais_ipea(fluxo)
            url, consultado = url or url_ipea, consultado or quando
            defasada = defasada or velha
        rotulo = "MDIC/Secex via IPEA/Ipeadata"
        notas.append(
            f"A API do ComexStat não respondeu ({str(erro)[:140]}). Estes valores vêm das séries SECEX12_XVTOT12 e "
            "SECEX12_MVTOT12 do Ipeadata, que reproduzem os totais da Secex mas podem estar defasadas em semanas e "
            "não incorporam as revisões mais recentes."
        )
    valores = combinar(tipo, series.get("export", {}), series.get("import", {}))
    obs: list[Observacao] = [
        (d, round(v, 3)) for d, v in sorted(valores.items()) if (inicio is None or d >= inicio) and (fim is None or d <= fim)
    ]
    if defasada:
        notas.append("O servidor de origem não respondeu; estes dados são uma cópia anterior guardada em cache.")
    return Serie(
        nome=_NOMES_SERIE[tipo],
        fonte=rotulo,
        codigo=f"COMEX {tipo}",
        unidade="US$ milhões",
        periodicidade="mensal",
        observacoes=obs,
        url=url,
        consultado_em=consultado,
        notas=notas,
    )


@fonte_de_dados("comex", ROTULO)
async def _buscar(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    serie = await serie_totais(str(ind.parametros), inicio, fim)
    return serie
