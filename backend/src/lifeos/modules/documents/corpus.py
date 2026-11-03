"""Measuring the local filter on a folder of documents (`lifeos-admin sensitivity-check`).

The owner runs it on the server, on their own documents: each file is read locally (plain
text in-process, PDF and images by the internal ml service when configured), then
classified and masked exactly as an upload would be. The report holds file names,
categories, reasons and counts of masked identifiers by kind: never a value, never a line
of text. Nothing is stored, nothing leaves the server.

A manifest (JSON object: file name -> expected categories) turns the report into a test:
missed categories (false negatives) and extra ones (false positives) are listed.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from lifeos.modules.documents.extraction import (
    ExtractionError,
    Extractor,
    UnreadableDocument,
    decode_text,
)
from lifeos.modules.documents.models import SENSITIVITY_CATEGORIES
from lifeos.modules.documents.sensitivity import classify_document
from lifeos.modules.documents.service import default_title
from lifeos.modules.documents.sniff import sniff
from lifeos.modules.guardian.privacy import mask

MANIFEST = "manifest.json"
# Files describing a corpus, not part of it.
_NOT_DOCUMENTS = {MANIFEST, "README.md"}
# Larger files are not documents an owner uploads (the API refuses them too).
MAX_BYTES = 50 * 1024 * 1024


@dataclass
class FileCheck:
    name: str
    categories: list[str] = field(default_factory=list)
    # Category -> reasons (labels, never values).
    reasons: dict[str, list[str]] = field(default_factory=dict)
    # Identifier kind -> count of what would be masked.
    masked: dict[str, int] = field(default_factory=dict)
    # Why the file could not be checked.
    skipped: str | None = None
    # From the manifest: categories expected for this file.
    expected: list[str] | None = None

    @property
    def missed(self) -> list[str]:
        """False negatives: expected categories the filter did not find."""
        if self.expected is None or self.skipped:
            return []
        return [c for c in self.expected if c not in self.categories]

    @property
    def extra(self) -> list[str]:
        """False positives: categories found that the manifest does not expect."""
        if self.expected is None or self.skipped:
            return []
        return [c for c in self.categories if c not in self.expected]


def files_in(paths: list[Path]) -> list[tuple[Path, str]]:
    """Every file under `paths` (recursively), with the name shown in the report."""
    found: list[tuple[Path, str]] = []
    for root in paths:
        if root.is_file():
            found.append((root, root.name))
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name not in _NOT_DOCUMENTS and not path.name.startswith("."):
                found.append((path, path.relative_to(root).as_posix()))
    return found


def load_manifest(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the manifest must be a JSON object: file name -> categories")
    manifest: dict[str, list[str]] = {}
    for name, categories in data.items():
        if not isinstance(categories, list) or any(
            c not in SENSITIVITY_CATEGORIES for c in categories
        ):
            raise ValueError(f"{name}: expected a list among {', '.join(SENSITIVITY_CATEGORIES)}")
        manifest[str(name)] = [str(c) for c in categories]
    return manifest


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.stat().st_size <= MAX_BYTES else None


async def _text(path: Path, extractor: Extractor | None, max_pages: int) -> str | FileCheck:
    name = path.name
    data = await asyncio.to_thread(_read, path)
    if data is None:
        return FileCheck(name, skipped="too large")
    detected = sniff(data)
    if detected is None or detected.kind == "audio":
        return FileCheck(name, skipped="unsupported type")
    if detected.kind == "text":
        return decode_text(data).text
    if extractor is None:
        return FileCheck(name, skipped=f"no reader for {detected.kind} (extraction disabled)")
    try:
        extraction = await extractor.extract(
            data, detected.mime, purpose="document", max_pages=max_pages
        )
    except UnreadableDocument as exc:
        return FileCheck(name, skipped=f"unreadable ({exc.code})")
    except ExtractionError:
        return FileCheck(name, skipped="reader unavailable")
    return extraction.text


async def check_file(
    path: Path, name: str, extractor: Extractor | None, *, max_pages: int
) -> FileCheck:
    text = await _text(path, extractor, max_pages)
    if isinstance(text, FileCheck):
        text.name = name
        return text
    # As an upload: the title defaults to the file name, and it counts.
    found = classify_document(default_title(path.name, "document"), text)
    return FileCheck(
        name, categories=found.categories, reasons=found.reasons, masked=mask(text).kinds()
    )


def report_line(check: FileCheck) -> str:
    if check.skipped:
        return f"{check.name}: skipped, {check.skipped}"
    found = "; ".join(f"{c} ({', '.join(check.reasons.get(c, []))})" for c in check.categories)
    masked = ", ".join(f"{kind}={count}" for kind, count in sorted(check.masked.items()))
    line = f"{check.name}: {found or 'not sensitive'} | masked: {masked or 'nothing'}"
    if check.missed:
        line += f" | MISSED: {', '.join(check.missed)}"
    if check.extra:
        line += f" | EXTRA: {', '.join(check.extra)}"
    return line
