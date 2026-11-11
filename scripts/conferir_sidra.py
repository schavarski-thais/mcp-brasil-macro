"""Confere todos os indicadores do catálogo com origem SIDRA: última observação e idade do dado."""
import asyncio
import sys
from datetime import date, timedelta

from brasil_macro import catalogo
from brasil_macro.modelos import rotulo_periodo

LIMITE_DIAS = {"mensal": 150, "trimestral": 300, "anual": 800}


async def main() -> int:
    hoje = date.today()
    problemas = 0
    for ind in catalogo.todos(com_privados=True):
        if ind.origem != "sidra":
            continue
        try:
            serie = await catalogo.obter_dados(ind, hoje - timedelta(days=900), None)
            validas = [(d, v) for d, v in serie.observacoes if v is not None]
            if not validas:
                raise RuntimeError("sem observações")
            d, v = validas[-1]
            velho = (hoje - d).days > LIMITE_DIAS[ind.periodicidade]
            problemas += velho
            marca = "PARADA" if velho else "ok"
            print(f"{marca:6} {ind.id:44} {rotulo_periodo(d, ind.periodicidade):8} {v:>14} {serie.unidade:10} n={len(validas)}")
        except Exception as erro:  # noqa: BLE001
            problemas += 1
            print(f"ERRO   {ind.id:44} {type(erro).__name__}: {str(erro)[:150]}")
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
