"""IPEA — Ipeadata (API OData v4).

O Ipeadata é um agregador: cada série traz a fonte original (FNTSIGLA), que pode
ser um órgão público ou uma entidade privada. Este módulo classifica a fonte e
deixa para quem chama a decisão de bloquear as não oficiais.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from ..catalogo import Indicador, fonte_de_dados, normalizar
from ..http import TTL_LONGO, TTL_MEDIO, ErroFonte, Resposta, requisitar
from ..modelos import Observacao, Serie

ROTULO = "IPEA/Ipeadata"
_BASE = "http://www.ipeadata.gov.br/api/odata4/"
_TTL_LISTA = 24 * 3600

BASES = {"macro": "Macroeconômico", "regional": "Regional", "social": "Social"}

_PERIODICIDADES = {"Diária": "diaria", "Mensal": "mensal", "Trimestral": "trimestral", "Anual": "anual"}

# Primeiro trecho da sigla da fonte (antes da barra), normalizado. Levantado em
# nov/2026 nas três bases do Ipeadata. Só entram órgãos e empresas públicas do
# Brasil, organismos internacionais e órgãos estatísticos de outros governos.
# Sigla que não está aqui é tratada como não oficial, inclusive as novas.
_OFICIAIS = {
    "ipea",
    "bacen",
    "bacen outras",
    "bcb",
    "ibge",
    "sis ibge",
    "min economia",
    "min fazenda",
    "mdic",
    "mte",
    "mps",
    "mds",
    "ms",
    "mme",
    "mivop",
    "anp",
    "ans",
    "antt",
    "antaq",
    "conab",
    "datasus",
    "tse",
    "rffsa",
    "seab pr",
    "seade",
    # Associação civil criada por lei (9.648/1998) para operar o sistema elétrico,
    # regulada e fiscalizada pela Aneel.
    "ons",
    "fmi",
    "banco mundial",
    "pnud",
    "undata",
    "bls",
    "eia",
}
# Compilações históricas sem produtor identificado.
_INDEFINIDAS = {"outras fontes", "outfonte"}

UFS = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26", "AL": "27", "SE": "28", "BA": "29",
    "MG": "31", "ES": "32", "RJ": "33", "SP": "35", "PR": "41", "SC": "42", "RS": "43",
    "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}  # fmt: skip


def classificar_fonte(sigla: str | None) -> str:
    """Natureza da fonte original de uma série: "oficial", "privada" ou "indefinida"."""
    chave = normalizar((sigla or "").split("/")[0])
    if chave in _OFICIAIS:
        return "oficial"
    if not chave or normalizar(sigla or "") in _INDEFINIDAS:
        return "indefinida"
    return "privada"


def fonte_oficial(sigla: str | None) -> bool:
    return classificar_fonte(sigla) == "oficial"


def _url(recurso: str, filtro: str | None = None) -> str:
    # A codificação é feita aqui: o servidor só entende espaços como %20 em $filter.
    return _BASE + recurso + (f"?$filter={quote(filtro, safe='')}" if filtro else "")


def _validar_codigo(codigo: str) -> str:
    limpo = codigo.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{2,60}", limpo):
        raise ValueError(
            f"Código de série do Ipeadata inválido: {codigo!r}. Use o código como aparece em ipea_buscar, "
            "ex.: CAGED12_SALDON12."
        )
    return limpo


def _valores(resposta: Resposta) -> list[dict[str, Any]]:
    dados = resposta.json()
    if not isinstance(dados, dict) or not isinstance(dados.get("value"), list):
        raise ErroFonte(f"Resposta inesperada do Ipeadata em {resposta.url}.")
    return dados["value"]


def limpar_texto(texto: str | None) -> str:
    """Tira as marcações HTML dos comentários do Ipeadata."""
    sem_tags = re.sub(r"<[^>]+>", " ", texto or "")
    return re.sub(r"\s+", " ", html.unescape(sem_tags)).strip()


def unidade_de(meta: dict[str, Any]) -> str:
    unidade = (meta.get("UNINOME") or "").strip()
    multiplo = (meta.get("MULNOME") or "").strip()
    if unidade in ("", "-"):
        unidade = ""
    if multiplo:
        return f"{unidade} ({multiplo})" if unidade else multiplo
    return unidade


def periodicidade_de(meta: dict[str, Any]) -> str:
    # Séries decenais, quadrienais etc. são datadas em 1º de janeiro, como as anuais.
    return _PERIODICIDADES.get(meta.get("PERNOME") or "", "anual")


async def metadados_ipea(codigo: str) -> dict[str, Any]:
    """Metadados de uma série. Levanta LookupError se o código não existir."""
    codigo = _validar_codigo(codigo)
    r = await requisitar(_url(f"Metadados('{codigo}')"), ttl=TTL_MEDIO, espera_json=True)
    itens = _valores(r)
    if not itens:
        # A API responde 200 com lista vazia para código inexistente.
        raise LookupError(f"A série {codigo!r} não existe no Ipeadata. Use ipea_buscar para localizar o código.")
    return itens[0]


def interpretar_valores(itens: list[dict[str, Any]]) -> dict[tuple[str, str], list[Observacao]]:
    """Agrupa as observações por território: {(nível, código do território): [(data, valor)]}."""
    grupos: dict[tuple[str, str], list[Observacao]] = {}
    for item in itens:
        bruto = item.get("VALDATA") or ""
        try:
            dia = date(int(bruto[0:4]), int(bruto[5:7]), int(bruto[8:10]))
        except ValueError:
            continue
        valor = item.get("VALVALOR")
        chave = ((item.get("NIVNOME") or "").strip(), (item.get("TERCODIGO") or "").strip())
        grupos.setdefault(chave, []).append((dia, float(valor) if valor is not None else None))
    for obs in grupos.values():
        obs.sort(key=lambda o: o[0])
    return grupos


def escolher_territorio(
    grupos: dict[tuple[str, str], list[Observacao]], territorio: str | None
) -> tuple[tuple[str, str], list[Observacao]]:
    """Seleciona um território entre os da série; sem pedido, usa o único disponível ou o Brasil."""
    if not grupos:
        return ("", ""), []
    if territorio and territorio.strip():
        pedido = territorio.strip()
        alvo = "0" if normalizar(pedido) == "brasil" else UFS.get(pedido.upper(), pedido)
        achados = [c for c in grupos if c[1] == alvo]
        if len(achados) > 1:
            # Os códigos 1 a 5 são regiões; os de dois dígitos, estados; sete dígitos, municípios.
            preferidos = [c for c in achados if c[0] in ("Brasil", "Regiões", "Estados", "Municípios")]
            achados = preferidos or achados
        if achados:
            return achados[0], grupos[achados[0]]
    elif len(grupos) == 1:
        chave = next(iter(grupos))
        return chave, grupos[chave]
    elif ("Brasil", "0") in grupos:
        return ("Brasil", "0"), grupos[("Brasil", "0")]

    niveis: dict[str, list[str]] = {}
    for nivel, cod in grupos:
        niveis.setdefault(nivel or "(sem nível)", []).append(cod)
    resumo = "; ".join(
        f"{nivel}: {len(cods)} território(s), ex. {', '.join(sorted(cods)[:4])}" for nivel, cods in sorted(niveis.items())
    )
    motivo = f"O território {territorio!r} não existe nesta série." if territorio else "A série tem vários territórios."
    raise ValueError(
        f"{motivo} Informe territorio com o código do IBGE (0 = Brasil, 1 a 5 = regiões, 35 = SP ou a sigla da UF, "
        f"7 dígitos = município). Disponíveis — {resumo}."
    )


async def serie_ipea(
    codigo: str,
    inicio: date | None = None,
    fim: date | None = None,
    territorio: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Serie:
    """Baixa uma série do Ipeadata, já recortada ao intervalo pedido.

    A API ignora $filter, $top e $orderby nos valores: sempre devolve a série
    inteira, e o recorte é feito aqui. Datas futuras (o salário mínimo vigente
    vem preenchido até dezembro) são descartadas, a menos que `fim` as peça.
    """
    codigo = _validar_codigo(codigo)
    if meta is None:
        meta = await metadados_ipea(codigo)
    codigo = meta.get("SERCODIGO") or codigo
    r = await requisitar(_url(f"ValoresSerie(SERCODIGO='{codigo}')"), ttl=TTL_MEDIO, tempo_limite=180, espera_json=True)
    (nivel, cod_territorio), obs = escolher_territorio(interpretar_valores(_valores(r)), territorio)

    notas: list[str] = []
    hoje = date.today()
    limite = max(hoje, fim) if fim else hoje
    visiveis = [(d, v) for d, v in obs if d <= limite]
    if len(visiveis) < len(obs):
        notas.append("A fonte publica datas futuras nesta série; elas foram omitidas.")
    recorte = [(d, v) for d, v in visiveis if (inicio is None or d >= inicio) and (fim is None or d <= fim)]
    if nivel:
        notas.append(f"Território: {nivel}, código {cod_territorio}.")
    if r.desatualizado:
        notas.append("O Ipeadata não respondeu; estes dados são uma cópia anterior guardada em cache.")
    if meta.get("SERSTATUS") == "I":
        notas.append("Série inativa no Ipeadata: não é mais atualizada.")

    return Serie(
        nome=(meta.get("SERNOME") or codigo).strip(),
        fonte=ROTULO,
        codigo=f"IPEA {codigo}",
        unidade=unidade_de(meta),
        periodicidade=periodicidade_de(meta),
        observacoes=recorte,
        url=r.url,
        consultado_em=r.consultado_em,
        notas=notas,
    )


# --------------------------------------------------------------------- busca

_memo_lista: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}


async def listar_series(base: str = "macro") -> tuple[list[dict[str, Any]], Resposta]:
    """Metadados de todas as séries de uma base (macro, regional ou social).

    A API não aceita $select nem contains(), e substringof() diferencia acentos
    e maiúsculas; por isso a lista da base é baixada inteira (até 4 MB) e a
    busca por texto é feita localmente.
    """
    if base not in BASES:
        raise ValueError(f"Base desconhecida: {base!r}. Opções: {', '.join(BASES)} ou todas.")
    r = await requisitar(
        _url("Metadados", f"BASNOME eq '{BASES[base]}'"), ttl=_TTL_LISTA, tempo_limite=180, espera_json=True
    )
    guardado = _memo_lista.get(base)
    if guardado and guardado[0] == r.consultado_em:
        return guardado[1], r
    itens = _valores(r)
    _memo_lista[base] = (r.consultado_em, itens)
    return itens, r


async def temas_ipea() -> dict[int, str]:
    r = await requisitar(_url("Temas"), ttl=TTL_LONGO, espera_json=True)
    return {int(t["TEMCODIGO"]): (t.get("TEMNOME") or "").strip() for t in _valores(r)}


def filtrar_series(
    itens: list[dict[str, Any]],
    consulta: str = "",
    fonte: str | None = None,
    tema: str | None = None,
    temas: dict[int, str] | None = None,
    apenas_ativas: bool = True,
) -> list[dict[str, Any]]:
    """Séries que casam com todos os termos da consulta, das mais para as menos relevantes."""
    temas = temas or {}
    termos = normalizar(consulta).split()
    alvo_fonte = normalizar(fonte) if fonte else ""
    alvo_tema = normalizar(tema) if tema else ""
    pontuadas: list[tuple[int, int, str, dict[str, Any]]] = []
    for item in itens:
        if apenas_ativas and item.get("SERSTATUS") == "I":
            continue
        if alvo_fonte and alvo_fonte not in normalizar(f"{item.get('FNTSIGLA') or ''} {item.get('FNTNOME') or ''}"):
            continue
        nome_tema = temas.get(item.get("TEMCODIGO") or -1, "")
        if alvo_tema and alvo_tema != str(item.get("TEMCODIGO")) and alvo_tema not in normalizar(nome_tema):
            continue
        nome = normalizar(item.get("SERNOME") or "")
        codigo = normalizar(item.get("SERCODIGO") or "")
        resto = normalizar(f"{item.get('FNTSIGLA') or ''} {nome_tema} {item.get('UNINOME') or ''}")
        pontos = 0
        for termo in termos:
            if termo in nome.split():
                pontos += 4
            elif termo in nome:
                pontos += 3
            elif termo in codigo:
                pontos += 2
            elif termo in resto:
                pontos += 1
            else:
                pontos = -1
                break
        if pontos < 0:
            continue
        ativa = 0 if item.get("SERSTATUS") == "I" else 1
        pontuadas.append((ativa, pontos, item.get("SERATUALIZACAO") or "", item))
    pontuadas.sort(key=lambda t: t[:3], reverse=True)
    return [t[3] for t in pontuadas]


@fonte_de_dados("ipea", ROTULO)
async def _buscar(ind: Indicador, inicio: date | None, fim: date | None) -> Serie:
    p = ind.parametros
    if isinstance(p, dict):
        return await serie_ipea(str(p["codigo"]), inicio, fim, str(p["territorio"]) if "territorio" in p else None)
    return await serie_ipea(str(p), inicio, fim)
