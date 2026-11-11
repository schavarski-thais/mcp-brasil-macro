"""Ferramentas MCP. Cada módulo desta pasta expõe `registrar(servidor)`."""

from __future__ import annotations

import importlib
import pkgutil


def registrar_todas(servidor) -> list[str]:
    """Registra as ferramentas de todos os módulos; devolve os módulos carregados."""
    carregados = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        modulo = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(modulo, "registrar"):
            modulo.registrar(servidor)
            carregados.append(info.name)
    return carregados
