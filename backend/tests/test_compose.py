"""The shipped Compose file keeps every service up when connectors are turned on.

Every service reading infra/.env sees LIFEOS_CONNECTORS_PROVIDER=google once the owner
enables it (docs/guide-google.md). Only the processes that use the connectors may depend
on their secrets: the others either force the provider off, or never build the
connectors (and `Settings` alone does not require the secrets).
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from lifeos.config import Settings
from lifeos.modules.connectors import ConnectorsConfigError, build_connectors

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "compose.yaml"
CONNECTOR_SECRETS = {"lifeos_connectors_key", "lifeos_google_client_secret"}
# Read .env, mount neither secret nor override the provider: their commands (alembic,
# lifeos-scheduler) never call `build_connectors`. A new service must be classified.
WITHOUT_CONNECTORS = {"migrate", "scheduler"}


def _services() -> dict[str, dict[str, Any]]:
    if not COMPOSE.is_file():
        pytest.skip("infra/compose.yaml is not part of this checkout")
    compose = yaml.safe_load(COMPOSE.read_text())
    services: dict[str, dict[str, Any]] = compose["services"]
    return services


def _environment(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) for item in env)
    return {key: str(value) for key, value in env.items()}


def test_every_service_reading_env_survives_the_google_provider() -> None:
    unclassified = []
    for name, service in _services().items():
        env_files = service.get("env_file") or []
        if ".env" not in ([env_files] if isinstance(env_files, str) else env_files):
            continue
        provider = _environment(service).get("LIFEOS_CONNECTORS_PROVIDER")
        secrets = {s if isinstance(s, str) else s["source"] for s in service.get("secrets", [])}
        if provider == "disabled" or secrets >= CONNECTOR_SECRETS:
            continue
        if name not in WITHOUT_CONNECTORS:
            unclassified.append(name)
    assert unclassified == []


def test_the_processes_using_connectors_get_their_secrets() -> None:
    services = _services()
    for name in ("api", "worker"):
        secrets = set(services[name]["secrets"])
        assert secrets >= CONNECTOR_SECRETS, name
        assert "LIFEOS_CONNECTORS_PROVIDER" not in _environment(services[name])
    for name in ("ml", "ml-download", "stt", "stt-download"):
        env = _environment(services[name])
        assert env["LIFEOS_CONNECTORS_PROVIDER"] == "disabled", name
    for name in WITHOUT_CONNECTORS:
        assert not CONNECTOR_SECRETS & set(services[name].get("secrets", [])), name


def test_settings_load_with_the_google_provider_and_no_secret() -> None:
    # What migrate, scheduler (and ml, stt without the override) see: they must start.
    settings = Settings(connectors_provider="google")
    assert settings.connectors_key is None and settings.google_client_secret is None


def test_the_api_refuses_to_start_without_the_key_or_the_client() -> None:
    key = SecretStr("a2" * 22)  # 44 base64 characters, 33 bytes: not an AES-256 key
    with pytest.raises(ConnectorsConfigError, match="LIFEOS_CONNECTORS_KEY"):
        build_connectors(Settings(connectors_provider="fake"))
    with pytest.raises(ConnectorsConfigError, match="LIFEOS_CONNECTORS_KEY"):
        build_connectors(Settings(connectors_provider="google", connectors_key=key))
    valid = SecretStr("a" * 43 + "=")
    with pytest.raises(ConnectorsConfigError, match="LIFEOS_GOOGLE_CLIENT_ID"):
        build_connectors(Settings(connectors_provider="google", connectors_key=valid))
    assert list(build_connectors(Settings(connectors_provider="fake", connectors_key=valid))) == [
        "fake"
    ]
    assert build_connectors(Settings()) == {}


def test_web_push_secret_goes_to_the_api_and_the_worker_only() -> None:
    """The worker signs pushes (egress to the push services), the api publishes the public
    key; migrate and the scheduler start with LIFEOS_PUSH_PROVIDER=webpush and no key."""
    services = _services()
    for name in ("api", "worker"):
        assert "lifeos_vapid_private_key" in services[name]["secrets"], name
    assert "egress" in services["worker"]["networks"]
    for name in WITHOUT_CONNECTORS:
        assert "lifeos_vapid_private_key" not in services[name].get("secrets", []), name
    assert Settings(push_provider="webpush").vapid_private_key is None
