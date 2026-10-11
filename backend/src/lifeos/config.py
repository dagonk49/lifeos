"""Application settings, read from `LIFEOS_*` environment variables and Docker secrets."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

_SECRETS_DIR = Path("/run/secrets")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LIFEOS_",
        # Docker secrets: a file named e.g. `lifeos_gemini_api_key` overrides the field.
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Origin of the web client as seen by the browser; also the WebAuthn expected origin.
    public_origin: str = "http://localhost:3000"
    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "LifeOS"
    cookie_secure: bool = True
    expose_openapi: bool = True

    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "lifeos"
    database_user: str = "lifeos"
    database_password: SecretStr = SecretStr("")
    database_pool_size: int = 5

    redis_url: str = "redis://localhost:6379/0"

    session_ttl_days: int = 30
    session_idle_days: int = 7
    recent_auth_minutes: int = 10
    enrollment_token_minutes: int = 15
    webauthn_challenge_minutes: int = 5

    # Default time zone for scheduled jobs.
    timezone: str = "Europe/Paris"
    audit_retention_days: int = 365

    llm_provider: Literal["disabled", "fake", "gemini"] = "disabled"
    llm_timeout_seconds: float = 60.0
    gemini_backend: Literal["developer", "vertex"] = "developer"
    gemini_api_key: SecretStr | None = None
    # No default on purpose: pin a stable, versioned model name (see ADR 0003).
    gemini_model: str | None = None
    gemini_vertex_project: str | None = None
    gemini_vertex_location: str | None = None

    @model_validator(mode="after")
    def _check_llm(self) -> Self:
        if self.llm_provider != "gemini":
            return self
        if not self.gemini_model:
            raise ValueError("LIFEOS_GEMINI_MODEL is required when LIFEOS_LLM_PROVIDER=gemini")
        if self.gemini_backend == "developer" and not (
            self.gemini_api_key and self.gemini_api_key.get_secret_value()
        ):
            raise ValueError("LIFEOS_GEMINI_API_KEY is required for the Gemini Developer API")
        if self.gemini_backend == "vertex" and not (
            self.gemini_vertex_project and self.gemini_vertex_location
        ):
            raise ValueError(
                "LIFEOS_GEMINI_VERTEX_PROJECT and LIFEOS_GEMINI_VERTEX_LOCATION are required "
                "for Vertex AI"
            )
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.database_user,
            password=self.database_password.get_secret_value() or None,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
