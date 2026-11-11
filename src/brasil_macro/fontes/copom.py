"""Banco Central — Copom: histórico de decisões, comunicados e atas (API do site do BCB)."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from ..http import TTL_CURTO, TTL_MEDIO, ErroFonte, requisitar
from . import sgs

ROTULO = "BCB/Copom"
_SITE = "https://www.bcb.gov.br/api/servico/sitebcb/"
PAGINA_HISTORICO = "https://www.bcb.gov.br/controleinflacao/historicotaxasjuros"


async def _site(caminho: str, ttl: float = TTL_MEDIO) -> tuple[list[dict[str, Any]], str, datetime]:
    r = await requisitar(_SITE + caminho, ttl=ttl, espera_json=True)
    dados = r.json()
    conteudo = dados.get("conteudo", dados) if isinstance(dados, dict) else dados
    return (conteudo if isinstance(conteudo, list) else [conteudo]), r.url, r.consultado_em


def _dia(texto: str | None) -> str | None:
    return texto[:10] if texto else None


async def historico() -> tuple[list[dict[str, Any]], str, datetime]:
    """Todas as reuniões do Copom, da mais recente para a mais antiga."""
    bruto, url, quando = await _site("historicotaxasjuros", TTL_CURTO)
    reunioes = []
    for x in bruto:
        if x.get("MetaSelic") is None and x.get("TaxaTban") is None:
            continue
        reunioes.append(
            {
                "reuniao": int(x["NumeroReuniaoCopom"]),
                "data": _dia(x.get("DataReuniaoCopom")),
                "meta_selic": x.get("MetaSelic"),
                "vies": x.get("Vies") or "",
                "extraordinaria": bool(x.get("ReuniaoExtraordinaria")),
                "vigencia_inicio": _dia(x.get("DataInicioVigencia")),
                "vigencia_fim": _dia(x.get("DataFimVigencia")),
                "selic_efetiva_anualizada": x.get("TaxaSelicEfetivaAnualizada"),
            }
        )
    reunioes.sort(key=lambda r: r["reuniao"], reverse=True)
    return reunioes, url, quando


async def proxima_decisao() -> str | None:
    """Data da próxima decisão do Copom.

    O BCB não publica o calendário por API, mas a série da meta Selic (SGS 432)
    é divulgada até o dia da próxima decisão; a última data futura é essa data.
    """
    hoje = date.today()
    try:
        serie = await sgs.serie_sgs(432, hoje, hoje + timedelta(days=120), "diaria")
    except ErroFonte:
        return None
    futuras = [d for d, _ in serie.observacoes if d > hoje]
    return futuras[-1].isoformat() if futuras else None


class _Texto(HTMLParser):
    _BLOCOS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "tr", "table", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style", "sup"):
            self._ignorar += 1
        elif tag in self._BLOCOS:
            self.partes.append("\n")
        if tag == "li":
            self.partes.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "sup"):
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in self._BLOCOS:
            self.partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignorar:
            self.partes.append(data)


def texto_de_html(conteudo: str) -> str:
    """Texto corrido a partir do HTML dos documentos do Copom."""
    leitor = _Texto()
    leitor.feed(conteudo or "")
    texto = html.unescape("".join(leitor.partes))
    texto = re.sub(r"[​‌‍﻿\xa0]", " ", texto)
    linhas = [re.sub(r"[ \t]+", " ", linha).strip() for linha in texto.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linhas)).strip()


async def listar_documentos(tipo: str, quantidade: int = 8) -> tuple[list[dict[str, Any]], str, datetime]:
    """Comunicados ou atas mais recentes (número da reunião, data e título)."""
    if tipo == "comunicado":
        bruto, url, quando = await _site(f"copom/comunicados?quantidade={quantidade}", TTL_CURTO)
        itens = [{"reuniao": x["nro_reuniao"], "data": x["dataReferencia"], "titulo": x["titulo"]} for x in bruto]
    else:
        bruto, url, quando = await _site(f"copom/atas?quantidade={quantidade}", TTL_CURTO)
        itens = [
            {
                "reuniao": x["nroReuniao"],
                "data": x["dataReferencia"],
                "publicacao": x.get("dataPublicacao"),
                "titulo": x["titulo"],
            }
            for x in bruto
        ]
    return itens, url, quando


async def documento(tipo: str, reuniao: int) -> dict[str, Any]:
    """Texto integral do comunicado ou da ata de uma reunião."""
    if tipo == "comunicado":
        bruto, url, quando = await _site(f"copom/comunicados_detalhes?nro_reuniao={reuniao}")
        campo, extra = "textoComunicado", {}
    else:
        bruto, url, quando = await _site(f"copom/atas_detalhes?nro_reuniao={reuniao}")
        campo, extra = "textoAta", {"pdf": (bruto[0].get("urlPdfAta") if bruto else None)}
    if not bruto or not bruto[0].get(campo):
        raise LookupError(
            f"Não há {tipo} publicado para a reunião {reuniao}. "
            "A ata sai cerca de uma semana depois da reunião; reuniões antigas podem não ter texto na API."
        )
    item = bruto[0]
    return {
        "reuniao": reuniao,
        "data": item.get("dataReferencia"),
        "titulo": item.get("titulo", ""),
        "texto": texto_de_html(item[campo]),
        "url": url,
        "consultado_em": quando,
        **{k: v for k, v in extra.items() if v},
    }
