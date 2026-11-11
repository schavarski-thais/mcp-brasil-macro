"""Chama ferramentas do servidor em processo, para testes manuais.

Uso: python scripts/chamar.py <ferramenta> ['{"arg": "valor"}'] [--max N]
     python scripts/chamar.py --listar
"""
import asyncio
import json
import sys

from mcp import Client

from brasil_macro.server import criar_servidor


async def main() -> None:
    args = sys.argv[1:]
    maximo = 1500
    if "--max" in args:
        i = args.index("--max")
        maximo = int(args[i + 1])
        del args[i : i + 2]
    async with Client(criar_servidor(), raise_exceptions=True) as cliente:
        if not args or args[0] == "--listar":
            for t in (await cliente.list_tools()).tools:
                print(f"- {t.name}: {(t.description or '').strip().splitlines()[0]}")
            return
        parametros = json.loads(args[1]) if len(args) > 1 else {}
        r = await cliente.call_tool(args[0], parametros)
        texto = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        print(f"[erro={r.is_error}] {len(texto)} caracteres")
        print(texto[:maximo])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
