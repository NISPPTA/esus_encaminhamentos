"""Ponto de entrada ASGI (FastAPI) com ciclo de vida moderno e tratamento específico."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.db import close_pool, create_pool, get_db_pool, open_pool
from app.repositories.especialidades_repo import RepositoryQueryError
from app.services.relatorio_service import (
    FiltroEspecialidadeInvalidoError,
    carregar_painel,
)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Gerencia abertura e fechamento do pool PostgreSQL 18 sem eventos depreciados."""
    settings = get_settings()
    pool = create_pool(settings)
    open_pool(pool)
    application.state.db_pool = pool
    try:
        yield
    finally:
        close_pool(pool)


app = FastAPI(
    title="e-SUS PEC — Painel de Encaminhamentos e Atendimentos",
    version="0.3.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
def exibir_encaminhamentos(
    request: Request,
    pool: Annotated[ConnectionPool[Connection[DictRow]], Depends(get_db_pool)],
    especialidade: Annotated[str | None, Query(max_length=64)] = None,
) -> HTMLResponse:
    """Renderiza o painel com dropdown dinâmico de todas as especialidades do banco."""
    try:
        painel = carregar_painel(pool, especialidade)
    except FiltroEspecialidadeInvalidoError as exc:
        mensagem_erro = str(exc)
        try:
            painel_fallback = carregar_painel(pool, None)
        except RepositoryQueryError as db_exc:
            return templates.TemplateResponse(
                request=request,
                name="encaminhamentos.html",
                context={"painel": None, "erro_usuario": str(db_exc)},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return templates.TemplateResponse(
            request=request,
            name="encaminhamentos.html",
            context={"painel": painel_fallback, "erro_usuario": mensagem_erro},
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except RepositoryQueryError as exc:
        return templates.TemplateResponse(
            request=request,
            name="encaminhamentos.html",
            context={"painel": None, "erro_usuario": str(exc)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    else:
        return templates.TemplateResponse(
            request=request,
            name="encaminhamentos.html",
            context={"painel": painel, "erro_usuario": None},
        )
