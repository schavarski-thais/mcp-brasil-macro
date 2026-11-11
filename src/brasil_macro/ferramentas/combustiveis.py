"""Ferramenta de preços de combustíveis nos postos (ANP)."""

from __future__ import annotations

from typing import Any

from ..catalogo import normalizar
from ..fontes import anp
from ._comum import arredondar, carimbo, ferramenta

LIMITE_MAXIMO = 50


def _rotulo_mes(ano: int, mes: int) -> str:
    return f"{mes:02d}/{ano}"


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def combustiveis_precos(
        produto: str = "gasolina",
        ano: int | None = None,
        mes: int | None = None,
        uf: str | None = None,
        municipio: str | None = None,
        bandeira: str | None = None,
        limite: int = 10,
    ) -> dict[str, Any]:
        """Preço de combustíveis ao consumidor nos postos (ANP, levantamento mensal).

        Sem UF: média nacional e ranking por UF (mais barato primeiro). Com UF: ranking dos municípios.
        Com UF e município: os postos mais baratos. Produtos: gasolina, gasolina_aditivada, etanol,
        diesel (S500), diesel_s10, gnv, glp. Sem ano e mês, usa o último mês publicado. Os valores são
        médias simples das coletas, sem ponderar pelo volume vendido. Para uma série mensal da média
        nacional, use os indicadores de combustíveis do catálogo.
        """
        if not 1 <= limite <= LIMITE_MAXIMO:
            raise ValueError(f"limite deve estar entre 1 e {LIMITE_MAXIMO}.")
        if (ano is None) != (mes is None):
            raise ValueError("Informe ano e mês juntos, ou nenhum dos dois para o último mês publicado.")
        if municipio and not uf:
            raise ValueError("Informe a UF junto com o município, porque há cidades homônimas em estados diferentes.")
        anp.resolver_produto(produto)  # valida o nome antes de baixar qualquer arquivo

        if ano is None or mes is None:
            ano, mes = await anp.ultimo_mes(produto)
        cs, url, consultado = await anp.coletas(produto, ano, mes)
        unidade = cs[0].unidade
        notas = [
            "Média simples de todas as coletas do período, sem ponderar pelo volume vendido por posto.",
            "Postos: estabelecimentos distintos (revenda, bairro, município e UF).",
        ]

        escopo = "Brasil"
        if uf:
            sigla = uf.strip().upper()
            cs = [c for c in cs if c.uf == sigla]
            if not cs:
                raise ValueError(f"Sem coletas de {produto} na UF {sigla} em {_rotulo_mes(ano, mes)}.")
            escopo = f"UF {sigla}"
        if municipio:
            alvo = normalizar(municipio)
            cs = [c for c in cs if normalizar(c.municipio) == alvo]
            if not cs:
                raise ValueError(f"Sem coletas de {produto} em {municipio}/{uf.strip().upper()} em {_rotulo_mes(ano, mes)}.")
            escopo = f"{cs[0].municipio}/{cs[0].uf}"
        if bandeira:
            termo = normalizar(bandeira)
            cs = [c for c in cs if termo in normalizar(c.bandeira)]
            if not cs:
                raise ValueError(f"Sem coletas de {produto} da bandeira {bandeira!r} no escopo pedido em {_rotulo_mes(ano, mes)}.")
            notas.append(f"Filtrado pela bandeira que contém {bandeira!r}.")

        datas = sorted(c.data for c in cs)
        resposta: dict[str, Any] = {
            "produto": produto,
            "unidade": unidade,
            "periodo": _rotulo_mes(ano, mes),
            "coletas_entre": [datas[0].isoformat(), datas[-1].isoformat()],
            "escopo": escopo,
            "resumo": anp.resumir(cs),
            "fonte": anp.ROTULO,
            "url": url,
            "consultado_em": carimbo(consultado),
            "notas": notas,
        }

        if not uf:
            por_uf = anp.agrupar(cs, lambda c: c.uf)
            resposta["por_uf"] = sorted(
                ({"uf": sigla, **anp.resumir(grupo)} for sigla, grupo in por_uf.items()),
                key=lambda r: r["media"],
            )
        elif not municipio:
            por_municipio = anp.agrupar(cs, lambda c: c.municipio)
            ranking = sorted(
                ({"municipio": nome, **anp.resumir(grupo)} for nome, grupo in por_municipio.items()),
                key=lambda r: r["media"],
            )
            resposta["municipios"] = len(ranking)
            resposta["por_municipio"] = ranking[:limite]
        else:
            postos = sorted(cs, key=lambda c: (c.valor, c.revenda))
            vistos: set[tuple[str, str]] = set()
            ranking_postos = []
            for c in postos:
                chave = (c.revenda, c.bairro)
                if chave in vistos:
                    continue  # mesmo posto em outra coleta: fica só o preço mais baixo
                vistos.add(chave)
                ranking_postos.append(
                    {
                        "revenda": c.revenda,
                        "bairro": c.bairro,
                        "bandeira": c.bandeira,
                        "valor": arredondar(c.valor, 2),
                        "data": c.data.isoformat(),
                    }
                )
            resposta["postos_distintos"] = len(ranking_postos)
            resposta["postos_mais_baratos"] = ranking_postos[:limite]
        return resposta
