"""IBGE — SIDRA (dados), API de agregados (metadados) e calendário de divulgações."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from ..catalogo import Indicador, fonte_de_dados
from ..http import TTL_CURTO, TTL_LONGO, TTL_MEDIO, ErroFonte, Resposta, requisitar
from ..modelos import Observacao, Serie

ROTULO = "IBGE/SIDRA"
_VALORES = "https://apisidra.ibge.gov.br/values"
_AGREGADOS = "https://servicodados.ibge.gov.br/api/v3/agregados"
_CALENDARIO = "https://servicodados.ibge.gov.br/api/v3/calendario/"

# Nome da dimensão de período no cabeçalho do SIDRA -> periodicidade do projeto.
_TIPOS_PERIODO = {
    "Mês": "mensal",
    "Trimestre Móvel": "mensal",
    "Trimestre": "trimestral",
    "Semestre": "semestral",
    "Ano": "anual",
}


def _decodificar(r: Resposta) -> Any:
    corpo = r.corpo
    if corpo[:2] == b"\x1f\x8b":  # o IBGE às vezes manda gzip sem avisar no cabeçalho
        corpo = gzip.decompress(corpo)
    try:
        return json.loads(corpo.decode("utf-8-sig", "replace"))
    except ValueError:
        trecho = corpo[:200].decode("utf-8", "replace")
        raise ErroFonte(f"O IBGE devolveu uma resposta fora do formato esperado para {r.url}: {trecho}") from None


def data_do_periodo(codigo: str, tipo: str) -> date:
    """Converte o código de período do SIDRA (202609, 202602, 2026) em data inicial do período."""
    ano = int(codigo[:4])
    if tipo in ("Mês", "Trimestre Móvel"):
        return date(ano, int(codigo[4:6]), 1)
    if tipo == "Trimestre":
        return date(ano, (int(codigo[4:6]) - 1) * 3 + 1, 1)
    if tipo == "Semestre":
        return date(ano, (int(codigo[4:6]) - 1) * 6 + 1, 1)
    return date(ano, 1, 1)


def codigo_do_periodo(d: date, periodicidade: str) -> str:
    if periodicidade == "mensal":
        return f"{d.year}{d.month:02d}"
    if periodicidade == "trimestral":
        return f"{d.year}{(d.month - 1) // 3 + 1:02d}"
    return str(d.year)


@dataclass
class LinhaSidra:
    periodo: str  # código do período, como o SIDRA devolve
    periodo_nome: str
    territorio: str
    territorio_codigo: str
    variavel: str
    variavel_codigo: str
    unidade: str
    valor: float | None
    classificacoes: dict[str, tuple[str, str]] = field(default_factory=dict)  # dimensão -> (código, nome)


@dataclass
class ResultadoSidra:
    linhas: list[LinhaSidra]
    tipo_periodo: str  # "Mês", "Trimestre", "Trimestre Móvel", "Ano"...
    url: str
    consultado_em: datetime

    @property
    def periodicidade(self) -> str:
        return _TIPOS_PERIODO.get(self.tipo_periodo, "anual")


def _lista(valor: Any) -> str:
    if isinstance(valor, (list, tuple, set)):
        return ",".join(str(v) for v in valor)
    return str(valor)


async def consultar(
    tabela: int | str,
    variaveis: Any = "all",
    periodos: str = "last 12",
    classificacoes: dict[Any, Any] | None = None,
    nivel: str = "n1",
    localidades: Any = "all",
    ttl: float = TTL_MEDIO,
) -> ResultadoSidra:
    """Consulta uma tabela do SIDRA.

    `periodos` usa a sintaxe do SIDRA: "last 12", "first 3", "all",
    "202001-202609" ou "202601,202602". `classificacoes` mapeia o id da
    classificação para as categorias ("all", um código ou vários).
    """
    caminho = f"/t/{tabela}/{nivel.lower()}/{_lista(localidades)}/v/{_lista(variaveis)}/p/{quote(periodos.strip())}"
    for cid, categorias in (classificacoes or {}).items():
        caminho += f"/c{str(cid).lower().lstrip('c')}/{_lista(categorias)}"
    r = await requisitar(_VALORES + caminho, ttl=ttl, aceitar=(200, 400), tempo_limite=90)
    if r.status == 400:
        raise ErroFonte(f"O SIDRA recusou a consulta à tabela {tabela}: {r.texto()[:400].strip()} (URL: {r.url})")
    dados = _decodificar(r)
    if not isinstance(dados, list) or not dados:
        raise ErroFonte(f"O SIDRA devolveu uma resposta vazia para {r.url}.")
    cabecalho, corpo = dados[0], dados[1:]

    # Dimensões: D1 território, depois variável, período e classificações (identificadas pelo nome).
    dims = sorted((k[:-1] for k in cabecalho if k.startswith("D") and k.endswith("N")), key=lambda k: int(k[1:]))
    nomes = {d: str(cabecalho[d + "N"]) for d in dims}
    d_var = next((d for d in dims if nomes[d] == "Variável"), None)
    d_per = next((d for d in dims if nomes[d] in _TIPOS_PERIODO), None)
    if d_var is None or d_per is None:
        raise ErroFonte(f"Formato inesperado do SIDRA na tabela {tabela}: dimensões {list(nomes.values())}.")
    d_ter = next(d for d in dims if d not in (d_var, d_per))
    d_cls = [d for d in dims if d not in (d_var, d_per, d_ter)]

    linhas: list[LinhaSidra] = []
    for item in corpo:
        try:
            valor: float | None = float(item["V"])
        except (TypeError, ValueError):
            valor = None  # "...", "-", "X": dado ausente, zero arredondado ou sigiloso
        linhas.append(
            LinhaSidra(
                periodo=item[d_per + "C"],
                periodo_nome=item[d_per + "N"],
                territorio=item[d_ter + "N"],
                territorio_codigo=item[d_ter + "C"],
                variavel=item[d_var + "N"],
                variavel_codigo=item[d_var + "C"],
                unidade=item.get("MN", ""),
                valor=valor,
                classificacoes={nomes[d]: (item[d + "C"], item[d + "N"]) for d in d_cls},
            )
        )
    return ResultadoSidra(linhas, nomes[d_per], r.url, r.consultado_em)


async def metadados(tabela: int | str) -> dict[str, Any]:
    """Metadados da tabela: nome, pesquisa, periodicidade, variáveis, classificações e níveis territoriais."""
    r = await requisitar(f"{_AGREGADOS}/{tabela}/metadados", ttl=TTL_LONGO, aceitar=(200, 404, 500), tempo_limite=90)
    if r.status != 200:
        raise ErroFonte(f"Tabela {tabela} não encontrada na API de agregados do IBGE (HTTP {r.status}).")
    dados = _decodificar(r)
    if not isinstance(dados, dict) or "variaveis" not in dados:
        raise ErroFonte(f"Tabela {tabela} não encontrada na API de agregados do IBGE.")
    return dados


async def listar_tabelas() -> list[dict[str, Any]]:
    """Todas as tabelas do SIDRA, com a pesquisa a que pertencem."""
    r = await requisitar(_AGREGADOS, ttl=TTL_LONGO, tempo_limite=120)
    saida = []
    for pesquisa in _decodificar(r):
        for agregado in pesquisa.get("agregados", []):
            saida.append(
                {"tabela": int(agregado["id"]), "nome": agregado["nome"], "pesquisa": pesquisa.get("nome", "")}
            )
    return saida


async def calendario(inicio: date, fim: date, maximo: int = 200) -> tuple[list[dict[str, Any]], str, datetime]:
    """Divulgações programadas pelo IBGE entre duas datas, da mais próxima para a mais distante."""
    itens: list[dict[str, Any]] = []
    pagina = 1
    url = ""
    quando = None
    while len(itens) < maximo:
        r = await requisitar(
            _CALENDARIO,
            params={"qtd": 100, "de": inicio.strftime("%m-%d-%Y"), "ate": fim.strftime("%m-%d-%Y"), "page": pagina},
            ttl=TTL_CURTO,
        )
        dados = _decodificar(r)
        url, quando = url or r.url, quando or r.consultado_em
        itens.extend(dados.get("items", []))
        if not dados.get("nextPage") or dados["nextPage"] <= pagina:
            break
        pagina = dados["nextPage"]
    saida = []
    for item in itens:
        try:
            momento = datetime.strptime(item["data_divulgacao"], "%d/%m/%Y %H:%M:%S")
        except (KeyError, ValueError):
            continue
        referencia = ""
        if item.get("ano_referencia_inicio"):
            referencia = f"{item['ano_referencia_inicio']}-{int(item.get('mes_referencia_inicio') or 1):02d}"
            if item.get("ano_referencia_fim") and (
                item["ano_referencia_fim"],
                item.get("mes_referencia_fim"),
            ) != (item["ano_referencia_inicio"], item.get("mes_referencia_inicio")):
                referencia += f" a {item['ano_referencia_fim']}-{int(item.get('mes_referencia_fim') or 12):02d}"
        saida.append(
            {
                "data": momento.strftime("%Y-%m-%d"),
                "hora": momento.strftime("%H:%M"),
                "titulo": item.get("titulo", ""),
                "tipo": item.get("tipo", ""),
                "referencia": referencia,
            }
        )
    saida.sort(key=lambda i: (i["data"], i["hora"]))
    return saida, url, quando  # type: ignore[return-value]


# ------------------------------------------------------------------ catálogo


async def serie_sidra(spec: dict[str, Any], periodicidade: str, inicio: date | None, fim: date | None) -> Serie:
    """Série de uma variável de uma tabela, com uma categoria por classificação e um território."""
    tabela = spec["tabela"]
    if inicio is None:
        periodos = "all"
    else:
        periodos = f"{codigo_do_periodo(inicio, periodicidade)}-{codigo_do_periodo(fim or date.today(), periodicidade)}"
    res = await consultar(
        tabela,
        spec["variavel"],
        periodos,
        spec.get("classificacoes"),
        spec.get("nivel", "n1"),
        spec.get("localidade", "all"),
    )
    obs: dict[date, float | None] = {}
    for linha in res.linhas:
        d = data_do_periodo(linha.periodo, res.tipo_periodo)
        if d in obs:
            raise ErroFonte(
                f"A consulta à tabela {tabela} devolveu mais de um valor por período; "
                "o indicador precisa fixar uma categoria em cada classificação."
            )
        obs[d] = linha.valor
    ordenadas: list[Observacao] = sorted(obs.items())
    while ordenadas and ordenadas[0][1] is None:  # períodos sem dado no começo da tabela
        ordenadas.pop(0)
    classif = spec.get("classificacoes") or {}
    codigo = f"SIDRA {tabela} v{spec['variavel']}" + "".join(f" c{c}={v}" for c, v in classif.items())
    return Serie(
        nome=res.linhas[0].variavel if res.linhas else f"Tabela {tabela}",
        fonte=ROTULO,
        codigo=codigo,
        unidade=res.linhas[0].unidade if res.linhas else "",
        periodicidade=periodicidade,
        observacoes=ordenadas,
        url=res.url,
        consultado_em=res.consultado_em,
    )


@fonte_de_dados("sidra", ROTULO)
async def _buscar(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    # Uma lista de especificações emenda tabelas que se sucedem (ex.: IPCA 2012-2019 e 2020 em diante).
    specs = ind.parametros if isinstance(ind.parametros, list) else [ind.parametros]
    partes = [await serie_sidra(s, ind.periodicidade, inicio, fim) for s in specs]
    serie = partes[-1]
    if len(partes) > 1:
        juntas: dict[date, float | None] = {}
        for parte in partes:
            juntas.update({d: v for d, v in parte.observacoes if v is not None or d not in juntas})
        serie.observacoes = sorted(juntas.items())
        serie.codigo = " + ".join(p.codigo for p in partes)
        serie.notas.append(f"Série emendada a partir de {len(partes)} tabelas do SIDRA.")
    if fim:
        serie.recortar(None, fim)
    return serie
