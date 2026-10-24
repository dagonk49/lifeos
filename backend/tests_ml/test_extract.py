"""Local text extraction of the `ml` service (CI job `ml-model`: needs the `ml` extra and
Tesseract with the French model).

Documents are generated in code (`ml_samples`: text rendered by Pillow, minimal PDFs
written by hand): no binary fixture to trust, and the expected text is known. Hostile
files: `test_hostile_files`.
"""

import base64
import io
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("pillow_heif")

from ml_samples import encode, image_pdf, render, text_pdf
from PIL import Image, ImageDraw, ImageFont

from lifeos.config import Settings
from lifeos.ml.app import create_ml_app
from lifeos.ml.extract import MAX_IMAGE_SIDE, ExtractFailure, extract
from lifeos.modules.documents.extraction import RemoteExtractor, UnreadableDocument
from lifeos.modules.finance.receipts import parse_receipt

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="needs Tesseract (tesseract-ocr-fra)"
)
# Pillow's built-in font has no accented letters.
DEJAVU = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

RECEIPT = [
    "CARREFOUR MARKET",
    "12 rue de la Paix 75002 Paris",
    "Le 10/10/2026 a 18:42",
    "PAIN COMPLET          2,15",
    "LAIT DEMI ECREME      1,29",
    "POMMES 1KG            2,99",
    "TOTAL TTC             6,43",
    "CB                    6,43",
]


# --- Images --------------------------------------------------------------------------------


@needs_tesseract
def test_receipt_photo_is_read_and_parsed() -> None:
    result = extract(encode(render(RECEIPT), "PNG"), "image/png", purpose="receipt", max_pages=30)
    assert result.source == "ocr"
    assert result.pages == 1
    assert result.confidence is not None and result.confidence > 0.6
    assert "CARREFOUR MARKET" in result.text
    proposal = parse_receipt(result.text, date(2026, 10, 12))
    assert proposal.total is not None and proposal.total.value == Decimal("6.43")
    assert proposal.tx_date is not None and proposal.tx_date.value == date(2026, 10, 10)
    assert proposal.merchant is not None and proposal.merchant.value == "CARREFOUR MARKET"


@needs_tesseract
@pytest.mark.skipif(not DEJAVU.exists(), reason="needs fonts-dejavu-core")
def test_french_accents_are_recognised() -> None:
    lines = ["Échéance de la facture d'électricité", "Reçu à régler avant le 15 décembre"]
    image = render(lines, width=1100, font_path=DEJAVU)
    result = extract(encode(image, "PNG"), "image/png", purpose="document", max_pages=30)
    for word in ("Échéance", "électricité", "Reçu", "régler", "décembre"):
        assert word in result.text, word


@needs_tesseract
def test_jpeg_metadata_is_dropped_and_orientation_applied() -> None:
    upright = render(RECEIPT)
    # Stored rotated, with the EXIF orientation telling viewers to turn it back.
    stored = upright.rotate(90, expand=True)
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise to display
    exif[0x010F] = "PhoneMaker"  # camera make
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (48.0, 51.0, 24.0)
    data = encode(stored, "JPEG", exif=exif, quality=95, comment=b"secret note")
    with Image.open(io.BytesIO(data)) as original:
        assert original.getexif()  # the upload does carry metadata

    result = extract(data, "image/jpeg", purpose="receipt", max_pages=30)
    assert result.sanitized_mime == "image/jpeg"
    assert result.sanitized is not None
    with Image.open(io.BytesIO(result.sanitized)) as clean:
        assert clean.size == upright.size  # turned upright
        assert not clean.getexif()
        assert "exif" not in clean.info and "comment" not in clean.info
        assert "icc_profile" not in clean.info
    assert b"PhoneMaker" not in result.sanitized and b"secret note" not in result.sanitized
    assert "CARREFOUR" in result.text  # read upright


@needs_tesseract
def test_png_text_chunks_are_dropped() -> None:
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Jeanne Martin")
    info.add_text("GPS", "48.85,2.35")
    data = encode(render(["Bonjour"]), "PNG", pnginfo=info)
    result = extract(data, "image/png", purpose="document", max_pages=30)
    assert result.sanitized_mime == "image/png"
    assert result.sanitized is not None and b"Jeanne" not in result.sanitized
    with Image.open(io.BytesIO(result.sanitized)) as clean:
        assert "Author" not in clean.info and "GPS" not in clean.info


@needs_tesseract
def test_heic_becomes_jpeg() -> None:
    data = encode(render(["Ordonnance du docteur"]), "HEIF", quality=90)
    assert data[4:12] == b"ftypheic"
    result = extract(data, "image/heic", purpose="document", max_pages=30)
    assert result.sanitized_mime == "image/jpeg"
    assert result.sanitized is not None and result.sanitized.startswith(b"\xff\xd8\xff")
    assert "Ordonnance" in result.text


@needs_tesseract
def test_transparent_webp_and_large_pictures_are_bounded() -> None:
    image = Image.new("RGBA", (6000, 1200), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (100, 400), "Facture", fill=(0, 0, 0, 255), font=ImageFont.load_default(size=200)
    )
    data = encode(image, "WEBP", lossless=True)
    result = extract(data, "image/webp", purpose="document", max_pages=30)
    assert result.sanitized_mime == "image/webp"
    assert result.sanitized is not None
    with Image.open(io.BytesIO(result.sanitized)) as clean:
        assert max(clean.size) == MAX_IMAGE_SIDE
    assert "Facture" in result.text


def test_decompression_bomb_is_refused_before_decoding() -> None:
    bomb = encode(Image.new("1", (10_000, 6_000)), "PNG")  # 60 Mpx, a few kB
    assert len(bomb) < 100_000
    with pytest.raises(ExtractFailure) as error:
        extract(bomb, "image/png", purpose="document", max_pages=30)
    assert error.value.code == "image_too_large"


def test_truncated_or_mislabelled_pictures_are_unreadable() -> None:
    jpeg = encode(render(["Bonjour"]), "JPEG")
    for data, mime in ((jpeg[: len(jpeg) // 2], "image/jpeg"), (jpeg, "image/png")):
        with pytest.raises(ExtractFailure) as error:
            extract(data, mime, purpose="document", max_pages=30)
        assert error.value.code == "unreadable"
        assert error.value.status == 422


# --- PDF -----------------------------------------------------------------------------------


def test_pdf_text_layer_is_read_without_ocr() -> None:
    data = text_pdf(["Contrat de location du 1er septembre 2026", "Loyer mensuel : 750 euros"])
    result = extract(data, "application/pdf", purpose="document", max_pages=30)
    assert result.source == "pdf"
    assert result.confidence is None
    assert result.pages == 2
    assert result.sanitized is None
    first, second = result.text.split("\f")
    assert first == "Contrat de location du 1er septembre 2026"
    assert second == "Loyer mensuel : 750 euros"


@needs_tesseract
def test_scanned_pdf_is_ocred() -> None:
    data = encode(render(RECEIPT), "PDF", resolution=150)
    result = extract(data, "application/pdf", purpose="receipt", max_pages=30)
    assert result.source == "ocr"
    assert result.pages == 1
    assert result.confidence is not None and result.confidence > 0.6
    proposal = parse_receipt(result.text, date(2026, 10, 12))
    assert proposal.total is not None and proposal.total.value == Decimal("6.43")


def test_pdf_limits() -> None:
    three_pages = text_pdf(["un", "deux", "trois"])
    with pytest.raises(ExtractFailure) as error:
        extract(three_pages, "application/pdf", purpose="document", max_pages=2)
    assert error.value.code == "too_many_pages"
    with pytest.raises(ExtractFailure) as error:
        extract(b"%PDF-1.7\nnot a pdf", "application/pdf", purpose="document", max_pages=30)
    assert error.value.code == "unreadable"


@needs_tesseract
def test_time_budget_fails_instead_of_retrying() -> None:
    data = encode(render(RECEIPT), "PDF", resolution=150)
    with pytest.raises(ExtractFailure) as error:
        extract(data, "application/pdf", purpose="document", max_pages=30, budget_s=0.0)
    assert (error.value.code, error.value.status) == ("too_slow", 422)


# --- HTTP contract (the API's RemoteExtractor against the real service) ---------------------


class _NoEmbedder:
    model = "none"

    async def embed(self, texts: list[str], kind: str) -> list[list[float]]:
        raise AssertionError("not used")


def _client(settings: Settings) -> httpx.AsyncClient:
    app = create_ml_app(settings, embedder=_NoEmbedder())  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ml")


@needs_tesseract
async def test_remote_extractor_round_trip() -> None:
    settings = Settings(ml_url="http://ml:8100")
    async with _client(settings) as client:
        remote = RemoteExtractor(settings, client=client)
        data = encode(render(RECEIPT), "JPEG", quality=92)
        extraction = await remote.extract(data, "image/jpeg", purpose="receipt", max_pages=30)
        assert extraction.source == "ocr"
        assert "CARREFOUR MARKET" in extraction.text
        assert extraction.sanitized_mime == "image/jpeg"
        assert extraction.sanitized is not None and extraction.sanitized != data

        pdf = await remote.extract(
            text_pdf(["Avis d imposition 2026"]),
            "application/pdf",
            purpose="document",
            max_pages=30,
        )
        assert (pdf.source, pdf.text, pdf.pages) == ("pdf", "Avis d imposition 2026", 1)

        with pytest.raises(UnreadableDocument) as error:
            await remote.extract(
                text_pdf(["a", "b"]), "application/pdf", purpose="document", max_pages=1
            )
        assert error.value.code == "too_many_pages"


async def test_service_checks_type_and_size() -> None:
    settings = Settings(documents_max_bytes=2048)
    async with _client(settings) as client:
        png = encode(render(["x"], size=12, width=40), "PNG")

        async def post(data: bytes, mime: str) -> httpx.Response:
            return await client.post(
                "/extract",
                files={"file": ("document", data, mime)},
                data={"mime": mime, "purpose": "document", "max_pages": "30"},
            )

        # Declared type differs from the signature: refused, no decoder runs.
        response = await post(png, "image/jpeg")
        assert (response.status_code, response.json()) == (415, {"code": "unsupported_type"})
        # Allowed by signature but not an extractable type (text, audio).
        response = await post(b"hello", "text/plain")
        assert response.status_code == 415
        response = await post(b"OggS" + bytes(64), "audio/ogg")
        assert response.status_code == 415
        response = await post(base64.b64encode(bytes(4096)), "text/plain")
        assert (response.status_code, response.json()) == (413, {"code": "too_large"})


# --- Hostile or huge files -----------------------------------------------------------------


def test_huge_image_inside_a_pdf_is_refused_before_rendering() -> None:
    with pytest.raises(ExtractFailure) as error:
        extract(image_pdf(8000, 8000), "application/pdf", purpose="document", max_pages=30)
    assert error.value.code == "image_too_large"


def test_an_extraction_cannot_use_more_memory_than_its_limit() -> None:
    from lifeos.ml.extract import run_isolated

    # The soft mask alone decodes to 400 MB: over the limit, PDFium gives up on drawing
    # it; the service survives and the document is read (blank page here).
    data = image_pdf(500, 500, mask=20_000)
    result = run_isolated(data, "application/pdf", purpose="document", max_pages=30, memory_mb=320)
    assert result.pages == 1
    # A normal document goes through the isolated path unchanged.
    normal = run_isolated(
        text_pdf(["Bonjour"]), "application/pdf", purpose="document", max_pages=30
    )
    assert (normal.source, normal.text) == ("pdf", "Bonjour")
    with pytest.raises(ExtractFailure) as error:
        run_isolated(b"%PDF-1.7\nbroken", "application/pdf", purpose="document", max_pages=30)
    assert (error.value.code, error.value.status) == ("unreadable", 422)


@needs_tesseract
def test_large_phone_photos_are_decoded_at_reduced_scale() -> None:
    # 9600 x 7200 = 69 Mpx: over the decoding limit, but a JPEG is read at 1/2 scale.
    photo = render(RECEIPT, size=36).resize((9600, 7200))
    result = extract(
        encode(photo, "JPEG", quality=80), "image/jpeg", purpose="receipt", max_pages=1
    )
    assert result.sanitized is not None
    with Image.open(io.BytesIO(result.sanitized)) as clean:
        assert max(clean.size) == MAX_IMAGE_SIDE
