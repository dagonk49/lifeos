"""Files for the `ml` tests, generated in code: no binary fixture to trust, and what each
one should give is known. Ordinary documents (text rendered by Pillow, minimal PDFs written
by hand) and hostile ones (ADR 0011), a few kilobytes each.

Not a test module: imported by them (and by the isolated child for `confinement`).
"""

import io
import os
import resource
import struct
import zipfile
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- Ordinary documents ----------------------------------------------------------------------


def render(
    lines: list[str], *, size: int = 36, width: int = 900, font_path: Path | None = None
) -> Image.Image:
    font = (
        ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default(size=size)
    )
    image = Image.new("RGB", (width, (size + 14) * len(lines) + 80), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((40, 40 + index * (size + 14)), line, fill="black", font=font)
    return image


def encode(image: Image.Image, fmt: str, **params: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def pdf(objects: list[bytes], *, trailer: bytes = b"", xref: bool = True) -> bytes:
    """A PDF made of these objects (numbered from 1, the first one is the catalog)."""
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    if xref:
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        for offset in offsets:
            out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R%s >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        trailer,
        start,
    )
    return bytes(out)


def text_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with a real text layer (Helvetica), one line per page."""
    count = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{4 + 2 * i} 0 R".encode() for i in range(count))
        + f"] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, text in enumerate(pages):
        stream = f"BT /F1 14 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> "
            + f"/Contents {5 + 2 * index} 0 R >>".encode()
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    return pdf(objects)


def _flate(row: bytes, rows: int) -> bytes:
    """`row` repeated `rows` times, Flate compressed without building it in memory."""
    compressor = zlib.compressobj(9)
    return b"".join(compressor.compress(row) for _ in range(rows)) + compressor.flush()


def image_pdf(width: int, height: int, mask: int = 0) -> bytes:
    """A small PDF whose single page draws a huge image of zeros (Flate compressed), and
    optionally a huge soft mask: kilobytes on disk, gigabytes once decoded."""
    smask = b" /SMask 6 0 R" if mask else b""
    picture = _flate(b"\x00" * (width * 3), height)
    content = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8%s /Filter /FlateDecode /Length %d >>\nstream\n"
        % (width, height, smask, len(picture))
        + picture
        + b"\nendstream",
    ]
    if mask:
        alpha = _flate(b"\x00" * mask, mask)
        objects.append(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n"
            % (mask, mask, len(alpha))
            + alpha
            + b"\nendstream"
        )
    return pdf(objects)


# --- Hostile PDFs ----------------------------------------------------------------------------

CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
ONE_PAGE = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
BLANK_PAGE = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"


def pdf_truncated() -> bytes:
    data = text_pdf(["Contrat de location"])
    return data[: len(data) // 2]


def pdf_garbage() -> bytes:
    """A PDF signature, then noise."""
    return b"%PDF-1.7\n" + bytes(range(256)) * 400


def pdf_page_tree_loop() -> bytes:
    """The page tree lists itself among its pages."""
    return pdf([CATALOG, b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>", BLANK_PAGE])


def pdf_xref_loop() -> bytes:
    """The cross-reference table points back to itself (/Prev)."""
    data = pdf([CATALOG, ONE_PAGE, BLANK_PAGE])
    start = int(data.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    return data.replace(b"/Root 1 0 R", b"/Root 1 0 R /Prev %d" % start)


def pdf_without_xref() -> bytes:
    """No cross-reference table: the reader has to rebuild it."""
    return pdf([CATALOG, ONE_PAGE, BLANK_PAGE], xref=False)


def pdf_length_loop() -> bytes:
    """The length of a stream is a reference to a reference to itself."""
    return pdf(
        [
            CATALOG,
            ONE_PAGE,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
            b"<< /Length 5 0 R >>\nstream\nBT ET\nendstream",
            b"6 0 R",
            b"5 0 R",
        ]
    )


def pdf_deep_nesting() -> bytes:
    """A hundred thousand nested arrays (a recursive parser would overflow its stack)."""
    nested = b"[" * 100_000 + b"]" * 100_000
    return pdf([b"<< /Type /Catalog /Pages 2 0 R /X " + nested + b" >>", ONE_PAGE, BLANK_PAGE])


def pdf_huge_count() -> bytes:
    """The page tree claims two billion pages, and holds one."""
    return pdf([CATALOG, b"<< /Type /Pages /Kids [3 0 R] /Count 2000000000 >>", BLANK_PAGE])


def pdf_huge_page() -> bytes:
    """A page of 10^30 points a side: the rendering scale must stay bounded."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1e30 1e30] >>"
    return pdf([CATALOG, ONE_PAGE, page])


def pdf_active_content() -> bytes:
    """JavaScript run at opening, a program launched, an embedded file: PDFium runs none."""
    return pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R "
            b"/Names << /JavaScript << /Names [(a) 4 0 R] >> "
            b"/EmbeddedFiles << /Names [(x.exe) 5 0 R] >> >> "
            b"/AA << /WC << /S /Launch /F (cmd.exe) >> >> >>",
            ONE_PAGE,
            BLANK_PAGE,
            b"<< /S /JavaScript /JS (app.alert('x'); this.exportDataObject({cName: 'x.exe'})) >>",
            b"<< /Type /Filespec /F (x.exe) /EF << /F 6 0 R >> >>",
            b"<< /Type /EmbeddedFile /Length 4 >>\nstream\nMZ\x90\x00\nendstream",
        ]
    )


def pdf_encrypted() -> bytes:
    """Protected by a password (any: not the empty one)."""
    handler = b"<< /Filter /Standard /V 1 /R 2 /O <%s> /U <%s> /P -4 >>" % (b"41" * 32, b"42" * 32)
    file_id = b"<00112233445566778899aabbccddeeff>"
    return pdf(
        [CATALOG, ONE_PAGE, BLANK_PAGE, handler],
        trailer=b" /Encrypt 4 0 R /ID [%s %s]" % (file_id, file_id),
    )


def pdf_content_bomb(megabytes: int) -> bytes:
    """A page whose drawing instructions decompress to `megabytes` MB (about 2 MB in the
    file for 1 GB): the memory limit stops it."""
    content = _flate(b"0 0 1 1 re f\n" * 80_000, megabytes)
    return pdf(
        [
            CATALOG,
            ONE_PAGE,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content)
            + content
            + b"\nendstream",
        ]
    )


def pdf_html_polyglot() -> bytes:
    """A valid PDF that a browser sniffing the content could also render as HTML."""
    page = b"<html><body><script>alert(document.cookie)</script></body></html>"
    return pdf([CATALOG, ONE_PAGE, BLANK_PAGE, b"(" + page + b")"])


# --- Hostile pictures ------------------------------------------------------------------------


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _with_png_chunk(chunk: bytes) -> bytes:
    """A small white PNG with `chunk` right after its header (IHDR)."""
    png = encode(Image.new("RGB", (64, 64), "white"), "PNG")
    return png[:33] + chunk + png[33:]


def png_text_bomb() -> bytes:
    """A compressed comment of 1 GB of zeros (1 MB in the file)."""
    return _with_png_chunk(_png_chunk(b"zTXt", b"Comment\x00\x00" + _flate(b"\x00" * 2**20, 1024)))


def png_icc_bomb() -> bytes:
    """A compressed colour profile of 1 GB of zeros."""
    return _with_png_chunk(_png_chunk(b"iCCP", b"icc\x00\x00" + _flate(b"\x00" * 2**20, 1024)))


def png_truncated() -> bytes:
    data = encode(render(["Bonjour"]), "PNG")
    return data[: len(data) // 2]


def apng(frames: int) -> bytes:
    """An animated PNG of `frames` frames: only the first one is read."""
    first = Image.new("RGB", (64, 64), "white")
    others = [Image.new("RGB", (64, 64), (index % 256, 0, 0)) for index in range(frames - 1)]
    return encode(first, "PNG", save_all=True, append_images=others)


def webp_huge_canvas(side: int) -> bytes:
    """An animated WebP of two tiny frames on a `side` x `side` canvas (header patched):
    140 bytes, gigabytes of canvas for the decoder."""
    first, second = Image.new("RGB", (16, 16), "white"), Image.new("RGB", (16, 16), "black")
    data = bytearray(encode(first, "WEBP", save_all=True, append_images=[second], lossless=True))
    assert data[12:16] == b"VP8X"
    data[24:27] = (side - 1).to_bytes(3, "little")
    data[27:30] = (side - 1).to_bytes(3, "little")
    return bytes(data)


def jpeg_scans(side: int, scans: int) -> bytes:
    """A progressive JPEG whose last scan is repeated: `scans` passes over the picture."""
    data = encode(Image.new("RGB", (side, side), "white"), "JPEG", progressive=True, quality=90)
    end = data.rfind(b"\xff\xd9")
    last = data.rfind(b"\xff\xda", 0, end)
    extra = scans - data.count(b"\xff\xda")
    return data[:end] + data[last:end] * extra + b"\xff\xd9"


def jpeg_huge_header(width: int, height: int) -> bytes:
    """An 8 x 8 JPEG whose header claims `width` x `height` pixels."""
    data = bytearray(encode(Image.new("L", (8, 8), "white"), "JPEG"))
    frame = data.find(b"\xff\xc0")
    data[frame + 5 : frame + 7] = height.to_bytes(2, "big")
    data[frame + 7 : frame + 9] = width.to_bytes(2, "big")
    return bytes(data)


def heic_huge_header(side: int) -> bytes:
    """A 64 x 64 HEIC whose image size box (ispe) claims `side` x `side` pixels."""
    data = bytearray(encode(Image.new("RGB", (64, 64), "white"), "HEIF", quality=50))
    box = data.find(b"ispe")
    data[box + 8 : box + 12] = side.to_bytes(4, "big")
    data[box + 12 : box + 16] = side.to_bytes(4, "big")
    return bytes(data)


ZIP_PAYLOAD = b"<script>alert(1)</script>"


def jpeg_zip_polyglot() -> bytes:
    """A valid JPEG followed by a ZIP archive (read from its end by unzip tools)."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("page.html", ZIP_PAYLOAD)
    return encode(render(["Facture"]), "JPEG") + archive.getvalue()


# --- Run in the isolated child -----------------------------------------------------------------


def confinement() -> dict[str, object]:
    """What the child process is allowed: its own process group, first to be killed when
    memory runs out, no core dump, a data limit."""
    return {
        "own_group": os.getpgid(0) == os.getpid(),
        "oom_score_adj": Path("/proc/self/oom_score_adj").read_text().strip(),
        "core": resource.getrlimit(resource.RLIMIT_CORE),
        "data": resource.getrlimit(resource.RLIMIT_DATA),
    }


def crash() -> None:
    """A decoder bug: the process dies on a signal, without answering."""
    os.abort()
