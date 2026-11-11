"""Banco Central do Brasil — Sistema Gerenciador de Séries Temporais (SGS)."""

from __future__ import annotations

import asyncio
import html
import re
from datetime import date, timedelta

from ..catalogo import Indicador, fonte_de_dados
from ..http import TTL_CURTO, TTL_LONGO, TTL_MEDIO, ErroFonte, requisitar
from ..modelos import Observacao, Serie

ROTULO = "BCB/SGS"
_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
_SOAP = "https://www3.bcb.gov.br/wssgs/services/FachadaWSSGS"

# A API recusa (HTTP 406) janelas acima de 10 anos em séries diárias.
_JANELA_DIARIA = timedelta(days=3600)

_PERIODICIDADES = {"D": "diaria", "M": "mensal", "T": "trimestral", "A": "anual"}


class _ExigeJanela(Exception):
    """A série é diária e a API exige data inicial e janela de até 10 anos."""


def _br(d: date) -> str:
    return d.strftime("%d/%m/%Y")


async def _trecho(codigo: int, inicio: date | None, fim: date | None, ttl: float) -> tuple[list[Observacao], str, object]:
    params = {"formato": "json"}
    if inicio:
        params["dataInicial"] = _br(inicio)
    if fim:
        params["dataFinal"] = _br(fim)
    r = await requisitar(
        _BASE.format(codigo=codigo), params=params, ttl=ttl, aceitar=(200, 404, 406), espera_json=True
    )
    if r.status == 406:
        raise _ExigeJanela
    if r.status == 404:
        # O SGS responde 404 tanto para "sem valores no período" quanto para série inexistente.
        if "not found" in r.texto().lower() or "o encontrad" in r.texto().lower():
            return [], r.url, r.consultado_em
        raise ErroFonte(f"O SGS não encontrou a série {codigo}. Resposta: {r.texto()[:200]}")
    obs: list[Observacao] = []
    for item in r.json():
        dia, mes, ano = item["data"].split("/")
        bruto = item.get("valor")
        valor = float(bruto) if bruto not in (None, "") else None
        obs.append((date(int(ano), int(mes), int(dia)), valor))
    return obs, r.url, r.consultado_em


async def serie_sgs(
    codigo: int,
    inicio: date | None = None,
    fim: date | None = None,
    periodicidade: str | None = None,
) -> Serie:
    """Baixa uma série do SGS.

    Séries diárias são consultadas em janelas de até 10 anos; sem `inicio`, traz
    os últimos 12 meses. Séries de outras periodicidades vêm completas quando
    não há `inicio`. Datas futuras (caso da meta Selic, publicada até a próxima
    reunião do Copom) são descartadas, a menos que `fim` as peça.
    """
    hoje = date.today()
    notas: list[str] = []
    diaria = periodicidade == "diaria"
    obs: list[Observacao] = []
    url = ""
    consultado = None

    if not diaria:
        try:
            # Um limite só no fim exige data inicial; 1900 cobre todo o histórico.
            ini = inicio or (date(1900, 1, 1) if fim else None)
            obs, url, consultado = await _trecho(codigo, ini, (fim or hoje) if ini else None, TTL_MEDIO)
        except _ExigeJanela:
            diaria = True
            if periodicidade is None:
                periodicidade = "diaria"

    if diaria:
        final = fim or hoje
        comeco = inicio or (final - timedelta(days=365))
        if inicio is None:
            notas.append("Série diária sem data inicial: exibidos os últimos 12 meses.")
        janelas: list[tuple[date, date]] = []
        a = comeco
        while a <= final:
            b = min(a + _JANELA_DIARIA, final)
            janelas.append((a, b))
            a = b + timedelta(days=1)
        if len(janelas) > 1:
            notas.append(f"Consulta dividida em {len(janelas)} janelas de até 10 anos (limite da API do BCB).")
        # A API pode omitir a observação mais recente em janelas longas: em teste, a
        # mesma série terminava em 06/11 numa janela de 13 meses e em 09/11 numa de
        # 7 meses. Uma janela curta no fim garante o dado mais novo.
        if final >= hoje - timedelta(days=7) and (final - comeco).days > 30:
            janelas.append((final - timedelta(days=10), final))
        partes = await asyncio.gather(*(_trecho(codigo, a, b, TTL_CURTO) for a, b in janelas))
        for parte, u, quando in partes:
            obs.extend(parte)
            if not url:
                url, consultado = u, quando

    unicas = dict(obs)  # remove datas repetidas nas emendas entre janelas
    limite = max(hoje, fim) if fim else hoje
    ordenadas = sorted((d, v) for d, v in unicas.items() if d <= limite)
    if len(ordenadas) < len(unicas):
        notas.append("A fonte publica datas futuras nesta série; elas foram omitidas.")

    return Serie(
        nome=f"Série SGS {codigo}",
        fonte=ROTULO,
        codigo=f"SGS {codigo}",
        unidade="",
        periodicidade=periodicidade or "mensal",
        observacoes=ordenadas,
        url=url,
        consultado_em=consultado,  # type: ignore[arg-type]
        notas=notas,
    )


_ENVELOPE = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:pub="http://publico.ws.casosdeuso.sgs.pec.bcb.gov.br"><soapenv:Body>'
    "<pub:getUltimoValorXML><in0>{codigo}</in0></pub:getUltimoValorXML></soapenv:Body></soapenv:Envelope>"
)


async def metadados_sgs(codigo: int) -> dict[str, str] | None:
    """Nome, unidade e periodicidade oficiais de uma série (serviço SOAP do SGS).

    A API JSON não traz metadados e o catálogo de dados abertos do BCB não cobre
    todas as séries; o serviço SOAP antigo cobre. Devolve None se indisponível.
    """
    try:
        r = await requisitar(
            _SOAP,
            metodo="POST",
            corpo=_ENVELOPE.format(codigo=codigo),
            cabecalhos={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            ttl=TTL_LONGO,
            tempo_limite=30,
            tentativas=2,
        )
    except ErroFonte:
        return None
    xml = html.unescape(r.texto())

    def campo(nome: str) -> str:
        m = re.search(rf"<{nome}>(.*?)</{nome}>", xml, re.S)
        return html.unescape(m.group(1)).strip() if m else ""

    if not campo("NOME"):
        return None
    sigla = campo("PERIODICIDADE")
    return {
        "nome": campo("NOME"),
        "unidade": campo("UNIDADE"),
        "periodicidade": _PERIODICIDADES.get(sigla, "diaria"),
        "periodicidade_sgs": sigla,
    }


@fonte_de_dados("sgs", ROTULO)
async def _buscar(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    return await serie_sgs(int(ind.parametros), inicio, fim, ind.periodicidade)
