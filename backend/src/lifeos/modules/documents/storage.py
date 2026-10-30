"""Encrypted storage of document originals (F10, ADR 0008).

Each file is encrypted with AES-256-GCM under a key held in a Docker secret, separate
from the data and from the backups. Layout: `<documents_dir>/<user>/<document>.bin`.
The file format is the one of `lifeos.core.crypto`: magic `LOS1` | key id (1 byte) |
nonce (12 bytes) | ciphertext+tag, always under key id 1. The document id is bound as
associated data: a file copied onto another document's path does not decrypt.
"""

import os
import uuid
from pathlib import Path

from lifeos.config import Settings
from lifeos.core import crypto

KEY_ID = 1


class StorageError(Exception):
    """Storage unavailable (no key) or file unreadable."""


class OriginalMissingError(StorageError):
    """No file for this storage key (purged, or deleted from the volume)."""


def _key(settings: Settings) -> bytes:
    try:
        return crypto.load_key(settings.documents_key, "documents key")
    except crypto.CryptoError as exc:
        raise StorageError(str(exc)) from exc


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
        sealed = crypto.seal(_key(self._settings), KEY_ID, data, document_id.bytes)
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Atomic replace: a crash never leaves a truncated original behind.
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with tmp.open("wb") as handle:
            handle.write(sealed)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)

    def read(self, storage_key: str, document_id: uuid.UUID) -> bytes:
        try:
            blob = self._path(storage_key).read_bytes()
        except FileNotFoundError as exc:
            raise OriginalMissingError("original not found") from exc
        try:
            key_id = crypto.key_id_of(blob)
        except crypto.CryptoError as exc:
            raise StorageError("unknown file format") from exc
        if key_id != KEY_ID:
            raise StorageError("unknown file format")
        try:
            return crypto.open_sealed({KEY_ID: _key(self._settings)}, blob, document_id.bytes)
        except crypto.CryptoError as exc:  # wrong key, tampering, or wrong document
            raise StorageError("original cannot be decrypted") from exc

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)
