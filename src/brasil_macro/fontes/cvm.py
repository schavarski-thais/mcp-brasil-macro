"""CVM — cadastro de fundos (Resolução CVM 175) e informe diário dos fundos.

O cadastro está em `registro_fundo_classe.zip`: `registro_fundo.csv` (o fundo, com gestor e
administrador), `registro_classe.csv` (a classe de cotas, com situação e patrimônio) e
`registro_subclasse.csv` (subclasses de cada classe). Os campos de pessoa física (diretor e CPF
do gestor) não são expostos.

O informe diário é um zip por mês (`inf_diario_fi_AAAAMM.zip`) com cota, PL, captação, resgate e
número de cotistas por dia. Antes da RCVM 175 a coluna do CNPJ é `CNPJ_FUNDO`; depois, é
`CNPJ_FUNDO_CLASSE`, com `ID_SUBCLASSE`. Ambos os formatos são aceitos.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from ..http import TTL_LONGO, TTL_MEDIO, ErroFonte, requisitar

ROTULO = "CVM/Cadastro de Fundos (RCVM 175)"
ROTULO_INFORME = "CVM/Informe Diário de Fundos"
URL = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
URL_INFORME = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{ano}{mes:02d}.zip"


def _csv(zf: zipfile.ZipFile, nome: str) -> list[dict[str, str]]:
    texto = zf.read(nome).decode("latin-1")  # os arquivos da CVM são ISO-8859-1
    return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


def _limpo(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor or None


def _numero(valor: str | None) -> float | None:
    texto = (valor or "").strip()
    if "," in texto:  # alguns arquivos usam vírgula decimal
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto) if texto else None
    except ValueError:
        return None


def somente_digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


async def cadastro() -> tuple[list[dict[str, Any]], str, datetime]:
    """Uma entrada por classe de cotas, com os dados do fundo e as subclasses.

    Devolve (registros, URL, momento da consulta).
    """
    r = await requisitar(URL, ttl=TTL_MEDIO, tempo_limite=180)
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.corpo))
        fundos = {f["ID_Registro_Fundo"]: f for f in _csv(zf, "registro_fundo.csv")}
        classes = _csv(zf, "registro_classe.csv")
        subclasses_brutas = _csv(zf, "registro_subclasse.csv")
    except (zipfile.BadZipFile, KeyError) as erro:
        raise ErroFonte(f"O cadastro da CVM não está no formato esperado ({erro}).") from None

    subclasses: dict[str, list[dict[str, Any]]] = {}
    for s in subclasses_brutas:
        subclasses.setdefault(s.get("ID_Registro_Classe", ""), []).append(
            {
                "id": _limpo(s.get("ID_Subclasse")),
                "nome": (s.get("Denominacao_Social") or "").strip(),
                "situacao": _limpo(s.get("Situacao")),
                "forma_condominio": _limpo(s.get("Forma_Condominio")),
                "exclusivo": _limpo(s.get("Exclusivo")),
                "publico_alvo": _limpo(s.get("Publico_Alvo")),
                "previdenciario": _limpo(s.get("Previdenciario")),
            }
        )

    registros: list[dict[str, Any]] = []
    for c in classes:
        f = fundos.get(c.get("ID_Registro_Fundo", ""), {})
        registros.append(
            {
                # fundo
                "cnpj_fundo": f.get("CNPJ_Fundo", ""),
                "nome_fundo": (f.get("Denominacao_Social") or "").strip(),
                "tipo_fundo": _limpo(f.get("Tipo_Fundo")),
                "data_cancelamento_fundo": _limpo(f.get("Data_Cancelamento")),
                "data_adaptacao_rcvm175": _limpo(f.get("Data_Adaptacao_RCVM175")),
                "gestor": _limpo(f.get("Gestor")),
                "tipo_pessoa_gestor": _limpo(f.get("Tipo_Pessoa_Gestor")),
                "cnpj_administrador": _limpo(f.get("CNPJ_Administrador")),
                "administrador": _limpo(f.get("Administrador")),
                "patrimonio_liquido_fundo": _numero(f.get("Patrimonio_Liquido")),
                # classe
                "cnpj_classe": c.get("CNPJ_Classe", ""),
                "nome": (c.get("Denominacao_Social") or "").strip(),
                "tipo": _limpo(c.get("Tipo_Classe")),
                "classificacao": _limpo(c.get("Classificacao")),
                "classificacao_anbima": _limpo(c.get("Classificacao_Anbima")),
                "situacao": _limpo(c.get("Situacao")),
                "situacao_desde": _limpo(c.get("Data_Inicio_Situacao")),
                "patrimonio_liquido": _numero(c.get("Patrimonio_Liquido")),
                "data_patrimonio": _limpo(c.get("Data_Patrimonio_Liquido")),
                "codigo_cvm": _limpo(c.get("Codigo_CVM")),
                "data_registro": _limpo(c.get("Data_Registro")),
                "data_constituicao": _limpo(c.get("Data_Constituicao")),
                "data_inicio": _limpo(c.get("Data_Inicio")),
                "indicador_desempenho": _limpo(c.get("Indicador_Desempenho")),
                "classe_cotas": _limpo(c.get("Classe_Cotas")),
                "tributacao_longo_prazo": _limpo(c.get("Tributacao_Longo_Prazo")),
                "entidade_investimento": _limpo(c.get("Entidade_Investimento")),
                "permite_exterior_100": _limpo(c.get("Permitido_Aplicacao_CemPorCento_Exterior")),
                "classe_esg": _limpo(c.get("Classe_ESG")),
                "forma_condominio": _limpo(c.get("Forma_Condominio")),
                "exclusivo": _limpo(c.get("Exclusivo")),
                "publico_alvo": _limpo(c.get("Publico_Alvo")),
                "cnpj_auditor": _limpo(c.get("CNPJ_Auditor")),
                "auditor": _limpo(c.get("Auditor")),
                "cnpj_custodiante": _limpo(c.get("CNPJ_Custodiante")),
                "custodiante": _limpo(c.get("Custodiante")),
                "cnpj_controlador": _limpo(c.get("CNPJ_Controlador")),
                "controlador": _limpo(c.get("Controlador")),
                "subclasses": subclasses.get(c.get("ID_Registro_Classe", ""), []),
            }
        )
    return registros, URL, r.consultado_em


def meses_entre(inicio: date, fim: date) -> list[tuple[int, int]]:
    meses = []
    ano, mes = inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        meses.append((ano, mes))
        mes += 1
        if mes > 12:
            ano, mes = ano + 1, 1
    return meses


async def informe_diario(
    meses: list[tuple[int, int]], aceita: Callable[[str], bool]
) -> tuple[list[dict[str, Any]], list[str], datetime]:
    """Linhas do informe diário dos meses pedidos, só das que `aceita` (recebe o CNPJ só com dígitos).

    Devolve (linhas, URLs lidas, momento da consulta). O zip de cada mês fica em memória (vem do cache
    ou da rede); o CSV dentro dele é lido em fluxo, e só as linhas do CNPJ pedido são guardadas.
    """
    linhas: list[dict[str, Any]] = []
    urls: list[str] = []
    consultado: datetime | None = None
    hoje = date.today()
    for ano, mes in meses:
        url = URL_INFORME.format(ano=ano, mes=mes)
        recente = (hoje.year * 12 + hoje.month) - (ano * 12 + mes) <= 2
        r = await requisitar(url, ttl=TTL_MEDIO if recente else TTL_LONGO, tempo_limite=240)
        consultado = r.consultado_em
        urls.append(url)
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.corpo))
            nome = zf.namelist()[0]
            with zf.open(nome) as arquivo:
                leitor = csv.DictReader(io.TextIOWrapper(arquivo, encoding="latin-1", newline=""), delimiter=";")
                for l in leitor:
                    cnpj = somente_digitos(l.get("CNPJ_FUNDO_CLASSE") or l.get("CNPJ_FUNDO"))
                    if not aceita(cnpj):
                        continue
                    linhas.append(
                        {
                            "cnpj": cnpj,
                            "subclasse": _limpo(l.get("ID_SUBCLASSE")),
                            "data": date.fromisoformat(l["DT_COMPTC"]),
                            "cota": _numero(l.get("VL_QUOTA")),
                            "pl": _numero(l.get("VL_PATRIM_LIQ")),
                            "captacao": _numero(l.get("CAPTC_DIA")) or 0.0,
                            "resgate": _numero(l.get("RESG_DIA")) or 0.0,
                            "cotistas": int(float(l["NR_COTST"])) if (l.get("NR_COTST") or "").strip() else None,
                        }
                    )
        except (zipfile.BadZipFile, KeyError, ValueError) as erro:
            raise ErroFonte(f"O informe diário da CVM em {url} não está no formato esperado ({erro}).") from None
    return linhas, urls, consultado
