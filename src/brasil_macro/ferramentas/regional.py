"""Indicadores por Unidade da Federação (SIDRA, nível n3): desocupação, rendimento, massa, ocupação e PIB."""

from __future__ import annotations

from typing import Any

from ..catalogo import normalizar
from ..fontes import ibge
from ._comum import arredondar, carimbo, ferramenta, tabela

_NIVEL_UF = "n3"  # no SIDRA, n2 são as Grandes Regiões e n3 são as Unidades da Federação
_PERIODOS_MAXIMOS = 12

# Tabelas do SIDRA validadas com corte por UF. Cada entrada informa a variável e as
# classificações fixas (ex.: sexo total) que o SIDRA exige na consulta.
INDICADORES_UF: dict[str, dict[str, Any]] = {
    "desocupacao": {
        "nome": "Taxa de desocupação, pessoas de 14 anos ou mais (PNAD Contínua)",
        "tabela": 4099,
        "variavel": 4099,
        "classificacoes": None,
        "unidade": "%",
        "periodicidade": "trimestral",
        "fonte": "IBGE/PNAD Contínua (SIDRA, tabela 4099)",
    },
    "rendimento": {
        "nome": "Rendimento médio mensal real do trabalho principal, habitualmente recebido (PNAD Contínua)",
        "tabela": 5436,
        "variavel": 5932,
        "classificacoes": {2: 6794},  # sexo: total
        "unidade": "R$ (real, mensal)",
        "periodicidade": "trimestral",
        "fonte": "IBGE/PNAD Contínua (SIDRA, tabela 5436)",
    },
    "massa_rendimento": {
        "nome": "Massa de rendimento mensal real de todos os trabalhos (PNAD Contínua)",
        "tabela": 5606,
        "variavel": 6293,
        "classificacoes": None,
        "unidade": "milhões de R$ (real, mensal)",
        "periodicidade": "trimestral",
        "fonte": "IBGE/PNAD Contínua (SIDRA, tabela 5606)",
    },
    "ocupados": {
        "nome": "Pessoas de 14 anos ou mais ocupadas na semana de referência (PNAD Contínua)",
        "tabela": 4097,
        "variavel": 4090,
        "classificacoes": {11913: 96165},  # posição na ocupação: total
        "unidade": "mil pessoas",
        "periodicidade": "trimestral",
        "fonte": "IBGE/PNAD Contínua (SIDRA, tabela 4097)",
    },
    "pib": {
        "nome": "PIB a preços correntes, total (Contas Regionais, referência 2010)",
        "tabela": 5938,
        "variavel": 37,
        "classificacoes": None,
        "unidade": "mil R$ (preços correntes)",
        "periodicidade": "anual",
        "fonte": "IBGE/Contas Regionais (SIDRA, tabela 5938)",
    },
}

# Siglas das UFs, para aceitar 'BA' além do nome.
SIGLAS_UF = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
    "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
    "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
    "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF",
}


def _ordenado(linhas: list[ibge.LinhaSidra]) -> dict[str, list[ibge.LinhaSidra]]:
    """Por território, as linhas em ordem cronológica."""
    por_uf: dict[str, list[ibge.LinhaSidra]] = {}
    for linha in linhas:
        por_uf.setdefault(linha.territorio_codigo, []).append(linha)
    for serie in por_uf.values():
        serie.sort(key=lambda l: l.periodo)
    return por_uf


def _casa(termo: str, codigo: str, nome: str) -> bool:
    if not termo:
        return True
    return termo == codigo or termo == SIGLAS_UF.get(codigo, "").lower() or termo in normalizar(nome)


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def indicadores_por_uf(
        indicador: str,
        uf: str | None = None,
        ordenar: str = "maior",
        periodos: int = 4,
    ) -> dict[str, Any]:
        """Indicador das 27 Unidades da Federação (IBGE), no último período divulgado, com comparação.

        `indicador`: desocupacao (PNAD, trimestral), rendimento (rendimento médio real, trimestral),
        massa_rendimento (trimestral), ocupados (trimestral) ou pib (PIB corrente por UF, anual).
        `uf` filtra por nome, sigla ou código (ex.: 'bahia', 'BA', '29'). `ordenar` é 'maior' ou 'menor'
        pelo valor atual. `periodos` (1 a 12) é quantos períodos recentes buscar. A resposta traz uma
        tabela com: uf, codigo, valor, anterior (período anterior), ano_anterior (mesmo período do ano
        anterior) e variacao (em pontos percentuais para taxas, em % para os demais).
        """
        chave = normalizar(indicador).replace(" ", "_")
        if chave not in INDICADORES_UF:
            raise ValueError(f"Indicador {indicador!r} inválido. Opções: {', '.join(INDICADORES_UF)}.")
        cfg = INDICADORES_UF[chave]
        if not 1 <= periodos <= _PERIODOS_MAXIMOS:
            raise ValueError(f"periodos deve estar entre 1 e {_PERIODOS_MAXIMOS}.")
        ordenar = ordenar.strip().lower()
        if ordenar not in ("maior", "menor"):
            raise ValueError("ordenar deve ser 'maior' ou 'menor'.")

        # Períodos extras para ter o anterior e o mesmo período do ano anterior.
        passo = 4 if cfg["periodicidade"] == "trimestral" else 1
        extra = passo + 1
        res = await ibge.consultar(
            cfg["tabela"], cfg["variavel"], f"last {periodos + extra}", cfg["classificacoes"], _NIVEL_UF, "all"
        )
        por_uf = _ordenado(res.linhas)
        if not por_uf:
            raise ValueError(f"O SIDRA não devolveu dados de {chave} por UF.")

        termo = normalizar(uf or "")
        ranking = []
        periodos_usados: dict[str, str] | None = None
        for codigo, serie in por_uf.items():
            nome = serie[-1].territorio
            if not _casa(termo, codigo, nome):
                continue
            ultimo = serie[-1]
            if ultimo.valor is None:
                continue
            anterior = serie[-2] if len(serie) >= 2 else None
            ano_anterior = serie[-1 - passo] if len(serie) > passo else None
            base = ano_anterior.valor if ano_anterior and ano_anterior.valor is not None else None
            if periodos_usados is None:
                periodos_usados = {
                    "atual": ultimo.periodo_nome,
                    "anterior": anterior.periodo_nome if anterior else None,
                    "ano_anterior": ano_anterior.periodo_nome if ano_anterior else None,
                }
            if base is None:
                variacao = None
            elif cfg["unidade"] == "%":
                variacao = arredondar(ultimo.valor - base, 2)
            else:
                variacao = arredondar((ultimo.valor / base - 1) * 100, 2) if base else None
            ranking.append(
                [
                    nome,
                    codigo,
                    arredondar(ultimo.valor, 2),
                    arredondar(anterior.valor, 2) if anterior and anterior.valor is not None else None,
                    arredondar(base, 2),
                    variacao,
                ]
            )

        if not ranking:
            raise ValueError("Nenhuma UF encontrada com esse filtro." if termo else f"Sem dados válidos de {chave} por UF.")
        ranking.sort(key=lambda r: r[2], reverse=(ordenar == "maior"))
        tipo_variacao = "pontos percentuais" if cfg["unidade"] == "%" else "%"
        return {
            "indicador": chave,
            "nome": cfg["nome"],
            "fonte": cfg["fonte"],
            "url": res.url,
            "consultado_em": carimbo(res.consultado_em),
            "unidade": cfg["unidade"],
            "periodicidade": cfg["periodicidade"],
            "periodos": periodos_usados,
            "ordenado_por": f"valor atual, {ordenar} primeiro",
            "variacao_em": tipo_variacao,
            **tabela(["uf", "codigo", "valor", "anterior", "ano_anterior", "variacao"], ranking),
            "notas": _notas(chave),
        }


def _notas(chave: str) -> list[str]:
    notas = {
        "desocupacao": [
            "Taxa de desocupação das pessoas de 14 anos ou mais, na semana de referência do trimestre.",
            "Valores de UF têm mais incerteza que o Brasil; o coeficiente de variação está na tabela 4099 do SIDRA.",
        ],
        "rendimento": ["Valores reais (deflacionados), total de sexos, trabalho principal habitualmente recebido."],
        "massa_rendimento": ["Massa = população ocupada com rendimento × rendimento médio, em valores reais."],
        "ocupados": ["Pessoas ocupadas, total de posições na ocupação."],
        "pib": [
            "PIB anual; a última edição das Contas Regionais tem dados até o ano indicado em 'periodos'.",
            "Valores em preços correntes, não deflacionados: a variação mistura inflação e crescimento real.",
        ],
    }
    return notas[chave]
