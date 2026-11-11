"""Ferramentas do IBGE: aberturas do IPCA, consulta livre ao SIDRA e calendário de divulgações."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from ..catalogo import normalizar
from ..fontes import copom as fonte_copom
from ..fontes import ibge
from ..http import ErroFonte
from ..modelos import Serie, data_opcional, interpretar_data, rotulo_periodo
from ._comum import arredondar, carimbo, ferramenta

# Tabelas com a estrutura completa (geral, grupo, subgrupo, item, subitem), da mais nova para a mais antiga.
_INDICES: dict[str, dict[str, Any]] = {
    "ipca": {"nome": "IPCA", "tabelas": [(7060, date(2020, 1, 1)), (1419, date(2012, 1, 1))]},
    "ipca15": {"nome": "IPCA-15", "tabelas": [(7062, date(2020, 2, 1)), (1705, date(2012, 2, 1))]},
    "inpc": {"nome": "INPC", "tabelas": [(7063, date(2020, 1, 1)), (1100, date(2012, 1, 1))]},
}
_APELIDOS_INDICE = {"ipca": "ipca", "ipca15": "ipca15", "ipca 15": "ipca15", "inpc": "inpc"}
_CLASSIFICACAO = 315  # "Geral, grupo, subgrupo, item e subitem"
_NIVEIS = {0: "geral", 1: "grupo", 2: "subgrupo", 4: "item", 7: "subitem"}
_PAPEIS = ("variacao_mensal", "peso", "acumulado_ano", "acumulado_12m")


def _resolver_indice(indice: str) -> tuple[str, dict[str, Any]]:
    chave = _APELIDOS_INDICE.get(normalizar(indice))
    if chave is None:
        raise ValueError(f"Índice {indice!r} inválido. Opções: ipca, ipca15, inpc.")
    return chave, _INDICES[chave]


def _tabela_do_periodo(config: dict[str, Any], d: date) -> int:
    for tabela, inicio in config["tabelas"]:
        if d >= inicio:
            return tabela
    primeiro = config["tabelas"][-1][1]
    raise ValueError(
        f"As aberturas do {config['nome']} por subitem no SIDRA começam em {primeiro:%Y-%m}; "
        "para antes disso há apenas o índice geral (indicador ipca do catálogo)."
    )


async def _variaveis(tabela: int) -> dict[str, str]:
    """Descobre nos metadados o id de cada variável (os ids mudam de índice para índice)."""
    papeis: dict[str, str] = {}
    for v in (await ibge.metadados(tabela))["variaveis"]:
        nome = v["nome"].lower()
        if "peso" in nome:
            papeis["peso"] = str(v["id"])
        elif "12 meses" in nome:
            papeis["acumulado_12m"] = str(v["id"])
        elif "no ano" in nome:
            papeis["acumulado_ano"] = str(v["id"])
        elif "mensal" in nome:
            papeis["variacao_mensal"] = str(v["id"])
    return papeis


def _estrutura(nome: str) -> tuple[str, str]:
    """Separa '1101002.Arroz' em ('1101002', 'Arroz'); o índice geral não tem código."""
    m = re.match(r"(\d+)\.(.*)", nome)
    return (m.group(1), m.group(2).strip()) if m else ("", nome.strip())


async def _corte(tabela: int, periodos: str) -> tuple[dict[str, dict[str, Any]], ibge.ResultadoSidra]:
    """Todas as categorias do índice num período: código SIDRA -> registro."""
    papeis = await _variaveis(tabela)
    res = await ibge.consultar(tabela, list(papeis.values()), periodos, {_CLASSIFICACAO: "all"})
    por_id = {v: k for k, v in papeis.items()}
    itens: dict[str, dict[str, Any]] = {}
    for linha in res.linhas:
        codigo, nome = next(iter(linha.classificacoes.values()))
        estrutura, rotulo = _estrutura(nome)
        item = itens.setdefault(
            codigo,
            {
                "codigo_sidra": codigo,
                "estrutura": estrutura,
                "nome": rotulo,
                "nivel": _NIVEIS.get(len(estrutura), "outro"),
                "periodo": linha.periodo,
            },
        )
        item[por_id[linha.variavel_codigo]] = linha.valor
    return itens, res


def _achar_item(itens: dict[str, dict[str, Any]], texto: str) -> dict[str, Any]:
    alvo = texto.strip()
    if alvo.isdigit():
        for item in itens.values():
            if item["estrutura"] == alvo:
                return item
    n = normalizar(alvo)
    if n in ("geral", "indice geral", "ipca", "total"):
        return next(i for i in itens.values() if i["nivel"] == "geral")
    exatos = [i for i in itens.values() if normalizar(i["nome"]) == n]
    candidatos = exatos or [i for i in itens.values() if normalizar(i["nome"]).startswith(n)]
    candidatos = candidatos or [i for i in itens.values() if n in normalizar(i["nome"])]
    if not candidatos:
        raise LookupError(
            f"Não achei {texto!r} na estrutura do índice. Use ipca_detalhado com `filtro` para localizar o item."
        )
    if len(candidatos) > 1:
        # Entre homônimos (ex.: grupo e subgrupo "Vestuário"), fica o nível mais agregado.
        menor = min(len(c["estrutura"]) for c in candidatos)
        candidatos = [c for c in candidatos if len(c["estrutura"]) == menor]
    if len(candidatos) > 1:
        opcoes = "; ".join(f"{c['estrutura']} {c['nome']} ({c['nivel']})" for c in candidatos[:15])
        raise LookupError(f"{texto!r} é ambíguo. Informe o código da estrutura. Opções: {opcoes}.")
    return candidatos[0]


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def ipca_detalhado(
        periodo: str | None = None,
        nivel: str = "grupo",
        indice: str = "ipca",
        dentro_de: str | None = None,
        filtro: str | None = None,
        ordenar: str = "contribuicao",
        limite: int = 40,
    ) -> dict[str, Any]:
        """Quebras do IPCA (ou IPCA-15, INPC) num mês: variação, peso e contribuição em p.p. de cada componente, desde 2012.

        Args:
            periodo: AAAA-MM; padrão o mais recente.
            nivel: "grupo", "subgrupo", "item", "subitem" ou "todos".
            indice: "ipca", "ipca15" ou "inpc".
            dentro_de: ramo da estrutura pelo código ("1" = alimentação) ou nome.
            filtro: texto para achar componentes pelo nome ("gasolina").
            ordenar: "contribuicao" (padrão), "variacao", "peso" ou "estrutura".
            limite: linhas devolvidas."""
        chave, config = _resolver_indice(indice)
        nivel_n = normalizar(nivel)
        if nivel_n not in ("grupo", "subgrupo", "item", "subitem", "todos"):
            raise ValueError('nivel deve ser "grupo", "subgrupo", "item", "subitem" ou "todos".')
        if ordenar not in ("contribuicao", "variacao", "peso", "estrutura"):
            raise ValueError('ordenar deve ser "contribuicao", "variacao", "peso" ou "estrutura".')
        if periodo:
            d = interpretar_data(periodo)
            tabela = _tabela_do_periodo(config, d)
            periodos = f"{d.year}{d.month:02d}"
        else:
            tabela, periodos = config["tabelas"][0][0], "last 1"
        itens, res = await _corte(tabela, periodos)
        if not itens:
            raise LookupError(f"O IBGE não tem dados do {config['nome']} para {periodo or 'o período mais recente'}.")
        for item in itens.values():
            v, p = item.get("variacao_mensal"), item.get("peso")
            item["contribuicao"] = arredondar(v * p / 100) if v is not None and p is not None else None
        geral = next((i for i in itens.values() if i["nivel"] == "geral"), None)
        codigo_periodo = next(iter(itens.values()))["periodo"]

        selecao = [i for i in itens.values() if i["nivel"] != "geral"]
        notas: list[str] = []
        if dentro_de:
            ramo = dentro_de.strip()
            if not ramo.isdigit():
                ramo = _achar_item(itens, ramo)["estrutura"]
            selecao = [i for i in selecao if i["estrutura"].startswith(ramo) and i["estrutura"] != ramo]
            pai = next((i for i in itens.values() if i["estrutura"] == ramo), None)
            if pai:
                notas.append(f"Componentes de {pai['estrutura']} {pai['nome']} ({pai['nivel']}).")
        if filtro:
            termos = normalizar(filtro).split()
            selecao = [i for i in selecao if all(t in normalizar(i["nome"]) for t in termos)]
        elif nivel_n != "todos":
            selecao = [i for i in selecao if i["nivel"] == nivel_n]
        if filtro and nivel_n != "grupo" and nivel_n != "todos":
            selecao = [i for i in selecao if i["nivel"] == nivel_n]

        chaves = {
            "contribuicao": lambda i: -abs(i["contribuicao"] or 0),
            "variacao": lambda i: -(i.get("variacao_mensal") if i.get("variacao_mensal") is not None else -1e9),
            "peso": lambda i: -(i.get("peso") or 0),
            "estrutura": lambda i: i["estrutura"],
        }
        selecao.sort(key=chaves[ordenar])
        total = len(selecao)
        limite = max(1, limite)
        if total > limite:
            notas.append(f"Exibidos {limite} de {total} componentes (ordenados por {ordenar}); aumente `limite` ou use `filtro`.")
        notas.append(
            "contribuicao_pp = variação mensal × peso ÷ 100: quanto o componente somou ao índice do mês, em pontos percentuais."
        )
        saida: dict[str, Any] = {
            "indice": config["nome"],
            "periodo": rotulo_periodo(ibge.data_do_periodo(codigo_periodo, res.tipo_periodo), "mensal"),
        }
        if geral:
            saida["indice_geral"] = {
                "variacao_mensal": geral.get("variacao_mensal"),
                "acumulado_ano": geral.get("acumulado_ano"),
                "acumulado_12m": geral.get("acumulado_12m"),
            }
        saida.update(
            nivel=nivel_n,
            unidade="% (variações e peso); pontos percentuais (contribuição)",
            colunas=["estrutura", "nome", "nivel", "variacao_mensal", "peso", "contribuicao_pp", "acumulado_ano", "acumulado_12m"],
            linhas=[
                [
                    i["estrutura"],
                    i["nome"],
                    i["nivel"],
                    i.get("variacao_mensal"),
                    i.get("peso"),
                    i["contribuicao"],
                    i.get("acumulado_ano"),
                    i.get("acumulado_12m"),
                ]
                for i in selecao[:limite]
            ],
            total_componentes=total,
            notas=notas,
            fonte=f"{ibge.ROTULO} — tabela {tabela}",
            url_fonte=res.url,
            consultado_em=carimbo(res.consultado_em),
        )
        return saida

    @ferramenta(servidor)
    async def ipca_item_historico(
        item: str,
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        variavel: str = "variacao_mensal",
        indice: str = "ipca",
    ) -> dict[str, Any]:
        """Série histórica de um componente do IPCA (grupo, subgrupo, item ou subitem), desde 2012.

        Exemplos de `item`: "Alimentação e bebidas", "Alimentação no domicílio", "Gasolina",
        "Energia elétrica residencial", "Aluguel residencial", ou o código da estrutura ("5104001").

        Args:
            item: nome ou código da estrutura do componente (veja ipca_detalhado).
            inicio: mês inicial (AAAA-MM ou AAAA).
            fim: mês final.
            ultimos: quantidade de meses mais recentes (padrão 24 quando não há `inicio`).
            variavel: "variacao_mensal", "acumulado_12m", "acumulado_ano" ou "peso".
            indice: "ipca", "ipca15" ou "inpc".
        """
        chave, config = _resolver_indice(indice)
        if variavel not in _PAPEIS:
            raise ValueError(f"variavel deve ser uma de: {', '.join(_PAPEIS)}.")
        d_ini, d_fim = data_opcional(inicio), data_opcional(fim, fim=True)
        if d_ini is None and ultimos is None:
            ultimos = 24
        hoje = date.today()
        if d_ini is None:
            d_ini_busca = (d_fim or hoje) - timedelta(days=(ultimos + 3) * 31)
        else:
            d_ini_busca = d_ini
        atuais, _ = await _corte(config["tabelas"][0][0], "last 1")
        alvo = _achar_item(atuais, item)

        observacoes: dict[date, float | None] = {}
        notas: list[str] = []
        urls: list[str] = []
        quando = None
        tabelas = config["tabelas"]
        for indice_tabela, (tabela, comeca) in enumerate(tabelas):
            termina = tabelas[indice_tabela - 1][1] - timedelta(days=1) if indice_tabela else hoje
            if d_ini_busca > termina or (d_fim and d_fim < comeca):
                continue
            ini, fim_ = max(d_ini_busca, comeca), min(d_fim or hoje, termina)
            papeis = await _variaveis(tabela)
            try:
                res = await ibge.consultar(
                    tabela,
                    papeis[variavel],
                    f"{ini.year}{ini.month:02d}-{fim_.year}{fim_.month:02d}",
                    {_CLASSIFICACAO: alvo["codigo_sidra"]},
                )
            except ErroFonte:
                # Componentes criados ou renomeados na estrutura de 2020 não existem na tabela anterior.
                notas.append(f"O componente não existe na tabela {tabela} ({comeca.year} a {termina.year}).")
                continue
            for linha in res.linhas:
                observacoes[ibge.data_do_periodo(linha.periodo, res.tipo_periodo)] = linha.valor
            urls.append(res.url)
            quando = quando or res.consultado_em
        serie = Serie(
            nome=f"{config['nome']} — {alvo['nome']} ({alvo['nivel']}) — {variavel.replace('_', ' ')}",
            fonte=ibge.ROTULO,
            codigo=f"SIDRA c315={alvo['codigo_sidra']} (estrutura {alvo['estrutura'] or 'geral'})",
            unidade="%",
            periodicidade="mensal",
            observacoes=sorted((d, v) for d, v in observacoes.items() if v is not None),
            url=urls[-1] if urls else "",
            consultado_em=quando,
            notas=notas,
        )
        serie.recortar(d_ini, d_fim)
        if ultimos:
            serie.observacoes = serie.observacoes[-ultimos:]
        if not serie.observacoes:
            raise LookupError(f"Sem dados de {alvo['nome']} no período pedido (as aberturas começam em 2012).")
        return serie.para_dict()

    @ferramenta(servidor)
    async def sidra_consulta(
        tabela: int,
        variaveis: str = "all",
        periodos: str = "last 12",
        classificacoes: dict[str, str] | None = None,
        nivel_territorial: str = "n1",
        localidades: str = "all",
        limite: int = 300,
    ) -> dict[str, Any]:
        """Consulta livre a qualquer tabela do SIDRA (IBGE). Antes, use sidra_metadados para ver variáveis e categorias.

        Args:
            tabela: número da tabela (ex.: 7060 IPCA, 5938 PIB).
            variaveis: ids separados por vírgula, ou "all".
            periodos: "last N", "all", "202001-202612" ou "202601,202602". Mensal AAAAMM; trimestral AAAA01 a AAAA04; anual AAAA.
            classificacoes: ex.: {"315": "7169,7170"}; "all" traz todas. Sem informar, vem o total.
            nivel_territorial: n1 Brasil, n2 grande região, n3 UF, n6 município, n7 região metropolitana.
            localidades: "all" ou códigos IBGE separados por vírgula ("35" = SP no n3).
            limite: linhas devolvidas."""
        res = await ibge.consultar(tabela, variaveis, periodos, classificacoes, nivel_territorial, localidades)
        linhas = res.linhas
        if not linhas:
            raise LookupError("A consulta não devolveu dados; confira períodos e categorias com sidra_metadados.")
        per = res.periodicidade

        def periodo(codigo: str) -> str:
            if per == "semestral":
                return codigo
            return rotulo_periodo(ibge.data_do_periodo(codigo, res.tipo_periodo), per)

        dims_cls = list(linhas[0].classificacoes)
        campos: list[tuple[str, Any]] = [
            ("territorio", lambda l: l.territorio),
            ("variavel", lambda l: l.variavel),
            ("unidade", lambda l: l.unidade),
        ] + [(d, (lambda l, d=d: l.classificacoes[d][1])) for d in dims_cls]
        # Dimensões com um único valor vão para o cabeçalho; só as que variam viram colunas.
        fixos, variaveis_cols = {}, []
        for nome, obter in campos:
            valores = {obter(l) for l in linhas}
            if len(valores) == 1:
                fixos[nome] = valores.pop()
            else:
                variaveis_cols.append((nome, obter))
        total = len(linhas)
        limite = max(1, limite)
        notas = []
        if res.tipo_periodo == "Trimestre Móvel":
            notas.append("Trimestre móvel: cada mês representa o trimestre encerrado nele.")
        if total > limite:
            linhas = linhas[-limite:]
            notas.append(f"Exibidas as últimas {limite} de {total} linhas; restrinja a consulta para ver o restante.")
        if any(l.valor is None for l in linhas):
            notas.append("Valor nulo: dado não disponível, não aplicável ou sigiloso na fonte.")
        saida: dict[str, Any] = {
            "tabela": tabela,
            "periodicidade": per,
            **fixos,
            "colunas": ["periodo"] + [n for n, _ in variaveis_cols] + ["valor"],
            "linhas": [[periodo(l.periodo)] + [o(l) for _, o in variaveis_cols] + [l.valor] for l in linhas],
            "fonte": ibge.ROTULO,
            "url_fonte": res.url,
            "consultado_em": carimbo(res.consultado_em),
        }
        if notas:
            saida["notas"] = notas
        return saida

    @ferramenta(servidor)
    async def sidra_metadados(
        tabela: int, classificacao: str | None = None, filtro: str | None = None, limite: int = 60
    ) -> dict[str, Any]:
        """Descreve uma tabela do SIDRA: variáveis, classificações, categorias, períodos e níveis territoriais.

        Args:
            tabela: número da tabela.
            classificacao: id de uma classificação para listar suas categorias (ex.: "315").
            filtro: texto para filtrar as categorias pelo nome.
            limite: máximo de categorias listadas por classificação.
        """
        m = await ibge.metadados(tabela)
        per = m.get("periodicidade", {})
        termos = normalizar(filtro).split() if filtro else []
        alvo = str(classificacao).lower().lstrip("c") if classificacao else None
        classificacoes = []
        for c in m.get("classificacoes", []):
            categorias = c.get("categorias", [])
            item: dict[str, Any] = {"id": c["id"], "nome": c["nome"], "n_categorias": len(categorias)}
            detalhar = alvo == str(c["id"]) or (alvo is None and (termos or len(categorias) <= 15))
            if detalhar:
                escolhidas = [x for x in categorias if all(t in normalizar(x["nome"]) for t in termos)]
                item["categorias"] = [[x["id"], x["nome"]] for x in escolhidas[: max(1, limite)]]
                if len(escolhidas) > limite:
                    item["nota"] = f"{len(escolhidas)} categorias casam; exibidas {limite}. Use `filtro` para refinar."
            classificacoes.append(item)
        niveis = m.get("nivelTerritorial", {})
        return {
            "tabela": tabela,
            "nome": m.get("nome"),
            "pesquisa": m.get("pesquisa"),
            "assunto": m.get("assunto"),
            "periodicidade": per.get("frequencia"),
            "periodo_inicial": per.get("inicio"),
            "periodo_final": per.get("fim"),
            "variaveis": [[v["id"], v["nome"], v.get("unidade", "")] for v in m.get("variaveis", [])],
            "classificacoes": classificacoes,
            "niveis_territoriais": sorted({n for grupo in niveis.values() for n in grupo}),
            "dica": "Para listar as categorias de uma classificação grande, informe `classificacao` (e `filtro`).",
            "fonte": "IBGE/API de agregados",
            "url_fonte": f"https://servicodados.ibge.gov.br/api/v3/agregados/{tabela}/metadados",
        }

    @ferramenta(servidor)
    async def sidra_buscar_tabelas(consulta: str, limite: int = 25) -> dict[str, Any]:
        """Procura tabelas do SIDRA (IBGE) por palavras-chave no nome da tabela ou da pesquisa.

        Args:
            consulta: palavras-chave, ex.: "pib municípios", "rendimento domiciliar per capita".
            limite: máximo de resultados.
        """
        termos = normalizar(consulta).split()
        if not termos:
            raise ValueError("Informe ao menos uma palavra-chave.")
        achadas = [
            t
            for t in await ibge.listar_tabelas()
            if all(termo in normalizar(f"{t['nome']} {t['pesquisa']}") for termo in termos)
        ]
        achadas.sort(key=lambda t: t["tabela"], reverse=True)  # números maiores costumam ser as tabelas vigentes
        return {
            "total_encontrado": len(achadas),
            "tabelas": [[t["tabela"], t["nome"], t["pesquisa"]] for t in achadas[: max(1, limite)]],
            "colunas": ["tabela", "nome", "pesquisa"],
            "dica": "Tabelas antigas podem estar descontinuadas; confira o período final com sidra_metadados.",
            "fonte": "IBGE/API de agregados",
        }

    @ferramenta(servidor)
    async def calendario_divulgacoes(
        dias: int = 30, filtro: str | None = None, inicio: str | None = None
    ) -> dict[str, Any]:
        """Próximas divulgações de indicadores do IBGE e a data da próxima decisão do Copom.

        Args:
            dias: tamanho da janela, em dias, a partir de `inicio`.
            filtro: texto para filtrar pelo título (ex.: "IPCA", "PIB", "PNAD", "industrial").
            inicio: data inicial (AAAA-MM-DD); padrão: hoje. Datas passadas mostram o que já saiu.
        """
        d_ini = data_opcional(inicio) or date.today()
        d_fim = d_ini + timedelta(days=max(1, dias))
        itens, url, quando = await ibge.calendario(d_ini, d_fim)
        if filtro:
            termos = normalizar(filtro).split()
            itens = [i for i in itens if all(t in normalizar(i["titulo"]) for t in termos)]
        saida: dict[str, Any] = {
            "de": d_ini.isoformat(),
            "ate": d_fim.isoformat(),
            "colunas": ["data", "hora", "titulo", "referencia", "tipo"],
            "divulgacoes_ibge": [[i["data"], i["hora"], i["titulo"], i["referencia"], i["tipo"]] for i in itens],
            "fonte": "IBGE/Calendário de divulgações",
            "url_fonte": url,
            "consultado_em": carimbo(quando),
        }
        proxima = await fonte_copom.proxima_decisao()
        if proxima:
            saida["proxima_decisao_copom"] = proxima
        return saida
