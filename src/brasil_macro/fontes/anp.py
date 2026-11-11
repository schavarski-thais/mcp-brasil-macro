"""ANP — preços de combustíveis nos postos (levantamento semanal publicado em CSV, um arquivo por mês e produto).

Os nomes dos arquivos mudam de um ano para outro (e às vezes dentro do próprio ano),
então a lista de arquivos é lida da página oficial da série histórica e não montada por padrão.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean

from ..catalogo import Indicador, fonte_de_dados, normalizar
from ..http import TTL_LONGO, TTL_MEDIO, ErroFonte, requisitar
from ..modelos import Serie

ROTULO = "ANP/Levantamento de Preços"
PAGINA = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/serie-historica-de-precos-de-combustiveis"

# produto do projeto -> (tipo de arquivo mensal, texto da coluna "Produto" no CSV)
PRODUTOS: dict[str, tuple[str, str]] = {
    "gasolina": ("gasolina-etanol", "GASOLINA"),
    "gasolina_aditivada": ("gasolina-etanol", "GASOLINA ADITIVADA"),
    "etanol": ("gasolina-etanol", "ETANOL"),
    "diesel": ("diesel-gnv", "DIESEL"),
    "diesel_s10": ("diesel-gnv", "DIESEL S10"),
    "gnv": ("diesel-gnv", "GNV"),
    "glp": ("glp", "GLP"),
}

_LINK = re.compile(r'href="(https://www\.gov\.br/anp/[^"]*?/shpc/dsan/(\d{4})/([^"#?]+))"')
_HISTORICO_INICIO = 24  # meses baixados quando o pedido não informa período


@dataclass(frozen=True)
class Coleta:
    """Um preço de venda informado por um posto em uma data de coleta."""

    uf: str
    municipio: str
    bairro: str
    revenda: str
    bandeira: str
    produto: str
    data: date
    valor: float
    unidade: str


def _tipo_e_mes(nome: str) -> tuple[str, int] | None:
    """Tipo do arquivo e mês a partir do nome publicado.

    Exemplos: '2025/precos-gasolina-etanol-06.csv', '2026/10-diesel-gnv.csv',
    '2026/06-dados-abertos-precos-2026-06-glp.csv'.
    """
    if re.search(r"gasolina|etanol", nome):
        tipo = "gasolina-etanol"
    elif re.search(r"diesel|gnv", nome):
        tipo = "diesel-gnv"
    elif "glp" in nome:
        tipo = "glp"
    else:
        return None
    m = re.match(r"(\d{2})-", nome) or re.search(r"-(\d{2})(?:\.csv)?$", nome)
    if m is None or not 1 <= int(m.group(1)) <= 12:
        return None
    return tipo, int(m.group(1))


def classificar_links(html: str) -> dict[tuple[str, int, int], str]:
    """Mapeia (tipo, ano, mês) para a URL do arquivo publicado na página da ANP."""
    achados: dict[tuple[str, int, int], str] = {}
    for url, ano, nome in _LINK.findall(html):
        info = _tipo_e_mes(nome)
        if info is None:
            continue
        chave = (info[0], int(ano), info[1])
        # quando a página publica duas versões do mesmo mês, fica com a que é CSV de fato
        if chave not in achados or url.lower().endswith(".csv"):
            achados[chave] = url
    return achados


async def arquivos() -> dict[tuple[str, int, int], str]:
    r = await requisitar(PAGINA, ttl=TTL_MEDIO, tempo_limite=60)
    achados = classificar_links(r.texto())
    if not achados:
        raise ErroFonte(f"Não encontrei arquivos de preços na página da ANP ({PAGINA}).")
    return achados


def _meses(mapa: dict[tuple[str, int, int], str], tipo: str) -> list[tuple[int, int]]:
    return sorted((a, m) for (t, a, m) in mapa if t == tipo)


def _texto(corpo: bytes) -> str:
    """A ANP mistura UTF-8 e cp1252 no mesmo arquivo (ex.: 'm³', nomes de bandeira); decodifica linha a linha."""
    partes = []
    for linha in corpo.split(b"\n"):
        try:
            partes.append(linha.decode("utf-8"))
        except UnicodeDecodeError:
            partes.append(linha.decode("cp1252", "replace"))
    return "\n".join(partes).lstrip("﻿")


def ler_csv(corpo: bytes) -> list[dict[str, str]]:
    leitor = csv.DictReader(io.StringIO(_texto(corpo)), delimiter=";")
    campos = leitor.fieldnames or []
    if "Produto" not in campos or "Valor de Venda" not in campos:
        raise ErroFonte("O arquivo da ANP não está no formato esperado (faltam as colunas Produto e Valor de Venda).")
    return list(leitor)


def _coleta(linha: dict[str, str]) -> Coleta | None:
    try:
        valor = float(linha.get("Valor de Venda", "").strip().replace(",", "."))
        data = datetime.strptime(linha.get("Data da Coleta", "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None  # linha sem preço ou sem data válida
    return Coleta(
        uf=linha.get("Estado - Sigla", "").strip(),
        municipio=linha.get("Municipio", "").strip(),
        bairro=linha.get("Bairro", "").strip(),
        revenda=linha.get("Revenda", "").strip(),
        bandeira=linha.get("Bandeira", "").strip(),
        produto=linha["Produto"].strip(),
        data=data,
        valor=valor,
        unidade=linha.get("Unidade de Medida", "").strip(),
    )


def resolver_produto(produto: str) -> tuple[str, str]:
    chave = normalizar(produto).replace(" ", "_")
    if chave not in PRODUTOS:
        raise ValueError(f"Produto {produto!r} inválido. Opções: {', '.join(PRODUTOS)}.")
    return PRODUTOS[chave]


async def coletas(produto: str, ano: int, mes: int) -> tuple[list[Coleta], str, datetime]:
    """Todas as coletas de um produto num mês: (coletas, URL do arquivo, momento da consulta)."""
    tipo, nome_csv = resolver_produto(produto)
    mapa = await arquivos()
    url = mapa.get((tipo, ano, mes))
    if url is None:
        disponiveis = _meses(mapa, tipo)
        faixa = f"{disponiveis[0][1]:02d}/{disponiveis[0][0]} a {disponiveis[-1][1]:02d}/{disponiveis[-1][0]}" if disponiveis else "nenhum"
        raise LookupError(f"A ANP não publicou o levantamento de {mes:02d}/{ano} ({tipo}). Meses publicados: {faixa}.")
    hoje = date.today()
    recente = (hoje.year * 12 + hoje.month) - (ano * 12 + mes) <= 2
    r = await requisitar(url, ttl=TTL_MEDIO if recente else TTL_LONGO, tempo_limite=180)
    cs = [c for linha in ler_csv(r.corpo) if linha.get("Produto", "").strip() == nome_csv if (c := _coleta(linha))]
    if not cs:
        raise ErroFonte(f"O arquivo da ANP de {mes:02d}/{ano} não tem coletas válidas de {produto}.")
    return cs, url, r.consultado_em


async def ultimo_mes(produto: str) -> tuple[int, int]:
    tipo, _ = resolver_produto(produto)
    meses = _meses(await arquivos(), tipo)
    if not meses:
        raise ErroFonte(f"A ANP não listou arquivos de {produto}.")
    return meses[-1]


def resumir(cs: list[Coleta]) -> dict[str, float | int | None]:
    """Média simples, mínimo e máximo das coletas, com o número de coletas e de postos distintos."""
    valores = [c.valor for c in cs]
    return {
        "media": round(mean(valores), 3),
        "minimo": round(min(valores), 2),
        "maximo": round(max(valores), 2),
        "coletas": len(cs),
        "postos": len({(c.uf, c.municipio, c.bairro, c.revenda) for c in cs}),
    }


def agrupar(cs: list[Coleta], chave) -> dict[str, list[Coleta]]:
    grupos: dict[str, list[Coleta]] = {}
    for c in cs:
        grupos.setdefault(chave(c), []).append(c)
    return grupos


@fonte_de_dados("anp", ROTULO)
async def _buscar(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    """Preço médio nacional mensal de um produto: média simples de todas as coletas do mês."""
    produto = str(ind.parametros)
    tipo, _ = resolver_produto(produto)
    meses = _meses(await arquivos(), tipo)
    if not meses:
        raise ErroFonte(f"A ANP não listou arquivos de {produto}.")
    notas: list[str] = []
    ini = (inicio.year, inicio.month) if inicio else meses[max(0, len(meses) - _HISTORICO_INICIO)]
    if inicio is None:
        notas.append(f"Sem período informado, a série cobre os últimos {_HISTORICO_INICIO} meses publicados; informe inicio para mais.")
    fim_ = (fim.year, fim.month) if fim else meses[-1]
    observacoes = []
    unidade = ""
    consultado = None
    for ano, mes in meses:
        if not (ini <= (ano, mes) <= fim_):
            continue
        cs, _, consultado = await coletas(produto, ano, mes)
        unidade = unidade or cs[0].unidade
        observacoes.append((date(ano, mes, 1), round(mean(c.valor for c in cs), 3)))
    if not observacoes:
        raise ErroFonte(f"Nenhum levantamento da ANP de {produto} no período pedido.")
    notas.append("Média simples de todas as coletas do mês, sem ponderar pelo volume vendido por cada posto.")
    return Serie(
        nome=f"Preço médio de {produto} nos postos (média nacional)",
        fonte=ROTULO,
        codigo=f"ANP {produto}",
        unidade=unidade,
        periodicidade="mensal",
        observacoes=observacoes,
        url=PAGINA,
        consultado_em=consultado,  # type: ignore[arg-type]
        notas=notas,
    )
