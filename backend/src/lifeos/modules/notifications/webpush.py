"""Web Push message encryption (RFC 8291, `aes128gcm` of RFC 8188) and VAPID signatures
(RFC 8292), written on `cryptography` alone (ADR 0019).

The push services (Google FCM, Apple, Mozilla) only ever carry the encrypted record: the
key agreement is between this server's one-time key and the browser's subscription key
(`p256dh`), mixed with the subscription's `auth` secret.
"""

import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF, HKDFExpand

# RFC 8188 record size: one record holds the whole message (at most 4096 bytes on the
# wire for push services, RFC 8291 §4).
RECORD_SIZE = 4096
_TAG = 16
_HEADER = 16 + 4 + 1 + 65  # salt, record size, key id length, key id (sender's key)
# Largest plaintext that fits in one 4096-byte record (delimiter and tag included).
MAX_PLAINTEXT = RECORD_SIZE - _HEADER - _TAG - 1
# RFC 8292 §2: a token must not be valid for more than 24 hours.
MAX_TOKEN_SECONDS = 24 * 3600


class WebPushError(ValueError):
    """Malformed subscription keys, plaintext too long, or an unusable VAPID key."""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise WebPushError("invalid base64url") from exc


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def load_subscription_key(p256dh: str) -> ec.EllipticCurvePublicKey:
    """The browser's P-256 key (65-byte uncompressed point, base64url); a point off the
    curve is refused."""
    raw = b64url_decode(p256dh)
    if len(raw) != 65 or raw[0] != 4:
        raise WebPushError("p256dh must be an uncompressed P-256 point")
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise WebPushError("p256dh is not a P-256 point") from exc


def load_auth_secret(auth: str) -> bytes:
    raw = b64url_decode(auth)
    if len(raw) != 16:
        raise WebPushError("auth must be 16 bytes")
    return raw


def encrypt(
    plaintext: bytes,
    p256dh: str,
    auth: str,
    *,
    sender_key: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The `aes128gcm` body of a push message (RFC 8291 §3.4, one record).

    `sender_key` and `salt` are fresh for every message; tests pass the RFC's values.
    """
    if len(plaintext) > MAX_PLAINTEXT:
        raise WebPushError("payload too large for one record")
    ua_public = load_subscription_key(p256dh)
    auth_secret = load_auth_secret(auth)
    sender_key = sender_key or ec.generate_private_key(ec.SECP256R1())
    salt = salt if salt is not None else os.urandom(16)
    if len(salt) != 16:
        raise WebPushError("salt must be 16 bytes")
    as_public = _public_bytes(sender_key.public_key())
    ua_bytes = _public_bytes(ua_public)
    ecdh_secret = sender_key.exchange(ec.ECDH(), ua_public)
    # IKM = HKDF(auth_secret, ecdh_secret, "WebPush: info" || 0x00 || ua_public || as_public)
    key_info = b"WebPush: info\x00" + ua_bytes + as_public
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=key_info).derive(
        ecdh_secret
    )
    # RFC 8188: PRK = HKDF-Extract(salt, IKM), then the content key and nonce.
    cek = HKDF(
        algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00"
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00"
    ).derive(ikm)
    # Single (last) record: plaintext, delimiter 0x02, no padding.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth: str) -> bytes:
    """The receiving side (what a browser does): used by the tests and the fake sender."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    if rs < 18 or len(ciphertext) > rs:
        raise WebPushError("invalid record size")
    sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ua_bytes = _public_bytes(ua_private.public_key())
    ecdh_secret = ua_private.exchange(ec.ECDH(), sender)
    key_info = b"WebPush: info\x00" + ua_bytes + as_public
    ikm = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=load_auth_secret(auth), info=key_info
    ).derive(ecdh_secret)
    prk = _extract(salt, ikm)
    cek = HKDFExpand(hashes.SHA256(), 16, b"Content-Encoding: aes128gcm\x00").derive(prk)
    nonce = HKDFExpand(hashes.SHA256(), 12, b"Content-Encoding: nonce\x00").derive(prk)
    record = AESGCM(cek).decrypt(nonce, ciphertext, None).rstrip(b"\x00")
    if not record.endswith(b"\x02"):
        raise WebPushError("not a last record")
    return record[:-1]


def _extract(salt: bytes, ikm: bytes) -> bytes:
    mac = hmac.HMAC(salt, hashes.SHA256())
    mac.update(ikm)
    return mac.finalize()


# --- VAPID (RFC 8292) ---------------------------------------------------------------------


@dataclass(frozen=True)
class VapidKey:
    private: ec.EllipticCurvePrivateKey

    @property
    def public_key(self) -> str:
        """The `applicationServerKey` given to browsers (base64url uncompressed point)."""
        return b64url_encode(_public_bytes(self.private.public_key()))

    @classmethod
    def from_pem(cls, pem: str) -> "VapidKey":
        try:
            key = serialization.load_pem_private_key(pem.strip().encode(), password=None)
        except (ValueError, TypeError) as exc:
            raise WebPushError("the VAPID key is not a PEM private key") from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise WebPushError("the VAPID key must be a P-256 (prime256v1) key")
        return cls(key)

    @classmethod
    def generate(cls) -> "VapidKey":
        return cls(ec.generate_private_key(ec.SECP256R1()))


def audience(endpoint: str) -> str:
    """The origin of the push service (the token's `aud`)."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def vapid_token(
    key: VapidKey, endpoint: str, subject: str, *, now: float | None = None, ttl: int = 12 * 3600
) -> str:
    """An ES256 JWT for this push service, valid `ttl` seconds (at most 24 hours)."""
    if not 0 < ttl <= MAX_TOKEN_SECONDS:
        raise WebPushError("a VAPID token is valid for 24 hours at most")
    issued = int(now if now is not None else time.time())
    header = b64url_encode(
        json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()
    )
    claims = {"aud": audience(endpoint), "exp": issued + ttl, "sub": subject}
    payload = b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    der = key.private.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{header}.{payload}.{b64url_encode(signature)}"


def vapid_authorization(key: VapidKey, endpoint: str, subject: str) -> str:
    """The `Authorization` header (RFC 8292 §3): token and public key."""
    return f"vapid t={vapid_token(key, endpoint, subject)}, k={key.public_key}"


def verify_token(token: str, public_key: str) -> dict[str, object]:
    """Checks an ES256 token against a base64url public key; returns its claims."""
    header, payload, signature = token.split(".")
    raw = b64url_decode(signature)
    if len(raw) != 64:
        raise WebPushError("invalid signature length")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key = load_subscription_key(public_key)
    try:
        key.verify(der, f"{header}.{payload}".encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise WebPushError("invalid signature") from exc
    if json.loads(b64url_decode(header)) != {"typ": "JWT", "alg": "ES256"}:
        raise WebPushError("unexpected header")
    claims: dict[str, object] = json.loads(b64url_decode(payload))
    return claims
