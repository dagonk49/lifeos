import base64
import json
import os
import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr

from lifeos import cli
from lifeos.config import Settings
from lifeos.modules.documents.sensitivity import REASONS, classify
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
# Documents that do not say what they are (no « ordonnance », no IBAN...).
PRESCRIPTION_PLAIN = """Docteur Paul Bernard — RPPS 10001234567
Le 02/10/2026
Sertraline 50 mg : 1 comprimé le matin pendant 3 mois"""
ULTRASOUND = """COMPTE RENDU D'ÉCHOGRAPHIE ABDOMINALE
Indication : douleurs. Conclusion : stéatose hépatique modérée."""
LAB_PLAIN = """LABORATOIRE DES ALPES
Hémoglobine 13,2 g/dL — Glycémie 1,02 g/L — HbA1c 6,1 % — Sérologie VIH négative"""
SICK_LEAVE = "AVIS D'ARRÊT DE TRAVAIL — prolongation jusqu'au 20/10/2026"
CPAM = """L'Assurance Maladie — Attestation de droits
Affection de longue durée (ALD) : prise en charge à 100 %"""
PSY = "Suivi en psychiatrie depuis l'hospitalisation de mars, idées suicidaires en baisse."
STATEMENT_PLAIN = """RELEVÉ DE VOS COMPTES — Compte chèques n° 0001234
Ancien solde au 01/09 : 1 020,00 € — PRLV SEPA EDF 82,00 — VIR SEPA SALAIRE 2 100,00"""
BIRTH = "EXTRAIT D'ACTE DE NAISSANCE — Commune de Lyon"
TAX = "Avis d'impôt 2026 sur les revenus de 2025 — Numéro fiscal : 12 34 567 890 123"
DOSED_RECEIPT = "PHARMACIE DU CENTRE\nDoliprane 1000 mg 8 comprimés 2,18\nTOTAL 2,18 EUR"
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
        (PRESCRIPTION_PLAIN, ["health"]),
        (ULTRASOUND, ["health"]),
        (LAB_PLAIN, ["health"]),
        (SICK_LEAVE, ["health"]),
        (CPAM, ["health"]),
        (PSY, ["health"]),
        (STATEMENT_PLAIN, ["bank"]),
        (BIRTH, ["identity"]),
        (TAX, ["identity"]),
        # A pharmacy receipt with a medicine: not enough on its own (the finance view needs
        # receipts), unless more health signals appear.
        (DOSED_RECEIPT, []),
        ("Compte rendu du conseil de classe : bons résultats en mathématiques.", []),
    ],
)
def test_local_sensitivity_filter(text: str, expected: list[str]) -> None:
    result = classify(text)
    assert result.categories == expected
    # Reasons explain without repeating the sensitive values.
    flat = " ".join(r for reasons in result.reasons.values() for r in reasons)
    assert "FR76" not in flat and "MARTIN" not in flat


# French corpus (tests/fixtures/sensitivity): one synthetic document per file, with OCR
# noise and typographic variants, and the categories expected in manifest.json.
CORPUS = Path(__file__).parent / "fixtures" / "sensitivity"
MANIFEST: dict[str, list[str]] = json.loads((CORPUS / "manifest.json").read_text("utf-8"))
# Values of the corpus that no report may repeat (see the corpus README).
CORPUS_VALUES = (
    "FR76",
    "3000 6000",
    "90 02 69",
    "006 084",
    "4111",
    "990000000001",
    "99XX99999",
    "9900000001",
    "30 99 999",
    "01 99 00",
    "06 39 98",
    "example.org",
    "example.com",
    "EXEMPLE",
    "Camille",
)


def test_the_corpus_covers_every_category_and_ordinary_documents() -> None:
    expected = [tuple(categories) for categories in MANIFEST.values()]
    for category in ("identity", "health", "bank"):
        assert sum(1 for c in expected if c == (category,)) >= 8, category
    assert sum(1 for c in expected if c == ()) >= 6
    assert sorted(MANIFEST) == sorted(p.name for p in CORPUS.iterdir() if p.suffix == ".txt"), (
        "each corpus file has an expectation, and each expectation a file"
    )


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_french_corpus(name: str) -> None:
    # The text alone (the file name would give the answer away).
    result = classify((CORPUS / name).read_text("utf-8"))
    assert result.categories == MANIFEST[name]
    # Reasons are fixed labels: never a value read in the document.
    for reasons in result.reasons.values():
        assert set(reasons) <= REASONS


def test_the_admin_command_measures_a_folder_without_showing_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = str(CORPUS / "manifest.json")
    assert cli.main(["sensitivity-check", str(CORPUS), "--expect", manifest]) == 0
    out = capsys.readouterr().out
    assert "banque-iban-nbsp.txt: bank (IBAN" in out and "masked: iban=1" in out
    assert "ordinaire-facture-siret.txt: not sensitive | masked: email=1, phone=1" in out
    assert "False negatives: 0; false positives: 0." in out
    for value in CORPUS_VALUES:
        assert value not in out, value

    # A wrong expectation is reported (and fails), still without any value.
    wrong = tmp_path / "manifest.json"
    wrong.write_text(json.dumps({**MANIFEST, "banque-rib.txt": ["health"]}), "utf-8")
    assert cli.main(["sensitivity-check", str(CORPUS), "--expect", str(wrong)]) == 1
    out = capsys.readouterr().out
    assert "banque-rib.txt: bank (" in out and "MISSED: health | EXTRA: bank" in out
    assert "False negatives: 1; false positives: 1." in out
    assert "FR76" not in out

    # Without a manifest: a plain report; an unknown path is an error.
    (tmp_path / "note.txt").write_text("Rappel : appeler le 06 39 98 12 34.", "utf-8")
    (tmp_path / "binaire.bin").write_bytes(b"\x7fELF binary")
    assert cli.main(["sensitivity-check", str(tmp_path / "note.txt"), str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "note.txt: not sensitive | masked: phone=1" in out
    assert "binaire.bin: skipped, unsupported type" in out
    assert "06 39 98" not in out
    assert cli.main(["sensitivity-check", str(tmp_path / "absent")]) == 1


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
        # French typography: non-breaking and narrow spaces are text.
        ("IBAN\u00a0:\u00a0FR76\u00a03000\u00a06000\u202f€".encode(), "text"),
        # Angle brackets that open no tag a browser would sniff as markup.
        (b"<3 merci !\na < b et b > c\n<brouillon> liste", "text"),
        ("\ufeffNotes avec une marque d'ordre".encode(), "text"),
        # Markup further in stays text: browsers only sniff the start, and the original is
        # served as a text/plain download (nosniff, sandbox), never rendered.
        (b"Notes\n<script>alert(1)</script>", "text"),
        # Markdown notes that open with an HTML comment or a common fragment (README
        # headers): text, served as a text/plain download, never rendered.
        (b"<!-- markdownlint-disable -->\n# Notes\n", "text"),
        (b'<p align="center"><img src="logo.png"></p>\n\n# Projet\n', "text"),
        (b'<div align="center">\n\n# Projet\n</div>', "text"),
        (b"<h1>Compte rendu</h1>\nPr\xc3\xa9sents : A, B", "text"),
        (b"<b>Important</b> : rappeler le notaire", "text"),
        (b"\n\t <p>Bonjour<img src=x onerror=alert(1)></p>", "text"),
        (b"<a href='https://example.org'>lien</a>", "text"),
        # Polyglots keep the type of their signature (images are re-encoded, originals are
        # downloads): the other reading is never used.
        (b"%PDF-1.4\n<html><script>alert(1)</script></html>", "pdf"),
        (b"\xff\xd8\xff\xe0 jpeg then PK\x03\x04 a zip archive", "image"),
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
        # A web page, a script, SVG or XML disguised as text: behind a byte order mark,
        # spaces or comments, or in capitals.
        "\ufeff<!DOCTYPE html><html><script>x</script></html>".encode(),
        b"<!-- note --><svg onload=alert(1)>",
        b"<!-- a -->\n<!-- b -->\n  <html><body>x</body></html>",
        b"<HTML><BODY onload=alert(1)>",
        b"<SCRIPT>alert(1)</SCRIPT>",
        b"<svg/onload=alert(1)>",
        b"<iframe src=x>",
        b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'/>",
        # Executables, archives and other formats renamed .pdf, .jpg or .txt.
        b"#!/bin/sh\x00\nrm -rf /",
        b"\xca\xfe\xba\xbe mach-o or java class",
        b"Rar!\x1a\x07\x00 archive",
        b"7z\xbc\xaf\x27\x1c archive",
        b"\x1f\x8b\x08\x00 gzip",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 Office document with macros",
        b"GIF89a\x01\x00\x01\x00",
        b"II*\x00 TIFF",
    ],
)
def test_dangerous_or_unknown_types_are_refused(data: bytes) -> None:
    assert sniff(data) is None
