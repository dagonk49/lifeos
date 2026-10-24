"""Owner's client of scripts/test-backup.sh, run INSIDE the api container:

    docker compose exec -T api python - setup ENROLLMENT_TOKEN < scripts/test-backup-client.py
    docker compose exec -T api python - check STATE_JSON < scripts/test-backup-client.py

`setup` enrolls a software passkey (ES256, 'none' attestation, like a synced passkey) and
creates a task, a transaction and a document; it prints the state (the passkey's private key
included: a throw-away test key). `check`, after the restore, signs in with that same passkey,
then finds the task by search, the transaction, and downloads the original (same SHA-256).

Standard library and `cryptography` only: what the api image provides.
"""

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

API = "http://127.0.0.1:8000"
ORIGIN = os.environ["LIFEOS_PUBLIC_ORIGIN"]
RP_ID = os.environ["LIFEOS_WEBAUTHN_RP_ID"]
TASK_TITLE = "Renouveler le passeport à la mairie"
DOCUMENT = "Facture d'électricité d'octobre : 87,40 euros.\n".encode()
# User present, user verified, backup eligible, backed up (a synced passkey: counter stays 0).
FLAGS = 0x01 | 0x04 | 0x08 | 0x10
ATTESTED = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def cbor(value: Any) -> bytes:
    """Minimal CBOR encoder: integers, byte and text strings, maps (COSE key, attestation)."""

    def head(major: int, n: int) -> bytes:
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        if n < 65536:
            return bytes([major << 5 | 25]) + n.to_bytes(2, "big")
        return bytes([major << 5 | 26]) + n.to_bytes(4, "big")

    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value).__name__)


class Client:
    def __init__(self) -> None:
        self.cookie: str | None = None

    def call(
        self, method: str, path: str, payload: Any = None, *, body: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, bytes]:
        headers = {"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())}
        if payload is not None:
            body = json.dumps(payload).encode()
        if body is not None:
            headers["Content-Type"] = content_type
        if self.cookie:
            headers["Cookie"] = self.cookie
        request = urllib.request.Request(API + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status, data, cookies = response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            status, data, cookies = error.code, error.read(), error.headers
        for cookie in cookies.get_all("Set-Cookie") or []:
            if cookie.startswith("lifeos_session="):
                self.cookie = cookie.split(";", 1)[0]
        return status, data

    def json(self, method: str, path: str, payload: Any = None, expect: int = 200) -> Any:
        status, data = self.call(method, path, payload)
        if status != expect:
            sys.exit(f"{method} {path}: HTTP {status} {data[:300]!r}")
        return json.loads(data) if data else None


def client_data(kind: str, challenge: str) -> bytes:
    return json.dumps(
        {"type": kind, "challenge": challenge, "origin": ORIGIN, "crossOrigin": False}
    ).encode()


def enroll(client: Client, token: str) -> dict[str, str]:
    options = client.json(
        "POST", "/v1/auth/passkeys/registration/options", {"enrollment_token": token}
    )
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    cose = cbor(
        {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}
    )
    credential_id = os.urandom(16)
    auth_data = (
        hashlib.sha256(RP_ID.encode()).digest()
        + bytes([FLAGS | ATTESTED])
        + (0).to_bytes(4, "big")
        + bytes(16)
        + len(credential_id).to_bytes(2, "big")
        + credential_id
        + cose
    )
    public_key = options["public_key"]
    credential = {
        "id": b64url(credential_id),
        "rawId": b64url(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": b64url(client_data("webauthn.create", public_key["challenge"])),
            "attestationObject": b64url(
                cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})
            ),
            "transports": ["internal", "hybrid"],
        },
        "clientExtensionResults": {},
        "authenticatorAttachment": "platform",
    }
    client.json(
        "POST",
        "/v1/auth/passkeys/registration/verify",
        {"ceremony_id": options["ceremony_id"], "credential": credential, "label": "Test"},
        expect=201,
    )
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return {
        "key": pem.decode(),
        "credential_id": b64url(credential_id),
        "user_handle": public_key["user"]["id"],
    }


def login(client: Client, passkey: dict[str, str]) -> None:
    options = client.json("POST", "/v1/auth/passkeys/authentication/options")
    key = serialization.load_pem_private_key(passkey["key"].encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    data = client_data("webauthn.get", options["public_key"]["challenge"])
    auth_data = hashlib.sha256(RP_ID.encode()).digest() + bytes([FLAGS]) + (0).to_bytes(4, "big")
    signature = key.sign(auth_data + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
    credential = {
        "id": passkey["credential_id"],
        "rawId": passkey["credential_id"],
        "type": "public-key",
        "response": {
            "clientDataJSON": b64url(data),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(signature),
            "userHandle": passkey["user_handle"],
        },
        "clientExtensionResults": {},
        "authenticatorAttachment": "platform",
    }
    client.json(
        "POST",
        "/v1/auth/passkeys/authentication/verify",
        {"ceremony_id": options["ceremony_id"], "credential": credential},
    )


def personal_space(client: Client) -> str:
    me = client.json("GET", "/v1/auth/me")
    return next(s["id"] for s in me["spaces"] if s["kind"] == "personal")


def upload(client: Client, space_id: str) -> dict[str, Any]:
    boundary = uuid.uuid4().hex
    fields = {"space_id": space_id, "title": "Facture", "keep_original": "true"}
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="facture.txt"'
        "\r\nContent-Type: text/plain\r\n\r\n".encode()
        + DOCUMENT
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, data = client.call(
        "POST",
        "/v1/documents",
        body=b"".join(parts),
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    if status != 201:
        sys.exit(f"upload: HTTP {status} {data[:300]!r}")
    return dict(json.loads(data))


def setup(token: str) -> None:
    client = Client()
    passkey = enroll(client, token)
    space_id = personal_space(client)
    task = client.json("POST", "/v1/tasks", {"space_id": space_id, "title": TASK_TITLE}, 201)
    transaction = client.json(
        "POST",
        "/v1/finance/transactions",
        {"space_id": space_id, "amount": "42.10", "tx_date": "2026-10-01", "merchant": "Dupain"},
        201,
    )
    document = upload(client, space_id)
    state = {
        "passkey": passkey,
        "task": task["id"],
        "transaction": transaction["id"],
        "document": document["id"],
        "sha256": hashlib.sha256(DOCUMENT).hexdigest(),
    }
    print(json.dumps(state))


def check(state: dict[str, Any]) -> None:
    client = Client()
    login(client, state["passkey"])
    print("ok - signed in with the passkey enrolled before the backup")
    hits = client.json("GET", "/v1/search?q=passeport")["hits"]
    if state["task"] not in [h["id"] for h in hits]:
        sys.exit("search: the task is not found")
    print("ok - the task is found by search")
    items = client.json("GET", "/v1/finance/transactions")["items"]
    found = [t for t in items if t["id"] == state["transaction"]]
    if not found or found[0]["amount"] != "42.10":
        sys.exit("finances: the transaction is missing or changed")
    print("ok - the transaction is there, same amount")
    status, data = client.call("GET", f"/v1/documents/{state['document']}/original")
    if status != 200 or hashlib.sha256(data).hexdigest() != state["sha256"]:
        sys.exit(f"original: HTTP {status}, or not the uploaded content")
    print("ok - the original decrypts to the uploaded content")


def delete_task(state: dict[str, Any]) -> None:
    """The owner erases the task after the backup: a restore must not bring it back."""
    client = Client()
    login(client, state["passkey"])
    client.json("DELETE", f"/v1/tasks/{state['task']}", expect=204)
    print("ok - task deleted")


if __name__ == "__main__":
    command, argument = sys.argv[1], sys.argv[2]
    if command == "setup":
        setup(argument)
    elif command == "check":
        check(json.loads(argument))
    elif command == "delete-task":
        delete_task(json.loads(argument))
    else:
        sys.exit(f"unknown command {command}")
