"""Ferramentas genéricas sobre o catálogo: busca, série, comparação e painel."""

from __future__ import annotations

import asyncio
from bisect import bisect_right
from datetime import date, timedelta
from typing import Any

from .. import catalogo
from ..catalogo import Indicador
from ..modelos import Serie, data_opcional, deslocar_meses, rotulo_periodo
from ..transformacoes import AGREGACOES, FOLGA_DIAS, TRANSFORMACOES, agregar, aplicar, mais_baixa
from ._comum import arredondar, ferramenta, tabela

_PADRAO_ULTIMOS = {"diaria": 30, "mensal": 24, "trimestral": 12, "anual": 10}
_DIAS_POR_PERIODO = {"diaria": 1.5, "mensal": 31, "trimestral": 92, "anual": 366}


async def carregar_serie(
    ind: Indicador,
    inicio: date | None = None,
    fim: date | None = None,
    ultimos: int | None = None,
    transformacao: str | None = None,
) -> Serie:
    """Busca a série do indicador já recortada e transformada conforme o pedido."""
    if transformacao and transformacao not in TRANSFORMACOES:
        raise ValueError(f"Transformação desconhecida: {transformacao!r}. Opções: {', '.join(TRANSFORMACOES)}.")
    if ultimos is not None and ultimos < 1:
        raise ValueError("ultimos deve ser um inteiro positivo.")
    per = ind.periodicidade
    if inicio is None and ultimos is None:
        ultimos = _PADRAO_ULTIMOS[per]

    inicio_busca = inicio
    if inicio_busca is None:
        referencia = fim or date.today()
        inicio_busca = referencia - timedelta(days=int((ultimos + 3) * _DIAS_POR_PERIODO[per]) + 15)
    if transformacao and transformacao != "base_100":
        inicio_busca -= timedelta(days=FOLGA_DIAS[per])

    serie = await catalogo.obter_dados(ind, inicio_busca, fim)

    def recortar() -> None:
        serie.recortar(inicio, fim)
        if ultimos:
            serie.observacoes = serie.observacoes[-ultimos:]

    if transformacao == "base_100":
        recortar()  # a base é a primeira observação do período pedido
    if transformacao:
        obs, serie.unidade = aplicar(transformacao, serie.observacoes, per, serie.unidade)
        serie.observacoes = [(d, arredondar(v)) for d, v in obs]
        serie.notas.append(f"Transformação aplicada: {transformacao} — {TRANSFORMACOES[transformacao]}.")
    recortar()
    return serie


def _resumo(ind: Indicador) -> dict[str, Any]:
    item = {
        "id": ind.id,
        "nome": ind.nome,
        "tema": ind.tema,
        "unidade": ind.unidade,
        "periodicidade": ind.periodicidade,
        "fonte": catalogo.rotulo_fonte(ind),
        "codigo": ind.codigo,
    }
    if ind.notas:
        item["notas"] = ind.notas
    return item


_COLUNAS_CATALOGO = ["id", "nome", "tema", "unidade", "periodicidade", "fonte", "codigo"]
_COLUNAS_PAINEL = ["indicador", "nome", "unidade", "periodo", "valor", "anterior", "ha_12_meses", "fonte", "codigo"]


def _notas_de(indicadores: list[Indicador]) -> dict[str, list[str]]:
    """Notas do catálogo só dos indicadores que têm alguma, fora da tabela."""
    return {i.id: i.notas for i in indicadores if i.notas}


def _valor_ha_12_meses(serie: Serie) -> float | None:
    obs = [(d, v) for d, v in serie.observacoes if v is not None]
    if not obs:
        return None
    alvo = deslocar_meses(obs[-1][0], -12)
    datas = [d for d, _ in obs]
    i = bisect_right(datas, alvo) - 1
    if i < 0:
        return None
    tolerancia = 10 if serie.periodicidade == "diaria" else 0
    return obs[i][1] if (alvo - datas[i]).days <= tolerancia else None


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def buscar_indicadores(
        consulta: str = "", tema: str | None = None, fonte: str | None = None, limite: int = 15
    ) -> dict[str, Any]:
        """Localiza indicadores no catálogo curado de dados oficiais do Brasil.

        Comece por aqui para descobrir o `id` a usar em obter_serie, comparar_series
        e painel. Sem argumentos, devolve os temas disponíveis e quantos
        indicadores cada um tem.

        Args:
            consulta: palavras-chave, ex.: "selic", "ipca serviços", "dívida bruta".
            tema: restringe a um tema (ex.: inflacao, fiscal, credito).
            fonte: restringe a uma fonte (ex.: BCB, IBGE, Tesouro, IPEA).
            limite: máximo de resultados (padrão 15).
        """
        if not consulta.strip() and not tema and not fonte:
            contagem: dict[str, int] = {}
            for ind in catalogo.todos():
                contagem[ind.tema] = contagem.get(ind.tema, 0) + 1
            return {
                "temas": [
                    {"tema": t, "titulo": titulo, "indicadores": contagem.get(t, 0)}
                    for t, titulo in catalogo.temas().items()
                ],
                "total_indicadores": sum(contagem.values()),
                "dica": "Informe consulta e/ou tema para listar os indicadores.",
            }
        achados = catalogo.buscar(consulta, tema, fonte)
        mostrados = achados[: max(1, limite)]
        saida: dict[str, Any] = {
            "total_encontrado": len(achados),
            **tabela(_COLUNAS_CATALOGO, [[r[c] for c in _COLUNAS_CATALOGO] for r in map(_resumo, mostrados)]),
        }
        notas = _notas_de(mostrados)
        if notas:
            saida["notas_indicadores"] = notas
        if not achados:
            saida["dica"] = (
                "Nada no catálogo. Tente outros termos, ou use sgs_serie (qualquer série do BCB) "
                "e sidra_consulta (qualquer tabela do IBGE) para dados fora do catálogo."
            )
        return saida

    @ferramenta(servidor)
    async def obter_serie(
        indicador: str,
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        transformacao: str | None = None,
    ) -> dict[str, Any]:
        """Série histórica de um indicador do catálogo, com fonte, unidade e código.

        Sem período, traz as observações mais recentes (30 dias úteis, 24 meses, 12 trimestres ou 10 anos).

        Args:
            indicador: id do catálogo (buscar_indicadores), ex.: "selic_meta", "ipca".
            inicio, fim: AAAA, AAAA-MM, AAAA-MM-DD ou AAAA-T1.
            ultimos: observações mais recentes.
            transformacao: variacao_periodo, variacao_anual, diferenca, acumulado_12m, acumulado_ano, soma_12m, media_movel_3m, media_movel_12m, anualizado, mm3m_anualizada ou base_100. Acumulados e anualizados valem para séries em variação % (ex.: ipca); soma_12m, para fluxos."""
        ind = catalogo.obter_indicador(indicador)
        serie = await carregar_serie(
            ind, data_opcional(inicio), data_opcional(fim, fim=True), ultimos, transformacao
        )
        return serie.para_dict()

    @ferramenta(servidor)
    async def comparar_series(
        indicadores: list[str],
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        transformacao: str | None = None,
        agregacao: str | None = None,
    ) -> dict[str, Any]:
        """Indicadores do catálogo lado a lado, alinhados por período (2 a 12).

        As periodicidades precisam ser iguais, a menos que `agregacao` seja informada: aí as séries mais frequentes são convertidas para a mais baixa do grupo.

        Args:
            indicadores: lista de ids do catálogo.
            inicio, fim, ultimos: recorte, como em obter_serie.
            transformacao: mesma lista de obter_serie, aplicada a todas.
            agregacao: "ultimo" (taxas e estoques), "media" ou "soma" (fluxos)."""
        if not 1 <= len(indicadores) <= 12:
            raise ValueError("Informe de 1 a 12 indicadores.")
        if agregacao and agregacao not in AGREGACOES:
            raise ValueError(f"Agregação desconhecida: {agregacao!r}. Opções: {', '.join(AGREGACOES)}.")
        inds = [catalogo.obter_indicador(i) for i in indicadores]
        periodicidades = sorted({i.periodicidade for i in inds})
        alvo = mais_baixa(periodicidades)
        if len(periodicidades) > 1 and not agregacao:
            raise ValueError(
                f"Os indicadores têm periodicidades diferentes ({', '.join(periodicidades)}). "
                'Informe agregacao ("ultimo", "media" ou "soma") para converter todos para a mais baixa.'
            )
        d_ini, d_fim = data_opcional(inicio), data_opcional(fim, fim=True)
        if d_ini is None and ultimos is None:
            ultimos = _PADRAO_ULTIMOS[alvo]
        if d_ini is None:
            # Janela comum em datas, para séries de frequências diferentes cobrirem o mesmo intervalo.
            referencia = d_fim or date.today()
            d_ini_busca = referencia - timedelta(days=int((ultimos + 3) * _DIAS_POR_PERIODO[alvo]) + 15)
        else:
            d_ini_busca = d_ini
        series = await asyncio.gather(
            *(carregar_serie(i, d_ini_busca, d_fim, None, None) for i in inds), return_exceptions=True
        )
        colunas: list[dict[str, Any]] = []
        tabelas: list[dict[date, float | None]] = []
        falhas: list[dict[str, str]] = []
        notas: list[str] = []
        for ind, serie in zip(inds, series):
            if isinstance(serie, BaseException):
                falhas.append({"indicador": ind.id, "erro": str(serie)})
                continue
            obs, unidade = serie.observacoes, serie.unidade
            if ind.periodicidade != alvo:
                obs = agregar(obs, ind.periodicidade, alvo, agregacao or "ultimo")
                notas.append(f"{ind.id}: convertido de {ind.periodicidade} para {alvo} por {agregacao}.")
            if transformacao:
                if transformacao != "base_100":
                    extra = await carregar_serie(
                        ind, d_ini_busca - timedelta(days=FOLGA_DIAS[ind.periodicidade]), d_fim, None, None
                    )
                    obs = extra.observacoes
                    if ind.periodicidade != alvo:
                        obs = agregar(obs, ind.periodicidade, alvo, agregacao or "ultimo")
                obs, unidade = aplicar(transformacao, obs, alvo, unidade)
                obs = [(d, arredondar(v)) for d, v in obs if d >= d_ini_busca]
            colunas.append(
                {"indicador": ind.id, "nome": ind.nome, "unidade": unidade, "fonte": serie.fonte, "codigo": serie.codigo}
            )
            tabelas.append(dict(obs))
        datas = sorted({d for t in tabelas for d in t if (d_ini is None or d >= d_ini)})
        if ultimos:
            datas = datas[-ultimos:]
        saida: dict[str, Any] = {
            "periodicidade": alvo,
            "colunas": colunas,
            "linhas": [[rotulo_periodo(d, alvo)] + [t.get(d) for t in tabelas] for d in datas],
        }
        if transformacao:
            notas.append(f"Transformação aplicada: {transformacao} — {TRANSFORMACOES[transformacao]}.")
        if notas:
            saida["notas"] = notas
        if falhas:
            saida["falhas"] = falhas
        return saida

    @ferramenta(servidor)
    async def painel(tema: str = "geral") -> dict[str, Any]:
        """Retrato rápido de um tema: último valor, valor anterior e valor de 12 meses antes.

        Args:
            tema: "geral" (principais indicadores do país) ou um tema do catálogo,
                ex.: politica_monetaria, inflacao, atividade, trabalho, fiscal,
                setor_externo, credito. Use buscar_indicadores sem argumentos para ver a lista.
        """
        alvo = catalogo.normalizar(tema).replace(" ", "_")
        if alvo == "geral":
            inds = [i for i in catalogo.todos() if i.painel_geral]
            titulo = "Principais indicadores"
        else:
            do_tema = [i for i in catalogo.todos() if i.tema == alvo]
            if not do_tema:
                raise ValueError(f"Tema {tema!r} não existe. Temas: geral, {', '.join(catalogo.temas())}.")
            inds = [i for i in do_tema if i.destaque] or do_tema[:15]
            titulo = do_tema[0].tema_titulo
        hoje = date.today()
        folga = {"diaria": 400, "mensal": 500, "trimestral": 800, "anual": 1500}
        series = await asyncio.gather(
            *(catalogo.obter_dados(i, hoje - timedelta(days=folga[i.periodicidade]), None) for i in inds),
            return_exceptions=True,
        )
        linhas: list[dict[str, Any]] = []
        falhas: list[dict[str, str]] = []
        for ind, serie in zip(inds, series):
            if isinstance(serie, BaseException):
                falhas.append({"indicador": ind.id, "erro": str(serie)})
                continue
            obs = [(d, v) for d, v in serie.observacoes if v is not None]
            if not obs:
                falhas.append({"indicador": ind.id, "erro": "sem observações recentes"})
                continue
            linha: dict[str, Any] = {
                "indicador": ind.id,
                "nome": ind.nome,
                "unidade": ind.unidade,
                "periodo": rotulo_periodo(obs[-1][0], ind.periodicidade),
                "valor": obs[-1][1],
                "anterior": obs[-2][1] if len(obs) > 1 else None,
                "ha_12_meses": _valor_ha_12_meses(serie),
                "fonte": serie.fonte,
                "codigo": serie.codigo,
            }
            linhas.append(linha)
        saida: dict[str, Any] = {
            "tema": alvo,
            "titulo": titulo,
            **tabela(_COLUNAS_PAINEL, [[l[c] for c in _COLUNAS_PAINEL] for l in linhas]),
        }
        notas = _notas_de([i for i in inds if any(l["indicador"] == i.id for l in linhas)])
        if notas:
            saida["notas_indicadores"] = notas
        if falhas:
            saida["falhas"] = falhas
        return saida
