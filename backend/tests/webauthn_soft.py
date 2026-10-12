"""Software WebAuthn authenticator (ES256, 'none' attestation) for end-to-end auth tests.

Produces the same JSON shapes as navigator.credentials.create()/get() serialised by
@simplewebauthn/browser.
"""

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftAuthenticator:
    def __init__(self, rp_id: str, origin: str, *, user_verification: bool = True) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.user_verification = user_verification
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0
        self.user_handle: str | None = None

    def _cose_public_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _flags(self, extra: int = 0) -> int:
        flags = FLAG_UP | FLAG_BE | FLAG_BS | extra
        if self.user_verification:
            flags |= FLAG_UV
        return flags

    def _auth_data(self, flags: int, *, attested: bool) -> bytes:
        data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([flags])
            + self.sign_count.to_bytes(4, "big")
        )
        if attested:
            data += (
                bytes(16)  # AAGUID
                + len(self.credential_id).to_bytes(2, "big")
                + self.credential_id
                + self._cose_public_key()
            )
        return data

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps(
            {
                "type": kind,
                "challenge": challenge,
                "origin": origin or self.origin,
                "crossOrigin": False,
            }
        ).encode()

    def create(self, public_key: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        self.user_handle = public_key["user"]["id"]
        client_data = self._client_data("webauthn.create", public_key["challenge"], origin)
        auth_data = self._auth_data(self._flags(FLAG_AT), attested=True)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(
        self,
        public_key: dict[str, Any],
        *,
        origin: str | None = None,
        sign_count_increment: int = 1,
    ) -> dict[str, Any]:
        self.sign_count += sign_count_increment
        client_data = self._client_data("webauthn.get", public_key["challenge"], origin)
        auth_data = self._auth_data(self._flags(), attested=False)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": self.user_handle,
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
