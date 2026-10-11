import hashlib
import secrets
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_token() -> str:
    """256-bit random token, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Only token hashes are stored; a database leak does not leak usable tokens."""
    return hashlib.sha256(token.encode()).digest()
