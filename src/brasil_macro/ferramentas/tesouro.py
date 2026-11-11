"""Ferramentas do Tesouro Nacional: RTN, dívida pública federal, Tesouro Direto e SICONFI."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

from ..catalogo import normalizar
from ..fontes import tesouro
from ..fontes.tesouro import Linha, Tabela
from ..modelos import data_opcional, interpretar_data, rotulo_periodo
from ._comum import arredondar, carimbo, ferramenta

_MAX_CELULAS = 1500  # linhas × períodos devolvidos numa resposta
_MAX_TITULOS = 120  # títulos devolvidos numa cotação do dia
_MAX_LINHAS_SICONFI = 60  # contas devolvidas por padrão
_MAX_DIAS = 1000  # cotações devolvidas no histórico de um título
_ULTIMOS_PADRAO = {"mensal": 12, "trimestral": 8, "anual": 10}


def _filtrar_linhas(linhas: list[Linha], filtro: str | None, nivel: int) -> list[Linha]:
    """Seleciona linhas por código (com as sublinhas) ou por palavras do nome.

    Vários critérios podem ser separados por ponto e vírgula. Sem filtro, ficam
    as linhas até o nível de detalhe pedido.
    """
    criterios = [c.strip() for c in (filtro or "").split(";") if c.strip()]
    if not criterios:
        return [l for l in linhas if l.nivel <= nivel]
    escolhidas: list[Linha] = []
    for linha in linhas:
        texto = normalizar(f"{linha.secao} {linha.nome}")
        for criterio in criterios:
            if re.fullmatch(r"\d+(\.\d+)*\.?", criterio):
                codigo = criterio.rstrip(".")
                casa = linha.codigo == codigo or linha.codigo.startswith(codigo + ".")
            else:
                casa = all(termo in texto for termo in normalizar(criterio).split())
            if casa:
                escolhidas.append(linha)
                break
    return escolhidas


def _recortar(tabela: Tabela, inicio: str | None, fim: str | None, ultimos: int | None) -> list[int]:
    """Índices das colunas (períodos) pedidas."""
    if ultimos is not None and ultimos < 1:
        raise ValueError("ultimos deve ser um inteiro positivo.")
    d_ini, d_fim = data_opcional(inicio), data_opcional(fim, fim=True)
    indices = [
        i for i, d in enumerate(tabela.periodos) if (d_ini is None or d >= d_ini) and (d_fim is None or d <= d_fim)
    ]
    if ultimos is None and d_ini is None:
        ultimos = _ULTIMOS_PADRAO[tabela.periodicidade]
    return indices[-ultimos:] if ultimos else indices


def _montar_tabela(
    tabela: Tabela, linhas: list[Linha], colunas: list[int], casas: int = 2
) -> tuple[dict[str, Any], list[str]]:
    """Parte da resposta com períodos e linhas, respeitando o limite de células."""
    avisos: list[str] = []
    if colunas and len(linhas) * len(colunas) > _MAX_CELULAS:
        cabem = max(1, _MAX_CELULAS // len(colunas))
        avisos.append(
            f"Saída truncada: exibidas {cabem} de {len(linhas)} linhas. "
            "Restrinja o período ou filtre as linhas para ver o restante."
        )
        linhas = linhas[:cabem]
    corpo = []
    for linha in linhas:
        item: dict[str, Any] = {}
        if linha.codigo:
            item["linha"] = linha.codigo
        item["nome"] = linha.nome
        if linha.secao:
            item["secao"] = linha.secao
        item["valores"] = [arredondar(linha.valores[i], casas) for i in colunas]
        corpo.append(item)
    periodos = [rotulo_periodo(tabela.periodos[i], tabela.periodicidade) for i in colunas]
    return {"periodos": periodos, "linhas": corpo}, avisos


def _procedencia(tabela: Tabela, fonte: str) -> dict[str, Any]:
    saida: dict[str, Any] = {
        "fonte": fonte,
        "url_fonte": tabela.url,
        "pagina_fonte": tabela.pagina,
        "arquivo": tabela.arquivo,
    }
    if tabela.modificado:
        saida["arquivo_atualizado_em"] = tabela.modificado
    saida["consultado_em"] = carimbo(tabela.consultado_em)
    return saida


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def resultado_tesouro(
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        linhas: str | None = None,
        tabela: str = "1.1",
        nivel: int = 2,
        listar: bool = False,
    ) -> dict[str, Any]:
        """Resultado do Tesouro Nacional (RTN): receitas, despesas e resultado primário do governo central.

        Sinal: no RTN, positivo é superávit; é o oposto das NFSP do BCB. Sem período, traz os 12 meses mais recentes. Os principais agregados também estão no catálogo (rtn_*).

        Args:
            inicio, fim: período (AAAA, AAAA-MM ou AAAA-MM-DD). ultimos: períodos recentes.
            linhas: filtro por texto ("previdenciarios") ou número da linha ("4.3"); separe com ";".
            tabela: "1.1" resumida (padrão), "1.2" completa, "1.1-A"/"1.2-A" a preços constantes, "1.2-B" acumulado em 12 meses, "2.1"/"2.2" anuais, "2.1-A"/"2.2-A" em % do PIB, "1.3" investimento, "1.4" custeio.
            nivel: profundidade das linhas sem filtro (1 totais, 2 padrão, 5 tudo).
            listar: devolve as tabelas e as linhas disponíveis."""
        dados = await tesouro.tabela_rtn(tabela)
        saida: dict[str, Any] = {
            "tabela": dados.id,
            "titulo": dados.titulo,
            "unidade": dados.unidade,
            "periodicidade": dados.periodicidade,
            "periodo_disponivel": [
                rotulo_periodo(dados.periodos[0], dados.periodicidade),
                rotulo_periodo(dados.periodos[-1], dados.periodicidade),
            ],
            "convencao_sinal": tesouro.SINAL_RTN,
        }
        notas = list(dados.notas)
        if listar:
            saida["tabelas_disponiveis"] = await tesouro.tabelas_rtn()
            saida["linhas_disponiveis"] = [
                {k: v for k, v in (("linha", l.codigo), ("nome", l.nome), ("secao", l.secao)) if v}
                for l in dados.linhas
            ]
        else:
            escolhidas = _filtrar_linhas(dados.linhas, linhas, max(1, nivel))
            if not escolhidas:
                raise LookupError(
                    f"Nenhuma linha da tabela {dados.id} casa com {linhas!r}. "
                    "Chame resultado_tesouro com listar=true para ver as linhas disponíveis."
                )
            colunas = _recortar(dados, inicio, fim, ultimos)
            if not colunas:
                raise LookupError(
                    f"A tabela {dados.id} não tem dados no período pedido; ela cobre de "
                    f"{saida['periodo_disponivel'][0]} a {saida['periodo_disponivel'][1]}."
                )
            corpo, avisos = _montar_tabela(dados, escolhidas, colunas, tesouro.casas_decimais(dados.unidade))
            saida.update(corpo)
            notas.extend(avisos)
            if any(l.codigo in ("8", "9", "10") for l in escolhidas):
                notas.append(
                    "As linhas 6 a 10 (ajustes, resultado abaixo da linha, juros e resultado nominal) vêm do "
                    "Banco Central e saem com um mês de atraso; no mês mais recente ficam vazias."
                )
        saida.update(_procedencia(dados, tesouro.ROTULO_RTN))
        if notas:
            saida["notas"] = notas
        return saida

    @ferramenta(servidor)
    async def divida_publica_federal(
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        linhas: str | None = None,
        anexo: str = "2.1",
        listar: bool = False,
    ) -> dict[str, Any]:
        """Dívida Pública Federal (Tesouro): estoque por tipo e instrumento, e fatores de variação.

        Lê o Relatório Mensal da Dívida. Anexo 2.1 (padrão): estoque no fim do mês, em R$ bilhões, desde dez/2000. Anexo 2.9: emissões, resgates e juros apropriados, em R$ milhões. Sem período, traz os 12 meses mais recentes. Não é a DBGG do BCB, que tem outro perímetro. Principais itens no catálogo (dpf_*).

        Args:
            inicio, fim: mês (AAAA-MM). ultimos: meses recentes.
            linhas: filtro por texto ("lft", "dpfe", "juros"); separe com ";".
            anexo: "2.1" (padrão) ou "2.9".
            listar: só a lista de linhas do anexo."""
        dados = await tesouro.tabela_dpf(anexo)
        saida: dict[str, Any] = {
            "anexo": dados.id,
            "titulo": dados.titulo,
            "unidade": dados.unidade,
            "periodicidade": dados.periodicidade,
            "periodo_disponivel": [
                rotulo_periodo(dados.periodos[0], dados.periodicidade),
                rotulo_periodo(dados.periodos[-1], dados.periodicidade),
            ],
        }
        notas = list(dados.notas)
        if listar:
            saida["linhas_disponiveis"] = [
                {k: v for k, v in (("nome", l.nome), ("secao", l.secao)) if v} for l in dados.linhas
            ]
        else:
            escolhidas = _filtrar_linhas(dados.linhas, linhas, 1)
            if not escolhidas:
                raise LookupError(
                    f"Nenhuma linha do anexo {dados.id} casa com {linhas!r}. "
                    "Chame divida_publica_federal com listar=true para ver as linhas disponíveis."
                )
            colunas = _recortar(dados, inicio, fim, ultimos)
            if not colunas:
                raise LookupError(
                    f"O anexo {dados.id} não tem dados no período pedido; ele cobre de "
                    f"{saida['periodo_disponivel'][0]} a {saida['periodo_disponivel'][1]}."
                )
            corpo, avisos = _montar_tabela(dados, escolhidas, colunas)
            saida.update(corpo)
            notas.extend(avisos)
            if dados.id == "2.1":
                notas.append(
                    "DPF em poder do público = DPMFi + DPFe. A linha 'DPMFi EM PODER DO BANCO CENTRAL' é uma carteira à "
                    "parte e não entra nessa soma. Estoque pela curva de rentabilidade; DPFe convertida pela cotação do último dia do mês."
                )
        saida.update(_procedencia(dados, tesouro.ROTULO_DPF))
        if notas:
            saida["notas"] = notas
        return saida

    @ferramenta(servidor)
    async def tesouro_direto_taxas(
        tipo: str | None = None,
        vencimento: str | None = None,
        data: str | None = None,
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
    ) -> dict[str, Any]:
        """Taxas e preços dos títulos do Tesouro Direto (mercado secundário).

        Sem `vencimento`, lista as cotações de todos os títulos numa data (padrão: a mais recente). Com `vencimento`, devolve o histórico de um título. Taxas em % ao ano; preços (PU) em R$. Taxa de compra é a do investidor que compra (menor que a de venda). Dados desde 2004; o arquivo oficial é grande e fica em cache.

        Args:
            tipo: parte do nome: "selic", "prefixado", "ipca+", "renda+", "educa+". Vazio = todos.
            vencimento: ano, mês ou dia (ex.: "2035-05"). Para histórico, precisa identificar um título.
            data: dia da cotação (AAAA-MM-DD); sem pregão, usa o anterior.
            inicio, fim: período do histórico. ultimos: cotações recentes do histórico (padrão 30; máximo 1000)."""
        dados = await tesouro.dados_tesouro_direto()
        notas = list(dados.notas)
        saida: dict[str, Any] = {
            "unidade": "taxas em % ao ano; PU (preço unitário) em R$",
            "periodo_disponivel": [dados.datas[0].isoformat(), dados.datas[-1].isoformat()],
        }
        titulos = tesouro.titulos_td(dados, tipo, vencimento)
        if not titulos:
            raise LookupError(
                f"Nenhum título do Tesouro Direto casa com tipo={tipo!r} e vencimento={vencimento!r}. "
                f"Tipos disponíveis: {', '.join(dados.tipos())}."
            )
        if vencimento and vencimento.strip():
            if len(titulos) > 1:
                opcoes = "; ".join(f"{t} {v.isoformat()}" for t, v in titulos[:12])
                raise ValueError(
                    f"O vencimento {vencimento!r} identifica {len(titulos)} títulos ({opcoes}). "
                    "Informe o tipo exato e o dia de vencimento para escolher um."
                )
            nome, venc = titulos[0]
            cotacoes = dados.por_titulo[(nome, venc)]
            d_ini, d_fim = data_opcional(inicio), data_opcional(fim, fim=True)
            sel = [c for c in cotacoes if (d_ini is None or c[0] >= d_ini) and (d_fim is None or c[0] <= d_fim)]
            if ultimos is not None and ultimos < 1:
                raise ValueError("ultimos deve ser um inteiro positivo.")
            n = min(ultimos or (len(sel) if d_ini else 30), _MAX_DIAS)
            if len(sel) > n:
                if ultimos is None and d_ini is not None:
                    notas.append(f"Histórico truncado: {len(sel)} cotações no período; exibidas as {n} mais recentes.")
                sel = sel[-n:]
            if not sel:
                raise LookupError(
                    f"O título {nome} {venc.isoformat()} não tem cotações no período; "
                    f"ele cobre de {cotacoes[0][0].isoformat()} a {cotacoes[-1][0].isoformat()}."
                )
            saida.update(
                {
                    "titulo": nome,
                    "vencimento": venc.isoformat(),
                    "cotacoes_de": cotacoes[0][0].isoformat(),
                    "cotacoes_ate": cotacoes[-1][0].isoformat(),
                    "colunas": ["data", "taxa_compra", "taxa_venda", "pu_compra", "pu_venda"],
                    "dados": [[c[0].isoformat(), c[1], c[2], c[3], c[4]] for c in sel],
                    "n_observacoes": len(sel),
                }
            )
        else:
            pedida = interpretar_data(data, fim=True) if data and data.strip() else None
            dia = max((d for d in dados.datas if pedida is None or d <= pedida), default=None)
            if dia is None:
                raise LookupError(f"Não há cotações até {pedida}; o arquivo começa em {dados.datas[0].isoformat()}.")
            if pedida is not None and dia != pedida:
                notas.append(f"Sem cotação em {pedida.isoformat()}; usado o último dia anterior com pregão ({dia.isoformat()}).")
            linhas = []
            for nome, venc in titulos:
                c = next((c for c in reversed(dados.por_titulo[(nome, venc)]) if c[0] == dia), None)
                if c:
                    linhas.append([nome, venc.isoformat(), c[1], c[2], c[3], c[4]])
            if not linhas:
                raise LookupError(f"Nenhum título casa com tipo={tipo!r} em {dia.isoformat()}.")
            if len(linhas) > _MAX_TITULOS:
                notas.append(f"Saída truncada: exibidos {_MAX_TITULOS} de {len(linhas)} títulos. Filtre por tipo.")
                linhas = linhas[:_MAX_TITULOS]
            saida.update(
                {
                    "data": dia.isoformat(),
                    "colunas": ["tipo", "vencimento", "taxa_compra", "taxa_venda", "pu_compra", "pu_venda"],
                    "titulos": linhas,
                    "n_titulos": len(linhas),
                }
            )
        saida.update(_procedencia(dados, tesouro.ROTULO_TD))  # type: ignore[arg-type]
        if notas:
            saida["notas"] = notas
        return saida

    registrar_siconfi(servidor)


def _anexo_siconfi(relatorio: str, anexo: str) -> str:
    """"1" -> "RREO-Anexo 01"; "I-C" -> "DCA-Anexo I-C"; nomes completos passam como estão."""
    texto = " ".join(anexo.split())
    if normalizar(texto).startswith(normalizar(relatorio)):
        return texto
    if texto.isdigit():
        texto = f"{int(texto):02d}"
    return f"{relatorio}-Anexo {texto}"


def _rotulo_ente(e: dict[str, Any]) -> str:
    esfera = {"U": "União", "E": "estado", "D": "Distrito Federal", "M": "município"}.get(e.get("esfera"), "")
    uf = f", {e['uf']}" if e.get("uf") and e["uf"] != "BR" else ""
    return f"{e['ente']} ({esfera}{uf}) — código {e['cod_ibge']}"


async def _resolver_ente(ente: str, uf: str | None, esfera: str | None) -> dict[str, Any]:
    entes = await tesouro.entes_siconfi()
    achados = tesouro.buscar_entes(entes, ente, uf, esfera)
    if not achados:
        raise LookupError(
            f"Nenhum ente do SICONFI casa com {ente!r}. Procure com siconfi_entes (nome, UF ou código IBGE)."
        )
    uniao = [x for x in achados if x.get("esfera") == "U"]
    if len(achados) > 1 and len(uniao) == 1:
        return uniao[0]  # "União" também é nome de um município do Piauí
    if len(achados) > 1:
        opcoes = "; ".join(_rotulo_ente(e) for e in achados[:8])
        raise ValueError(
            f"{ente!r} corresponde a {len(achados)} entes: {opcoes}. "
            "Informe o código IBGE, ou use uf e esfera (E = estado, M = município) para escolher."
        )
    return achados[0]


def _sem_dados(tipo: str, e: dict[str, Any], ano: int, per: int | None) -> str:
    quando = f"{ano}" + (f", período {per}" if per else "")
    return (
        f"O SICONFI não tem {tipo} de {e['ente']} (código {e['cod_ibge']}) para {quando}. "
        "O ente pode não ter entregue o demonstrativo, ou o período ainda não foi publicado; "
        "tente outro exercício ou período, ou (RGF) outro poder/periodicidade, ou simplificado=true."
    )


def _tabela_siconfi(
    saida: dict[str, Any],
    itens: list[dict[str, Any]],
    conta: str | None,
    coluna: str | None,
    max_linhas: int,
    notas: list[str],
) -> None:
    """Monta a tabela contas × colunas de um anexo, com filtros e limite de linhas."""
    termos_conta = normalizar(conta or "").split()
    termos_coluna = normalizar(coluna or "").split()
    colunas: dict[str, None] = {}  # na ordem em que aparecem
    contas: dict[tuple[str, str, str], dict[str, float | None]] = {}
    repetidos = 0
    for it in itens:
        if termos_conta and not all(t in normalizar(it["conta"]) for t in termos_conta):
            continue
        if termos_coluna and not all(t in normalizar(it["coluna"]) for t in termos_coluna):
            continue
        colunas[it["coluna"]] = None
        linha = contas.setdefault((it.get("rotulo") or "", it.get("cod_conta") or "", it["conta"]), {})
        if it["coluna"] in linha:
            repetidos += 1
        linha[it["coluna"]] = it.get("valor")
    if not contas:
        raise LookupError(
            f"Nenhuma conta/coluna do anexo casa com conta={conta!r}, coluna={coluna!r}. "
            "Retire os filtros para ver as contas disponíveis."
        )
    nomes = list(colunas)
    com_rotulo = any(r and r != "Padrão" for r, _, _ in contas)
    nomes_repetidos = {n for n, c in Counter(n for _, _, n in contas).items() if c > 1}
    total = len(contas)
    corpo = []
    for (rotulo, cod, nome), valores in list(contas.items())[:max_linhas]:
        item: dict[str, Any] = {"conta": nome}
        if nome in nomes_repetidos and cod:
            item["cod_conta"] = cod  # a mesma conta aparece em blocos diferentes (ex.: intra-orçamentárias)
        if com_rotulo and rotulo and rotulo != "Padrão":
            item["rotulo"] = rotulo
        item["valores"] = [arredondar(valores.get(c), 2) for c in nomes]
        corpo.append(item)
    # Anexos largos têm colunas que só valem para outros blocos de contas: some o que está vazio no que é exibido.
    usadas = [i for i in range(len(nomes)) if any(l["valores"][i] is not None for l in corpo)]
    for l in corpo:
        l["valores"] = [l["valores"][i] for i in usadas]
    nomes = [nomes[i] for i in usadas]
    saida["unidade"] = "R$ (reais), salvo colunas expressas em %"
    saida["colunas"] = nomes
    saida["linhas"] = corpo
    saida["n_contas"] = total
    if total > max_linhas:
        notas.append(
            f"Saída truncada: exibidas {max_linhas} de {total} contas. Use conta= ou coluna= para filtrar, ou max_linhas."
        )
    if repetidos:
        notas.append(
            f"{repetidos} valores repetidos para a mesma conta e coluna (rótulos diferentes na fonte); prevalece o último."
        )


def registrar_siconfi(servidor) -> None:
    @ferramenta(servidor)
    async def siconfi_entes(
        busca: str | None = None, uf: str | None = None, esfera: str | None = None, limite: int = 20
    ) -> dict[str, Any]:
        """Procura o código de um ente (União, estado, DF ou município) no SICONFI. O código IBGE é o que siconfi_relatorio usa.

        Args:
            busca: parte do nome ("campinas") ou código IBGE. Vazio = lista pelos filtros.
            uf: sigla da UF, para restringir.
            esfera: "U", "E", "D" ou "M".
            limite: entes devolvidos (padrão 20)."""
        entes = await tesouro.entes_siconfi()
        achados = tesouro.buscar_entes(entes, busca, uf, esfera)
        limite = max(1, min(limite, 200))
        saida: dict[str, Any] = {
            "n_encontrados": len(achados),
            "entes": [
                {
                    "codigo": e["cod_ibge"],
                    "nome": e["ente"],
                    "esfera": e.get("esfera"),
                    "uf": None if e.get("uf") == "BR" else e.get("uf"),
                    "populacao": e.get("populacao"),
                }
                for e in achados[:limite]
            ],
            "fonte": tesouro.ROTULO_SICONFI,
            "url_fonte": tesouro.PAGINA_SICONFI,
            "consultado_em": carimbo(),
        }
        if len(achados) > limite:
            saida["notas"] = [f"Exibidos {limite} de {len(achados)} entes; restrinja a busca por uf ou esfera."]
        return saida

    @ferramenta(servidor)
    async def siconfi_relatorio(
        relatorio: str,
        ente: str,
        exercicio: int | None = None,
        periodo: int | None = None,
        anexo: str | None = None,
        conta: str | None = None,
        coluna: str | None = None,
        uf: str | None = None,
        esfera: str | None = None,
        poder: str = "E",
        periodicidade: str = "Q",
        simplificado: bool = False,
        max_linhas: int = _MAX_LINHAS_SICONFI,
    ) -> dict[str, Any]:
        """Demonstrativos fiscais do SICONFI, declarados pelos entes, em R$: RREO (bimestral), RGF (quadrimestral) e DCA (anual).

        Sem `anexo`, lista os anexos disponíveis; com `anexo`, devolve as contas em colunas. Para achar o código do ente, use siconfi_entes.

        Args:
            relatorio: "RREO", "RGF" ou "DCA".
            ente: código IBGE (35 = SP, 3550308 = São Paulo, 1 = União) ou nome; nome ambíguo pede uf ou esfera.
            exercicio: ano (padrão: ano anterior).
            periodo: RREO, bimestre 1 a 6 (padrão 6); RGF, quadrimestre 1 a 3 ou semestre 1 a 2. Não vale para DCA.
            anexo: ex.: "01" (RREO, balanço orçamentário), "02" (RGF, dívida consolidada), "I-C" (DCA); aceita o nome.
            conta: trecho do nome da conta, ex.: "pessoal".
            coluna: trecho do nome da coluna, ex.: "empenhadas".
            uf: sigla, para desambiguar o ente. esfera: "U", "E", "D" ou "M".
            poder: só RGF: "E" (padrão), "L", "J", "M" ou "D".
            periodicidade: só RGF: "Q" (padrão) ou "S".
            simplificado: demonstrativo simplificado (municípios com menos de 50 mil habitantes).
            max_linhas: contas devolvidas (padrão 60)."""
        tipo = relatorio.strip().upper()
        if tipo not in ("RREO", "RGF", "DCA"):
            raise ValueError("relatorio deve ser RREO, RGF ou DCA.")
        e = await _resolver_ente(ente, uf, esfera)
        ano = exercicio or date.today().year - 1
        params: dict[str, Any] = {"an_exercicio": ano, "id_ente": e["cod_ibge"]}
        per: int | None = None
        if tipo == "RREO":
            per = periodo or 6
            if not 1 <= per <= 6:
                raise ValueError("Para o RREO, periodo é o bimestre, de 1 a 6.")
            params.update(nr_periodo=per, co_tipo_demonstrativo="RREO Simplificado" if simplificado else "RREO")
        elif tipo == "RGF":
            quad = periodicidade.strip().upper() not in ("S", "SEMESTRAL")
            per = periodo or (3 if quad else 2)
            if not 1 <= per <= (3 if quad else 2):
                raise ValueError("Para o RGF, periodo é o quadrimestre (1 a 3) ou o semestre (1 a 2).")
            if poder.strip().upper() not in ("E", "L", "J", "M", "D"):
                raise ValueError("poder deve ser E, L, J, M ou D.")
            params.update(
                in_periodicidade="Q" if quad else "S",
                nr_periodo=per,
                co_tipo_demonstrativo="RGF Simplificado" if simplificado else "RGF",
                co_poder=poder.strip().upper(),
            )
        endpoint = tipo.lower()
        saida: dict[str, Any] = {
            "relatorio": tipo,
            "ente": {"codigo": e["cod_ibge"], "nome": e["ente"], "esfera": e.get("esfera")},
            "exercicio": ano,
        }
        if per is not None:
            saida["periodo"] = per
        notas: list[str] = []

        if not anexo or not anexo.strip():
            consulta = await tesouro.consultar_siconfi(endpoint, params, max_paginas=3)
            if not consulta.itens:
                raise LookupError(_sem_dados(tipo, e, ano, per))
            colunas_por_anexo: dict[str, set[str]] = {}
            registros: dict[str, int] = {}
            for it in consulta.itens:
                colunas_por_anexo.setdefault(it["anexo"], set()).add(it["coluna"])
                registros[it["anexo"]] = registros.get(it["anexo"], 0) + 1
            saida["anexos"] = [
                {"anexo": a, "n_registros": registros[a], "n_colunas": len(c)}
                for a, c in sorted(colunas_por_anexo.items())
            ]
            notas.append("Informe `anexo` (nome exato ou só o número) para ver as contas e valores de um deles.")
            if consulta.truncada:
                notas.append("Lista possivelmente incompleta: o relatório tem mais registros que o limite lido.")
        else:
            nome_anexo = _anexo_siconfi(tipo, anexo)
            consulta = await tesouro.consultar_siconfi(endpoint, {**params, "no_anexo": nome_anexo}, max_paginas=5)
            if not consulta.itens:
                geral = await tesouro.consultar_siconfi(endpoint, params, max_paginas=1)
                if not geral.itens:
                    raise LookupError(_sem_dados(tipo, e, ano, per))
                disponiveis = ", ".join(sorted({it["anexo"] for it in geral.itens}))
                raise LookupError(
                    f"O anexo {nome_anexo!r} não existe neste relatório. Anexos encontrados: {disponiveis}."
                )
            saida["anexo"] = nome_anexo
            _tabela_siconfi(saida, consulta.itens, conta, coluna, max(1, min(max_linhas, 300)), notas)
            if consulta.truncada:
                notas.append("Resposta da fonte truncada no limite de páginas lidas; o anexo pode estar incompleto.")
        saida["fonte"] = tesouro.ROTULO_SICONFI
        saida["url_fonte"] = consulta.url
        saida["pagina_fonte"] = tesouro.PAGINA_SICONFI
        saida["consultado_em"] = carimbo(consulta.consultado_em)
        notas.extend(consulta.notas)
        if notas:
            saida["notas"] = notas
        return saida
