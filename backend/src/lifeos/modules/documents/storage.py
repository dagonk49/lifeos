"""Encrypted storage of document originals (F10, ADR 0008).

Each file is encrypted with AES-256-GCM under a key held in a Docker secret, separate
from the data and from the backups. Layout: `<documents_dir>/<user>/<document>.bin`.
The file format is: magic `LOS1` | key id (1 byte) | nonce (12 bytes) | ciphertext+tag.
The document id is bound as associated data: a file copied onto another document's
path does not decrypt.
"""

import base64
import os
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lifeos.config import Settings

MAGIC = b"LOS1"
KEY_ID = 1
NONCE_BYTES = 12


class StorageError(Exception):
    """Storage unavailable (no key) or file unreadable."""


def _key(settings: Settings) -> bytes:
    secret = settings.documents_key.get_secret_value().strip() if settings.documents_key else ""
    if not secret:
        raise StorageError("documents key not configured")
    try:
        key = base64.b64decode(secret, validate=True)
    except ValueError as exc:
        raise StorageError("documents key is not valid base64") from exc
    if len(key) != 32:
        raise StorageError("documents key must be 32 bytes (AES-256)")
    return key


class DocumentStore:
    def __init__(self, settings: Settings) -> None:
        self._root = Path(settings.documents_dir)
        self._settings = settings

    @property
    def available(self) -> bool:
        try:
            _key(self._settings)
        except StorageError:
            return False
        return True

    def _path(self, storage_key: str) -> Path:
        path = (self._root / storage_key).resolve()
        if self._root.resolve() not in path.parents:
            raise StorageError("invalid storage key")
        return path

    @staticmethod
    def key_for(user_id: uuid.UUID, document_id: uuid.UUID) -> str:
        return f"{user_id}/{document_id}.bin"

    def write(self, storage_key: str, document_id: uuid.UUID, data: bytes) -> None:
        aead = AESGCM(_key(self._settings))
        nonce = os.urandom(NONCE_BYTES)
        sealed = aead.encrypt(nonce, data, document_id.bytes)
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Atomic replace: a crash never leaves a truncated original behind.
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with tmp.open("wb") as handle:
            handle.write(MAGIC + bytes([KEY_ID]) + nonce + sealed)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)

    def read(self, storage_key: str, document_id: uuid.UUID) -> bytes:
        try:
            blob = self._path(storage_key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("original not found") from exc
        header = len(MAGIC) + 1 + NONCE_BYTES
        if len(blob) < header or not blob.startswith(MAGIC) or blob[len(MAGIC)] != KEY_ID:
            raise StorageError("unknown file format")
        nonce = blob[len(MAGIC) + 1 : header]
        try:
            return AESGCM(_key(self._settings)).decrypt(nonce, blob[header:], document_id.bytes)
        except Exception as exc:  # InvalidTag: wrong key, tampering, or wrong document
            raise StorageError("original cannot be decrypted") from exc

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)
