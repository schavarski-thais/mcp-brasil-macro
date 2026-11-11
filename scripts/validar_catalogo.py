"""Valida os indicadores SGS do catálogo contra o Banco Central do Brasil.

Para cada indicador com chave `sgs` (inclusive os de produtor privado), consulta
os metadados oficiais (serviço SOAP do SGS: nome, unidade e periodicidade) e a
última observação (API JSON, janela recente) e aponta:

  - periodicidade do catálogo diferente da oficial;
  - série parada: última observação mais velha que o limite da periodicidade
    (diária 15 dias, mensal 150, trimestral 300, anual 800);
  - série sem dados ou inexistente;
  - unidade incompatível com a oficial (escala mil/milhões/bilhões, moeda,
    percentual ou base da taxa) — heurística, só acusa divergência evidente.

Também imprime, para conferência humana, uma tabela com id, código, nome e
unidade do catálogo e oficiais, e data e valor da última observação.

Uso:
    python scripts/validar_catalogo.py [--tema TEMA] [--so-problemas] [--json]
                                       [--sem-cache] [--concorrencia N]

Falha de rede não derruba o script: o indicador sai como "não verificado".

Código de saída: 0 = tudo verificado, sem problemas; 1 = há problemas;
2 = nenhum problema encontrado, mas algum indicador não pôde ser verificado.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

# Idade máxima da última observação, em dias, antes de a série contar como parada.
LIMITE_DIAS = {"diaria": 15, "mensal": 150, "trimestral": 300, "anual": 800}

# Janela da primeira consulta, em dias: curta, mas com folga sobre o limite acima.
JANELA_DIAS = {"diaria": 45, "mensal": 400, "trimestral": 800, "anual": 2200}

_ESCALAS = {
    "mil": "mil",
    "milhares": "mil",
    "milhao": "milhões",
    "milhoes": "milhões",
    "bilhao": "bilhões",
    "bilhoes": "bilhões",
}


@dataclass
class Resultado:
    id: str
    tema: str
    codigo: int
    oficial: bool
    nome_catalogo: str
    unidade_catalogo: str
    periodicidade_catalogo: str
    nome_oficial: str = ""
    unidade_oficial: str = ""
    periodicidade_oficial: str = ""
    ultima_data: str = ""
    ultimo_valor: float | None = None
    idade_dias: int | None = None
    problemas: list[str] = field(default_factory=list)
    nao_verificado: list[str] = field(default_factory=list)

    @property
    def situacao(self) -> str:
        if self.problemas:
            return "problema"
        return "nao_verificado" if self.nao_verificado else "ok"


# ------------------------------------------------------------------ unidades


def _palavras(texto: str) -> list[str]:
    from brasil_macro.catalogo import normalizar

    return normalizar(texto).split()


def _escala(unidade: str) -> str:
    for palavra in _palavras(unidade):
        if palavra in _ESCALAS:
            return _ESCALAS[palavra]
    return ""


def _moedas(unidade: str) -> set[str]:
    u = unidade.lower()
    achadas = set()
    if "us$" in u or "dólar" in u or "dolar" in u:
        achadas.add("US$")
    # "u.m.c." é "unidade monetária corrente": o real, nas séries atuais.
    if "r$" in u or "u.m.c" in u:
        achadas.add("R$")
    return achadas


def _base_taxa(unidade: str) -> str:
    palavras = _palavras(unidade)
    for i, palavra in enumerate(palavras[:-1]):
        if palavra == "a" and palavras[i + 1] in ("a", "m", "d"):
            return f"a.{palavras[i + 1]}."
    return ""


def divergencias_de_unidade(catalogo: str, oficial: str) -> list[str]:
    """Diferenças evidentes entre a unidade do catálogo e a oficial do SGS.

    A grafia varia muito ("R$ (milhões)", "u.m.c. (mil)", "%"), então só o que
    muda a leitura do número é comparado; o resto fica para a tabela.
    """
    if not oficial.strip():
        return []
    achados: list[str] = []
    escala_c, escala_o = _escala(catalogo), _escala(oficial)
    if escala_o and escala_c != escala_o:
        achados.append(f"escala: catálogo {escala_c or 'sem escala'!r}, oficial {escala_o!r}")
    elif escala_c and not escala_o and _moedas(oficial):
        achados.append(f"escala: catálogo {escala_c!r}, oficial sem escala")
    moedas_c, moedas_o = _moedas(catalogo), _moedas(oficial)
    if moedas_c and moedas_o and moedas_c != moedas_o:
        achados.append(f"moeda: catálogo {'/'.join(sorted(moedas_c))}, oficial {'/'.join(sorted(moedas_o))}")
    pct_c = "%" in catalogo or "p.p." in catalogo.lower()
    pct_o = "%" in oficial or "p.p." in oficial.lower()
    if pct_o and not pct_c:
        achados.append("oficial é percentual e o catálogo não")
    elif pct_c and moedas_o and not pct_o:
        achados.append("catálogo é percentual e a oficial é monetária")
    base_c, base_o = _base_taxa(catalogo), _base_taxa(oficial)
    if base_c and base_o and base_c != base_o:
        achados.append(f"base da taxa: catálogo {base_c}, oficial {base_o}")
    return achados


# ---------------------------------------------------------------- validação


async def _ultima_observacao(codigo: int, periodicidade: str, hoje: date):
    """Última observação com valor, buscando numa janela recente e, se vazia, numa longa."""
    from brasil_macro.fontes.sgs import serie_sgs

    inicio = hoje - timedelta(days=JANELA_DIAS[periodicidade])
    serie = await serie_sgs(codigo, inicio=inicio, periodicidade=periodicidade)
    validas = [(d, v) for d, v in serie.observacoes if v is not None]
    if not validas:
        # Nada recente: procura a última observação existente, para dizer desde quando parou.
        # Séries diárias só aceitam janelas de até 10 anos; as demais vêm completas.
        longo = hoje - timedelta(days=3600) if periodicidade == "diaria" else None
        serie = await serie_sgs(codigo, inicio=longo, periodicidade=periodicidade)
        validas = [(d, v) for d, v in serie.observacoes if v is not None]
    return validas[-1] if validas else None


async def validar(ind, semaforo: asyncio.Semaphore, hoje: date) -> Resultado:
    from brasil_macro.fontes.sgs import metadados_sgs
    from brasil_macro.http import ErroFonte

    res = Resultado(
        id=ind.id,
        tema=ind.tema,
        codigo=int(ind.parametros),
        oficial=ind.oficial,
        nome_catalogo=ind.nome,
        unidade_catalogo=ind.unidade,
        periodicidade_catalogo=ind.periodicidade,
    )
    async with semaforo:
        try:
            meta = await metadados_sgs(res.codigo)
        except Exception as erro:  # noqa: BLE001 — nenhuma falha de rede pode derrubar a validação
            meta = None
            res.nao_verificado.append(f"metadados: {type(erro).__name__}: {erro}"[:200])
        else:
            if meta is None:
                res.nao_verificado.append("metadados: serviço SOAP do SGS indisponível ou série desconhecida")
        if meta:
            res.nome_oficial = meta["nome"]
            res.unidade_oficial = meta["unidade"]
            res.periodicidade_oficial = meta["periodicidade"]
            if meta["periodicidade"] != ind.periodicidade:
                res.problemas.append(
                    f"periodicidade: catálogo {ind.periodicidade!r}, oficial {meta['periodicidade']!r} "
                    f"(sigla SGS {meta['periodicidade_sgs']!r})"
                )
            for achado in divergencias_de_unidade(ind.unidade, meta["unidade"]):
                res.problemas.append(f"unidade ({ind.unidade!r} x {meta['unidade']!r}): {achado}")

        # A idade é medida pela periodicidade oficial, quando conhecida.
        periodicidade = res.periodicidade_oficial or ind.periodicidade
        try:
            ultima = await _ultima_observacao(res.codigo, periodicidade, hoje)
        except ErroFonte as erro:
            if "não encontrou a série" in str(erro):
                res.problemas.append("série inexistente no SGS")
            else:
                res.nao_verificado.append(f"dados: {erro}"[:200])
            return res
        except Exception as erro:  # noqa: BLE001
            res.nao_verificado.append(f"dados: {type(erro).__name__}: {erro}"[:200])
            return res

    if ultima is None:
        res.problemas.append("sem dados: a série não devolveu nenhuma observação")
        return res
    quando, valor = ultima
    res.ultima_data = quando.isoformat()
    res.ultimo_valor = valor
    res.idade_dias = (hoje - quando).days
    limite = LIMITE_DIAS[periodicidade]
    if res.idade_dias > limite:
        res.problemas.append(
            f"série parada: última observação em {quando.isoformat()}, há {res.idade_dias} dias "
            f"(limite de {limite} para série {periodicidade})"
        )
    return res


async def validar_todos(tema: str | None, concorrencia: int) -> list[Resultado]:
    from brasil_macro import catalogo
    from brasil_macro.http import fechar

    indicadores = [i for i in catalogo.todos(com_privados=True) if i.origem == "sgs"]
    if tema:
        alvo = catalogo.normalizar(tema).replace(" ", "_")
        conhecidos = catalogo.temas()
        if alvo not in conhecidos:
            raise SystemExit(f"Tema {tema!r} não existe. Temas: {', '.join(sorted(conhecidos))}.")
        indicadores = [i for i in indicadores if i.tema == alvo]
    semaforo = asyncio.Semaphore(concorrencia)
    hoje = date.today()
    try:
        return list(await asyncio.gather(*(validar(i, semaforo, hoje) for i in indicadores)))
    finally:
        await fechar()


# -------------------------------------------------------------------- saída


def _repetidos(resultados: list[Resultado]) -> dict[int, list[str]]:
    por_codigo: dict[int, list[str]] = {}
    for r in resultados:
        por_codigo.setdefault(r.codigo, []).append(r.id)
    return {c: ids for c, ids in por_codigo.items() if len(ids) > 1}


def _resumo(resultados: list[Resultado]) -> dict[str, int]:
    return {
        "total": len(resultados),
        "ok": sum(r.situacao == "ok" for r in resultados),
        "com_problema": sum(r.situacao == "problema" for r in resultados),
        "nao_verificados": sum(r.situacao == "nao_verificado" for r in resultados),
    }


def _celula(texto: object) -> str:
    return str(texto).replace("|", "/").replace("\n", " ").strip()


def _valor(v: float | None) -> str:
    return "" if v is None else f"{v:,.4f}".rstrip("0").rstrip(".")


def imprimir_texto(resultados: list[Resultado], so_problemas: bool) -> None:
    resumo = _resumo(resultados)
    visiveis = [r for r in resultados if r.situacao != "ok"] if so_problemas else resultados

    print(f"# Validação do catálogo SGS — {date.today().isoformat()}")
    print()
    print(
        f"{resumo['total']} indicadores SGS: {resumo['ok']} ok, {resumo['com_problema']} com problema, "
        f"{resumo['nao_verificados']} não verificados."
    )

    com_problema = [r for r in resultados if r.problemas]
    print()
    print(f"## Problemas ({len(com_problema)})")
    for r in com_problema:
        for p in r.problemas:
            print(f"- [{r.tema}] {r.id} (SGS {r.codigo}): {p}")
    if not com_problema:
        print("Nenhum.")

    pendentes = [r for r in resultados if r.nao_verificado]
    if pendentes:
        print()
        print(f"## Não verificados ({len(pendentes)})")
        for r in pendentes:
            for p in r.nao_verificado:
                print(f"- [{r.tema}] {r.id} (SGS {r.codigo}): {p}")

    repetidos = _repetidos(resultados)
    if repetidos:
        print()
        print("## Códigos SGS usados por mais de um indicador")
        for codigo, ids in sorted(repetidos.items()):
            print(f"- SGS {codigo}: {', '.join(ids)}")

    print()
    print("## Tabela para conferência" + (" (só problemas e não verificados)" if so_problemas else ""))
    if not visiveis:
        print("Nada a mostrar.")
        return
    tema_atual = None
    for r in visiveis:
        if r.tema != tema_atual:
            tema_atual = r.tema
            print()
            print(f"### {tema_atual}")
            print()
            print(
                "| id | SGS | nome no catálogo | nome oficial | unid. catálogo | unid. oficial "
                "| period. catálogo | period. oficial | última obs. | valor | situação |"
            )
            print("|---|---|---|---|---|---|---|---|---|---|---|")
        situacao = {"ok": "ok", "problema": "PROBLEMA", "nao_verificado": "não verificado"}[r.situacao]
        if not r.oficial:
            situacao += " (privado)"
        print(
            "| "
            + " | ".join(
                _celula(c)
                for c in (
                    r.id,
                    r.codigo,
                    r.nome_catalogo,
                    r.nome_oficial or "?",
                    r.unidade_catalogo,
                    r.unidade_oficial or ("?" if not r.nome_oficial else ""),
                    r.periodicidade_catalogo,
                    r.periodicidade_oficial or "?",
                    r.ultima_data or "?",
                    _valor(r.ultimo_valor),
                    situacao,
                )
            )
            + " |"
        )


def imprimir_json(resultados: list[Resultado], so_problemas: bool) -> None:
    visiveis = [r for r in resultados if r.situacao != "ok"] if so_problemas else resultados
    saida = {
        "gerado_em": date.today().isoformat(),
        "limites_dias": LIMITE_DIAS,
        "resumo": _resumo(resultados),
        "codigos_repetidos": {str(c): ids for c, ids in sorted(_repetidos(resultados).items())},
        "indicadores": [{**asdict(r), "situacao": r.situacao} for r in visiveis],
    }
    print(json.dumps(saida, ensure_ascii=False, indent=2))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Valida os indicadores SGS do catálogo contra o Banco Central.")
    parser.add_argument("--json", action="store_true", help="saída em JSON")
    parser.add_argument("--tema", help="valida só um tema (ex.: fiscal, credito)")
    parser.add_argument("--so-problemas", action="store_true", help="mostra só problemas e não verificados")
    parser.add_argument("--sem-cache", action="store_true", help="ignora o cache em disco e consulta tudo de novo")
    parser.add_argument("--concorrencia", type=int, default=5, help="consultas simultâneas (1 a 6; padrão 5)")
    args = parser.parse_args()
    if args.sem_cache:
        os.environ["BRASIL_MACRO_SEM_CACHE"] = "1"

    resultados = asyncio.run(validar_todos(args.tema, max(1, min(args.concorrencia, 6))))
    if args.json:
        imprimir_json(resultados, args.so_problemas)
    else:
        imprimir_texto(resultados, args.so_problemas)

    resumo = _resumo(resultados)
    if resumo["com_problema"]:
        return 1
    return 2 if resumo["nao_verificados"] else 0


if __name__ == "__main__":
    sys.exit(main())
