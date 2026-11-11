"""Ferramentas do Ipeadata (IPEA): qualquer série por código e busca por texto."""

from __future__ import annotations

from typing import Any

from .. import catalogo
from ..fontes import ipea
from ..modelos import data_opcional
from ._comum import carimbo, ferramenta

_PADRAO_ULTIMOS = {"diaria": 30, "mensal": 24, "trimestral": 12, "anual": 10}
_NATUREZA = {
    "privada": "é uma entidade privada",
    "indefinida": "não é um produtor identificado (compilação de várias fontes)",
}


def _fonte_original(meta: dict[str, Any]) -> dict[str, Any]:
    natureza = ipea.classificar_fonte(meta.get("FNTSIGLA"))
    return {
        "sigla": meta.get("FNTSIGLA") or "",
        "nome": (meta.get("FNTNOME") or "").strip(),
        "oficial": natureza == "oficial",
        "natureza": natureza,
    }


def _resumo(meta: dict[str, Any], temas: dict[int, str]) -> dict[str, Any]:
    natureza = ipea.classificar_fonte(meta.get("FNTSIGLA"))
    item: dict[str, Any] = {
        "codigo": meta.get("SERCODIGO"),
        "nome": (meta.get("SERNOME") or "").strip(),
        "unidade": ipea.unidade_de(meta),
        "periodicidade": (meta.get("PERNOME") or "").strip(),
        "fonte_original": meta.get("FNTSIGLA") or "",
        "tema": temas.get(meta.get("TEMCODIGO") or -1, ""),
        "atualizada_em": (meta.get("SERATUALIZACAO") or "")[:10],
    }
    if natureza != "oficial":
        item["oficial"] = False
    if meta.get("SERSTATUS") == "I":
        item["inativa"] = True
    return item


def registrar(servidor) -> None:
    @ferramenta(servidor)
    async def ipea_serie(
        codigo: str,
        inicio: str | None = None,
        fim: str | None = None,
        ultimos: int | None = None,
        territorio: str | None = None,
    ) -> dict[str, Any]:
        """Série do Ipeadata pelo código, com metadados e fonte original. O Ipeadata redistribui dados de IBGE, BCB, Receita, ANP etc. Séries de fonte privada (FGV, CNI, Anfavea...) ficam bloqueadas por padrão. Sem período, traz as observações mais recentes. Localize o código com ipea_buscar.

        Args:
            codigo: ex.: "CAGED12_SALDON12".
            inicio, fim, ultimos: período e recorte, como em obter_serie.
            territorio: só séries regionais: "0" Brasil, "1" a "5" regiões, "35" ou "SP" UF, 7 dígitos município."""
        if ultimos is not None and ultimos < 1:
            raise ValueError("ultimos deve ser um inteiro positivo.")
        meta = await ipea.metadados_ipea(codigo)
        original = _fonte_original(meta)
        if not original["oficial"] and not catalogo.incluir_privados():
            raise LookupError(
                f"A série {meta.get('SERCODIGO')} ({(meta.get('SERNOME') or '').strip()}) é distribuída pelo Ipeadata, "
                f"mas a fonte original — {original['sigla']} — {_NATUREZA[original['natureza']]}. Este servidor entrega "
                "apenas dados de fontes oficiais; defina BRASIL_MACRO_INCLUIR_PRIVADOS=1 para habilitar."
            )
        d_ini, d_fim = data_opcional(inicio), data_opcional(fim, fim=True)
        serie = await ipea.serie_ipea(codigo, d_ini, d_fim, territorio, meta)
        if d_ini is None and ultimos is None:
            ultimos = _PADRAO_ULTIMOS[serie.periodicidade]
        if ultimos:
            serie.observacoes = serie.observacoes[-ultimos:]
        if not original["oficial"]:
            serie.notas.insert(0, f"Fonte original não oficial: {original['sigla']} ({original['natureza']}).")

        saida = serie.para_dict()
        saida["fonte_original"] = original
        saida["periodicidade_ipea"] = (meta.get("PERNOME") or "").strip()
        saida["situacao"] = "inativa" if meta.get("SERSTATUS") == "I" else "ativa"
        saida["ultima_atualizacao"] = (meta.get("SERATUALIZACAO") or "")[:10]
        saida["base"] = meta.get("BASNOME") or ""
        comentario = ipea.limpar_texto(meta.get("SERCOMENTARIO"))
        if comentario:
            saida["descricao"] = comentario[:600] + ("…" if len(comentario) > 600 else "")
        if not serie.observacoes:
            saida.setdefault("notas", []).append("Nenhuma observação no período pedido.")
        return saida

    @ferramenta(servidor)
    async def ipea_buscar(
        consulta: str = "",
        fonte: str | None = None,
        tema: str | None = None,
        base: str = "macro",
        incluir_inativas: bool = False,
        limite: int = 25,
    ) -> dict[str, Any]:
        """Procura séries no Ipeadata por texto no nome, com filtro por fonte original e tema; devolve o código para ipea_serie. Sem argumentos, lista fontes e temas.

        Args:
            consulta: palavras do nome, todas obrigatórias; sem acento nem maiúscula.
            fonte: trecho da sigla ou do nome da fonte original ("ANP", "Receita", "Caged").
            tema: nome ou código do tema.
            base: "macro" (padrão), "regional", "social" ou "todas".
            incluir_inativas: inclui séries descontinuadas.
            limite: até 100 resultados."""
        bases = list(ipea.BASES) if catalogo.normalizar(base) == "todas" else [catalogo.normalizar(base)]
        itens: list[dict[str, Any]] = []
        url = ""
        consultado = None
        for nome_base in bases:
            parte, resposta = await ipea.listar_series(nome_base)
            itens.extend(parte)
            url, consultado = url or resposta.url, consultado or resposta.consultado_em
        temas = await ipea.temas_ipea()
        privados = catalogo.incluir_privados()
        saida: dict[str, Any] = {"fonte": ipea.ROTULO, "base": ", ".join(bases)}

        if not consulta.strip() and not fonte and not tema:
            por_fonte: dict[str, int] = {}
            por_tema: dict[str, int] = {}
            for item in itens:
                if item.get("SERSTATUS") == "I" and not incluir_inativas:
                    continue
                sigla = item.get("FNTSIGLA") or ""
                por_fonte[sigla] = por_fonte.get(sigla, 0) + 1
                nome_tema = temas.get(item.get("TEMCODIGO") or -1, "")
                por_tema[nome_tema] = por_tema.get(nome_tema, 0) + 1
            saida["fontes_originais"] = [
                {"sigla": s, "series": n, "oficial": ipea.fonte_oficial(s)}
                for s, n in sorted(por_fonte.items(), key=lambda t: -t[1])
            ]
            saida["temas"] = [{"tema": t, "series": n} for t, n in sorted(por_tema.items(), key=lambda t: -t[1])]
            saida["dica"] = "Informe consulta, fonte e/ou tema para listar as séries."
        else:
            achadas = ipea.filtrar_series(itens, consulta, fonte, tema, temas, apenas_ativas=not incluir_inativas)
            visiveis = [m for m in achadas if privados or ipea.fonte_oficial(m.get("FNTSIGLA"))]
            limite = max(1, min(limite, 100))
            saida["total_encontrado"] = len(visiveis)
            saida["series"] = [_resumo(m, temas) for m in visiveis[:limite]]
            notas: list[str] = []
            if len(visiveis) > limite:
                notas.append(f"Exibidas {limite} de {len(visiveis)} séries; refine a consulta ou aumente limite.")
            omitidas = len(achadas) - len(visiveis)
            if omitidas:
                siglas = sorted({m.get("FNTSIGLA") or "" for m in achadas if not ipea.fonte_oficial(m.get("FNTSIGLA"))})
                notas.append(
                    f"{omitidas} série(s) de fonte original não oficial foram omitidas ({', '.join(siglas[:8])}). "
                    "Defina BRASIL_MACRO_INCLUIR_PRIVADOS=1 para vê-las."
                )
            if not visiveis:
                notas.append(
                    "Nada encontrado. Tente menos palavras, incluir_inativas=true ou outra base (regional, social)."
                )
            if notas:
                saida["notas"] = notas
        saida["url_fonte"] = url
        saida["consultado_em"] = carimbo(consultado)
        return saida
