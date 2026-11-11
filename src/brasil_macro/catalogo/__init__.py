"""Catálogo curado de indicadores e despacho para as fontes de dados.

Cada arquivo YAML desta pasta descreve um tema. Um indicador tem os campos
comuns listados em `_CAMPOS` e exatamente uma chave extra, que diz de onde vêm
os dados (`sgs`, `sidra`, `ipea`, ...). Essa chave é resolvida por um buscador
registrado com `@fonte_de_dados` num módulo de `brasil_macro.fontes`.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from importlib import resources
from typing import Any, Awaitable, Callable

import yaml

from ..modelos import PERIODICIDADES, Serie

_CAMPOS = {
    "id",
    "nome",
    "subtema",
    "unidade",
    "periodicidade",
    "aliases",
    "notas",
    "destaque",
    "painel_geral",
    "produtor",
    "oficial",
}


@dataclass(frozen=True)
class Indicador:
    id: str
    nome: str
    tema: str
    tema_titulo: str
    unidade: str
    periodicidade: str
    origem: str  # chave do buscador: sgs, sidra, ipea, ...
    parametros: Any  # valor da chave de origem no YAML
    subtema: str = ""
    aliases: tuple[str, ...] = ()
    notas: str = ""
    destaque: bool = False  # aparece no painel do tema
    painel_geral: bool = False  # aparece no painel "geral"
    produtor: str = ""  # quem produz o dado, quando difere de quem distribui
    oficial: bool = True  # False para dados de entidades privadas distribuídos por órgão oficial

    @property
    def codigo(self) -> str:
        p = self.parametros
        if isinstance(p, (dict, list)):
            return f"{self.origem.upper()} {json.dumps(p, ensure_ascii=False, separators=(',', ':'))}"
        return f"{self.origem.upper()} {p}"


# ------------------------------------------------------------------ fontes

Buscador = Callable[[Indicador, "date | None", "date | None"], Awaitable[Serie]]


@dataclass(frozen=True)
class _Fonte:
    chave: str
    rotulo: str
    buscar: Buscador


_FONTES: dict[str, _Fonte] = {}


def fonte_de_dados(chave: str, rotulo: str) -> Callable[[Buscador], Buscador]:
    """Registra a função que busca os dados dos indicadores com a chave dada.

    A função recebe (indicador, inicio, fim) e devolve uma `Serie` com todas as
    observações do intervalo; `inicio` ou `fim` nulos significam sem limite.
    """

    def decorador(funcao: Buscador) -> Buscador:
        _FONTES[chave] = _Fonte(chave, rotulo, funcao)
        return funcao

    return decorador


def _fontes() -> dict[str, _Fonte]:
    from .. import fontes

    fontes.carregar()
    return _FONTES


def rotulo_fonte(ind: Indicador) -> str:
    fonte = _fontes().get(ind.origem)
    rotulo = fonte.rotulo if fonte else ind.origem
    return f"{rotulo} (produtor: {ind.produtor})" if ind.produtor else rotulo


async def obter_dados(ind: Indicador, inicio: date | None = None, fim: date | None = None) -> Serie:
    """Busca as observações do indicador e carimba a série com os dados do catálogo."""
    fonte = _fontes().get(ind.origem)
    if fonte is None:
        raise LookupError(f"O indicador {ind.id!r} usa a origem {ind.origem!r}, que não tem buscador registrado.")
    serie = await fonte.buscar(ind, inicio, fim)
    serie.id = ind.id
    serie.nome = ind.nome
    serie.fonte = rotulo_fonte(ind)
    serie.periodicidade = ind.periodicidade
    if ind.unidade:
        serie.unidade = ind.unidade
    if ind.notas:
        serie.notas.insert(0, ind.notas)
    return serie


# ---------------------------------------------------------------- carregamento

_indicadores: dict[str, Indicador] | None = None
_temas: dict[str, str] = {}


def _ler_arquivos() -> dict[str, Indicador]:
    indicadores: dict[str, Indicador] = {}
    _temas.clear()
    arquivos = sorted(
        (a for a in resources.files(__package__).iterdir() if a.name.endswith(".yaml")), key=lambda a: a.name
    )
    for arquivo in arquivos:
        try:
            doc = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as erro:
            raise ValueError(f"{arquivo.name}: YAML inválido — {erro}") from erro
        tema = doc.get("tema")
        if not tema:
            raise ValueError(f"{arquivo.name}: falta o campo 'tema'.")
        titulo = doc.get("titulo", tema)
        _temas[tema] = titulo
        for bruto in doc.get("indicadores") or []:
            onde = f"{arquivo.name}: indicador {bruto.get('id', '?')!r}"
            for campo in ("id", "nome", "periodicidade"):
                if not bruto.get(campo):
                    raise ValueError(f"{onde}: falta o campo {campo!r}.")
            if bruto["periodicidade"] not in PERIODICIDADES:
                raise ValueError(f"{onde}: periodicidade {bruto['periodicidade']!r} inválida; use {PERIODICIDADES}.")
            origens = [c for c in bruto if c not in _CAMPOS]
            if len(origens) != 1:
                raise ValueError(f"{onde}: deve ter exatamente uma chave de origem, encontrei {origens}.")
            if bruto["id"] in indicadores:
                raise ValueError(f"{onde}: id duplicado.")
            origem = origens[0]
            indicadores[bruto["id"]] = Indicador(
                id=bruto["id"],
                nome=bruto["nome"],
                tema=tema,
                tema_titulo=titulo,
                unidade=str(bruto.get("unidade", "")),
                periodicidade=bruto["periodicidade"],
                origem=origem,
                parametros=bruto[origem],
                subtema=str(bruto.get("subtema", "")),
                aliases=tuple(str(a) for a in bruto.get("aliases") or ()),
                notas=str(bruto.get("notas", "")).strip(),
                destaque=bool(bruto.get("destaque", False)),
                painel_geral=bool(bruto.get("painel_geral", False)),
                produtor=str(bruto.get("produtor", "")),
                oficial=bool(bruto.get("oficial", True)),
            )
    return indicadores


def recarregar() -> None:
    global _indicadores
    _indicadores = _ler_arquivos()


def incluir_privados() -> bool:
    return os.environ.get("BRASIL_MACRO_INCLUIR_PRIVADOS", "").strip().lower() in ("1", "true", "sim")


def todos(*, com_privados: bool | None = None) -> list[Indicador]:
    """Indicadores do catálogo; os de produtor privado só entram se habilitados."""
    if _indicadores is None:
        recarregar()
    assert _indicadores is not None
    privados = incluir_privados() if com_privados is None else com_privados
    return [i for i in _indicadores.values() if i.oficial or privados]


def temas() -> dict[str, str]:
    todos()
    return dict(_temas)


def obter_indicador(id_: str) -> Indicador:
    chave = id_.strip().lower()
    disponiveis = {i.id: i for i in todos()}
    if chave in disponiveis:
        return disponiveis[chave]
    if _indicadores and chave in _indicadores:
        raise LookupError(
            f"O indicador {chave!r} é de produtor privado ({_indicadores[chave].produtor}) e está desativado. "
            "Defina BRASIL_MACRO_INCLUIR_PRIVADOS=1 para habilitar."
        )
    proximos = difflib.get_close_matches(chave, disponiveis, n=5, cutoff=0.5)
    dica = f" Parecidos: {', '.join(proximos)}." if proximos else ""
    raise LookupError(f"Indicador {id_!r} não existe no catálogo.{dica} Use buscar_indicadores para localizar o id.")


# --------------------------------------------------------------------- busca


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", sem_acento.lower()).strip()


def buscar(consulta: str = "", tema: str | None = None, fonte: str | None = None) -> list[Indicador]:
    """Indicadores que casam com a consulta, do mais para o menos relevante."""
    candidatos = todos()
    if tema:
        alvo = normalizar(tema).replace(" ", "_")
        candidatos = [i for i in candidatos if i.tema == alvo or alvo in normalizar(i.tema_titulo).replace(" ", "_")]
    if fonte:
        alvo = normalizar(fonte)
        candidatos = [i for i in candidatos if alvo in normalizar(rotulo_fonte(i)) or alvo == i.origem]
    termos = normalizar(consulta).split()
    if not termos:
        return candidatos

    pontuados: list[tuple[int, int, int, Indicador]] = []
    for ordem, ind in enumerate(candidatos):
        id_ = normalizar(ind.id)
        apelidos = " | ".join(normalizar(a) for a in ind.aliases)
        nome = normalizar(ind.nome)
        resto = normalizar(f"{ind.subtema} {ind.tema_titulo} {ind.notas} {ind.codigo}")
        casados = pontos = 0
        for termo in termos:
            if termo in id_.split() or termo in apelidos.split():
                peso = 4
            elif termo in id_ or termo in apelidos:
                peso = 3
            elif termo in nome:
                peso = 2
            elif termo in resto:
                peso = 1
            else:
                continue
            casados += 1
            pontos += peso
        if casados:
            pontuados.append((casados, pontos, -ordem, ind))
    pontuados.sort(key=lambda t: t[:3], reverse=True)
    completos = [t for t in pontuados if t[0] == len(termos)]
    return [t[3] for t in (completos or pontuados)]
