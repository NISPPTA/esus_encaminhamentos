"""Repositório para apuração de encaminhamentos e atendimentos por especialidade/CBO."""

from dataclasses import dataclass

import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool, PoolTimeout

from app.repositories.especialidades_repo import OrigemEspecialidade, RepositoryQueryError


@dataclass(frozen=True, slots=True)
class MetricasEspecialidade:
    """Métricas consolidadas das camadas transacional e analítica do e-SUS PEC."""

    encaminhamentos_transacional: int
    guias_esus_reg: int
    encaminhamentos_analitico_destino: int
    encaminhamentos_emitidos_pelo_cbo: int
    atendimentos_transacional_cbo: int
    atendimentos_analitico_cbo: int
    cbo_associado: str | None


class EncaminhamentosRepository:
    """Executa consultas parametrizadas de encaminhamentos e atendimentos."""

    _SQL_GERAL = """
        SELECT
            (SELECT COUNT(DISTINCT co_seq_encaminhamento)
             FROM public.tb_encaminhamento) AS enc_transacional,
            (SELECT COUNT(DISTINCT co_seq_encaminhamento)
             FROM public.tb_guia_encaminhamento) AS guias_esus_reg,
            (SELECT COUNT(DISTINCT co_seq_fat_atd_ind_encaminham)
             FROM public.tb_fat_atd_ind_encaminhamentos) AS enc_analitico_destino,
            0::bigint AS enc_emitidos_cbo,
            (SELECT COUNT(DISTINCT co_atend)
             FROM public.tb_atend_prof
             WHERE COALESCE(st_cancelado, 0) = 0) AS atend_trans_cbo,
            (SELECT COUNT(DISTINCT nu_uuid_ficha)
             FROM public.tb_fat_atendimento_individual) AS atend_analitico_cbo,
            NULL::varchar AS cbo_associado;
    """

    _SQL_POR_SISREG = """
        WITH alvo AS (
            SELECT
                es.co_especialidade_sisreg,
                es.ds_especialidade_sisreg,
                es.co_cbo,
                c.co_cbo_2002
            FROM public.tb_especialidade_sisreg es
            LEFT JOIN public.tb_cbo c ON c.co_cbo = es.co_cbo
            WHERE es.co_especialidade_sisreg = %(codigo)s
        )
        SELECT
            (SELECT COUNT(DISTINCT e.co_seq_encaminhamento)
             FROM public.tb_encaminhamento e
             JOIN alvo a ON a.co_especialidade_sisreg = e.co_especialidade_sisreg
            ) AS enc_transacional,

            (SELECT COUNT(DISTINCT g.co_seq_encaminhamento)
             FROM public.tb_guia_encaminhamento g
             JOIN alvo a ON a.co_cbo IS NOT NULL AND g.co_cbo = a.co_cbo
            ) AS guias_esus_reg,

            (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
             FROM public.tb_fat_atd_ind_encaminhamentos fe
             JOIN public.tb_dim_especialidade de
               ON de.co_seq_dim_especialidade = fe.co_dim_especialidade
             JOIN alvo a
               ON UPPER(TRIM(de.ds_especialidade)) = UPPER(TRIM(a.ds_especialidade_sisreg))
            ) AS enc_analitico_destino,

            (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
             FROM public.tb_fat_atd_ind_encaminhamentos fe
             JOIN public.tb_dim_cbo dc ON dc.co_seq_dim_cbo = fe.co_dim_cbo_1
             JOIN alvo a ON a.co_cbo_2002 IS NOT NULL AND dc.nu_cbo = a.co_cbo_2002
            ) AS enc_emitidos_cbo,

            (SELECT COUNT(DISTINCT ap.co_atend)
             FROM public.tb_atend_prof ap
             JOIN public.tb_lotacao l ON l.co_prof = ap.co_lotacao
             JOIN alvo a ON a.co_cbo IS NOT NULL AND l.co_cbo = a.co_cbo
             WHERE COALESCE(ap.st_cancelado, 0) = 0
               AND l.dt_desativacao_lotacao IS NULL
            ) AS atend_trans_cbo,

            (SELECT COUNT(DISTINCT fai.nu_uuid_ficha)
             FROM public.tb_fat_atendimento_individual fai
             JOIN public.tb_dim_cbo dc ON dc.co_seq_dim_cbo = fai.co_dim_cbo_1
             JOIN alvo a ON a.co_cbo_2002 IS NOT NULL AND dc.nu_cbo = a.co_cbo_2002
            ) AS atend_analitico_cbo,

            (SELECT co_cbo_2002 FROM alvo LIMIT 1) AS cbo_associado;
    """

    _SQL_POR_DIM = """
        SELECT
            (SELECT COUNT(DISTINCT e.co_seq_encaminhamento)
             FROM public.tb_encaminhamento e
             JOIN public.tb_especialidade_sisreg es
               ON es.co_especialidade_sisreg = e.co_especialidade_sisreg
             JOIN public.tb_dim_especialidade de
               ON UPPER(TRIM(de.ds_especialidade)) = UPPER(TRIM(es.ds_especialidade_sisreg))
             WHERE de.co_seq_dim_especialidade = %(codigo)s
            ) AS enc_transacional,
            0::bigint AS guias_esus_reg,
            (SELECT COUNT(DISTINCT fe.co_seq_fat_atd_ind_encaminham)
             FROM public.tb_fat_atd_ind_encaminhamentos fe
             WHERE fe.co_dim_especialidade = %(codigo)s
            ) AS enc_analitico_destino,
            0::bigint AS enc_emitidos_cbo,
            0::bigint AS atend_trans_cbo,
            0::bigint AS atend_analitico_cbo,
            NULL::varchar AS cbo_associado;
    """

    _SQL_POR_ESUS_REG = """
        SELECT
            0::bigint AS enc_transacional,
            (SELECT COUNT(DISTINCT g.co_seq_encaminhamento)
             FROM public.tb_guia_encaminhamento g
             WHERE g.co_especialidade_esus_reg = %(codigo)s
            ) AS guias_esus_reg,
            0::bigint AS enc_analitico_destino,
            0::bigint AS enc_emitidos_cbo,
            0::bigint AS atend_trans_cbo,
            0::bigint AS atend_analitico_cbo,
            NULL::varchar AS cbo_associado;
    """

    def __init__(self, pool: ConnectionPool[psycopg.Connection[DictRow]]) -> None:
        self._pool = pool

    def obter_metricas_gerais(self) -> MetricasEspecialidade:
        """Retorna totais consolidados quando nenhuma especialidade é filtrada."""
        return self._executar_metrica(self._SQL_GERAL, {})

    def obter_metricas_por_filtro(
        self,
        origem: OrigemEspecialidade,
        codigo_interno: int,
    ) -> MetricasEspecialidade:
        """Retorna métricas filtradas pela origem e código interno da especialidade."""
        mapa_sql: dict[OrigemEspecialidade, str] = {
            "sisreg": self._SQL_POR_SISREG,
            "dim": self._SQL_POR_DIM,
            "esus_reg": self._SQL_POR_ESUS_REG,
        }
        return self._executar_metrica(mapa_sql[origem], {"codigo": codigo_interno})

    def _executar_metrica(
        self,
        query: str,
        params: dict[str, int],
    ) -> MetricasEspecialidade:
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        except (psycopg.DatabaseError, PoolTimeout) as exc:
            raise RepositoryQueryError(
                "Erro ao apurar métricas de encaminhamentos e atendimentos."
            ) from exc

        if row is None:
            return MetricasEspecialidade(0, 0, 0, 0, 0, 0, None)

        cbo_raw = row["cbo_associado"]
        return MetricasEspecialidade(
            encaminhamentos_transacional=int(row["enc_transacional"] or 0),
            guias_esus_reg=int(row["guias_esus_reg"] or 0),
            encaminhamentos_analitico_destino=int(row["enc_analitico_destino"] or 0),
            encaminhamentos_emitidos_pelo_cbo=int(row["enc_emitidos_cbo"] or 0),
            atendimentos_transacional_cbo=int(row["atend_trans_cbo"] or 0),
            atendimentos_analitico_cbo=int(row["atend_analitico_cbo"] or 0),
            cbo_associado=str(cbo_raw) if cbo_raw is not None else None,
        )
