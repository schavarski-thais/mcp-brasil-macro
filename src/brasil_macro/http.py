"""Acesso HTTP compartilhado por todas as fontes.

Concentra o que as APIs oficiais exigem na prática: cache em disco, novas
tentativas, limite de concorrência por host e reaproveitamento de uma resposta
antiga quando a fonte está fora do ar.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

AGENTE = "mcp-brasil-macro/0.1"

TTL_CURTO = 15 * 60  # séries diárias, expectativas, calendário
TTL_MEDIO = 6 * 3600  # séries mensais e trimestrais
TTL_LONGO = 7 * 24 * 3600  # metadados, que quase nunca mudam

_REPETIVEIS = {429, 500, 502, 503, 504}


class ErroFonte(RuntimeError):
    """Falha ao consultar uma fonte oficial; a mensagem já está pronta para o usuário."""


@dataclass(frozen=True)
class _Politica:
    concorrencia: int = 4
    intervalo: float = 0.0  # segundos mínimos entre requisições ao mesmo host


# Limites observados em teste: o IBGE devolve corpo vazio sob chamadas paralelas
# e o ComexStat responde 429 pedindo 10 segundos entre requisições.
_POLITICAS: dict[str, _Politica] = {
    "servicodados.ibge.gov.br": _Politica(1, 0.25),
    "apisidra.ibge.gov.br": _Politica(2, 0.1),
    "api-comexstat.mdic.gov.br": _Politica(1, 20.0),
}
_PADRAO = _Politica()


@dataclass
class Resposta:
    corpo: bytes
    url: str
    status: int
    consultado_em: datetime
    do_cache: bool = False
    desatualizado: bool = False  # a fonte falhou e isto é uma cópia vencida do cache

    def texto(self, codificacao: str = "utf-8") -> str:
        return self.corpo.decode(codificacao, "replace")

    def json(self) -> Any:
        return json.loads(self.corpo.decode("utf-8-sig", "replace"))


# --------------------------------------------------------------------- cache


def _dir_cache() -> Path:
    definido = os.environ.get("BRASIL_MACRO_CACHE_DIR")
    if definido:
        return Path(definido)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "mcp-brasil-macro" / "cache"
    return Path.home() / ".cache" / "mcp-brasil-macro"


class _Cache:
    """Cache de respostas em SQLite. Se o disco falhar, o servidor segue sem cache."""

    def __init__(self) -> None:
        self._trava = threading.Lock()
        self._con: sqlite3.Connection | None = None
        self._pasta: Path | None = None
        self._indisponivel = False

    def _conexao(self) -> sqlite3.Connection | None:
        pasta = _dir_cache()
        if self._con is not None and pasta == self._pasta:
            return self._con
        if self._indisponivel and pasta == self._pasta:
            return None
        self._pasta = pasta
        try:
            pasta.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(pasta / "respostas.sqlite", check_same_thread=False, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "CREATE TABLE IF NOT EXISTS respostas ("
                "chave TEXT PRIMARY KEY, url TEXT NOT NULL, obtido_em REAL NOT NULL, "
                "expira_em REAL NOT NULL, corpo BLOB NOT NULL)"
            )
            con.commit()
        except (sqlite3.Error, OSError) as erro:
            print(f"[brasil-macro] cache desativado: {erro}", file=sys.stderr)
            self._con = None
            self._indisponivel = True
            return None
        self._con = con
        self._indisponivel = False
        return con

    def ler(self, chave: str) -> tuple[bytes, float, float] | None:
        with self._trava:
            con = self._conexao()
            if con is None:
                return None
            try:
                linha = con.execute(
                    "SELECT corpo, obtido_em, expira_em FROM respostas WHERE chave = ?", (chave,)
                ).fetchone()
            except sqlite3.Error:
                return None
        return (bytes(linha[0]), linha[1], linha[2]) if linha else None

    def gravar(self, chave: str, url: str, corpo: bytes, ttl: float) -> None:
        agora = time.time()
        with self._trava:
            con = self._conexao()
            if con is None:
                return
            try:
                con.execute(
                    "INSERT OR REPLACE INTO respostas (chave, url, obtido_em, expira_em, corpo) VALUES (?, ?, ?, ?, ?)",
                    (chave, url, agora, agora + ttl, corpo),
                )
                con.commit()
            except sqlite3.Error as erro:
                print(f"[brasil-macro] falha ao gravar no cache: {erro}", file=sys.stderr)

    def limpar(self) -> int:
        with self._trava:
            con = self._conexao()
            if con is None:
                return 0
            try:
                n = con.execute("DELETE FROM respostas").rowcount
                con.commit()
                return n
            except sqlite3.Error:
                return 0


_cache = _Cache()


def limpar_cache() -> int:
    """Apaga todas as respostas guardadas; devolve quantas foram removidas."""
    return _cache.limpar()


# ------------------------------------------------------------------- sessão


class _Sessao:
    """Estado preso a um laço de eventos: cliente HTTP, semáforos e relógios por host."""

    def __init__(self) -> None:
        self.cliente = httpx.AsyncClient(
            headers={"User-Agent": AGENTE, "Accept": "application/json, text/plain, */*"},
            follow_redirects=True,
        )
        self.semaforos: dict[str, asyncio.Semaphore] = {}
        self.ultimo: dict[str, float] = {}


_sessao: _Sessao | None = None
_laco: asyncio.AbstractEventLoop | None = None


def _sessao_atual() -> _Sessao:
    global _sessao, _laco
    laco = asyncio.get_running_loop()
    if _sessao is None or laco is not _laco:
        _sessao = _Sessao()
        _laco = laco
    return _sessao


async def fechar() -> None:
    global _sessao, _laco
    if _sessao is not None and _laco is asyncio.get_running_loop():
        await _sessao.cliente.aclose()
    _sessao = None
    _laco = None


# --------------------------------------------------------------- requisição


def _chave(metodo: str, url: str, json_corpo: Any, corpo: bytes | str | None) -> str:
    partes = [metodo.upper(), url]
    partes.append(json.dumps(json_corpo, sort_keys=True, ensure_ascii=False) if json_corpo is not None else "")
    if isinstance(corpo, bytes):
        partes.append(corpo.decode("utf-8", "replace"))
    else:
        partes.append(corpo or "")
    return hashlib.sha256("\x1f".join(partes).encode("utf-8")).hexdigest()


def _json_valido(corpo: bytes) -> bool:
    try:
        json.loads(corpo.decode("utf-8-sig", "replace"))
        return True
    except ValueError:
        return False


async def requisitar(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    metodo: str = "GET",
    json_corpo: Any = None,
    corpo: bytes | str | None = None,
    cabecalhos: dict[str, str] | None = None,
    ttl: float = TTL_MEDIO,
    tempo_limite: float = 60.0,
    tentativas: int = 4,
    aceitar: tuple[int, ...] = (200,),
    espera_json: bool = False,
) -> Resposta:
    """Faz uma requisição a uma fonte oficial, com cache e novas tentativas.

    `aceitar` lista os status devolvidos ao chamador sem erro (algumas APIs usam
    404 para "sem dados no período"). Só respostas 200 entram no cache.
    Com `espera_json`, um corpo que não é JSON conta como falha passageira.
    """
    url_completa = str(httpx.URL(url, params=params)) if params else url
    chave = _chave(metodo, url_completa, json_corpo, corpo)
    usar_cache = ttl > 0 and not os.environ.get("BRASIL_MACRO_SEM_CACHE")

    guardado = _cache.ler(chave) if usar_cache else None
    if guardado and guardado[2] > time.time():
        return Resposta(
            corpo=guardado[0],
            url=url_completa,
            status=200,
            consultado_em=datetime.fromtimestamp(guardado[1], timezone.utc),
            do_cache=True,
        )

    sessao = _sessao_atual()
    host = urlsplit(url_completa).hostname or ""
    politica = _POLITICAS.get(host, _PADRAO)
    semaforo = sessao.semaforos.setdefault(host, asyncio.Semaphore(politica.concorrencia))
    extras = dict(cabecalhos or {})
    ultimo_erro = "sem resposta"

    for tentativa in range(1, tentativas + 1):
        espera = min(2.0 ** (tentativa - 1), 8.0)
        try:
            async with semaforo:
                if politica.intervalo:
                    falta = sessao.ultimo.get(host, 0.0) + politica.intervalo - time.monotonic()
                    if falta > 0:
                        await asyncio.sleep(falta)
                try:
                    r = await sessao.cliente.request(
                        metodo,
                        url_completa,
                        json=json_corpo,
                        content=corpo,
                        headers=extras or None,
                        timeout=tempo_limite,
                    )
                finally:
                    sessao.ultimo[host] = time.monotonic()
        except httpx.HTTPError as erro:
            ultimo_erro = f"{type(erro).__name__}: {erro}".rstrip(": ")
        else:
            if r.status_code == 304:
                # A API do BCB às vezes devolve 304 sem corpo a quem não mandou
                # cabeçalho condicional; pedir sem cache contorna.
                extras.update({"Cache-Control": "no-cache", "Pragma": "no-cache"})
                ultimo_erro = "HTTP 304 sem conteúdo"
                espera = 0.3
            elif r.status_code in aceitar:
                dados = r.content
                if r.status_code == 200 and espera_json and not _json_valido(dados):
                    ultimo_erro = "resposta vazia ou fora do formato JSON"
                else:
                    if usar_cache and r.status_code == 200:
                        _cache.gravar(chave, url_completa, dados, ttl)
                    return Resposta(dados, url_completa, r.status_code, datetime.now(timezone.utc))
            elif r.status_code in _REPETIVEIS:
                ultimo_erro = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    pedido = r.headers.get("Retry-After", "")
                    espera = max(espera, politica.intervalo, float(pedido) if pedido.isdigit() else 5.0)
            else:
                trecho = r.text[:300].strip().replace("\n", " ")
                raise ErroFonte(f"{host} respondeu HTTP {r.status_code} para {url_completa}. Resposta: {trecho}")
        if tentativa < tentativas:
            await asyncio.sleep(espera)

    if guardado:
        return Resposta(
            corpo=guardado[0],
            url=url_completa,
            status=200,
            consultado_em=datetime.fromtimestamp(guardado[1], timezone.utc),
            do_cache=True,
            desatualizado=True,
        )
    raise ErroFonte(
        f"Não foi possível consultar {host} após {tentativas} tentativas ({ultimo_erro}). URL: {url_completa}"
    )
