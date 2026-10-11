"""Imports every model so that `Base.metadata` is complete (Alembic, tests)."""

from lifeos.modules.audit.models import AuditEvent
from lifeos.modules.identity.models import (
    AuthChallenge,
    EnrollmentToken,
    PasskeyCredential,
    Space,
    User,
    UserSession,
)

__all__ = [
    "AuditEvent",
    "AuthChallenge",
    "EnrollmentToken",
    "PasskeyCredential",
    "Space",
    "User",
    "UserSession",
]
