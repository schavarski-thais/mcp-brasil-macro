"""Ferramenta de emendas Pix (transferências especiais) — TransfereGov."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from ..catalogo import normalizar
from ..fontes import transferegov as tg
from ._comum import arredondar, carimbo, ferramenta

_AGRUPAMENTOS = {
    "uf": ("uf_beneficiario_plano_acao", "UF do beneficiário"),
    "parlamentar": ("nome_parlamentar_emenda_plano_acao", "parlamentar"),
    "beneficiario": ("nome_beneficiario_plano_acao", "beneficiário"),
}
_LIMITE_MAXIMO = 100


def _valor(plano: dict[str, Any]) -> float:
    return float(plano.get("valor_custeio_plano_acao") or 0) + float(plano.get("valor_investimento_plano_acao") or 0)


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def emendas_pix(
        ano: int | None = None,
        por: str = "uf",
        uf: str | None = None,
        situacao: str | None = None,
        parlamentar: str | None = None,
        beneficiario: str | None = None,
        limite: int = 15,
    ) -> dict[str, Any]:
        """Emendas Pix (transferências especiais da União a estados e municípios), pelo TransfereGov.

        Agrupa os planos de ação por `por` (uf, parlamentar ou beneficiario), ordenados pelo valor.
        Filtros: uf (sigla), situacao (ex.: CIENTE, IMPEDIDO), parlamentar e beneficiario (trecho do nome).
        Os valores são os previstos no plano de ação (custeio + investimento), não o montante já pago.
        Sem ano, usa o ano corrente.
        """
        if por not in _AGRUPAMENTOS:
            raise ValueError(f"por inválido: {por!r}. Opções: {', '.join(_AGRUPAMENTOS)}.")
        if not 1 <= limite <= _LIMITE_MAXIMO:
            raise ValueError(f"limite deve estar entre 1 e {_LIMITE_MAXIMO}.")
        ano = ano or date.today().year
        todos, url = await tg.planos(ano)

        planos = todos
        if uf:
            sigla = uf.strip().upper()
            planos = [p for p in planos if (p.get("uf_beneficiario_plano_acao") or "").upper() == sigla]
        if situacao:
            termo = normalizar(situacao)
            planos = [p for p in planos if termo in normalizar(p.get("situacao_plano_acao") or "")]
        if parlamentar:
            termo = normalizar(parlamentar)
            planos = [p for p in planos if termo in normalizar(p.get("nome_parlamentar_emenda_plano_acao") or "")]
        if beneficiario:
            termo = normalizar(beneficiario)
            planos = [p for p in planos if termo in normalizar(p.get("nome_beneficiario_plano_acao") or "")]
        if not planos:
            raise ValueError(f"Nenhum plano de ação em {ano} com esses filtros.")

        campo, _ = _AGRUPAMENTOS[por]
        grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in planos:
            grupos[(p.get(campo) or "(sem informação)").strip()].append(p)
        ranking = sorted(
            (
                {"chave": nome, "planos": len(ps), "valor": arredondar(sum(_valor(p) for p in ps), 2)}
                for nome, ps in grupos.items()
            ),
            key=lambda r: r["valor"],
            reverse=True,
        )

        por_situacao: dict[str, dict[str, Any]] = defaultdict(lambda: {"planos": 0, "valor": 0.0})
        for p in planos:
            s = por_situacao[(p.get("situacao_plano_acao") or "(sem situação)")]
            s["planos"] += 1
            s["valor"] += _valor(p)

        filtros = {k: v for k, v in {"uf": uf, "situacao": situacao, "parlamentar": parlamentar, "beneficiario": beneficiario}.items() if v}
        return {
            "ano": ano,
            "agrupado_por": _AGRUPAMENTOS[por][1],
            "filtros": filtros or None,
            "planos": len(planos),
            "valor_total": arredondar(sum(_valor(p) for p in planos), 2),
            "valor_custeio": arredondar(sum(float(p.get("valor_custeio_plano_acao") or 0) for p in planos), 2),
            "valor_investimento": arredondar(sum(float(p.get("valor_investimento_plano_acao") or 0) for p in planos), 2),
            "por_situacao": {k: {"planos": v["planos"], "valor": arredondar(v["valor"], 2)} for k, v in por_situacao.items()},
            "grupos": len(ranking),
            "ranking": ranking[:limite],
            "fonte": tg.ROTULO,
            "url": url,
            "consultado_em": carimbo(),
            "notas": [
                "Valores previstos nos planos de ação (custeio + investimento); não indicam quanto já foi pago.",
                "Situação IMPEDIDO indica plano com pendência ou rejeição; os valores também entram no total.",
            ],
        }
