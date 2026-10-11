import base64
import os
import uuid

import pytest
from pydantic import SecretStr

from lifeos.config import Settings
from lifeos.modules.documents.sensitivity import classify
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.documents.storage import DocumentStore, StorageError

# --- Local sensitivity filter: synthetic corpus (no real personal data) -------------------

IDENTITY = """RÉPUBLIQUE FRANÇAISE
CARTE NATIONALE D'IDENTITÉ N° : 123456789012
Nom : MARTIN  Prénom(s) : CAMILLE
Né(e) le : 01.02.1990 à : LYON
IDFRAMARTIN<<<<<<<<<<<<<<<<<<<<<<<<<<<
1234567890123CAMILLE<<<<<9002012F8"""
PASSPORT_MRZ_ONLY = (
    "P<FRAMARTIN<<CAMILLE<<<<<<<<<<<<<<<<<<<<<<<<\n12AB345678FRA9002012F3001014<<<<<<<<<<<<<<06"
)
PRESCRIPTION = """Dr Jeanne Petit — Médecin généraliste
ORDONNANCE du 03/10/2026
Paracétamol 1 g : 1 comprimé 3 fois par jour pendant 5 jours"""
LAB = "Laboratoire — Résultats d'analyses — Glycémie à jeun : 0,95 g/L"
RIB = """RELEVÉ D'IDENTITÉ BANCAIRE
Titulaire : Camille Martin
IBAN : FR76 3000 6000 0112 3456 7890 189
BIC : AGRIFRPP"""
STATEMENT = "Relevé de compte n° 12 — Solde créditeur au 30/09 : 1 234,56 €"
RECEIPT = """CARREFOUR MARKET
12 rue de la Paix 75002 Paris
LAIT 1L 1,15
PAIN 1,30
TOTAL TTC 2,45 EUR
CB **** **** **** 1234
Merci de votre visite"""
PHARMACY_RECEIPT = "PHARMACIE DU CENTRE\nDentifrice 3,90\nTOTAL 3,90 EUR\nCB"
NOTE = "Liste de courses : tomates, pâtes, café. Appeler Paul pour le week-end."
MEETING = "Compte rendu de la réunion d'équipe : livraison de la version 2 en novembre."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (IDENTITY, ["identity"]),
        (PASSPORT_MRZ_ONLY, ["identity"]),
        (PRESCRIPTION, ["health"]),
        (LAB, ["health"]),
        (RIB, ["bank"]),
        (STATEMENT, ["bank"]),
        (RECEIPT, []),
        (PHARMACY_RECEIPT, []),
        (NOTE, []),
        (MEETING, []),
        ("Carte Vitale : 1 90 02 69 123 456 78", ["health"]),
    ],
)
def test_local_sensitivity_filter(text: str, expected: list[str]) -> None:
    result = classify(text)
    assert result.categories == expected
    # Reasons explain without repeating the sensitive values.
    flat = " ".join(r for reasons in result.reasons.values() for r in reasons)
    assert "FR76" not in flat and "MARTIN" not in flat


# --- Encrypted storage -----------------------------------------------------------------------


def _store(tmp_path: object, key: bytes | None = None) -> DocumentStore:
    settings = Settings(
        documents_dir=str(tmp_path),
        documents_key=SecretStr(base64.b64encode(key or os.urandom(32)).decode()),
    )
    return DocumentStore(settings)


def test_originals_are_encrypted_and_bound_to_their_document(tmp_path: object) -> None:
    store = _store(tmp_path)
    user, doc, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    key = DocumentStore.key_for(user, doc)
    store.write(key, doc, b"secret receipt bytes")
    raw = (tmp_path / key).read_bytes()  # type: ignore[operator]
    assert b"secret receipt" not in raw and raw.startswith(b"LOS1")
    assert store.read(key, doc) == b"secret receipt bytes"
    with pytest.raises(StorageError):
        store.read(key, other)  # copied onto another document: refused
    with pytest.raises(StorageError):
        _store(tmp_path).read(key, doc)  # another key
    store.delete(key)
    with pytest.raises(StorageError):
        store.read(key, doc)
    with pytest.raises(StorageError):
        store.read("../../etc/passwd", doc)


def test_storage_requires_a_valid_key(tmp_path: object) -> None:
    assert not DocumentStore(Settings(documents_dir=str(tmp_path))).available
    short = Settings(documents_dir=str(tmp_path), documents_key=SecretStr("c2hvcnQ="))
    with pytest.raises(StorageError):
        DocumentStore(short).write("u/d.bin", uuid.uuid4(), b"x")


# --- Type allowlist --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"%PDF-1.7\n...", "pdf"),
        (b"\x89PNG\r\n\x1a\n....", "image"),
        (b"\xff\xd8\xff\xe0....", "image"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image"),
        (b"\x00\x00\x00\x18ftypheic....", "image"),
        (b"\x1a\x45\xdf\xa3....webm", "audio"),
        (b"OggS\x00....", "audio"),
        (b"\x00\x00\x00\x20ftypM4A ....", "audio"),
        ("Notes de réunion\nAccents : é è à ç".encode(), "text"),
    ],
)
def test_allowed_types_are_detected_by_signature(data: bytes, kind: str) -> None:
    detected = sniff(data)
    assert detected is not None and detected.kind == kind


@pytest.mark.parametrize(
    "data",
    [
        b"MZ\x90\x00 executable",
        b"\x7fELF binary",
        b"PK\x03\x04 zip or docx with macros",
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<!DOCTYPE html><html><script>x</script></html>",
        b"\x00\x01\x02 binary noise \x00",
    ],
)
def test_dangerous_or_unknown_types_are_refused(data: bytes) -> None:
    assert sniff(data) is None
