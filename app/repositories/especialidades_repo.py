"""Funções de leitura do catálogo de especialidades do e-SUS PEC."""

from typing import Literal, TypedDict, cast

import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool, PoolTimeout

type OrigemEspecialidade = Literal["sisreg", "dim", "esus_reg"]


class RepositoryQueryError(RuntimeError):
    """Erro específico de falha na execução de consulta SQL no PostgreSQL."""


class EspecialidadeOption(TypedDict):
    """Estrutura de cada item exibido no dropdown de especialidades."""

    chave_filtro: str
    origem: OrigemEspecialidade
    codigo_interno: int
    nome_exibicao: str
    nu_cbo: str | None
    no_cbo: str | None


_ORIGENS_VALIDAS: frozenset[str] = frozenset({"sisreg", "dim", "esus_reg"})

_SQL_LISTAR_ESPECIALIDADES = """
    SELECT
        'sisreg' AS origem,
        es.co_especialidade_sisreg AS codigo_interno,
        TRIM(es.ds_especialidade_sisreg) AS nome_exibicao,
        c.co_cbo_2002 AS nu_cbo,
        TRIM(c.no_cbo) AS no_cbo
    FROM public.tb_especialidade_sisreg es
    LEFT JOIN public.tb_cbo c
           ON c.co_cbo = es.co_cbo
    WHERE es.ds_especialidade_sisreg IS NOT NULL

    UNION ALL

    SELECT
        'dim' AS origem,
        de.co_seq_dim_especialidade AS codigo_interno,
        TRIM(de.ds_especialidade) AS nome_exibicao,
        NULL::varchar AS nu_cbo,
        NULL::varchar AS no_cbo
    FROM public.tb_dim_especialidade de
    WHERE de.ds_especialidade IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.tb_especialidade_sisreg es2
          WHERE UPPER(TRIM(es2.ds_especialidade_sisreg)) = UPPER(TRIM(de.ds_especialidade))
      )

    UNION ALL

    SELECT
        'esus_reg' AS origem,
        er.co_seq_espec_esus_reg AS codigo_interno,
        TRIM(er.no_especialidade) AS nome_exibicao,
        NULL::varchar AS nu_cbo,
        NULL::varchar AS no_cbo
    FROM public.tb_especialidades_esus_reg er
    WHERE er.no_especialidade IS NOT NULL

    ORDER BY nome_exibicao ASC, origem ASC;
"""


def listar_opcoes_dropdown(
    pool: ConnectionPool[psycopg.Connection[DictRow]],
) -> list[EspecialidadeOption]:
    """Retorna todas as especialidades cadastradas sem omitir registros."""
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(_SQL_LISTAR_ESPECIALIDADES)
            rows = cur.fetchall()
    except (psycopg.DatabaseError, PoolTimeout) as exc:
        raise RepositoryQueryError(
            "Erro ao consultar o catálogo de especialidades no PostgreSQL."
        ) from exc

    opcoes: list[EspecialidadeOption] = []
    for row in rows:
        origem_str = str(row["origem"])
        if origem_str not in _ORIGENS_VALIDAS:
            continue
        origem = cast("OrigemEspecialidade", origem_str)
        codigo = int(row["codigo_interno"])
        nu_cbo_raw = row["nu_cbo"]
        no_cbo_raw = row["no_cbo"]
        opcoes.append(
            {
                "chave_filtro": f"{origem}:{codigo}",
                "origem": origem,
                "codigo_interno": codigo,
                "nome_exibicao": str(row["nome_exibicao"]),
                "nu_cbo": str(nu_cbo_raw) if nu_cbo_raw is not None else None,
                "no_cbo": str(no_cbo_raw) if no_cbo_raw is not None else None,
            }
        )
    return opcoes
