"""Encryption at rest shared by the document originals and the connector tokens."""

import base64
import os
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from lifeos.config import Settings
from lifeos.core import crypto
from lifeos.modules.documents.storage import DocumentStore, StorageError


def _b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


def test_seal_round_trip_is_bound_to_its_associated_data() -> None:
    key = os.urandom(32)
    owner, other = uuid.uuid4(), uuid.uuid4()
    blob = crypto.seal(key, 1, b"refresh-token-value", owner.bytes)
    assert blob.startswith(b"LOS1\x01") and b"refresh-token-value" not in blob
    assert crypto.open_sealed({1: key}, blob, owner.bytes) == b"refresh-token-value"
    # Two seals of the same value differ (random nonce).
    assert crypto.seal(key, 1, b"refresh-token-value", owner.bytes) != blob
    with pytest.raises(crypto.CryptoError):
        crypto.open_sealed({1: key}, blob, other.bytes)  # copied onto another object
    with pytest.raises(crypto.CryptoError):
        crypto.open_sealed({1: os.urandom(32)}, blob, owner.bytes)  # another key
    with pytest.raises(crypto.CryptoError):
        crypto.open_sealed({2: key}, blob, owner.bytes)  # unknown key id
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(crypto.CryptoError):
        crypto.open_sealed({1: key}, tampered, owner.bytes)
    with pytest.raises(crypto.CryptoError):
        crypto.open_sealed({1: key}, b"LOS1", owner.bytes)  # truncated


def test_keyring_parsing_and_rotation() -> None:
    old, new = os.urandom(32), os.urandom(32)
    single = crypto.load_keyring(SecretStr(_b64(old) + "\n"), "test key")
    assert single.current == 1 and single.key == old
    blob = crypto.seal(single.key, single.current, b"value", b"aad")

    rotated = crypto.load_keyring(SecretStr(f"2:{_b64(new)}, 1:{_b64(old)}"), "test key")
    assert rotated.current == 2 and rotated.key == new
    # Blobs of the previous key still open; new ones are sealed with key id 2.
    assert crypto.open_sealed(rotated.keys, blob, b"aad") == b"value"
    assert crypto.key_id_of(crypto.seal(rotated.key, rotated.current, b"v", b"aad")) == 2

    for invalid in ("", "   ", "not base64!", _b64(b"short"), f"1:{_b64(old)},1:{_b64(new)}"):
        with pytest.raises(crypto.KeyUnavailableError):
            crypto.load_keyring(SecretStr(invalid), "test key")
    with pytest.raises(crypto.KeyUnavailableError):
        crypto.load_keyring(None, "test key")
    with pytest.raises(crypto.KeyUnavailableError) as error:
        crypto.load_keyring(SecretStr(f"0:{_b64(old)}"), "test key")
    assert _b64(old) not in str(error.value)  # an error never shows the key


def _legacy_seal(key: bytes, document_id: uuid.UUID, data: bytes) -> bytes:
    """The file format written by DocumentStore before the shared helper existed."""
    nonce = os.urandom(12)
    return b"LOS1" + bytes([1]) + nonce + AESGCM(key).encrypt(nonce, data, document_id.bytes)


def _legacy_open(key: bytes, document_id: uuid.UUID, blob: bytes) -> bytes:
    return AESGCM(key).decrypt(blob[5:17], blob[17:], document_id.bytes)


def test_document_originals_keep_their_byte_format(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = DocumentStore(Settings(documents_dir=str(tmp_path), documents_key=SecretStr(_b64(key))))
    user, document = uuid.uuid4(), uuid.uuid4()
    storage_key = DocumentStore.key_for(user, document)

    # An original written before the refactoring still opens...
    legacy = tmp_path / storage_key
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(_legacy_seal(key, document, b"original from 1.0"))
    assert store.read(storage_key, document) == b"original from 1.0"

    # ...and a new one can be read by the former code (same layout, same key id).
    store.write(storage_key, document, b"new original")
    assert _legacy_open(key, document, legacy.read_bytes()) == b"new original"

    # Only key id 1 is a document format: anything else is refused.
    legacy.write_bytes(crypto.seal(key, 2, b"x", document.bytes))
    with pytest.raises(StorageError):
        store.read(storage_key, document)
