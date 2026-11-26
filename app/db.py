"""Gerenciamento do pool de conexões com PostgreSQL 18 via psycopg 3."""

import logging
from typing import cast

import psycopg
from fastapi import Request
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from app.config import Settings

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """Erro específico para indisponibilidade ou falha operacional no PostgreSQL."""


def create_pool(settings: Settings) -> ConnectionPool[psycopg.Connection[DictRow]]:
    """Cria o pool de conexões configurado com dict_row sem abri-lo imediatamente."""
    return ConnectionPool(
        conninfo=str(settings.database_url),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=settings.db_pool_timeout,
        kwargs={"row_factory": dict_row, "autocommit": True},
        open=False,
    )


def open_pool(pool: ConnectionPool[psycopg.Connection[DictRow]]) -> None:
    """Abre o pool e valida conectividade inicial com o PostgreSQL 18."""
    try:
        pool.open(wait=True)
    except (psycopg.OperationalError, PoolTimeout) as exc:
        logger.exception("Não foi possível conectar ao PostgreSQL 18.")
        raise DatabaseUnavailableError(
            "Banco de dados indisponível ao inicializar o pool de conexões."
        ) from exc


def close_pool(pool: ConnectionPool[psycopg.Connection[DictRow]]) -> None:
    """Fecha o pool de conexões com segurança no encerramento da aplicação."""
    try:
        pool.close()
    except psycopg.OperationalError as exc:
        logger.warning("Aviso operacional ao encerrar pool do PostgreSQL: %s", exc)


def get_db_pool(request: Request) -> ConnectionPool[psycopg.Connection[DictRow]]:
    """Dependência FastAPI para obter o pool anexado ao estado da aplicação."""
    return cast(
        "ConnectionPool[psycopg.Connection[DictRow]]",
        request.app.state.db_pool,
    )
