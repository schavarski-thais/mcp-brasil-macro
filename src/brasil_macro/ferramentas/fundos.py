"""Ferramentas de fundos de investimento da CVM: cadastro (RCVM 175) e informe diário."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from ..catalogo import normalizar
from ..fontes import cvm
from ._comum import arredondar, carimbo, ferramenta, tabela

_LIMITE_PADRAO = 15
_LIMITE_MAXIMO = 30
_MESES_MAXIMOS = 12
_DIAS_POR_PAGINA = 60
_COLUNAS_LISTA = ["cnpj", "nome", "tipo", "classificacao", "gestor", "administrador", "patrimonio_liquido", "data_patrimonio"]
_COLUNAS_DIARIO = ["data", "cota", "patrimonio_liquido", "captacao", "resgate", "cotistas"]
_COLUNAS_MES = ["mes", "ultimo_informe", "cota", "patrimonio_liquido", "captacao_liquida_mes", "cotistas"]


def _resumo(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "cnpj": r["cnpj_classe"],
        "nome": r["nome"],
        "fundo": r["nome_fundo"],
        "cnpj_fundo": r["cnpj_fundo"],
        "tipo": r["tipo"],
        "classificacao": r["classificacao"],
        "situacao": r["situacao"],
        "gestor": r["gestor"],
        "administrador": r["administrador"],
        "cnpj_administrador": r["cnpj_administrador"],
        "patrimonio_liquido": arredondar(r["patrimonio_liquido"], 2),
        "data_patrimonio": r["data_patrimonio"],
    }


def _linha_lista(r: dict[str, Any]) -> list[Any]:
    return [
        r["cnpj_classe"],
        r["nome"],
        r["tipo"],
        r["classificacao"],
        r["gestor"],
        r["administrador"],
        arredondar(r["patrimonio_liquido"], 2),
        r["data_patrimonio"],
    ]


def _ficha(r: dict[str, Any]) -> dict[str, Any]:
    return {
        **_resumo(r),
        "codigo_cvm": r["codigo_cvm"],
        "data_registro": r["data_registro"],
        "data_constituicao": r["data_constituicao"],
        "data_inicio": r["data_inicio"],
        "situacao_desde": r["situacao_desde"],
        "classificacao_anbima": r["classificacao_anbima"],
        "classe_cotas": r["classe_cotas"],
        "indicador_desempenho": r["indicador_desempenho"],
        "tributacao_longo_prazo": r["tributacao_longo_prazo"],
        "entidade_investimento": r["entidade_investimento"],
        "permite_aplicacao_exterior_100": r["permite_exterior_100"],
        "classe_esg": r["classe_esg"],
        "forma_condominio": r["forma_condominio"],
        "exclusivo": r["exclusivo"],
        "publico_alvo": r["publico_alvo"],
        "cnpj_auditor": r["cnpj_auditor"],
        "auditor": r["auditor"],
        "cnpj_custodiante": r["cnpj_custodiante"],
        "custodiante": r["custodiante"],
        "cnpj_controlador": r["cnpj_controlador"],
        "controlador": r["controlador"],
        "subclasses": r["subclasses"],
    }


def _periodo(texto: str, fim: bool) -> date:
    """'AAAA-MM' (mês inteiro: primeiro ou último dia) ou 'AAAA-MM-DD'."""
    t = texto.strip()
    try:
        if len(t) == 7:
            ano, mes = int(t[:4]), int(t[5:7])
            return date(ano, mes, calendar.monthrange(ano, mes)[1] if fim else 1)
        return date.fromisoformat(t)
    except ValueError:
        raise ValueError(f"Período inválido: {texto!r}. Use AAAA-MM ou AAAA-MM-DD.") from None


def _rentabilidade_simples(dias: list[dict[str, Any]]) -> float | None:
    cotas = [d for d in dias if d["cota"]]
    if len(cotas) < 2:
        return None
    return (cotas[-1]["cota"] / cotas[0]["cota"] - 1) * 100


def _meses(dias: list[dict[str, Any]]) -> list[list[Any]]:
    """Uma linha por mês: último informe do mês e a captação líquida somada no mês."""
    por_mes: dict[str, list[dict[str, Any]]] = {}
    for d in dias:
        por_mes.setdefault(d["data"].strftime("%Y-%m"), []).append(d)
    linhas = []
    for mes, do_mes in por_mes.items():
        ultimo = do_mes[-1]
        linhas.append(
            [
                mes,
                ultimo["data"].isoformat(),
                arredondar(ultimo["cota"], 8),
                arredondar(ultimo["pl"], 2),
                arredondar(sum(d["captacao"] - d["resgate"] for d in do_mes), 2),
                ultimo["cotistas"],
            ]
        )
    return linhas


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def fundos_cvm(
        consulta: str | None = None,
        cnpj: str | None = None,
        situacao: str | None = "Em Funcionamento Normal",
        classificacao: str | None = None,
        tipo: str | None = None,
        gestor: str | None = None,
        administrador: str | None = None,
        publico_alvo: str | None = None,
        limite: int = _LIMITE_PADRAO,
    ) -> dict[str, Any]:
        """Fundos de investimento registrados na CVM (cadastro público da RCVM 175: classes de cotas).

        Filtros: `consulta` (trecho do nome), `classificacao` (ex.: Ações, Renda Fixa), `tipo` (ex.: Fundo de
        Investimento Imobiliário), `gestor`, `administrador` (nome ou CNPJ) e `publico_alvo`. A lista vem em
        tabela (colunas: cnpj, nome, tipo, classificacao, gestor, administrador, patrimonio_liquido, data_patrimonio),
        ordenada pelo patrimônio informado, que pode estar defasado. Com `cnpj` exato (de classe ou de fundo),
        devolve a ficha completa: CNPJs e nomes de administrador, auditor, custodiante e controlador, e subclasses.
        Situação padrão 'Em Funcionamento Normal'; '' inclui todas. Cotas diárias: fundos_informe_diario.
        """
        if not 1 <= limite <= _LIMITE_MAXIMO:
            raise ValueError(f"limite deve estar entre 1 e {_LIMITE_MAXIMO}.")
        if not (consulta or cnpj or gestor or administrador or classificacao or tipo or publico_alvo):
            raise ValueError("Informe consulta (nome), cnpj, gestor, administrador, classificacao, tipo ou publico_alvo.")

        registros, url, consultado = await cvm.cadastro()
        base = {"fonte": cvm.ROTULO, "url": url, "consultado_em": carimbo(consultado)}

        if cnpj:
            digitos = cvm.somente_digitos(cnpj)
            achados = [
                r for r in registros
                if digitos in (cvm.somente_digitos(r["cnpj_classe"]), cvm.somente_digitos(r["cnpj_fundo"]))
            ]
            if not achados:
                raise ValueError(f"CNPJ {cnpj} não encontrado no cadastro de fundos da CVM.")
            return {**base, "registros": [_ficha(r) for r in achados]}

        filtrados = registros
        if situacao:
            termo = normalizar(situacao)
            filtrados = [r for r in filtrados if termo in normalizar(r["situacao"] or "")]
        if consulta:
            termo = normalizar(consulta)
            filtrados = [r for r in filtrados if termo in normalizar(r["nome"]) or termo in normalizar(r["nome_fundo"])]
        if classificacao:
            termo = normalizar(classificacao)
            filtrados = [
                r for r in filtrados
                if termo in normalizar(r["classificacao"] or "") or termo in normalizar(r["classificacao_anbima"] or "")
            ]
        if tipo:
            termo = normalizar(tipo)
            filtrados = [r for r in filtrados if termo in normalizar(r["tipo"] or "")]
        if gestor:
            termo = normalizar(gestor)
            filtrados = [r for r in filtrados if termo in normalizar(r["gestor"] or "")]
        if administrador:
            termo = normalizar(administrador)
            digitos = cvm.somente_digitos(administrador)
            filtrados = [
                r for r in filtrados
                if termo in normalizar(r["administrador"] or "")
                or (digitos and digitos == cvm.somente_digitos(r["cnpj_administrador"]))
            ]
        if publico_alvo:
            termo = normalizar(publico_alvo)
            filtrados = [r for r in filtrados if termo in normalizar(r["publico_alvo"] or "")]
        if not filtrados:
            raise ValueError("Nenhum fundo encontrado com esses filtros.")

        ordenados = sorted(filtrados, key=lambda r: r["patrimonio_liquido"] or 0.0, reverse=True)
        return {
            **base,
            "encontrados": len(filtrados),
            **tabela(_COLUNAS_LISTA, [_linha_lista(r) for r in ordenados[:limite]]),
            "notas": ["Patrimônio líquido é o último informado à CVM (ver data_patrimonio); pode estar defasado."],
        }

    @ferramenta(servidor)
    async def fundos_informe_diario(
        cnpj: str,
        inicio: str | None = None,
        fim: str | None = None,
        subclasse: str | None = None,
        detalhe: str = "resumo",
        pagina: int = 1,
    ) -> dict[str, Any]:
        """Informe diário de um fundo financeiro (FIF) na CVM: cota, patrimônio, captação, resgate e cotistas.

        `cnpj` é da classe ou do fundo. Cobre só os FIF: FII, FIDC, FIP e FIAGRO estão em outros conjuntos da CVM, ainda não incluídos.
        `inicio` e `fim` aceitam AAAA-MM ou AAAA-MM-DD (padrão: últimos 3 meses; máximo 12 meses por chamada).
        `detalhe`='resumo' (padrão) traz rentabilidade simples, captação líquida e uma linha por mês (último informe
        do mês, com a captação líquida somada). `detalhe`='diario' traz cada dia, 60 por página: use `pagina` (1, 2...).
        Para fundos com subclasses há uma série por subclasse, ou só a de `subclasse`. A rentabilidade simples é cota
        final sobre cota inicial menos 1; não é a rentabilidade oficial (ignora distribuições e taxas).
        """
        if detalhe not in ("resumo", "diario"):
            raise ValueError("detalhe deve ser 'resumo' ou 'diario'.")
        if pagina < 1:
            raise ValueError("pagina deve ser 1 ou maior.")
        digitos = cvm.somente_digitos(cnpj)
        if len(digitos) not in (11, 14):
            raise ValueError("CNPJ deve ter 14 dígitos (fundo ou classe).")

        hoje = date.today()
        fim_d = _periodo(fim, fim=True) if fim else hoje
        inicio_d = _periodo(inicio, fim=False) if inicio else (date(fim_d.year, fim_d.month, 1) - timedelta(days=60)).replace(day=1)
        if inicio_d > fim_d:
            raise ValueError("inicio é posterior a fim.")
        meses = cvm.meses_entre(inicio_d, fim_d)
        if len(meses) > _MESES_MAXIMOS:
            raise ValueError(f"O período cobre {len(meses)} meses; o máximo por chamada é {_MESES_MAXIMOS}.")

        linhas, urls, consultado = await cvm.informe_diario(meses, lambda c: c == digitos)
        linhas = [l for l in linhas if inicio_d <= l["data"] <= fim_d]
        if subclasse:
            linhas = [l for l in linhas if l["subclasse"] == subclasse]
        if not linhas:
            raise ValueError(
                "Nenhum informe diário para esse CNPJ no período. Este informe cobre os fundos de investimento "
                "financeiro (FIF); FII, FIDC, FIP e FIAGRO não estão nele. Fundos sem informe no período também não aparecem."
            )

        grupos: dict[str | None, list[dict[str, Any]]] = {}
        for l in linhas:
            grupos.setdefault(l["subclasse"], []).append(l)

        series = []
        for chave in sorted(grupos, key=lambda k: (k is not None, k or "")):
            dias = sorted(grupos[chave], key=lambda d: d["data"])
            item: dict[str, Any] = {
                "subclasse": chave,
                "dias_com_informe": len(dias),
                "rentabilidade_simples_pct": arredondar(_rentabilidade_simples(dias), 4),
                "captacao_liquida_total": arredondar(sum(d["captacao"] - d["resgate"] for d in dias), 2),
                "pl_ultimo": arredondar(dias[-1]["pl"], 2),
                "cotistas_ultimo": dias[-1]["cotistas"],
            }
            if detalhe == "diario":
                total_paginas = max(1, -(-len(dias) // _DIAS_POR_PAGINA))
                if pagina > total_paginas:
                    raise ValueError(f"pagina {pagina} além do fim; a série tem {total_paginas} página(s).")
                trecho = dias[(pagina - 1) * _DIAS_POR_PAGINA : pagina * _DIAS_POR_PAGINA]
                item["pagina"] = pagina
                item["paginas"] = total_paginas
                item.update(
                    tabela(
                        _COLUNAS_DIARIO,
                        [
                            [
                                d["data"].isoformat(),
                                arredondar(d["cota"], 8),
                                arredondar(d["pl"], 2),
                                arredondar(d["captacao"], 2),
                                arredondar(d["resgate"], 2),
                                d["cotistas"],
                            ]
                            for d in trecho
                        ],
                    )
                )
            else:
                item.update(tabela(_COLUNAS_MES, _meses(dias)))
            series.append(item)

        return {
            "fonte": cvm.ROTULO_INFORME,
            "urls": urls,
            "consultado_em": carimbo(consultado),
            "cnpj": digitos,
            "periodo": {"inicio": inicio_d.isoformat(), "fim": fim_d.isoformat()},
            "detalhe": detalhe,
            "series": series,
            "notas": [
                "Rentabilidade simples = cota final / cota inicial - 1 no período. Não considera distribuições "
                "nem o momento das captações; não substitui a rentabilidade divulgada pelo administrador.",
                "Captação líquida = captação menos resgate, somada no período.",
                "Patrimônio, cotistas e captação vêm do informe diário da CVM, com a data de referência de cada dia.",
            ],
        }
