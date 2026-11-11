"""Tesouro Nacional — Resultado do Tesouro Nacional (RTN), publicado no Tesouro Transparente.

O Tesouro não oferece API para as séries do RTN: o recurso "API Séries
Temporais" listado no catálogo de dados abertos responde 404. A fonte é a
planilha "Série Histórica" do conjunto de dados, cujo endereço muda a cada mês
e por isso é resolvido pelo CKAN a cada consulta.
"""

from __future__ import annotations

import asyncio
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Awaitable, Callable

from ..catalogo import Indicador, fonte_de_dados, normalizar
from ..http import TTL_CURTO, TTL_MEDIO, ErroFonte, Resposta, requisitar
from ..modelos import Observacao, Serie, interpretar_data

ROTULO_RTN = "Tesouro Nacional/RTN"

_CKAN = "https://www.tesourotransparente.gov.br/ckan/api/3/action/package_show"
_PAGINA_CKAN = "https://www.tesourotransparente.gov.br/ckan/dataset/{conjunto}"

_RTN_CONJUNTO = "resultado-do-tesouro-nacional"
_RTN_RECURSO = "527ccdb1-3059-42f3-bf23-b5e3ab4c6dc6"

SINAL_RTN = (
    "Convenção do RTN: resultado positivo = superávit, negativo = déficit. "
    "É o oposto das séries NFSP do Banco Central, em que positivo = déficit."
)


# ----------------------------------------------------------- infraestrutura


@dataclass(frozen=True)
class Recurso:
    """Arquivo de um conjunto de dados do Tesouro Transparente."""

    url: str
    nome: str
    arquivo: str
    modificado: str  # data da última carga no CKAN (AAAA-MM-DD), quando informada
    pagina: str


async def recurso_ckan(conjunto: str, id_recurso: str, formato: str) -> Recurso:
    """Localiza o endereço atual de download de um recurso do CKAN do Tesouro."""
    r = await requisitar(_CKAN, params={"id": conjunto}, ttl=TTL_MEDIO, espera_json=True)
    recursos = (r.json().get("result") or {}).get("resources") or []
    achado = next((x for x in recursos if x.get("id") == id_recurso), None)
    if achado is None:
        # O id costuma ser estável; se o Tesouro recriar o recurso, vale o formato.
        achado = next((x for x in recursos if (x.get("format") or "").upper() == formato.upper()), None)
    if achado is None or not achado.get("url"):
        raise ErroFonte(
            f"O conjunto de dados {conjunto!r} do Tesouro Transparente não tem mais um recurso {formato}. "
            f"Confira em {_PAGINA_CKAN.format(conjunto=conjunto)}."
        )
    url = achado["url"]
    return Recurso(
        url=url,
        nome=achado.get("name") or conjunto,
        arquivo=url.rsplit("/", 1)[-1],
        modificado=(achado.get("last_modified") or "")[:10],
        pagina=_PAGINA_CKAN.format(conjunto=conjunto),
    )


_em_curso: dict[tuple[int, str], asyncio.Future] = {}


async def _uma_vez(chave: str, fabrica: Callable[[], Awaitable[Any]]) -> Any:
    """Compartilha uma operação em andamento entre chamadas simultâneas.

    O painel pede vários indicadores ao mesmo tempo; sem isto, cada um baixaria
    de novo o mesmo arquivo de vários megabytes.
    """
    laco = asyncio.get_running_loop()
    k = (id(laco), chave)
    tarefa = _em_curso.get(k)
    if tarefa is None:
        tarefa = laco.create_task(fabrica())  # type: ignore[arg-type]
        _em_curso[k] = tarefa
        tarefa.add_done_callback(lambda _: _em_curso.pop(k, None))
    return await asyncio.shield(tarefa)


async def _baixar(url: str) -> Resposta:
    return await _uma_vez(f"baixar:{url}", lambda: requisitar(url, ttl=TTL_MEDIO, tempo_limite=180.0))


# Resultado da leitura de cada arquivo, para não reprocessar a cada chamada.
# A chave inclui o momento da consulta, que muda quando o arquivo é baixado de novo.
_memo: dict[tuple, Any] = {}


async def _memorizado(chave: tuple, calcular: Callable[[], Any]) -> Any:
    async def fazer() -> Any:
        if chave not in _memo:
            if len(_memo) >= 48:
                _memo.clear()
            _memo[chave] = await asyncio.to_thread(calcular)
        return _memo[chave]

    return await _uma_vez(f"memo:{chave!r}", fazer)


# ------------------------------------------------------- tabelas em planilha


@dataclass
class Linha:
    codigo: str  # numeração da linha na planilha ("4.3.01"); vazio se não houver
    nome: str  # rótulo sem a numeração e sem a chamada de nota de rodapé
    secao: str  # título do bloco, para linhas sem numeração (ex.: "Memorando")
    valores: list[float | None]

    @property
    def nivel(self) -> int:
        return self.codigo.count(".") + 1


@dataclass
class Tabela:
    """Tabela "linhas × períodos" lida de uma planilha do Tesouro."""

    id: str
    titulo: str
    unidade: str
    periodicidade: str
    periodos: list[date]
    linhas: list[Linha]
    url: str = ""
    arquivo: str = ""
    modificado: str = ""
    pagina: str = ""
    consultado_em: datetime | None = None
    notas: list[str] = field(default_factory=list)


_TRIMESTRES = {"I": 1, "II": 4, "III": 7, "IV": 10}
_CODIGO = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|$)\s*(?:-\s+)?(.*)$")
_NOTA_RODAPE = re.compile(r"\s*\d+/\s*$")
_FIM_DOS_DADOS = re.compile(r"^(obs\b|obs\.|\d+/\s)", re.I)


def _periodo(celula: Any) -> tuple[date, str] | None:
    """Interpreta uma célula de cabeçalho como período: (data inicial, periodicidade)."""
    if isinstance(celula, datetime):
        return date(celula.year, celula.month, 1), "mensal"
    if isinstance(celula, int) and not isinstance(celula, bool) and 1900 <= celula <= 2100:
        return date(celula, 1, 1), "anual"
    if isinstance(celula, str) and (m := re.fullmatch(r"\s*(\d{4})-(I{1,3}|IV)\s*", celula)):
        return date(int(m[1]), _TRIMESTRES[m[2]], 1), "trimestral"
    return None


def _numero(celula: Any) -> float | None:
    if isinstance(celula, bool) or not isinstance(celula, (int, float)):
        return None  # vazio ou texto como "Não Disp"
    return float(celula)


def _separar_rotulo(texto: str) -> tuple[str, str]:
    """'4.3.07  Créditos Extraordinários 16/' -> ('4.3.07', 'Créditos Extraordinários')."""
    limpo = _NOTA_RODAPE.sub("", " ".join(texto.split()))
    if m := _CODIGO.match(limpo):
        return m[1], m[2].strip()
    return "", limpo


def ler_tabela(linhas: list[tuple], id_: str) -> Tabela:
    """Converte as linhas brutas de uma aba numa `Tabela`.

    Formato das abas do RTN: título e unidade nas primeiras linhas, uma linha
    de cabeçalho com os períodos (datas, anos ou "2006-I") e, abaixo, uma linha
    por item, com o rótulo na coluna imediatamente à esquerda do primeiro período.
    """
    cabecalho = None
    for i, linha in enumerate(linhas[:15]):
        periodos = [(j, p) for j, c in enumerate(linha) if (p := _periodo(c))]
        if len(periodos) >= 3:
            datas = [p[0] for _, p in periodos]
            if datas != sorted(set(datas)):
                break  # períodos repetidos: tabela comparativa (3.1, 3.2), não é série
            cabecalho = i
            break
    if cabecalho is None:
        raise ValueError(f"A tabela {id_} não é uma série histórica (não tem uma linha de períodos crescentes).")
    colunas = [j for j, _ in periodos]
    col_rotulo = max(colunas[0] - 1, 0)

    textos = [str(l[0]).strip() for l in linhas[:cabecalho] if l and isinstance(l[0], str) and l[0].strip()]
    textos = [t for t in textos if normalizar(t) != "voltar"]
    titulo = " ".join(textos[0].split()) if textos else f"Tabela {id_}"
    unidade = " ".join(textos[1].split()) if len(textos) > 1 else ""
    # As tabelas em % do PIB guardam frações (0,167 para 16,7%).
    fator = 100.0 if normalizar(unidade) == "do pib" else 1.0

    itens: list[Linha] = []
    secao = ""
    for linha in linhas[cabecalho + 1 :]:
        rotulo = linha[col_rotulo] if len(linha) > col_rotulo else None
        if not isinstance(rotulo, str) or not rotulo.strip():
            continue
        if col_rotulo == 0 and _FIM_DOS_DADOS.match(rotulo.strip()):
            break
        valores = [_numero(linha[j]) if j < len(linha) else None for j in colunas]
        if col_rotulo > 0:
            codigo, nome = str(linha[0]).strip() if linha[0] is not None else "", " ".join(rotulo.split())
        else:
            codigo, nome = _separar_rotulo(rotulo)
        if all(v is None for v in valores) or nome.endswith(":"):
            secao = nome.rstrip(":")  # título de bloco, como "Memorando:" (que repete os anos)
            continue
        if fator != 1.0 and "R$" not in nome:
            valores = [None if v is None else v * fator for v in valores]
        itens.append(Linha(codigo, nome, "" if codigo else secao, valores))
    if not itens:
        raise ValueError(f"A tabela {id_} não tem linhas de dados.")
    return Tabela(
        id=id_,
        titulo=titulo,
        unidade=unidade,
        periodicidade=periodos[0][1][1],
        periodos=[p[0] for _, p in periodos],
        linhas=itens,
    )


def localizar_linha(tabela: Tabela, codigo: str | None = None, confere: str | None = None) -> Linha:
    """Acha uma linha pela numeração e confere o texto esperado no rótulo.

    A conferência protege contra renumeração: se o Tesouro inserir uma linha e
    deslocar os códigos, a busca cai para o texto em vez de devolver outra série.
    """
    alvo = normalizar(confere or "")
    if codigo:
        candidatas = [l for l in tabela.linhas if l.codigo == codigo]
        if len(candidatas) == 1 and (not alvo or alvo in normalizar(candidatas[0].nome)):
            return candidatas[0]
    if alvo:
        exatas = [l for l in tabela.linhas if normalizar(l.nome) == alvo]
        contidas = exatas or [l for l in tabela.linhas if alvo in normalizar(l.nome)]
        if len(contidas) == 1:
            return contidas[0]
    descricao = " / ".join(x for x in (codigo, confere) if x)
    raise ErroFonte(
        f"A tabela {tabela.id} ({tabela.titulo}) não tem uma linha única correspondente a {descricao!r}. "
        "A estrutura da planilha do Tesouro pode ter mudado; liste as linhas disponíveis com a ferramenta da fonte."
    )


def casas_decimais(unidade: str) -> int:
    """Valores em R$ milhões ou bilhões saem com 2 casas; percentuais, com 4."""
    return 4 if "%" in unidade else 2


def observacoes(tabela: Tabela, valores: list[float | None], inicio: date | None, fim: date | None) -> list[Observacao]:
    casas = casas_decimais(tabela.unidade)
    return [
        (d, round(v, casas))
        for d, v in zip(tabela.periodos, valores)
        if v is not None and (inicio is None or d >= inicio) and (fim is None or d <= fim)
    ]


def _ler_abas(corpo: bytes) -> dict[str, list[tuple]]:
    from openpyxl import load_workbook

    livro = load_workbook(io.BytesIO(corpo), read_only=True, data_only=True)
    try:
        return {aba.title.strip(): list(aba.iter_rows(values_only=True)) for aba in livro.worksheets}
    finally:
        livro.close()


async def _planilha(recurso: Recurso) -> tuple[Resposta, dict[str, list[tuple]]]:
    """Baixa a planilha e devolve as linhas brutas de cada aba."""
    r = await _baixar(recurso.url)
    try:
        abas = await _memorizado(("xlsx", r.url, r.consultado_em), lambda: _ler_abas(r.corpo))
    except Exception as erro:  # arquivo corrompido ou que deixou de ser XLSX
        if isinstance(erro, (ErroFonte, asyncio.CancelledError)):
            raise
        raise ErroFonte(f"Não foi possível ler a planilha {recurso.arquivo} do Tesouro ({erro}).") from erro
    return r, abas


def _carimbar(tabela: Tabela, recurso: Recurso, r: Resposta) -> Tabela:
    tabela.url = r.url
    tabela.arquivo = recurso.arquivo
    tabela.modificado = recurso.modificado
    tabela.pagina = recurso.pagina
    tabela.consultado_em = r.consultado_em
    if r.desatualizado:
        tabela.notas.append("A fonte está fora do ar: dados de uma cópia antiga guardada em cache.")
    return tabela


# ------------------------------------------------------------------------ RTN


def _id_tabela(texto: str) -> str:
    """'1.4 - A' e '1.1.' viram '1.4-A' e '1.1', como nos nomes das abas."""
    return re.sub(r"\s+", "", texto).rstrip(".").upper()


async def _planilha_rtn() -> tuple[Recurso, Resposta, dict[str, list[tuple]]]:
    recurso = await recurso_ckan(_RTN_CONJUNTO, _RTN_RECURSO, "XLSX")
    r, abas = await _planilha(recurso)
    return recurso, r, abas


async def tabelas_rtn() -> dict[str, str]:
    """Tabelas da série histórica do RTN que podem ser consultadas: id -> descrição."""
    _, r, abas = await _planilha_rtn()

    def montar() -> dict[str, str]:
        descricoes = {}
        for linha in abas.get("Índice", []):
            if len(linha) > 1 and isinstance(linha[0], str) and isinstance(linha[1], str):
                descricoes[_id_tabela(linha[0])] = " ".join(linha[1].split())
        saida = {}
        for nome, linhas in abas.items():
            try:
                tabela = ler_tabela(linhas, nome)
            except ValueError:
                continue  # índice e tabelas comparativas (3.1, 3.2) não são séries
            saida[nome] = descricoes.get(_id_tabela(nome)) or tabela.titulo
        return saida

    return await _memorizado(("rtn-tabelas", r.url, r.consultado_em), montar)


async def tabela_rtn(tabela: str = "1.1") -> Tabela:
    """Lê uma tabela da série histórica do RTN (ex.: "1.1", "1.2-B", "2.1-A")."""
    recurso, r, abas = await _planilha_rtn()
    alvo = _id_tabela(tabela)
    nome = next((n for n in abas if _id_tabela(n) == alvo), None)
    if nome is None:
        raise ValueError(f"A série histórica do RTN não tem a tabela {tabela!r}. Tabelas: {', '.join(await tabelas_rtn())}.")
    try:
        lida: Tabela = await _memorizado(("rtn", r.url, r.consultado_em, nome), lambda: ler_tabela(abas[nome], nome))
    except ValueError as erro:
        raise ValueError(f"{erro} Tabelas disponíveis: {', '.join(await tabelas_rtn())}.") from None
    # Cópia rasa: o carimbo e as notas são de cada consulta, as linhas são compartilhadas.
    return _carimbar(Tabela(lida.id, lida.titulo, lida.unidade, lida.periodicidade, lida.periodos, lida.linhas), recurso, r)


def _parcela(parametro: Any) -> tuple[str | None, str | None]:
    if isinstance(parametro, dict):
        codigo = parametro.get("linha")
        return (str(codigo) if codigo is not None else None), parametro.get("confere")
    return str(parametro), None


def combinar_linhas(tabela: Tabela, parametros: dict[str, Any]) -> tuple[list[float | None], str]:
    """Valores de uma linha, opcionalmente somada (`mais`) ou subtraída (`menos`) de outras.

    Devolve também a descrição do cálculo, para constar no código da série.
    """
    principal = localizar_linha(tabela, *_parcela(parametros))
    valores = list(principal.valores)
    descricao = f"linha {principal.codigo or principal.nome}"
    for chave, sinal, simbolo in (("mais", 1.0, "+"), ("menos", -1.0, "−")):
        for item in parametros.get(chave) or []:
            outra = localizar_linha(tabela, *_parcela(item))
            valores = [None if a is None or b is None else a + sinal * b for a, b in zip(valores, outra.valores)]
            descricao += f" {simbolo} linha {outra.codigo or outra.nome}"
    return valores, descricao


@fonte_de_dados("rtn", ROTULO_RTN)
async def _buscar_rtn(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    p = ind.parametros if isinstance(ind.parametros, dict) else {"linha": ind.parametros}
    tabela = await tabela_rtn(str(p.get("tabela", "1.1")))
    valores, descricao = combinar_linhas(tabela, p)
    notas = list(tabela.notas)
    if "constantes" in normalizar(ind.unidade) or "ipca" in normalizar(tabela.unidade):
        notas.append(f"Unidade na planilha: {tabela.unidade}. A data-base dos preços muda a cada divulgação.")
    return Serie(
        nome=ind.nome,
        fonte=ROTULO_RTN,
        codigo=f"RTN série histórica, tabela {tabela.id}, {descricao} ({tabela.arquivo})",
        unidade=tabela.unidade,
        periodicidade=tabela.periodicidade,
        observacoes=observacoes(tabela, valores, inicio, fim),
        url=tabela.url,
        consultado_em=tabela.consultado_em,
        notas=notas,
    )


# ------------------------------------------------- Dívida Pública Federal (DPF)

ROTULO_DPF = "Tesouro Nacional/DPF"

_DPF_CONJUNTOS = {
    # anexo -> (conjunto CKAN, id do recurso XLSX)
    "2.1": ("estoque-da-divida-publica-federal", "0402cb77-5e4c-4414-966f-0e87d802a29a"),
    "2.9": ("fatores-de-variacao-da-divida-publica-federal", "ef6a250b-75bf-44c7-8c9f-8a746b6f469e"),
}
_MESES_ABREV = {m: i for i, m in enumerate("jan fev mar abr mai jun jul ago set out nov dez".split(), 1)}
_MES_ROTULO = re.compile(r"^\s*([A-Za-zç]{3})/(\d{2})\s*$")
# Chamadas de rodapé coladas ao rótulo: "Estoque anterior1", "(DPMFi)3", "DPFe ¹"; "NTN-B1" é nome de título.
_RODAPE_ANEXO = re.compile(r"(?:\s*[¹²³⁴⁵⁶⁷⁸⁹]+|(?<=[a-zà-ú)])\d{1,2}|\s\d{1,2})$")
# Linhas de bloco do anexo 2.1 sem série própria confiável (valores parciais, sem relação com os itens abaixo).
_IGNORAR_ANEXO = {"tesouro nacional", "banco central"}


def _data_mes(celula: Any) -> date | None:
    if isinstance(celula, str) and (m := _MES_ROTULO.match(celula)):
        mes = _MESES_ABREV.get(normalizar(m[1]))
        if mes:
            return date(2000 + int(m[2]), mes, 1)
    return None


def ler_anexo(linhas: list[tuple], id_: str) -> Tabela:
    """Lê um anexo mensal do Relatório Mensal da Dívida (colunas "Jan/07", "Fev/07"...).

    Os anexos repetem rótulos como "DPMFi" ou "LTN" em blocos diferentes; os
    repetidos ganham o prefixo do bloco ("DPMFi EM PODER DO BANCO CENTRAL / LTN").
    Colunas anuais (com o ano inteiro no cabeçalho) são ignoradas.
    """
    cab = next((i for i, l in enumerate(linhas[:15]) if sum(_data_mes(c) is not None for c in l) >= 3), None)
    if cab is None:
        raise ValueError(f"O anexo {id_} não tem uma linha de meses (Jan/07, Fev/07...).")
    colunas = [(j, d) for j, c in enumerate(linhas[cab]) if (d := _data_mes(c))]
    textos = [" ".join(str(l[0]).split()) for l in linhas[:cab] if l and isinstance(l[0], str) and l[0].strip()]
    titulo = textos[0] if textos else f"Anexo {id_}"
    unidade = ""
    for linha in linhas[: cab + 3]:
        for c in linha:
            if isinstance(c, str) and "R$" in c:
                unidade = c.strip().strip("()")
    if not unidade:
        raise ValueError(f"O anexo {id_} não informa a unidade (R$ milhões ou bilhões).")

    brutas = []  # (rotulo sem rodapé, tem recuo, valores)
    for linha in linhas[cab + 1 :]:
        rotulo = linha[0] if linha else None
        if not isinstance(rotulo, str) or not rotulo.strip():
            continue
        valores = [_numero(linha[j]) if j < len(linha) else None for j, _ in colunas]
        if all(v is None for v in valores):
            if _FIM_DOS_DADOS.match(rotulo.strip()) or re.match(r"^[¹²³\d]+[\s/]", rotulo.strip()) or rotulo.strip().startswith(("OBS", "Obs")):
                break
            continue
        nome = re.sub(r"^-\s*", "", _RODAPE_ANEXO.sub("", " ".join(rotulo.split())))
        if normalizar(nome) in _IGNORAR_ANEXO:
            continue
        brutas.append((nome, rotulo[:1].isspace(), valores))

    repetidos = {n for n, c in Counter(normalizar(b[0]) for b in brutas).items() if c > 1}
    itens: list[Linha] = []
    bloco = ""
    for nome, recuado, valores in brutas:
        pai = ""
        if normalizar(nome) in repetidos:
            pai = bloco
        elif not recuado:
            bloco = nome
        itens.append(Linha("", f"{pai} / {nome}" if pai else nome, pai, valores))
    return Tabela(
        id=id_,
        titulo=titulo,
        unidade=unidade,
        periodicidade="mensal",
        periodos=[d for _, d in colunas],
        linhas=itens,
    )


async def tabela_dpf(anexo: str = "2.1") -> Tabela:
    """Lê um anexo da Dívida Pública Federal: "2.1" (estoque) ou "2.9" (fatores de variação)."""
    if anexo not in _DPF_CONJUNTOS:
        raise ValueError(f"Anexo da DPF desconhecido: {anexo!r}. Disponíveis: {', '.join(_DPF_CONJUNTOS)}.")
    conjunto, id_recurso = _DPF_CONJUNTOS[anexo]
    recurso = await recurso_ckan(conjunto, id_recurso, "XLSX")
    r, abas = await _planilha(recurso)
    nome = next((n for n in abas if _id_tabela(n) == anexo), None) or next(iter(abas), None)
    if nome is None:
        raise ErroFonte(f"A planilha {recurso.arquivo} do Tesouro está vazia.")
    try:
        lida: Tabela = await _memorizado(("dpf", r.url, r.consultado_em, anexo), lambda: ler_anexo(abas[nome], anexo))
    except ValueError as erro:
        raise ErroFonte(f"Não foi possível interpretar o anexo {anexo} da DPF ({erro}) Confira {recurso.pagina}.") from erro
    return _carimbar(Tabela(lida.id, lida.titulo, lida.unidade, lida.periodicidade, lida.periodos, lida.linhas), recurso, r)


@fonte_de_dados("dpf", ROTULO_DPF)
async def _buscar_dpf(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    p = ind.parametros if isinstance(ind.parametros, dict) else {"linha": ind.parametros}
    tabela = await tabela_dpf(str(p.get("anexo", "2.1")))
    principal = {"confere": p["linha"]}
    valores, descricao = combinar_linhas(tabela, {**principal, **{k: p[k] for k in ("mais", "menos") if k in p}})
    return Serie(
        nome=ind.nome,
        fonte=ROTULO_DPF,
        codigo=f"Relatório Mensal da Dívida, anexo {tabela.id}, {descricao} ({tabela.arquivo})",
        unidade=tabela.unidade,
        periodicidade="mensal",
        observacoes=observacoes(tabela, valores, inicio, fim),
        url=tabela.url,
        consultado_em=tabela.consultado_em,
        notas=list(tabela.notas),
    )


# ------------------------------------------------------------ Tesouro Direto

ROTULO_TD = "Tesouro Nacional/Tesouro Direto"

_TD_CONJUNTO = "taxas-dos-titulos-ofertados-pelo-tesouro-direto"
_TD_RECURSO = "796d2059-14e9-44e3-80c9-2d9e30b405c1"

# (data, taxa de compra, taxa de venda, PU de compra, PU de venda, PU base)
CotacaoTD = tuple[date, float, float, float, float, float]


@dataclass
class DadosTD:
    """Cotações diárias do Tesouro Direto, indexadas por dia e por título."""

    por_titulo: dict[tuple[str, date], list[CotacaoTD]]  # (tipo, vencimento) -> cotações em ordem de data
    datas: list[date]  # dias com cotação, em ordem
    url: str = ""
    arquivo: str = ""
    modificado: str = ""
    pagina: str = ""
    consultado_em: datetime | None = None
    notas: list[str] = field(default_factory=list)

    def tipos(self) -> list[str]:
        return sorted({t for t, _ in self.por_titulo})


def _data_br(texto: str) -> date:
    d, m, a = texto.strip().split("/")
    return date(int(a), int(m), int(d))


def _decimal_br(texto: str) -> float:
    return float(texto.strip().replace(".", "").replace(",", "."))


def ler_cotacoes_td(corpo: bytes) -> tuple[dict[tuple[str, date], list[CotacaoTD]], list[date]]:
    """Lê o CSV de taxas e preços (";" como separador, vírgula decimal, datas DD/MM/AAAA)."""
    import csv

    texto = corpo.decode("utf-8-sig")
    leitor = csv.reader(io.StringIO(texto), delimiter=";")
    cabecalho = next(leitor, None)
    if not cabecalho or len(cabecalho) < 8 or "titulo" not in normalizar(cabecalho[0]):
        raise ValueError("o cabeçalho do CSV do Tesouro Direto mudou")
    por_titulo: dict[tuple[str, date], list[CotacaoTD]] = {}
    datas: set[date] = set()
    for linha in leitor:
        if len(linha) < 8:
            continue
        try:
            dia, venc = _data_br(linha[2]), _data_br(linha[1])
            valores = tuple(_decimal_br(x) for x in linha[3:8])
        except ValueError:
            continue  # linha em branco ou com valor ausente
        por_titulo.setdefault((linha[0].strip(), venc), []).append((dia, *valores))  # type: ignore[arg-type]
        datas.add(dia)
    if not por_titulo:
        raise ValueError("o CSV do Tesouro Direto não tem cotações")
    for cotacoes in por_titulo.values():
        cotacoes.sort(key=lambda c: c[0])
    return por_titulo, sorted(datas)


async def dados_tesouro_direto() -> DadosTD:
    """Baixa (ou reaproveita) o CSV de taxas do Tesouro Direto, com mais de 14 MB."""
    recurso = await recurso_ckan(_TD_CONJUNTO, _TD_RECURSO, "CSV")
    r = await _uma_vez(f"baixar:{recurso.url}", lambda: requisitar(recurso.url, ttl=TTL_CURTO, tempo_limite=180.0))
    try:
        por_titulo, datas = await _memorizado(("td", r.url, r.consultado_em), lambda: ler_cotacoes_td(r.corpo))
    except Exception as erro:
        if isinstance(erro, (ErroFonte, asyncio.CancelledError)):
            raise
        raise ErroFonte(f"Não foi possível ler o CSV do Tesouro Direto ({erro}). Confira {recurso.pagina}.") from erro
    dados = DadosTD(
        por_titulo, datas, r.url, recurso.arquivo, recurso.modificado, recurso.pagina, r.consultado_em
    )
    if r.desatualizado:
        dados.notas.append("A fonte está fora do ar: dados de uma cópia antiga guardada em cache.")
    return dados


def _norm_tipo(texto: str) -> str:
    """Normaliza o nome do título mantendo o "+" (que distingue IPCA+ de IPCA+ com Juros Semestrais)."""
    return normalizar(texto.replace("+", " mais "))


def titulos_td(dados: DadosTD, tipo: str | None = None, vencimento: str | None = None) -> list[tuple[str, date]]:
    """Títulos (tipo, vencimento) que casam com o texto do tipo e com o vencimento (ano, mês ou dia)."""
    alvo = _norm_tipo(tipo or "")
    candidatos = list(dados.por_titulo)
    if alvo:
        exatos = [t for t in candidatos if _norm_tipo(t[0]) in (alvo, f"tesouro {alvo}")]
        termos = alvo.split()
        candidatos = exatos or [t for t in candidatos if all(x in _norm_tipo(t[0]) for x in termos)]
    if vencimento and vencimento.strip():
        v = vencimento.strip()
        if m := re.fullmatch(r"(\d{4})", v):
            casa = lambda d: d.year == int(m[1])  # noqa: E731
        elif m := re.fullmatch(r"(\d{4})-(\d{1,2})|(\d{1,2})/(\d{4})", v):
            ano, mes = (int(m[1]), int(m[2])) if m[1] else (int(m[4]), int(m[3]))
            casa = lambda d: (d.year, d.month) == (ano, mes)  # noqa: E731
        else:
            alvo_dia = interpretar_data(v)
            casa = lambda d: d == alvo_dia  # noqa: E731
        candidatos = [t for t in candidatos if casa(t[1])]
    return sorted(candidatos, key=lambda t: (t[0], t[1]))


# ------------------------------------------------------------------- SICONFI

ROTULO_SICONFI = "Tesouro Nacional/SICONFI"
PAGINA_SICONFI = "https://apidatalake.tesouro.gov.br/docs/siconfi/"

_SICONFI = "https://apidatalake.tesouro.gov.br/ords/siconfi/tt"
_POR_PAGINA = 5000


@dataclass
class ConsultaSiconfi:
    itens: list[dict[str, Any]]
    url: str  # endereço da primeira página
    consultado_em: datetime
    truncada: bool  # havia mais páginas além do limite pedido
    notas: list[str] = field(default_factory=list)


async def consultar_siconfi(endpoint: str, params: dict[str, Any], max_paginas: int = 5) -> ConsultaSiconfi:
    """Consulta um endpoint do SICONFI, seguindo a paginação (`hasMore`/`offset`) até `max_paginas`.

    Resposta sem dados é um 200 com lista vazia, e não um erro.
    """
    itens: list[dict[str, Any]] = []
    primeira: Resposta | None = None
    truncada = False
    for pagina in range(max_paginas):
        r = await requisitar(
            f"{_SICONFI}/{endpoint}",
            params={**params, "limit": _POR_PAGINA, "offset": pagina * _POR_PAGINA},
            ttl=TTL_MEDIO,
            tempo_limite=120.0,
            espera_json=True,
        )
        primeira = primeira or r
        corpo = r.json()
        if not isinstance(corpo, dict) or not isinstance(corpo.get("items"), list):
            raise ErroFonte(f"O SICONFI devolveu uma resposta inesperada em {endpoint}.")
        itens.extend(corpo["items"])
        if not corpo.get("hasMore"):
            break
        truncada = pagina == max_paginas - 1
    assert primeira is not None
    notas = [] if not primeira.desatualizado else ["A fonte está fora do ar: dados de uma cópia antiga guardada em cache."]
    return ConsultaSiconfi(itens, primeira.url, primeira.consultado_em, truncada, notas)


async def entes_siconfi() -> list[dict[str, Any]]:
    """Todos os entes da federação cadastrados no SICONFI (União, estados, DF e municípios)."""
    consulta = await consultar_siconfi("entes", {}, max_paginas=3)
    return consulta.itens


def buscar_entes(
    entes: list[dict[str, Any]], busca: str | None = None, uf: str | None = None, esfera: str | None = None
) -> list[dict[str, Any]]:
    """Filtra entes por código IBGE ou nome, UF (sigla) e esfera (U, E, D ou M).

    Atenção ao cadastro: nos estados o campo `uf` vem "BR"; a UF é deduzida do código IBGE.
    """
    codigo_uf = {e["uf"]: e["cod_ibge"] // 100000 for e in entes if e.get("esfera") == "M" and e.get("uf")}

    def prefixo(e: dict[str, Any]) -> int | None:
        c = e["cod_ibge"]
        return c if e.get("esfera") == "E" else (c // 100000 if e.get("esfera") == "M" else None)

    saida = entes
    if esfera:
        saida = [e for e in saida if (e.get("esfera") or "").upper() == esfera.strip().upper()]
    if uf:
        alvo = codigo_uf.get(uf.strip().upper())
        if alvo is None:
            raise ValueError(f"UF desconhecida: {uf!r}. Use a sigla, como SP ou RJ.")
        saida = [e for e in saida if prefixo(e) == alvo]
    texto = (busca or "").strip()
    if texto:
        if texto.isdigit():
            saida = [e for e in saida if e["cod_ibge"] == int(texto)]
        else:
            alvo_nome = normalizar(texto)
            exatos = [e for e in saida if normalizar(e["ente"]) == alvo_nome]
            saida = exatos or [e for e in saida if all(t in normalizar(e["ente"]) for t in alvo_nome.split())]
    return sorted(saida, key=lambda e: (e.get("esfera") != "U", e["ente"], e["cod_ibge"]))
