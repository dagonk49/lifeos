"""Structured logging.

Logs never contain request bodies, prompts, LLM outputs, tokens or e-mail addresses
(see docs/politique-de-donnees.md). Only identifiers, codes, counters and durations.
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from lifeos.config import Settings

REDACTED = "[redacted]"
# Defence in depth (threat T19): no code path logs a credential, but a value that looks
# like one is masked anyway, in any field, in the message and in exception texts.
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "code_verifier",
        "client_secret",
        "authorization",
        "cookie",
        "password",
        "secret",
    }
)
_SECRET_PATTERNS = (
    # OAuth parameters in a URL, a form or a repr: keep the name, mask the value.
    re.compile(
        r"(?i)(?<![\w-])((?:access_token|refresh_token|id_token|client_secret|code_verifier"
        r"|code|token|state)[\"']?\s*[=:]\s*[\"']?)[^&\s\"',}]+"
    ),
    re.compile(r"(?i)(bearer\s+)[\w.~+/-]+=*"),
    re.compile(r"()ya29\.[\w.-]+"),  # Google access tokens
    re.compile(r"()1//[\w.-]{10,}"),  # Google refresh tokens
    re.compile(r"()eyJ[\w-]+\.eyJ[\w-]+\.[\w-]*"),  # JSON Web Tokens (ID tokens)
)


def scrub(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + REDACTED, text)
    return text


def redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key, value in list(event_dict.items()):
        if key.lower() in _SECRET_KEYS and value is not None:
            event_dict[key] = REDACTED
        elif isinstance(value, str):
            event_dict[key] = scrub(value)
    return event_dict


def configure_logging(settings: Settings) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_secrets,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # Requests are logged by our middleware (route template only, no query string).
    logging.getLogger("uvicorn.access").disabled = True
    # The Google SDK may log request details at debug level.
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
