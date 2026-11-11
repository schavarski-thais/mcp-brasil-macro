"""Ferramentas do Banco Central: séries do SGS fora do catálogo, Focus, PTAX e Copom."""

from __future__ import annotations

import re
from datetime import date, timedelta
from statistics import median
from typing import Any

from .. import catalogo
from ..catalogo import Indicador, normalizar
from ..fontes import copom as fonte_copom
from ..fontes import olinda, sgs
from ..http import TTL_MEDIO, requisitar
from ..modelos import data_opcional
from ._comum import carimbo, ferramenta
from .series import carregar_serie

_CKAN = "https://dadosabertos.bcb.gov.br/api/3/action/package_search"

# ------------------------------------------------------------------- Focus

_TIPOS_FOCUS = {
    "anual": "anual",
    "anuais": "anual",
    "ano": "anual",
    "mensal": "mensal",
    "mensais": "mensal",
    "trimestral": "trimestral",
    "trimestrais": "trimestral",
    "12_meses": "12_meses",
    "12m": "12_meses",
    "24_meses": "24_meses",
    "24m": "24_meses",
    "selic": "selic",
}

_APELIDOS_FOCUS = {
    "pib": "PIB Total",
    "cambio": "Câmbio",
    "dolar": "Câmbio",
    "juros": "Selic",
    "desemprego": "Taxa de desocupação",
    "desocupacao": "Taxa de desocupação",
    "primario": "Resultado primário",
    "nominal": "Resultado nominal",
    "divida bruta": "Dívida bruta do governo geral",
    "dbgg": "Dívida bruta do governo geral",
    "divida liquida": "Dívida líquida do setor público",
    "dlsp": "Dívida líquida do setor público",
    "idp": "Investimento direto no país",
    "investimento direto": "Investimento direto no país",
    "balanca": "Balança comercial",
    "transacoes correntes": "Conta corrente",
    "administrados": "IPCA Administrados",
    "monitorados": "IPCA Administrados",
    "servicos": "IPCA Serviços",
    "livres": "IPCA Livres",
}

_TIPOS_TOP5 = {"curto": "C", "medio": "M", "longo": "L"}


def _unidade_focus(indicador: str) -> str:
    if indicador.startswith(("IPCA", "IGP")):
        return "% (variação no período de referência)"
    if indicador.startswith("PIB"):
        return "% (variação real no período de referência)"
    if indicador == "Câmbio":
        return "R$/US$"
    if indicador == "Selic":
        return "% a.a."
    if indicador == "Taxa de desocupação":
        return "%"
    if indicador.startswith(("Dívida", "Resultado")):
        return "% do PIB"
    return "US$ bilhões"


def _chave_referencia(ref: str) -> tuple[int, int]:
    """Ordena '2026', '10/2026', '4/2026' e 'R7/2026' cronologicamente."""
    numeros = [int(n) for n in re.findall(r"\d+", ref)]
    if len(numeros) == 1:
        return (numeros[0], 0)
    return (numeros[1], numeros[0]) if len(numeros) >= 2 else (0, 0)


def _normalizar_referencia(tipo: str, texto: str) -> str:
    t = texto.strip().upper()
    if tipo == "anual":
        if m := re.fullmatch(r"(\d{4})", t):
            return m[1]
    elif tipo == "mensal":
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})", t):
            return f"{int(m[2]):02d}/{m[1]}"
        if m := re.fullmatch(r"(\d{1,2})/(\d{4})", t):
            return f"{int(m[1]):02d}/{m[2]}"
    elif tipo == "trimestral":
        if m := re.fullmatch(r"(\d{4})-?[TQ]([1-4])", t):
            return f"{m[2]}/{m[1]}"
        if m := re.fullmatch(r"[TQ]?([1-4])[TQ]?/(\d{4})", t):
            return f"{m[1]}/{m[2]}"
    elif tipo == "selic":
        if m := re.fullmatch(r"R?(\d{1,2})/(\d{4})", t):
            return f"R{int(m[1])}/{m[2]}"
    exemplos = {"anual": "2027", "mensal": "2026-12", "trimestral": "2026-T4", "selic": "R8/2026"}
    raise ValueError(f"Referência {texto!r} inválida para o tipo {tipo}. Exemplo: {exemplos.get(tipo, '')}.")


def _resolver_indicador(nome: str, disponiveis: list[str], tipo: str) -> str:
    alvo = normalizar(_APELIDOS_FOCUS.get(normalizar(nome), nome))
    por_nome = {normalizar(d): d for d in disponiveis}
    if alvo in por_nome:
        return por_nome[alvo]
    parecidos = [d for n, d in por_nome.items() if alvo in n]
    if len(parecidos) == 1:
        return parecidos[0]
    raise ValueError(
        f"Indicador {nome!r} não está disponível no Focus para o tipo {tipo!r}. "
        f"Disponíveis: {', '.join(disponiveis)}."
    )


def _linha_focus(x: dict[str, Any]) -> list[Any]:
    return [x.get("Mediana"), x.get("Media"), x.get("DesvioPadrao"), x.get("Minimo"), x.get("Maximo"), x.get("numeroRespondentes")]


_COLUNAS_FOCUS = ["mediana", "media", "desvio_padrao", "minimo", "maximo", "respondentes"]


def _inferir_periodicidade(datas: list[date]) -> str:
    if len(datas) < 3:
        return "mensal"
    passo = median((b - a).days for a, b in zip(datas, datas[1:]))
    if passo <= 7:
        return "diaria"
    if passo <= 45:
        return "mensal"
    return "trimestral" if passo <= 135 else "anual"


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def sgs_serie(
        codigo: int,
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        transformacao: str | None = None,
    ) -> dict[str, Any]:
        """Qualquer série do SGS (BCB) pelo código, quando não está no catálogo. Use sgs_buscar para achar códigos.

        Args:
            codigo: número da série, ex.: 432 (meta Selic).
            inicio, fim, ultimos, transformacao: como em obter_serie."""
        meta = await sgs.metadados_sgs(codigo)
        notas = []
        if meta is None:
            hoje = date.today()
            amostra = await sgs.serie_sgs(codigo, hoje - timedelta(days=800), hoje)
            meta = {
                "nome": f"Série SGS {codigo}",
                "unidade": "",
                "periodicidade": _inferir_periodicidade([d for d, _ in amostra.observacoes]),
            }
            notas.append("Metadados oficiais indisponíveis no momento: nome e unidade não informados; periodicidade inferida.")
        ind = Indicador(
            id=f"sgs_{codigo}",
            nome=meta["nome"],
            tema="",
            tema_titulo="",
            unidade=meta["unidade"],
            periodicidade=meta["periodicidade"],
            origem="sgs",
            parametros=codigo,
        )
        serie = await carregar_serie(
            ind, data_opcional(inicio), data_opcional(fim, fim=True), ultimos, transformacao
        )
        serie.id = None
        serie.notas.extend(notas)
        no_catalogo = [i.id for i in catalogo.todos(com_privados=True) if i.origem == "sgs" and i.parametros == codigo]
        if no_catalogo:
            serie.notas.append(f"Esta série está no catálogo como {no_catalogo[0]!r}.")
        return serie.para_dict()

    @ferramenta(servidor)
    async def sgs_buscar(consulta: str, limite: int = 20) -> dict[str, Any]:
        """Procura séries do SGS no catálogo de dados abertos do Banco Central, por palavras-chave.

        Devolve código, título e periodicidade; use o código em sgs_serie. A busca
        cobre só as séries publicadas como dados abertos, não o SGS inteiro.

        Args:
            consulta: palavras-chave, ex.: "inadimplência cartão de crédito".
            limite: máximo de resultados (até 100).
        """
        if not consulta.strip():
            raise ValueError("Informe ao menos uma palavra-chave.")
        r = await requisitar(
            _CKAN, params={"q": consulta, "rows": min(max(limite, 1), 100) * 2}, ttl=TTL_MEDIO, espera_json=True
        )
        resultado = r.json().get("result", {})
        por_codigo = {
            i.parametros: i.id for i in catalogo.todos(com_privados=True) if i.origem == "sgs"
        }
        series = []
        for item in resultado.get("results", []):
            bruto = str(item.get("codigo_sgs") or "").strip()
            if not bruto.isdigit():
                continue
            entrada = {
                "codigo": int(bruto),
                "titulo": item.get("title", ""),
                "periodicidade": item.get("frequencia") or item.get("periodicidade") or "",
            }
            if int(bruto) in por_codigo:
                entrada["id_no_catalogo"] = por_codigo[int(bruto)]
            series.append(entrada)
        return {
            "total_encontrado": resultado.get("count", len(series)),
            "series": series[: max(limite, 1)],
            "fonte": "BCB/Portal de Dados Abertos",
            "url_fonte": r.url,
            "consultado_em": carimbo(r.consultado_em),
        }

    @ferramenta(servidor)
    async def expectativas_focus(
        indicador: str = "",
        tipo: str = "anual",
        referencia: str | None = None,
        data: str | None = None,
        inicio: str | None = None,
        ultimos: int | None = None,
        frequencia: str = "semanal",
        detalhe: str | None = None,
        top5: str | None = None,
        base_calculo: int = 0,
        suavizada: bool = True,
    ) -> dict[str, Any]:
        """Expectativas do Relatório Focus (BCB): mediana, média e dispersão.

        Sem `indicador`, lista os indicadores por tipo. Com `indicador`, traz o retrato da pesquisa mais recente (uma linha por período); com `referencia` ou tipo 12_meses/24_meses, traz a evolução no tempo.

        Args:
            indicador: ex.: "IPCA", "Selic", "PIB Total", "Câmbio", "Resultado primário"; aceita apelidos (pib, dolar).
            tipo: "anual", "mensal", "trimestral", "12_meses", "24_meses" ou "selic" (decisões do Copom).
            referencia: "2027" (anual), "2026-12" (mensal), "2026-T4" (trimestral) ou "R8/2026" (selic).
            data: data da pesquisa (AAAA-MM-DD) para o retrato; padrão: a mais recente.
            inicio, ultimos, frequencia: para a evolução (padrão 12 pontos, semanal).
            detalhe: para "Balança comercial": "Exportações", "Importações" ou "Saldo".
            top5: "curto", "medio" ou "longo" para as instituições Top 5.
            base_calculo: 0 = últimos 30 dias (padrão); 1 = últimos 5 dias úteis.
            suavizada: para 12_meses e 24_meses (padrão)."""
        chave_tipo = _TIPOS_FOCUS.get(normalizar(tipo).replace(" ", "_"))
        if chave_tipo is None:
            raise ValueError(f"Tipo {tipo!r} inválido. Opções: anual, mensal, trimestral, 12_meses, 24_meses, selic.")
        geral, conjunto_top5, campo_ref = olinda.CONJUNTOS[chave_tipo]
        if top5 is not None and normalizar(top5) not in _TIPOS_TOP5:
            raise ValueError('top5 deve ser "curto", "medio" ou "longo".')
        conjunto = conjunto_top5 if top5 else geral

        if not indicador.strip() and chave_tipo != "selic":
            disponiveis: dict[str, Any] = {}
            for nome_tipo, (g, _, _) in olinda.CONJUNTOS.items():
                pares = await olinda.focus_indicadores(g)
                agrupado: dict[str, list[str]] = {}
                for ind_, det in pares:
                    agrupado.setdefault(ind_, [])
                    if det:
                        agrupado[ind_].append(det)
                disponiveis[nome_tipo] = [f"{k} ({', '.join(v)})" if v else k for k, v in agrupado.items()]
            return {"indicadores_por_tipo": disponiveis, "fonte": olinda.ROTULO_FOCUS}

        pares = await olinda.focus_indicadores(conjunto)
        nomes = sorted({p[0] for p in pares})
        nome = "Selic" if chave_tipo == "selic" and not indicador.strip() else _resolver_indicador(indicador, nomes, chave_tipo)
        detalhes = sorted({p[1] for p in pares if p[0] == nome and p[1]})

        filtros = [olinda.filtro_igual("Indicador", nome)]
        notas: list[str] = []
        if top5:
            filtros.append(olinda.filtro_igual("tipoCalculo", _TIPOS_TOP5[normalizar(top5)]))
            notas.append(f"Instituições Top 5 de {normalizar(top5)} prazo.")
        else:
            if base_calculo not in (0, 1):
                raise ValueError("base_calculo deve ser 0 (últimos 30 dias) ou 1 (últimos 5 dias úteis).")
            filtros.append(olinda.filtro_igual("baseCalculo", base_calculo))
        if campo_ref is None:
            filtros.append(olinda.filtro_igual("Suavizada", "S" if suavizada else "N"))
            notas.append("Série suavizada." if suavizada else "Série não suavizada.")
        det_escolhido = None
        if detalhes:
            if detalhe:
                por_nome = {normalizar(d): d for d in detalhes}
                det_escolhido = por_nome.get(normalizar(detalhe))
                if det_escolhido is None:
                    raise ValueError(f"Detalhe {detalhe!r} inválido para {nome}. Opções: {', '.join(detalhes)}.")
                filtros.append(olinda.filtro_igual("IndicadorDetalhe", det_escolhido))

        base: dict[str, Any] = {
            "indicador": nome if not det_escolhido else f"{nome} — {det_escolhido}",
            "tipo": chave_tipo,
            "unidade": _unidade_focus(nome),
            "fonte": olinda.ROTULO_FOCUS,
        }

        evolucao = referencia is not None or campo_ref is None or inicio is not None or ultimos is not None
        if not evolucao:
            # Retrato: todos os períodos de referência numa data de pesquisa.
            dia = data_opcional(data).isoformat() if data else await olinda.focus_ultima_data(conjunto, filtros)
            if not dia:
                raise LookupError(f"O Focus não tem expectativas de {nome} para o tipo {chave_tipo}.")
            linhas, url, quando = await olinda.focus(conjunto, filtros + [olinda.filtro_igual("Data", dia)])
            if not linhas:
                raise LookupError(f"Sem expectativas de {nome} na data {dia} (dia sem pesquisa?). Omita `data` para a mais recente.")
            linhas.sort(key=lambda x: (_chave_referencia(str(x[campo_ref])), x.get("IndicadorDetalhe") or ""))
            com_detalhe = bool(detalhes) and not det_escolhido
            base.update(
                data_pesquisa=dia,
                colunas=["referencia"] + (["detalhe"] if com_detalhe else []) + _COLUNAS_FOCUS,
                linhas=[
                    [x[campo_ref]] + ([x.get("IndicadorDetalhe")] if com_detalhe else []) + _linha_focus(x)
                    for x in linhas
                ],
            )
        else:
            if frequencia not in ("semanal", "diaria"):
                raise ValueError('frequencia deve ser "semanal" ou "diaria".')
            if detalhes and not det_escolhido:
                raise ValueError(f"Para a evolução de {nome}, informe detalhe: {', '.join(detalhes)}.")
            if campo_ref is not None:
                if referencia is None:
                    raise ValueError("Para a evolução no tempo, informe `referencia` (ex.: \"2027\").")
                ref = _normalizar_referencia(chave_tipo, referencia)
                filtros.append(olinda.filtro_igual(campo_ref, ref))
                base["referencia"] = ref
            pontos = ultimos or 12
            d_inicio = data_opcional(inicio)
            if d_inicio:
                filtros.append(olinda.filtro_desde("Data", d_inicio))
                topo = 5000
            else:
                topo = pontos * (5 if frequencia == "semanal" else 1) + 5
            linhas, url, quando = await olinda.focus(conjunto, filtros, topo=topo)
            if not linhas:
                raise LookupError(f"Sem expectativas de {nome} para os filtros informados.")
            linhas.sort(key=lambda x: x["Data"])
            if frequencia == "semanal":
                por_semana: dict[tuple[int, int], dict[str, Any]] = {}
                for x in linhas:
                    semana = date.fromisoformat(x["Data"]).isocalendar()[:2]
                    por_semana[semana] = x  # fica a última data de cada semana
                linhas = list(por_semana.values())
            if not d_inicio or ultimos:
                linhas = linhas[-pontos:]
            base.update(
                frequencia=frequencia,
                colunas=["data_pesquisa"] + _COLUNAS_FOCUS,
                linhas=[[x["Data"]] + _linha_focus(x) for x in linhas],
            )
        vazias = [i for i, _ in enumerate(base["colunas"]) if all(linha[i] is None for linha in base["linhas"])]
        if vazias:
            base["colunas"] = [c for i, c in enumerate(base["colunas"]) if i not in vazias]
            base["linhas"] = [[v for i, v in enumerate(linha) if i not in vazias] for linha in base["linhas"]]
        if nome.startswith("Resultado"):
            notas.append("No Focus, resultado negativo = déficit (convenção oposta à das séries NFSP do catálogo).")
        if notas:
            base["notas"] = notas
        base["url_fonte"] = url
        base["consultado_em"] = carimbo(quando)
        return base

    @ferramenta(servidor)
    async def ptax(
        moeda: str = "USD", inicio: str | None = None, fim: str | None = None, ultimos: int = 10
    ) -> dict[str, Any]:
        """Cotações PTAX de fechamento do Banco Central (compra e venda, em reais por unidade da moeda).

        Args:
            moeda: símbolo da moeda (USD, EUR, GBP, JPY, CHF...). Use "lista" para ver as moedas disponíveis.
            inicio: data inicial (AAAA-MM-DD ou AAAA-MM).
            fim: data final; padrão: hoje.
            ultimos: quantidade de dias úteis mais recentes quando não há `inicio`.
        """
        if normalizar(moeda) in ("lista", "moedas", ""):
            return {"moedas": await olinda.ptax_moedas(), "fonte": olinda.ROTULO_PTAX}
        d_fim = data_opcional(fim, fim=True) or date.today()
        d_inicio = data_opcional(inicio) or d_fim - timedelta(days=int(max(ultimos, 1) * 1.6) + 10)
        linhas, url, quando = await olinda.ptax(moeda, d_inicio, d_fim)
        if not linhas:
            raise LookupError(
                f"Sem cotações PTAX para {moeda.upper()} entre {d_inicio} e {d_fim}. "
                'Confira o símbolo com moeda="lista".'
            )
        if not inicio:
            linhas = linhas[-max(ultimos, 1) :]
        total = len(linhas)
        notas = []
        if total > 600:
            linhas = linhas[-600:]
            notas.append(f"Exibidos os últimos 600 de {total} dias; restrinja o período para ver o restante.")
        com_paridade = "paridade_compra" in linhas[0]
        colunas = ["data", "compra", "venda"] + (["paridade_compra", "paridade_venda"] if com_paridade else [])
        saida: dict[str, Any] = {
            "moeda": moeda.upper(),
            "unidade": f"R$/{moeda.upper()}",
            "colunas": colunas,
            "linhas": [[x[c] for c in colunas] for x in linhas],
            "fonte": olinda.ROTULO_PTAX,
            "url_fonte": url,
            "consultado_em": carimbo(quando),
        }
        if com_paridade:
            notas.append("Paridade: unidades da moeda por dólar americano (ou o inverso, conforme o tipo da moeda).")
        if notas:
            saida["notas"] = notas
        return saida

    @ferramenta(servidor)
    async def copom(
        ultimas: int = 8,
        documento: str | None = None,
        reuniao: int | None = None,
        max_caracteres: int = 12000,
        a_partir_de: int = 0,
    ) -> dict[str, Any]:
        """Decisões do Copom sobre a Selic, com comunicados e atas.

        Sem `documento`, devolve as últimas reuniões (data, meta decidida, variação em p.p.) e a data da próxima. Com `documento`, devolve o texto do comunicado ou da ata.

        Args:
            ultimas: reuniões listadas, da mais recente para trás.
            documento: "comunicado" ou "ata".
            reuniao: número da reunião; padrão: a mais recente disponível.
            max_caracteres: tamanho do trecho de texto devolvido.
            a_partir_de: posição inicial, para continuar a leitura de um documento longo."""
        if documento is None:
            reunioes, url, quando = await fonte_copom.historico()
            n = max(1, ultimas)
            recorte = reunioes[: n + 1]
            linhas = []
            for atual, anterior in zip(recorte, recorte[1:] + [None]):
                variacao = None
                if anterior and atual["meta_selic"] is not None and anterior["meta_selic"] is not None:
                    variacao = round(atual["meta_selic"] - anterior["meta_selic"], 2)
                linhas.append(
                    [atual["reuniao"], atual["data"], atual["meta_selic"], variacao, atual["vies"], atual["extraordinaria"]]
                )
            return {
                "colunas": ["reuniao", "data", "meta_selic", "variacao_pp", "vies", "extraordinaria"],
                "linhas": linhas[:n],
                "unidade": "% a.a.",
                "proxima_decisao": await fonte_copom.proxima_decisao(),
                "total_reunioes": len(reunioes),
                "fonte": fonte_copom.ROTULO,
                "pagina": fonte_copom.PAGINA_HISTORICO,
                "url_fonte": url,
                "consultado_em": carimbo(quando),
            }
        tipo = normalizar(documento)
        if tipo not in ("comunicado", "ata"):
            raise ValueError('documento deve ser "comunicado" ou "ata".')
        if reuniao is None:
            recentes, _, _ = await fonte_copom.listar_documentos(tipo, 1)
            if not recentes:
                raise LookupError(f"Nenhum(a) {tipo} disponível na API do BCB.")
            reuniao = int(recentes[0]["reuniao"])
        doc = await fonte_copom.documento(tipo, reuniao)
        texto = doc["texto"]
        inicio_ = max(0, a_partir_de)
        trecho = texto[inicio_ : inicio_ + max(500, max_caracteres)]
        saida = {
            "documento": tipo,
            "reuniao": reuniao,
            "data": doc["data"],
            "titulo": doc["titulo"],
            "total_caracteres": len(texto),
            "texto": trecho,
            "fonte": fonte_copom.ROTULO,
            "url_fonte": doc["url"],
            "consultado_em": carimbo(doc["consultado_em"]),
        }
        if doc.get("pdf"):
            saida["pdf"] = doc["pdf"]
        fim_ = inicio_ + len(trecho)
        if fim_ < len(texto):
            saida["continua"] = f"Texto parcial: para continuar, chame de novo com a_partir_de={fim_}."
        return saida
