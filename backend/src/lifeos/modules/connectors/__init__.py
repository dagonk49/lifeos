from lifeos.config import Settings
from lifeos.core import crypto
from lifeos.modules.connectors.base import OAuthProvider

Connectors = dict[str, OAuthProvider]


class ConnectorsConfigError(ValueError):
    """A provider is chosen but its key or client is missing: the API refuses to start."""


def _check_config(settings: Settings) -> None:
    """Checked here, by the processes that use the connectors (API), and not by `Settings`:
    migrate, scheduler, ml and stt read the same .env without these secrets (ADR 0012)."""
    try:
        crypto.load_keyring(settings.connectors_key, "connectors key")
    except crypto.CryptoError as exc:
        raise ConnectorsConfigError(
            f"LIFEOS_CONNECTORS_KEY (secret lifeos_connectors_key) is required when "
            f"LIFEOS_CONNECTORS_PROVIDER is set: {exc}"
        ) from exc
    if settings.connectors_provider == "google" and not (
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_client_secret.get_secret_value().strip()
    ):
        raise ConnectorsConfigError(
            "LIFEOS_GOOGLE_CLIENT_ID and the secret lifeos_google_client_secret are "
            "required when LIFEOS_CONNECTORS_PROVIDER=google"
        )


def build_connectors(settings: Settings) -> Connectors:
    """The providers an account can be connected with, by name (none when disabled).

    Raises ConnectorsConfigError when a provider is chosen without its key or client.
    """
    if settings.connectors_provider == "disabled":
        return {}
    _check_config(settings)
    if settings.connectors_provider == "google":
        from lifeos.modules.connectors.google import GoogleConnector

        return {"google": GoogleConnector(settings)}
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from lifeos.modules.connectors.fake import FakeConnector

    # End-to-end runs: every process (API, worker) holds the same demo calendars.
    fake = FakeConnector()
    fake.add_demo_week(datetime.now(ZoneInfo(settings.timezone)).date(), settings.timezone)
    fake.add_demo_inbox(datetime.now(ZoneInfo("UTC")))
    return {"fake": fake}
