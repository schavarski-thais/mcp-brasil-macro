"""Clientes das fontes oficiais. Cada módulo fala com uma fonte e pode registrar buscadores do catálogo."""

from __future__ import annotations

import importlib
import pkgutil

_carregado = False


def carregar() -> None:
    """Importa todos os módulos de fonte, para que registrem seus buscadores."""
    global _carregado
    if _carregado:
        return
    _carregado = True
    for info in pkgutil.iter_modules(__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"{__name__}.{info.name}")
