"""Configurações da aplicação carregadas via variáveis de ambiente."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, PostgresDsn, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Erro levantado quando variáveis de ambiente obrigatórias são inválidas."""


class Settings(BaseSettings):
    """Esquema validado de configuração para Python 3.13 e PostgreSQL 18."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: PostgresDsn = Field(alias="DATABASE_URL")
    db_pool_min_size: int = Field(default=2, ge=1, le=50, alias="DB_POOL_MIN_SIZE")
    db_pool_max_size: int = Field(default=10, ge=1, le=100, alias="DB_POOL_MAX_SIZE")
    db_pool_timeout: float = Field(default=10.0, gt=0.0, alias="DB_POOL_TIMEOUT")
    app_env: Literal["development", "staging", "production"] = Field(
        default="development",
        alias="APP_ENV",
    )

    @model_validator(mode="after")
    def validar_limites_pool(self) -> Self:
        """Garante consistência entre tamanho mínimo e máximo do pool."""
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("DB_POOL_MIN_SIZE não pode ser maior que DB_POOL_MAX_SIZE.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna instância única e validada das configurações."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError(
            "Falha ao validar variáveis de ambiente. Verifique o arquivo .env."
        ) from exc
