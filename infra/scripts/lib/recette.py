"""Measurements of infra/scripts/recette.sh, run inside the `ml` and `stt` containers.

    python -c "$(cat recette.py)" COMMAND [ARGS]

Synthetic inputs only (generated French receipts and scans, generated passages; the audio
comes from the owner's --audio file or is a generated signal): no personal data goes in. Each
measurement is one JSON line of durations, statuses and sizes on standard output, never a text.

Commands:
  receipt                 write a generated receipt photo (JPEG) to standard output
  ocr N                   N receipt photos, then N scanned 3-page PDFs, read by POST /extract
  embed N                 N batches of 32 passages embedded by POST /embed
  stt N                   N transcriptions of the audio read on standard input (or generated)
  loop OP SECONDS [ARG]   repeat ocr / embed / stt (one item each time) for SECONDS
"""

import io
import json
import math
import struct
import sys
import time
from typing import Any

import httpx

ML_URL = "http://127.0.0.1:8100"
STT_URL = "http://127.0.0.1:8200"
TIMEOUT = 600.0
SHOPS = ["SUPERMARCHE DU PARC", "EPICERIE CENTRALE", "BOULANGERIE DES LILAS"]
ITEMS = [
    ("PAIN DE CAMPAGNE", "2,40"),
    ("LAIT DEMI-ECREME 1L", "1,15"),
    ("POMMES GOLDEN 1KG", "2,99"),
    ("CAFE MOULU 250G", "3,49"),
    ("YAOURTS NATURE X8", "2,10"),
    ("PATES FUSILLI 500G", "0,95"),
    ("HUILE OLIVE 75CL", "7,80"),
    ("EMMENTAL RAPE 200G", "2,35"),
]


def emit(**fields: Any) -> None:
    print(json.dumps(fields), flush=True)


def receipt_image(index: int, *, width: int = 1200, height: int = 0, scale: int = 1) -> Any:
    """A generated till receipt (fixed shop names, items and prices: no real data)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=34 * scale)
    lines = [
        SHOPS[index % len(SHOPS)],
        "12 RUE DES TESTS 75000 PARIS",
        f"TICKET {1000 + index}",
        "",
    ]
    total = 0
    for offset in range(6):
        name, price = ITEMS[(index + offset) % len(ITEMS)]
        lines.append(f"{name:<24}{price:>8}")
        total += int(price.replace(",", ""))
    lines += ["", f"TOTAL TTC {total // 100},{total % 100:02d} EUR", "CB SANS CONTACT", "MERCI"]
    size = (width * scale, max(height, (120 + 56 * len(lines)) * scale))
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(lines):
        draw.text((60 * scale, (60 + 56 * row) * scale), line, fill="black", font=font)
    return image


def jpeg(image: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def scanned_pdf(index: int, pages: int = 3) -> bytes:
    """A scan without text layer (A4 at 200 dpi): every page goes through the OCR."""
    images = [
        receipt_image(index + page, width=1654, height=2339).convert("RGB") for page in range(pages)
    ]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:], resolution=200)
    return buffer.getvalue()


def extract(client: httpx.Client, op: str, data: bytes, mime: str, pages: int) -> None:
    started = time.monotonic()
    try:
        response = client.post(
            f"{ML_URL}/extract",
            files={"file": ("recette", data, mime)},
            data={"mime": mime, "purpose": "document", "max_pages": str(pages)},
            timeout=TIMEOUT,
        )
        status = response.status_code
        body = response.json() if status == 200 else {}
    except httpx.HTTPError as exc:
        status, body = 0, {"error": type(exc).__name__}
    emit(
        op=op,
        status=status,
        seconds=round(time.monotonic() - started, 3),
        pages=body.get("pages"),
        chars=len(body.get("text") or ""),
        bytes=len(data),
    )


def ocr_one(client: httpx.Client, index: int) -> None:
    if index % 2 == 0:
        extract(client, "ocr_photo", jpeg(receipt_image(index, scale=2)), "image/jpeg", 1)
    else:
        extract(client, "ocr_scan_3p", scanned_pdf(index), "application/pdf", 3)


def embed_one(client: httpx.Client, index: int) -> None:
    passages = [
        f"Note de recette {index}-{n} : liste de courses, rendez-vous et tâches de la semaine,"
        " rédigée pour mesurer l'indexation."
        for n in range(32)
    ]
    started = time.monotonic()
    try:
        status = client.post(
            f"{ML_URL}/embed", json={"texts": passages, "kind": "passage"}, timeout=TIMEOUT
        ).status_code
    except httpx.HTTPError:
        status = 0
    emit(op="embed_32", status=status, seconds=round(time.monotonic() - started, 3))


def generated_audio(seconds: float = 20.0, rate: int = 16000) -> bytes:
    """A voice-like signal (harmonics, syllable rhythm): exercises the model, not French."""
    frames = bytearray()
    for n in range(int(seconds * rate)):
        t = n / rate
        envelope = max(0.0, math.sin(2 * math.pi * 3.0 * t)) * (0.6 + 0.4 * math.sin(t))
        pitch = 140 + 30 * math.sin(2 * math.pi * 0.5 * t)
        sample = sum(math.sin(2 * math.pi * pitch * k * t) / k for k in (1, 2, 3, 4))
        frames += struct.pack("<h", int(9000 * envelope * sample / 2))
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return header + b"data" + struct.pack("<I", len(frames)) + bytes(frames)


def audio_input() -> tuple[bytes, str, bool]:
    from lifeos.modules.documents.sniff import sniff

    data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    if not data:
        return generated_audio(), "audio/wav", True
    detected = sniff(data)
    if detected is None or detected.kind != "audio":
        print("the --audio file is not a supported recording", file=sys.stderr)
        raise SystemExit(2)
    return data, detected.mime, False


def stt_one(client: httpx.Client, audio: tuple[bytes, str, bool]) -> None:
    data, mime, generated = audio
    try:
        loaded = bool(client.get(f"{STT_URL}/healthz", timeout=10).json().get("loaded"))
    except (httpx.HTTPError, ValueError):
        loaded = False
    started = time.monotonic()
    try:
        response = client.post(
            f"{STT_URL}/transcribe",
            files={"file": ("recette", data, mime)},
            data={"mime": mime},
            timeout=TIMEOUT,
        )
        status = response.status_code
        body = response.json() if status == 200 else {}
    except httpx.HTTPError:
        status, body = 0, {}
    emit(
        op="stt" if loaded else "stt_with_model_load",
        status=status,
        seconds=round(time.monotonic() - started, 3),
        audio_seconds=body.get("duration"),
        speech_seconds=body.get("speech"),
        device=body.get("device"),
        generated_audio=generated,
    )


def main(argv: list[str]) -> int:
    command, args = (argv[0], argv[1:]) if argv else ("help", [])
    with httpx.Client() as client:
        if command == "receipt":
            sys.stdout.buffer.write(jpeg(receipt_image(7, scale=2)))
        elif command == "ocr":
            for index in range(2 * int(args[0])):
                ocr_one(client, index)
        elif command == "embed":
            for index in range(int(args[0])):
                embed_one(client, index)
        elif command == "stt":
            audio = audio_input()
            for _ in range(int(args[0])):
                stt_one(client, audio)
        elif command == "loop":
            op, deadline = args[0], time.monotonic() + float(args[1])
            recording = audio_input() if op == "stt" else None
            index = int(args[2]) if len(args) > 2 else 0
            while time.monotonic() < deadline:
                if op == "ocr":
                    ocr_one(client, index)
                elif op == "embed":
                    embed_one(client, index)
                elif recording is not None:
                    stt_one(client, recording)
                index += 1
        else:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
