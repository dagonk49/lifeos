"""Authenticated encryption at rest (AES-256-GCM), shared by every encrypted store.

Used for the document originals (ADR 0008) and the connector tokens (ADR 0012). A sealed
blob is: magic `LOS1` | key id (1 byte) | nonce (12 bytes) | ciphertext+tag. The caller
binds each blob to its owner object through the associated data (e.g. the document id):
a blob copied onto another object does not open.

Keys come from Docker secrets, apart from the data and from the backups. A keyring maps
key ids to keys: blobs sealed under an older key still open after a rotation, and are
sealed again under the current key when next written.
"""

import base64
import binascii
import os
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

MAGIC = b"LOS1"
NONCE_BYTES = 12
HEADER_BYTES = len(MAGIC) + 1 + NONCE_BYTES
KEY_BYTES = 32


class CryptoError(Exception):
    """Key missing or invalid, unknown blob format, or a blob that does not open."""


class KeyUnavailableError(CryptoError):
    """No usable key is configured."""


def decode_key(encoded: str, name: str) -> bytes:
    """A 32-byte key from its base64 text (the content of a secret file)."""
    try:
        key = base64.b64decode(encoded.strip(), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise KeyUnavailableError(f"{name} is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise KeyUnavailableError(f"{name} must be {KEY_BYTES} bytes (AES-256)")
    return key


def load_key(secret: SecretStr | None, name: str) -> bytes:
    """The single key held by a secret (documents key: always key id 1)."""
    value = secret.get_secret_value().strip() if secret else ""
    if not value:
        raise KeyUnavailableError(f"{name} not configured")
    return decode_key(value, name)


@dataclass(frozen=True)
class Keyring:
    """Keys by id; `current` seals, every key opens."""

    current: int
    keys: Mapping[int, bytes]

    @property
    def key(self) -> bytes:
        return self.keys[self.current]


def load_keyring(secret: SecretStr | None, name: str) -> Keyring:
    """Parses `base64` (key id 1) or `id:base64,id:base64,...` (the first one is current).

    Rotation: write the new key first with the next id, keep the previous ones after it
    (e.g. `2:<new key>,1:<old key>`). Ids go from 1 to 255.
    """
    value = secret.get_secret_value().strip() if secret else ""
    if not value:
        raise KeyUnavailableError(f"{name} not configured")
    keys: dict[int, bytes] = {}
    order: list[int] = []
    for entry in value.replace("\n", ",").split(","):
        entry = entry.strip()
        if not entry:
            continue
        key_id_text, sep, encoded = entry.partition(":")
        if not sep:
            key_id_text, encoded = "1", entry
        try:
            key_id = int(key_id_text)
        except ValueError as exc:
            raise KeyUnavailableError(f"{name}: invalid key id") from exc
        if not 1 <= key_id <= 255 or key_id in keys:
            raise KeyUnavailableError(f"{name}: key ids must be unique, from 1 to 255")
        keys[key_id] = decode_key(encoded, name)
        order.append(key_id)
    if not order:
        raise KeyUnavailableError(f"{name} not configured")
    return Keyring(current=order[0], keys=keys)


def seal(key: bytes, key_id: int, plaintext: bytes, aad: bytes) -> bytes:
    """Encrypts `plaintext`, bound to `aad`, under `key` (recorded as `key_id`)."""
    if not 1 <= key_id <= 255:
        raise CryptoError("key id out of range")
    nonce = os.urandom(NONCE_BYTES)
    return MAGIC + bytes([key_id]) + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def key_id_of(blob: bytes) -> int:
    if len(blob) < HEADER_BYTES or not blob.startswith(MAGIC):
        raise CryptoError("unknown format")
    return blob[len(MAGIC)]


def open_sealed(keys: Mapping[int, bytes], blob: bytes, aad: bytes) -> bytes:
    """Decrypts a sealed blob with the key of its id; raises CryptoError otherwise."""
    key = keys.get(key_id_of(blob))
    if key is None:
        raise CryptoError("unknown key id")
    nonce = blob[len(MAGIC) + 1 : HEADER_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, blob[HEADER_BYTES:], aad)
    except (InvalidTag, ValueError) as exc:  # wrong key, tampering, another object's blob
        raise CryptoError("cannot be decrypted") from exc
