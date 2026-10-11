import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lifeos.db.base import Base, Timestamps, UUIDPrimaryKey


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner')", name="role"),)

    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, default="owner")
    locale: Mapped[str] = mapped_column(Text, default="fr-FR", server_default="fr-FR")
    timezone: Mapped[str] = mapped_column(
        Text, default="Europe/Paris", server_default="Europe/Paris"
    )
    disabled_at: Mapped[datetime | None]


class Space(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "spaces"
    __table_args__ = (
        CheckConstraint("kind IN ('personal', 'professional')", name="kind"),
        UniqueConstraint("user_id", "name", name="uq_spaces_user_id_name"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)


class PasskeyCredential(UUIDPrimaryKey, Base):
    __tablename__ = "passkey_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger)
    transports: Mapped[list[str]] = mapped_column(JSONB, default=list)
    aaguid: Mapped[str] = mapped_column(Text)
    backup_eligible: Mapped[bool]
    backup_state: Mapped[bool]
    label: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class EnrollmentToken(UUIDPrimaryKey, Base):
    __tablename__ = "enrollment_tokens"
    __table_args__ = (CheckConstraint("purpose IN ('bootstrap', 'recovery')", name="purpose"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    purpose: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class AuthChallenge(UUIDPrimaryKey, Base):
    __tablename__ = "auth_challenges"
    __table_args__ = (
        CheckConstraint("purpose IN ('registration', 'authentication')", name="purpose"),
    )

    purpose: Mapped[str] = mapped_column(Text)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    enrollment_token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enrollment_tokens.id", ondelete="CASCADE")
    )
    challenge: Mapped[bytes] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    consumed_at: Mapped[datetime | None]
    created_at: Mapped[datetime]


class UserSession(UUIDPrimaryKey, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("passkey_credentials.id", ondelete="SET NULL")
    )
    authenticated_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime] = mapped_column(index=True)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
