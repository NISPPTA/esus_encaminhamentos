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
from app.repositories.encaminhamentos_repo import EncaminhamentosRepository
from app.repositories.especialidades_repo import (
    EspecialidadesRepository,
    RepositoryQueryError,
)
from app.services.relatorio_service import (
    FiltroEspecialidadeInvalidoError,
    RelatorioService,
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
    version="0.1.0",
    lifespan=lifespan,
)


def get_relatorio_service(
    pool: Annotated[ConnectionPool[Connection[DictRow]], Depends(get_db_pool)],
) -> RelatorioService:
    """Injeta repositórios e serviço por requisição."""
    return RelatorioService(
        especialidades_repo=EspecialidadesRepository(pool),
        encaminhamentos_repo=EncaminhamentosRepository(pool),
    )


@app.get("/", response_class=HTMLResponse)
def exibir_encaminhamentos(
    request: Request,
    service: Annotated[RelatorioService, Depends(get_relatorio_service)],
    especialidade: Annotated[str | None, Query(max_length=64)] = None,
) -> HTMLResponse:
    """Renderiza o painel com dropdown dinâmico de todas as especialidades do banco."""
    try:
        painel = service.carregar_painel(especialidade)
    except FiltroEspecialidadeInvalidoError as exc:
        mensagem_erro = str(exc)
        try:
            painel_fallback = service.carregar_painel(None)
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
