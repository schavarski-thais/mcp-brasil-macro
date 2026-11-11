"""Ferramenta de comércio exterior (MDIC/Secex — ComexStat)."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..fontes import comexstat as cx
from ..modelos import deslocar_meses, interpretar_data, rotulo_periodo
from ._comum import carimbo, ferramenta

_LIMITE_MAXIMO = 50
_GRADE_FILTROS = {"pais": "country", "bloco": "economicBlock", "uf": "state", "via": "via", "secao": "section", "capitulo": "chapter"}
_ROTULO_FILTRO = {"pais": "país", "bloco": "bloco econômico", "uf": "UF", "via": "via de transporte", "secao": "seção do SH", "capitulo": "capítulo do SH"}


def agregar(linhas: list[dict[str, Any]], detalhe: str | None) -> tuple[dict[str, list[float]], dict[date, list[float]]]:
    """Soma valor FOB e peso por item do detalhamento e por mês. Valores em unidades da API (US$ e kg)."""
    por_item: dict[str, list[float]] = {}
    por_mes: dict[date, list[float]] = {}
    for linha in linhas:
        fob, kg = cx.numero(linha.get("metricFOB")), cx.numero(linha.get("metricKG"))
        if detalhe:
            acumulado = por_item.setdefault(cx.nome_do_detalhe(linha, detalhe) or "(sem nome)", [0.0, 0.0])
            acumulado[0] += fob
            acumulado[1] += kg
        ano, mes = cx.periodo_da_linha(linha)
        if mes is not None:
            mensal = por_mes.setdefault(date(ano, mes, 1), [0.0, 0.0])
            mensal[0] += fob
            mensal[1] += kg
    return por_item, por_mes


def _milhoes(valor: float) -> float:
    return round(valor / 1e6, 2)


def _toneladas_mil(kg: float) -> float:
    return round(kg / 1e6, 1)  # mil toneladas


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def comercio_exterior(
        fluxo: str = "exportacao",
        por: str = "pais",
        inicio: str | None = None,
        fim: str | None = None,
        pais: str | None = None,
        bloco: str | None = None,
        uf: str | None = None,
        via: str | None = None,
        secao: str | None = None,
        capitulo: str | None = None,
        limite: int = 10,
        mensal: bool = False,
    ) -> dict[str, Any]:
        """Exportações, importações ou saldo do Brasil (MDIC/Secex, ComexStat), por país, bloco, UF, via, seção ou produto.

        Valores em US$ milhões (FOB) e peso em mil toneladas, somados no período; sem datas, usa os 12 meses fechados mais recentes. Para totais mensais como série, use obter_serie (tema comercio_exterior). O ComexStat recusa requisições de forma irregular: se recusar, tente de novo em instantes.

        Args:
            fluxo: "exportacao", "importacao" ou "saldo".
            por: "pais", "bloco", "uf", "via", "secao", "capitulo", "sh4" ou "nenhum" (só o total).
            inicio, fim: mês (AAAA ou AAAA-MM); ano inteiro: inicio="2025", fim="2025".
            pais, bloco, uf, via, secao, capitulo: filtros por nome ou código; pais aceita lista separada por vírgula.
            limite: itens do ranking (até 50); o restante vai em "demais".
            mensal: inclui os totais mês a mês."""
        tipo_fluxo = (fluxo or "").strip().lower()
        if tipo_fluxo in ("saldo", "balanca", "balanca comercial", "balança"):
            tipo_fluxo = "saldo"
        else:
            tipo_fluxo = cx.fluxo_api(tipo_fluxo)
        chave_por = (por or "nenhum").strip().lower().replace("ç", "c").replace("í", "i")
        if chave_por in ("nenhum", "total", "brasil", ""):
            detalhe = None
        elif chave_por in cx.DETALHES:
            detalhe = cx.DETALHES[chave_por]
        else:
            raise ValueError(f"Agrupamento {por!r} inválido. Use: {', '.join(cx.DETALHES)} ou nenhum.")
        if limite < 1:
            raise ValueError("limite deve ser um inteiro positivo.")
        limite = min(limite, _LIMITE_MAXIMO)

        ultimo = await cx.ultimo_mes_disponivel()
        d_fim = interpretar_data(fim, fim=True).replace(day=1) if fim else ultimo
        if inicio:
            d_ini = interpretar_data(inicio).replace(day=1)
        else:
            d_ini = deslocar_meses(d_fim, -11)
        if d_ini > d_fim:
            raise ValueError("A data inicial é posterior à final.")
        if d_fim > ultimo:
            d_fim = ultimo  # meses ainda não divulgados
            if d_ini > d_fim:
                raise ValueError(f"O ComexStat só tem dados até {rotulo_periodo(ultimo, 'mensal')}.")
        if d_ini.year < cx.PRIMEIRO_ANO:
            raise ValueError(f"O ComexStat começa em {cx.PRIMEIRO_ANO}.")

        pedidos = {"pais": pais, "bloco": bloco, "uf": uf, "via": via, "secao": secao, "capitulo": capitulo}
        filtros: dict[str, list[str]] = {}
        aplicados: dict[str, list[str]] = {}
        for chave, texto in pedidos.items():
            if not texto:
                continue
            nome_filtro = _GRADE_FILTROS[chave]
            opcoes = await cx.valores_do_filtro(nome_filtro)
            escolhidos = cx.escolher_valores(nome_filtro, texto, opcoes, _ROTULO_FILTRO[chave])
            filtros[nome_filtro] = [id_ for id_, _ in escolhidos]
            aplicados[chave] = [texto_ for _, texto_ in escolhidos]

        fluxos = ["export", "import"] if tipo_fluxo == "saldo" else [tipo_fluxo]
        detalhes = (detalhe,) if detalhe else ()
        por_item: dict[str, dict[str, list[float]]] = {}
        por_mes: dict[str, dict[date, list[float]]] = {}
        url, quando, desatualizado = "", None, False
        for f in fluxos:
            # Sempre mês a mês: o total do período é somado aqui, sem depender de como a API trata os limites do período.
            consulta = await cx.consultar_periodo(f, d_ini, d_fim, detalhes=detalhes, filtros=filtros)
            por_item[f], por_mes[f] = agregar(consulta.linhas, detalhe)
            url, quando = url or consulta.url, quando or consulta.consultado_em
            desatualizado = desatualizado or consulta.desatualizado

        def valor(fluxo_: str, item: str) -> list[float]:
            return por_item[fluxo_].get(item, [0.0, 0.0])

        if tipo_fluxo == "saldo":
            nomes = set(por_item["export"]) | set(por_item["import"])
            total_itens = {n: [valor("export", n)[0] - valor("import", n)[0], valor("export", n)[1] - valor("import", n)[1]] for n in nomes}
            ordenar = lambda par: par[1][0]  # noqa: E731 — saldo: do maior superávit ao maior déficit
        else:
            total_itens = por_item[tipo_fluxo]
            ordenar = lambda par: par[1][0]  # noqa: E731

        meses = sorted({m for f in fluxos for m in por_mes[f]})

        def total_mes(m: date) -> list[float]:
            if tipo_fluxo == "saldo":
                x, i = por_mes["export"].get(m, [0.0, 0.0]), por_mes["import"].get(m, [0.0, 0.0])
                return [x[0] - i[0], x[1] - i[1]]
            return por_mes[tipo_fluxo].get(m, [0.0, 0.0])

        total_fob = sum(total_mes(m)[0] for m in meses)
        total_kg = sum(total_mes(m)[1] for m in meses)

        notas = [
            "Valores FOB em US$ milhões; peso líquido em mil toneladas. Dados do MDIC/Secex sujeitos a revisão; "
            "o saldo da Secex difere do saldo da balança comercial do balanço de pagamentos do BCB.",
        ]
        if desatualizado:
            notas.append("O ComexStat não respondeu; estes dados são uma cópia anterior guardada em cache.")

        saida: dict[str, Any] = {
            "fluxo": {"export": "exportacao", "import": "importacao", "saldo": "saldo"}[tipo_fluxo],
            "periodo": f"{rotulo_periodo(d_ini, 'mensal')} a {rotulo_periodo(d_fim, 'mensal')}",
            "unidade": "US$ milhões FOB",
            "total_valor": _milhoes(total_fob),
            "total_peso_mil_t": _toneladas_mil(total_kg),
            "fonte": cx.ROTULO,
            "url_fonte": url,
            "consultado_em": carimbo(quando),
        }
        if aplicados:
            saida["filtros"] = aplicados
        if detalhe:
            ordenados = sorted(total_itens.items(), key=ordenar, reverse=True)
            if tipo_fluxo == "saldo":
                # ranking por tamanho do resultado (superávit e déficit), sem esconder os déficits
                ordenados = sorted(total_itens.items(), key=lambda par: abs(par[1][0]), reverse=True)
            base = abs(total_fob) if total_fob else None
            itens = []
            for nome, (fob, kg) in ordenados[:limite]:
                item: dict[str, Any] = {"nome": nome if len(nome) <= 90 else nome[:89] + "…", "valor": _milhoes(fob), "peso_mil_t": _toneladas_mil(kg)}
                if base and tipo_fluxo != "saldo":
                    item["participacao_pct"] = round(100 * fob / base, 1)
                itens.append(item)
            saida["agrupado_por"] = chave_por
            saida["itens"] = itens
            resto = ordenados[limite:]
            if resto:
                saida["demais"] = {"quantidade": len(resto), "valor": _milhoes(sum(v[0] for _, v in resto))}
            saida["itens_total"] = len(ordenados)
        if mensal or not detalhe:
            saida["mensal"] = [[rotulo_periodo(m, "mensal"), _milhoes(total_mes(m)[0])] for m in meses]
        if not meses:
            notas.append("Nenhum registro para a seleção e o período pedidos.")
        saida["notas"] = notas
        return saida
