"""Transformações de séries: variações, acumulados, médias móveis e mudança de frequência."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta

from .modelos import Observacao, deslocar_meses, rotulo_periodo

_PASSO_MESES = {"mensal": 1, "trimestral": 3, "anual": 12}

TRANSFORMACOES: dict[str, str] = {
    "variacao_periodo": "variação % contra o período imediatamente anterior",
    "variacao_anual": "variação % contra o mesmo período do ano anterior",
    "diferenca": "diferença absoluta contra o período anterior",
    "acumulado_12m": "taxas % compostas nos últimos 12 meses (para séries de variação %)",
    "acumulado_ano": "taxas % compostas desde o início do ano (para séries de variação %)",
    "soma_12m": "soma dos últimos 12 meses (para fluxos)",
    "media_movel_3m": "média móvel de 3 meses",
    "media_movel_12m": "média móvel de 12 meses",
    "anualizado": "taxa % do período convertida para taxa anual composta",
    "mm3m_anualizada": "taxas % dos últimos 3 meses compostas e anualizadas",
    "base_100": "índice com base 100 na primeira observação do período pedido",
}

# Quanto histórico extra buscar antes do início pedido, em dias.
FOLGA_DIAS = {"diaria": 380, "mensal": 400, "trimestral": 460, "anual": 740}


def _validas(obs: list[Observacao]) -> list[tuple[date, float]]:
    return [(d, v) for d, v in obs if v is not None]


def _composta(valores: list[float]) -> float:
    fator = 1.0
    for v in valores:
        fator *= 1 + v / 100
    return (fator - 1) * 100


def _janela(mapa: dict[date, float], d: date, n: int, passo: int) -> list[float] | None:
    """Valores dos n períodos que terminam em d; None se faltar algum."""
    valores = []
    for i in range(n):
        v = mapa.get(deslocar_meses(d, -i * passo))
        if v is None:
            return None
        valores.append(v)
    return valores


def aplicar(nome: str, obs: list[Observacao], periodicidade: str, unidade: str) -> tuple[list[Observacao], str]:
    """Aplica a transformação e devolve (observações, nova unidade)."""
    if nome not in TRANSFORMACOES:
        raise ValueError(f"Transformação desconhecida: {nome!r}. Opções: {', '.join(TRANSFORMACOES)}.")
    dados = _validas(obs)
    if nome == "base_100":
        if not dados or dados[0][1] == 0:
            raise ValueError("base_100 exige uma primeira observação diferente de zero.")
        base_d, base_v = dados[0]
        return (
            [(d, v / base_v * 100) for d, v in dados],
            f"índice ({rotulo_periodo(base_d, periodicidade)} = 100)",
        )
    if periodicidade == "diaria":
        return _aplicar_diaria(nome, dados, unidade)
    if periodicidade not in _PASSO_MESES:
        raise ValueError(f"Periodicidade sem suporte a transformações: {periodicidade!r}.")
    return _aplicar_regular(nome, dados, periodicidade, unidade)


def _aplicar_regular(
    nome: str, dados: list[tuple[date, float]], periodicidade: str, unidade: str
) -> tuple[list[Observacao], str]:
    passo = _PASSO_MESES[periodicidade]
    por_ano = 12 // passo
    mapa = dict(dados)
    saida: list[Observacao] = []

    def contra(d: date, v: float, meses: int, relativa: bool) -> float | None:
        anterior = mapa.get(deslocar_meses(d, -meses))
        if anterior is None:
            return None
        if not relativa:
            return v - anterior
        return (v / anterior - 1) * 100 if anterior else None

    for d, v in dados:
        r: float | None
        if nome == "variacao_periodo":
            r = contra(d, v, passo, True)
        elif nome == "variacao_anual":
            r = contra(d, v, 12, True)
        elif nome == "diferenca":
            r = contra(d, v, passo, False)
        elif nome == "acumulado_12m":
            j = _janela(mapa, d, por_ano, passo)
            r = _composta(j) if j else None
        elif nome == "acumulado_ano":
            j = _janela(mapa, d, (d.month - 1) // passo + 1, passo)
            r = _composta(j) if j else None
        elif nome == "soma_12m":
            j = _janela(mapa, d, por_ano, passo)
            r = sum(j) if j else None
        elif nome == "media_movel_12m":
            j = _janela(mapa, d, por_ano, passo)
            r = sum(j) / len(j) if j else None
        elif nome == "media_movel_3m":
            if periodicidade != "mensal":
                raise ValueError("media_movel_3m só se aplica a séries mensais.")
            j = _janela(mapa, d, 3, 1)
            r = sum(j) / 3 if j else None
        elif nome == "anualizado":
            r = ((1 + v / 100) ** por_ano - 1) * 100
        elif nome == "mm3m_anualizada":
            if periodicidade != "mensal":
                raise ValueError("mm3m_anualizada só se aplica a séries mensais.")
            j = _janela(mapa, d, 3, 1)
            r = ((1 + _composta(j) / 100) ** 4 - 1) * 100 if j else None
        else:  # pragma: no cover - nomes já validados em aplicar()
            raise ValueError(nome)
        if r is not None:
            saida.append((d, r))
    return saida, _unidade(nome, unidade)


def _aplicar_diaria(nome: str, dados: list[tuple[date, float]], unidade: str) -> tuple[list[Observacao], str]:
    datas = [d for d, _ in dados]
    saida: list[Observacao] = []
    if nome in ("variacao_periodo", "diferenca"):
        for (_, antes), (d, v) in zip(dados, dados[1:]):
            if nome == "diferenca":
                saida.append((d, v - antes))
            elif antes:
                saida.append((d, (v / antes - 1) * 100))
    elif nome == "variacao_anual":
        for d, v in dados:
            alvo = deslocar_meses(d, -12)
            i = bisect_right(datas, alvo) - 1
            if i >= 0 and (alvo - datas[i]).days <= 10 and dados[i][1]:
                saida.append((d, (v / dados[i][1] - 1) * 100))
    elif nome in ("acumulado_12m", "acumulado_ano"):
        if dados:
            primeira = datas[0]
            for i, (d, _) in enumerate(dados):
                inicio = deslocar_meses(d, -12) if nome == "acumulado_12m" else date(d.year, 1, 1) - timedelta(days=1)
                if primeira > inicio + timedelta(days=10):
                    continue  # o histórico não cobre a janela inteira
                j = bisect_right(datas, inicio)
                saida.append((d, _composta([v for _, v in dados[j : i + 1]])))
    elif nome == "anualizado":
        saida = [(d, ((1 + v / 100) ** 252 - 1) * 100) for d, v in dados]
    else:
        raise ValueError(f"A transformação {nome!r} não se aplica a séries diárias.")
    return saida, _unidade(nome, unidade)


def _unidade(nome: str, unidade: str) -> str:
    if nome in ("variacao_periodo", "variacao_anual", "acumulado_12m", "acumulado_ano"):
        return "%"
    if nome in ("anualizado", "mm3m_anualizada"):
        return "% a.a."
    if nome == "diferenca":
        return f"{unidade} (diferença)"
    return unidade


# ------------------------------------------------------- mudança de frequência

_ORDEM = {"diaria": 0, "mensal": 1, "trimestral": 2, "anual": 3}
AGREGACOES = ("ultimo", "media", "soma")


def inicio_do_periodo(d: date, periodicidade: str) -> date:
    if periodicidade == "mensal":
        return date(d.year, d.month, 1)
    if periodicidade == "trimestral":
        return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
    if periodicidade == "anual":
        return date(d.year, 1, 1)
    return d


def mais_baixa(periodicidades: list[str]) -> str:
    return max(periodicidades, key=lambda p: _ORDEM[p])


def agregar(obs: list[Observacao], origem: str, destino: str, metodo: str) -> list[Observacao]:
    """Converte para uma frequência mais baixa (ex.: diária para mensal)."""
    if metodo not in AGREGACOES:
        raise ValueError(f"Agregação desconhecida: {metodo!r}. Opções: {', '.join(AGREGACOES)}.")
    if _ORDEM[destino] < _ORDEM[origem]:
        raise ValueError(f"Não é possível converter de {origem} para {destino} (frequência mais alta).")
    if origem == destino:
        return list(obs)
    grupos: dict[date, list[float]] = {}
    for d, v in _validas(obs):
        grupos.setdefault(inicio_do_periodo(d, destino), []).append(v)
    saida: list[Observacao] = []
    for d in sorted(grupos):
        valores = grupos[d]
        if metodo == "ultimo":
            saida.append((d, valores[-1]))
        elif metodo == "media":
            saida.append((d, sum(valores) / len(valores)))
        else:
            saida.append((d, sum(valores)))
    return saida
