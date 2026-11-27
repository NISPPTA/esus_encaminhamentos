"""Ponto de entrada ASGI (FastAPI) com controles OWASP e ciclo de vida moderno."""

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response, status
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

_FILTRO_REGEX = r"^$|^(sisreg|dim|esus_reg):[1-9]\d{0,9}$"
_SETTINGS = get_settings()
_IS_PROD = _SETTINGS.app_env == "production"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Gerencia abertura e fechamento do pool PostgreSQL 18 sem eventos depreciados."""
    pool = create_pool(_SETTINGS)
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
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
)


@app.middleware("http")
async def aplicar_headers_seguranca(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Aplica cabeçalhos de segurança OWASP (CSP com nonce, anti-clickjacking e no-store)."""
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce

    response = await call_next(request)

    csp = (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; "
        "img-src 'self' data:; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/", response_class=HTMLResponse)
def exibir_encaminhamentos(
    request: Request,
    pool: Annotated[ConnectionPool[Connection[DictRow]], Depends(get_db_pool)],
    especialidade: Annotated[
        str | None,
        Query(max_length=32, pattern=_FILTRO_REGEX),
    ] = None,
) -> HTMLResponse:
    """Renderiza o painel com dropdown dinâmico e nonce CSP injetado no template."""
    csp_nonce: str = request.state.csp_nonce

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
                context={
                    "painel": None,
                    "erro_usuario": str(db_exc),
                    "csp_nonce": csp_nonce,
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return templates.TemplateResponse(
            request=request,
            name="encaminhamentos.html",
            context={
                "painel": painel_fallback,
                "erro_usuario": mensagem_erro,
                "csp_nonce": csp_nonce,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    except RepositoryQueryError as exc:
        return templates.TemplateResponse(
            request=request,
            name="encaminhamentos.html",
            context={
                "painel": None,
                "erro_usuario": str(exc),
                "csp_nonce": csp_nonce,
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    else:
        return templates.TemplateResponse(
            request=request,
            name="encaminhamentos.html",
            context={
                "painel": painel,
                "erro_usuario": None,
                "csp_nonce": csp_nonce,
            },
        )
