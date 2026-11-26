"""Funções de apuração de encaminhamentos e atendimentos sem omissão de linhas."""

from typing import TypedDict

import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool, PoolTimeout

from app.repositories.especialidades_repo import OrigemEspecialidade, RepositoryQueryError


class MetricasEspecialidade(TypedDict):
    """Indicadores apurados nas camadas transacional e analítica do e-SUS PEC."""

    encaminhamentos_transacional: int
    guias_esus_reg: int
    encaminhamentos_analitico_destino: int
    encaminhamentos_emitidos_pelo_cbo: int
    atendimentos_transacional_cbo: int
    atendimentos_analitico_cbo: int
    cbo_associado: str | None
    nome_cbo_associado: str | None
    nome_especialidade_alvo: str


_SQL_GERAL = """
    SELECT
        (SELECT COUNT(DISTINCT co_seq_encaminhamento)
         FROM public.tb_encaminhamento) AS enc_transacional,
        (SELECT COUNT(DISTINCT co_seq_encaminhamento)
         FROM public.tb_guia_encaminhamento) AS guias_esus_reg,
        (SELECT COUNT(DISTINCT co_seq_fat_atd_ind_encaminham)
         FROM public.tb_fat_atd_ind_encaminhamentos) AS enc_analitico_destino,
        (SELECT COUNT(DISTINCT co_seq_fat_atd_ind_encaminham)
         FROM public.tb_fat_atd_ind_encaminhamentos
         WHERE co_dim_cbo_1 IS NOT NULL OR co_dim_cbo_guia_encaminhamento IS NOT NULL
        ) AS enc_emitidos_cbo,
        (SELECT COUNT(DISTINCT co_atend)
         FROM public.tb_atend_prof) AS atend_trans_cbo,
        (SELECT COUNT(DISTINCT nu_uuid_ficha)
         FROM public.tb_fat_atendimento_individual) AS atend_analitico_cbo,
        NULL::varchar AS cbo_associado,
        NULL::varchar AS nome_cbo_associado,
        'Todas as especialidades cadastradas'::varchar AS nome_especialidade_alvo;
"""

_SQL_POR_SISREG = """
    WITH alvo AS (
        SELECT
            es.co_especialidade_sisreg,
            TRIM(es.ds_especialidade_sisreg) AS ds_especialidade_sisreg,
            es.co_cbo,
            c.co_cbo_2002,
            TRIM(c.no_cbo) AS no_cbo
        FROM public.tb_especialidade_sisreg es
        LEFT JOIN public.tb_cbo c ON c.co_cbo = es.co_cbo
        WHERE es.co_especialidade_sisreg = %(codigo)s
    )
    SELECT
        (SELECT COUNT(DISTINCT e.co_seq_encaminhamento)
         FROM public.tb_encaminhamento e
         LEFT JOIN public.tb_especialidade_sisreg es
                ON es.co_especialidade_sisreg = e.co_especialidade_sisreg
         LEFT JOIN alvo a ON TRUE
         WHERE e.co_especialidade_sisreg = %(codigo)s
            OR UPPER(TRIM(COALESCE(es.ds_especialidade_sisreg, '')))
               = UPPER(TRIM(a.ds_especialidade_sisreg))
        ) AS enc_transacional,

        (SELECT COUNT(DISTINCT g.co_seq_encaminhamento)
         FROM public.tb_guia_encaminhamento g
         LEFT JOIN public.tb_especialidades_esus_reg er
                ON er.co_seq_espec_esus_reg = g.co_especialidade_esus_reg
         LEFT JOIN alvo a ON TRUE
         WHERE (a.co_cbo IS NOT NULL AND g.co_cbo = a.co_cbo)
            OR UPPER(TRIM(COALESCE(er.no_especialidade, '')))
               = UPPER(TRIM(a.ds_especialidade_sisreg))
        ) AS guias_esus_reg,

        (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
         FROM public.tb_fat_atd_ind_encaminhamentos fe
         LEFT JOIN public.tb_dim_especialidade de
                ON de.co_seq_dim_especialidade = fe.co_dim_especialidade
         LEFT JOIN alvo a ON TRUE
         WHERE UPPER(TRIM(COALESCE(de.ds_especialidade, '')))
               = UPPER(TRIM(a.ds_especialidade_sisreg))
        ) AS enc_analitico_destino,

        (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
         FROM public.tb_fat_atd_ind_encaminhamentos fe
         LEFT JOIN public.tb_dim_cbo dc1 ON dc1.co_seq_dim_cbo = fe.co_dim_cbo_1
         LEFT JOIN public.tb_dim_cbo dcg ON dcg.co_seq_dim_cbo = fe.co_dim_cbo_guia_encaminhamento
         LEFT JOIN alvo a ON TRUE
         WHERE a.co_cbo_2002 IS NOT NULL
           AND (dc1.nu_cbo = a.co_cbo_2002 OR dcg.nu_cbo = a.co_cbo_2002)
        ) AS enc_emitidos_cbo,

        (SELECT COUNT(DISTINCT ap.co_atend)
         FROM public.tb_atend_prof ap
         LEFT JOIN public.tb_lotacao l ON l.co_prof = ap.co_lotacao
         LEFT JOIN alvo a ON TRUE
         WHERE a.co_cbo IS NOT NULL AND l.co_cbo = a.co_cbo
        ) AS atend_trans_cbo,

        (SELECT COUNT(DISTINCT fai.nu_uuid_ficha)
         FROM public.tb_fat_atendimento_individual fai
         LEFT JOIN public.tb_dim_cbo dc1 ON dc1.co_seq_dim_cbo = fai.co_dim_cbo_1
         LEFT JOIN public.tb_dim_cbo dc2 ON dc2.co_seq_dim_cbo = fai.co_dim_cbo_2
         LEFT JOIN alvo a ON TRUE
         WHERE a.co_cbo_2002 IS NOT NULL
           AND (dc1.nu_cbo = a.co_cbo_2002 OR dc2.nu_cbo = a.co_cbo_2002)
        ) AS atend_analitico_cbo,

        (SELECT co_cbo_2002 FROM alvo LIMIT 1) AS cbo_associado,
        (SELECT no_cbo FROM alvo LIMIT 1) AS nome_cbo_associado,
        (SELECT ds_especialidade_sisreg FROM alvo LIMIT 1) AS nome_especialidade_alvo;
"""

_SQL_POR_DIM = """
    WITH alvo_dim AS (
        SELECT
            de.co_seq_dim_especialidade,
            TRIM(de.ds_especialidade) AS ds_especialidade
        FROM public.tb_dim_especialidade de
        WHERE de.co_seq_dim_especialidade = %(codigo)s
    )
    SELECT
        (SELECT COUNT(DISTINCT e.co_seq_encaminhamento)
         FROM public.tb_encaminhamento e
         LEFT JOIN public.tb_especialidade_sisreg es
                ON es.co_especialidade_sisreg = e.co_especialidade_sisreg
         LEFT JOIN alvo_dim ad ON TRUE
         WHERE UPPER(TRIM(COALESCE(es.ds_especialidade_sisreg, '')))
               = UPPER(TRIM(ad.ds_especialidade))
        ) AS enc_transacional,

        (SELECT COUNT(DISTINCT g.co_seq_encaminhamento)
         FROM public.tb_guia_encaminhamento g
         LEFT JOIN public.tb_especialidades_esus_reg er
                ON er.co_seq_espec_esus_reg = g.co_especialidade_esus_reg
         LEFT JOIN alvo_dim ad ON TRUE
         WHERE UPPER(TRIM(COALESCE(er.no_especialidade, ''))) = UPPER(TRIM(ad.ds_especialidade))
        ) AS guias_esus_reg,

        (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
         FROM public.tb_fat_atd_ind_encaminhamentos fe
         LEFT JOIN alvo_dim ad ON TRUE
         WHERE fe.co_dim_especialidade = %(codigo)s
        ) AS enc_analitico_destino,

        0::bigint AS enc_emitidos_cbo,
        0::bigint AS atend_trans_cbo,
        0::bigint AS atend_analitico_cbo,
        NULL::varchar AS cbo_associado,
        NULL::varchar AS nome_cbo_associado,
        (SELECT ds_especialidade FROM alvo_dim LIMIT 1) AS nome_especialidade_alvo;
"""

_SQL_POR_ESUS_REG = """
    WITH alvo_reg AS (
        SELECT
            er.co_seq_espec_esus_reg,
            TRIM(er.no_especialidade) AS no_especialidade
        FROM public.tb_especialidades_esus_reg er
        WHERE er.co_seq_espec_esus_reg = %(codigo)s
    )
    SELECT
        (SELECT COUNT(DISTINCT e.co_seq_encaminhamento)
         FROM public.tb_encaminhamento e
         LEFT JOIN public.tb_especialidade_sisreg es
                ON es.co_especialidade_sisreg = e.co_especialidade_sisreg
         LEFT JOIN alvo_reg ar ON TRUE
         WHERE UPPER(TRIM(COALESCE(es.ds_especialidade_sisreg, '')))
               = UPPER(TRIM(ar.no_especialidade))
        ) AS enc_transacional,

        (SELECT COUNT(DISTINCT g.co_seq_encaminhamento)
         FROM public.tb_guia_encaminhamento g
         LEFT JOIN alvo_reg ar ON TRUE
         WHERE g.co_especialidade_esus_reg = %(codigo)s
        ) AS guias_esus_reg,

        (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
         FROM public.tb_fat_atd_ind_encaminhamentos fe
         LEFT JOIN public.tb_dim_especialidade de
                ON de.co_seq_dim_especialidade = fe.co_dim_especialidade
         LEFT JOIN alvo_reg ar ON TRUE
         WHERE UPPER(TRIM(COALESCE(de.ds_especialidade, ''))) = UPPER(TRIM(ar.no_especialidade))
        ) AS enc_analitico_destino,

        0::bigint AS enc_emitidos_cbo,
        0::bigint AS atend_trans_cbo,
        0::bigint AS atend_analitico_cbo,
        NULL::varchar AS cbo_associado,
        NULL::varchar AS nome_cbo_associado,
        (SELECT no_especialidade FROM alvo_reg LIMIT 1) AS nome_especialidade_alvo;
"""

_MAPA_SQL_ESPECIALIDADE: dict[OrigemEspecialidade, str] = {
    "sisreg": _SQL_POR_SISREG,
    "dim": _SQL_POR_DIM,
    "esus_reg": _SQL_POR_ESUS_REG,
}


def _metricas_vazias(nome_alvo: str = "Especialidade não informada") -> MetricasEspecialidade:
    """Retorna estrutura zerada tipada para ausência de linha."""
    return {
        "encaminhamentos_transacional": 0,
        "guias_esus_reg": 0,
        "encaminhamentos_analitico_destino": 0,
        "encaminhamentos_emitidos_pelo_cbo": 0,
        "atendimentos_transacional_cbo": 0,
        "atendimentos_analitico_cbo": 0,
        "cbo_associado": None,
        "nome_cbo_associado": None,
        "nome_especialidade_alvo": nome_alvo,
    }


def _executar_metrica(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
    query: str,
    params: dict[str, int],
) -> MetricasEspecialidade:
    """Executa consulta parametrizada no PostgreSQL 18 com tratamento específico."""
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
    except (psycopg.DatabaseError, PoolTimeout) as exc:
        raise RepositoryQueryError(
            "Erro ao apurar métricas de encaminhamentos e atendimentos."
        ) from exc

    if row is None:
        return _metricas_vazias()

    cbo_raw = row["cbo_associado"]
    nome_cbo_raw = row["nome_cbo_associado"]
    alvo_raw = row["nome_especialidade_alvo"]
    return {
        "encaminhamentos_transacional": int(row["enc_transacional"] or 0),
        "guias_esus_reg": int(row["guias_esus_reg"] or 0),
        "encaminhamentos_analitico_destino": int(row["enc_analitico_destino"] or 0),
        "encaminhamentos_emitidos_pelo_cbo": int(row["enc_emitidos_cbo"] or 0),
        "atendimentos_transacional_cbo": int(row["atend_trans_cbo"] or 0),
        "atendimentos_analitico_cbo": int(row["atend_analitico_cbo"] or 0),
        "cbo_associado": str(cbo_raw) if cbo_raw is not None else None,
        "nome_cbo_associado": str(nome_cbo_raw) if nome_cbo_raw is not None else None,
        "nome_especialidade_alvo": str(alvo_raw or "Especialidade selecionada"),
    }


def obter_metricas_gerais(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
) -> MetricasEspecialidade:
    """Retorna totais consolidados sem filtro de especialidade."""
    return _executar_metrica(pool, _SQL_GERAL, {})


def obter_metricas_por_filtro(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
    origem: OrigemEspecialidade,
    codigo_interno: int,
) -> MetricasEspecialidade:
    """Retorna métricas filtradas pela origem e código interno da especialidade."""
    return _executar_metrica(pool, _MAPA_SQL_ESPECIALIDADE[origem], {"codigo": codigo_interno})
