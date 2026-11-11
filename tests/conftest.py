"""Configuração comum dos testes.

Os testes sem o marcador `rede` não acessam a internet. Os marcados com
`@pytest.mark.rede` consultam as APIs oficiais e só rodam com `pytest -m rede`.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _cache_isolado(tmp_path, monkeypatch):
    """Cada teste usa um cache próprio, para não ler nem sujar o cache do usuário."""
    monkeypatch.setenv("BRASIL_MACRO_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def rodar():
    """Executa uma corrotina até o fim: `resultado = rodar(funcao_assincrona(...))`."""
    return asyncio.run
