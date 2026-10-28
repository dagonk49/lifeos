"""Application settings, read from `LIFEOS_*` environment variables and Docker secrets."""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
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

    # Supervision (docs/exploitation.md §5). The API refreshes its queue, backlog and
    # dependency gauges this often (0: never, e.g. in tests); the worker serves its own
    # metrics on this port (0: not at all), one process per container (`--processes 1`).
    metrics_refresh_seconds: int = Field(default=60, ge=0)
    worker_metrics_port: int = Field(default=9101, ge=0, le=65535)
    # Tag of the deployed images (infra/scripts/update.sh writes it to infra/.env), exported
    # as the `revision` label of lifeos_build_info.
    image_tag: str = Field(default="local", max_length=64)

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
    # "free": project WITHOUT billing account (cannot be charged); local daily request caps
    # keep usage under Google's free quotas. "paid": prepaid billing; local cost budgets.
    llm_billing: Literal["free", "paid"] = "free"
    # Local caps per tier and per day (Google's daily quotas reset at midnight Pacific).
    llm_daily_requests_fast: int = 400
    llm_daily_requests_deep: int = 15
    # Paid mode: estimated cost from these prices (USD per 1M tokens) and hard local budgets.
    llm_price_input_fast: float = 0.30
    llm_price_output_fast: float = 2.50
    llm_price_input_deep: float = 0.75
    llm_price_output_deep: float = 3.75
    llm_daily_budget_usd: float = 0.50
    llm_monthly_budget_usd: float = 5.00
    # Quota reservation (ADR 0010): every call reserves its share of the caps in a short
    # locked transaction before reaching the provider, then settles it with real usage.
    # Requests kept below each daily cap: covers the thinking-level retry (two Google
    # requests for one call) and calls whose outcome was lost.
    llm_quota_margin_requests: int = Field(default=2, ge=0)
    # Share of the USD budgets kept in reserve (paid billing: Google's billing data lags
    # about ten minutes and real usage may exceed the reserved estimate).
    llm_budget_margin_ratio: float = Field(default=0.05, ge=0, lt=1)
    # Output tokens reserved when a request sets no max_output_tokens (thinking tokens are
    # billed as output). Real usage replaces the reservation after the call.
    llm_reserve_output_tokens_fast: int = Field(default=2048, ge=1)
    llm_reserve_output_tokens_deep: int = Field(default=8192, ge=1)
    # A reservation older than this (process killed during the call) is released: its
    # tokens and cost no longer count, the request still does. Never shorter than two
    # provider timeouts plus a minute (see `llm_reservation_ttl`).
    llm_reservation_ttl_seconds: int = Field(default=600, ge=1)

    gemini_backend: Literal["developer", "vertex"] = "developer"
    gemini_api_key: SecretStr | None = None
    # No default on purpose: pin stable, versioned model names (see ADR 0003).
    # "fast" tier: chat, extraction, agents. "deep" tier: weekly/monthly reviews.
    gemini_model: str | None = None
    gemini_model_deep: str | None = None
    gemini_thinking_fast: Literal["minimal", "low", "medium", "high"] = "minimal"
    gemini_thinking_deep: Literal["minimal", "low", "medium", "high"] = "low"
    # Paid mode: background jobs use the Flex service tier (cheaper, slower).
    gemini_flex_for_background: bool = True
    gemini_vertex_project: str | None = None
    gemini_vertex_location: str | None = None

    # Local embeddings (ADR 0006). "remote": the internal `ml` service (one model in RAM
    # for the API and the worker); "local": in-process; "fake": tests; "disabled":
    # full-text search only.
    embeddings_provider: Literal["disabled", "fake", "local", "remote"] = "disabled"
    embeddings_model: str = "google/embeddinggemma-300m"
    embeddings_cache_dir: str = "/models"
    # ONNX Runtime intra-op threads: physical cores the ml service may use.
    embeddings_threads: int = 4
    ml_url: str = "http://ml:8100"
    # Only for the one-time download of a gated model (`lifeos-ml download`).
    hf_token: SecretStr | None = None

    # Documents (F10, ADR 0008): encrypted originals on disk, kept for a limited time.
    documents_dir: str = "/data/documents"
    documents_max_bytes: int = 20 * 1024 * 1024
    documents_original_retention_days: int = 90
    documents_max_pages: int = 30
    # Bank statement imports (F04, task 25): CSV only, read in memory, never stored; the
    # preview (parsed rows) is erased at validation, cancellation or after this delay.
    finance_import_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    finance_import_max_rows: int = Field(default=5000, ge=10, le=50_000)
    finance_import_preview_hours: int = Field(default=24, ge=1, le=7 * 24)
    # AES-256-GCM key (base64, 32 bytes), Docker secret `lifeos_documents_key`.
    documents_key: SecretStr | None = None
    # Local text extraction and OCR: "remote" = the internal ml service.
    extraction_provider: Literal["disabled", "fake", "local", "remote"] = "disabled"
    # ml service: memory of one extraction (a child process), in MiB.
    extraction_memory_mb: int = 1280
    # Local speech-to-text (ADR 0009): "remote" = the internal stt service.
    stt_provider: Literal["disabled", "fake", "remote"] = "disabled"
    stt_url: str = "http://stt:8200"
    voice_max_seconds: int = 15 * 60
    # stt service: Whisper model (faster-whisper name or local path), its cache, and where
    # it runs. "auto" uses the GPU when one is visible, else (or if it fails) the CPU.
    stt_model: str = "large-v3-turbo"
    stt_cache_dir: str = "/models"
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"
    stt_threads: int = 4
    stt_beam_size: int = 5
    # The model (about 1.6 GB of RAM) is unloaded after this long without a dictation.
    stt_idle_unload_s: int = 600

    # Memory quality (F07): cosine distances between two facts' vectors. Below the first,
    # same meaning (duplicate); below the second, related (update or contradiction?).
    # Depend on the embedding model: check them after changing it.
    memory_duplicate_distance: float = 0.08
    memory_related_distance: float = 0.25
    memory_preference_max_age_days: int = 180

    # Connectors (ADR 0012): calendar and mail accounts linked through OAuth 2.0. "fake":
    # tests and end-to-end runs (no network); "disabled": no account can be connected.
    # The key and the client are checked by `connectors.build_connectors` (API start), not
    # here: migrate, scheduler, ml and stt read the same .env without these secrets.
    connectors_provider: Literal["disabled", "fake", "google"] = "disabled"
    # AES-256-GCM keyring of the OAuth tokens, Docker secret `lifeos_connectors_key`:
    # base64 of 32 bytes, or `id:base64,...` during a rotation (the first one seals).
    # Losing it only forces reconnecting the accounts.
    connectors_key: SecretStr | None = None
    # Google OAuth client of type "Web application" (docs/guide-google.md); the secret is
    # the Docker secret `lifeos_google_client_secret`.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # A consent started in LifeOS must come back within this delay (single-use state).
    oauth_state_minutes: int = Field(default=10, ge=1, le=60)
    # Calendar sync (task 14): every mapped calendar is read again this often, over a
    # window from `calendar_past_days` before today to `calendar_future_days` after.
    calendar_sync_minutes: int = Field(default=15, ge=5, le=24 * 60)
    calendar_past_days: int = Field(default=30, ge=1, le=365)
    calendar_future_days: int = Field(default=365, ge=7, le=3 * 365)
    # Mail reading (task 17): every mapped label is read again this often (its changes
    # only, after a first pass over the last `mail_retention_days`). Local copies are
    # deleted `mail_retention_days` after their reception.
    mail_sync_minutes: int = Field(default=15, ge=5, le=24 * 60)
    mail_retention_days: int = Field(default=90, ge=1, le=365)
    connectors_timeout_seconds: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def _check_llm(self) -> Self:
        if self.llm_provider != "gemini":
            return self
        if self.gemini_backend == "vertex" and self.llm_billing == "free":
            # Vertex AI has no free tier: without cost budgets, spending would be unbounded.
            raise ValueError(
                "Vertex AI is always billed: set LIFEOS_LLM_BILLING=paid and cost budgets"
            )
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
    def oauth_redirect_uri(self) -> str:
        """Where the provider sends the browser back: a page of the web client, which
        hands the code to the API (the session cookie is SameSite=Strict)."""
        return self.public_origin.rstrip("/") + "/connexions/retour"

    @property
    def llm_reservation_ttl(self) -> timedelta:
        """Age after which a pending reservation is released as abandoned.

        A live call makes at most two provider requests (thinking-level retry), each
        bounded by the timeout: it is never released while it may still be running.
        """
        floor = 2 * self.llm_timeout_seconds + 60
        return timedelta(seconds=max(float(self.llm_reservation_ttl_seconds), floor))

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
