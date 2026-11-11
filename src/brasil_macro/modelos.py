"""Tipos e utilitários de data compartilhados."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

PERIODICIDADES = ("diaria", "mensal", "trimestral", "anual")

Observacao = tuple[date, float | None]

LIMITE_PADRAO = 240  # máximo de observações devolvidas numa resposta de ferramenta


def rotulo_periodo(d: date, periodicidade: str) -> str:
    """Texto curto do período: 2026-11-06, 2026-09, 2026-T2 ou 2026."""
    if periodicidade == "mensal":
        return f"{d.year}-{d.month:02d}"
    if periodicidade == "trimestral":
        return f"{d.year}-T{(d.month - 1) // 3 + 1}"
    if periodicidade == "anual":
        return str(d.year)
    return d.isoformat()


def deslocar_meses(d: date, meses: int) -> date:
    total = d.year * 12 + (d.month - 1) + meses
    ano, mes0 = divmod(total, 12)
    dia = min(d.day, calendar.monthrange(ano, mes0 + 1)[1])
    return date(ano, mes0 + 1, dia)


def _fim_do_mes(ano: int, mes: int) -> date:
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def interpretar_data(texto: str, *, fim: bool = False) -> date:
    """Converte texto em data.

    Aceita AAAA, AAAA-MM, AAAA-MM-DD, AAAA-T1..T4, MM/AAAA e DD/MM/AAAA.
    Para períodos (ano, mês, trimestre), devolve o primeiro dia, ou o último
    quando `fim` é verdadeiro.
    """
    t = texto.strip()
    try:
        if m := re.fullmatch(r"(\d{4})", t):
            ano = int(m[1])
            return date(ano, 12, 31) if fim else date(ano, 1, 1)
        if m := re.fullmatch(r"(\d{4})-?[TtQq]([1-4])", t):
            ano, tri = int(m[1]), int(m[2])
            return _fim_do_mes(ano, tri * 3) if fim else date(ano, tri * 3 - 2, 1)
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})", t):
            ano, mes = int(m[1]), int(m[2])
            return _fim_do_mes(ano, mes) if fim else date(ano, mes, 1)
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := re.fullmatch(r"(\d{1,2})/(\d{4})", t):
            ano, mes = int(m[2]), int(m[1])
            return _fim_do_mes(ano, mes) if fim else date(ano, mes, 1)
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t):
            return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError as erro:
        raise ValueError(f"Data inválida: {texto!r} ({erro}).") from None
    raise ValueError(
        f"Não entendi a data {texto!r}. Use AAAA, AAAA-MM, AAAA-MM-DD, AAAA-T1 a T4, MM/AAAA ou DD/MM/AAAA."
    )


def data_opcional(texto: str | None, *, fim: bool = False) -> date | None:
    return interpretar_data(texto, fim=fim) if texto and texto.strip() else None


@dataclass
class Serie:
    """Série temporal com a procedência necessária para citar a fonte."""

    nome: str
    fonte: str
    codigo: str
    unidade: str
    periodicidade: str
    observacoes: list[Observacao]
    url: str = ""
    consultado_em: datetime | None = None
    id: str | None = None
    notas: list[str] = field(default_factory=list)

    def recortar(self, inicio: date | None, fim: date | None) -> None:
        self.observacoes = [
            (d, v) for d, v in self.observacoes if (inicio is None or d >= inicio) and (fim is None or d <= fim)
        ]

    def para_dict(self, limite: int | None = LIMITE_PADRAO) -> dict:
        obs = self.observacoes
        notas = list(self.notas)
        total = len(obs)
        if limite is not None and total > limite:
            obs = obs[-limite:]
            notas.append(
                f"Série truncada: exibidas as últimas {limite} de {total} observações. "
                "Restrinja o período com inicio e fim para ver o restante."
            )
        saida: dict = {}
        if self.id:
            saida["indicador"] = self.id
        saida.update(
            nome=self.nome,
            fonte=self.fonte,
            codigo=self.codigo,
            unidade=self.unidade,
            periodicidade=self.periodicidade,
            n_observacoes=len(obs),
        )
        if obs:
            saida["inicio"] = rotulo_periodo(obs[0][0], self.periodicidade)
            saida["fim"] = rotulo_periodo(obs[-1][0], self.periodicidade)
        saida["dados"] = [[rotulo_periodo(d, self.periodicidade), v] for d, v in obs]
        if self.url:
            saida["url_fonte"] = self.url
        momento = self.consultado_em or datetime.now(timezone.utc)
        saida["consultado_em"] = momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        if notas:
            saida["notas"] = notas
        return saida
