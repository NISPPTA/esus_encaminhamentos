"""Funções de validação de filtros e montagem do painel."""

from typing import TypedDict, cast

import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from app.repositories.encaminhamentos_repo import (
    MetricasEspecialidade,
    obter_metricas_gerais,
    obter_metricas_por_filtro,
)
from app.repositories.especialidades_repo import (
    EspecialidadeOption,
    OrigemEspecialidade,
    listar_opcoes_dropdown,
)


class FiltroEspecialidadeInvalidoError(ValueError):
    """Exceção específica para parâmetros de filtro malformados ou inexistentes."""


class PainelRelatorioDTO(TypedDict):
    """Dados prontos para renderização no template Jinja2."""

    especialidades: list[EspecialidadeOption]
    selecionada: EspecialidadeOption | None
    metricas: MetricasEspecialidade


_ORIGENS_PERMITIDAS: frozenset[str] = frozenset({"sisreg", "dim", "esus_reg"})


def _parsear_filtro(valor: str) -> tuple[OrigemEspecialidade, int]:
    """Valida e separa o parâmetro '<origem>:<codigo>'."""
    partes = valor.split(":", maxsplit=1)
    if len(partes) != 2:
        raise FiltroEspecialidadeInvalidoError(
            "Formato de filtro inválido. Esperado '<origem>:<codigo>'."
        )

    origem_raw, codigo_raw = partes
    if origem_raw not in _ORIGENS_PERMITIDAS:
        raise FiltroEspecialidadeInvalidoError(
            f"Origem de especialidade desconhecida: '{origem_raw}'."
        )

    try:
        codigo = int(codigo_raw)
    except ValueError as exc:
        raise FiltroEspecialidadeInvalidoError(
            "O código da especialidade deve ser um número inteiro positivo."
        ) from exc

    if codigo <= 0:
        raise FiltroEspecialidadeInvalidoError("O código da especialidade deve ser maior que zero.")

    origem = cast("OrigemEspecialidade", origem_raw)
    return origem, codigo


def carregar_painel(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
    filtro_bruto: str | None,
) -> PainelRelatorioDTO:
    """Carrega todas as especialidades no dropdown e apura os indicadores."""
    opcoes = listar_opcoes_dropdown(pool)
    filtro_limpo = (filtro_bruto or "").strip()

    if not filtro_limpo:
        metricas = obter_metricas_gerais(pool)
        return {
            "especialidades": opcoes,
            "selecionada": None,
            "metricas": metricas,
        }

    origem, codigo = _parsear_filtro(filtro_limpo)
    indice_opcoes = {item["chave_filtro"]: item for item in opcoes}
    chave_normalizada = f"{origem}:{codigo}"

    selecionada = indice_opcoes.get(chave_normalizada)
    if selecionada is None:
        raise FiltroEspecialidadeInvalidoError(
            f"A especialidade '{chave_normalizada}' não existe no cadastro atual."
        )

    metricas = obter_metricas_por_filtro(pool, origem, codigo)
    return {
        "especialidades": opcoes,
        "selecionada": selecionada,
        "metricas": metricas,
    }
