"""The export archive (ZIP): the complete JSON, the same data in formats other tools read
(CSV, iCalendar, Markdown), a README that describes them and, on request only, the
decrypted originals of the documents.

The rows are read before the response starts (in the request's transaction); the archive
is then written while it is sent, one file at a time: only the originals' files are read
during the stream, never the database. Text cells that a spreadsheet would run as a
formula are neutralised (OCR'd receipts and synced events are third-party text).
"""

import asyncio
import csv
import io
import json
import unicodedata
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import IO, Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lifeos.logging import get_logger
from lifeos.modules.documents.models import Document
from lifeos.modules.documents.router import EXTENSIONS
from lifeos.modules.documents.service import original_withheld
from lifeos.modules.documents.storage import DocumentStore, StorageError
from lifeos.modules.privacy import ics
from lifeos.modules.privacy.export import EXPORT_EXCLUDED, VERSION, Export

log = get_logger(__name__)

# Characters that make a spreadsheet read a cell as a formula (CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

TASK_COLUMNS = (
    "id",
    "space",
    "title",
    "status",
    "priority",
    "due_at",
    "remind_at",
    "completed_at",
    "tags",
    "depends_on",
    "goal_id",
    "goal",
    "notes",
    "source_type",
    "created_at",
    "updated_at",
)
TRANSACTION_COLUMNS = (
    "id",
    "space",
    "date",
    "direction",
    "amount",
    "signed_amount",
    "currency",
    "merchant",
    "label",
    "category",
    "category_kind",
    "category_confirmed",
    "notes",
    "source_type",
    "document_id",
    "created_at",
    "updated_at",
)
TASK_STATUS = {
    "todo": "NEEDS-ACTION",
    "in_progress": "IN-PROCESS",
    "done": "COMPLETED",
    "cancelled": "CANCELLED",
}
TASK_PRIORITY = {"high": "1", "normal": "5", "low": "9"}


@dataclass(frozen=True)
class Original:
    """A decrypted original to put in the archive."""

    document_id: uuid.UUID
    storage_key: str
    path: str


@dataclass
class Written:
    """What the stream actually put in the archive (known once it is sent)."""

    originals: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def root_name(exported_at: datetime) -> str:
    return f"lifeos-export-{exported_at:%Y%m%d}"


def safe_name(title: str, limit: int = 60) -> str:
    """A file name part: letters and digits (accents kept), `-` and `_` only."""
    text = unicodedata.normalize("NFC", title)
    kept = "".join(c if c.isalnum() or c in "-_" else " " for c in text)
    return "-".join(kept.split())[:limit].strip("-_") or "document"


def neutral(value: str | None) -> str:
    """A text cell a spreadsheet shows as text, never runs as a formula."""
    if not value:
        return ""
    return f"'{value}" if value.startswith(FORMULA_PREFIXES) else value


def _zone(name: str | None) -> ZoneInfo | None:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return None


class _Formats:
    """Builds the derived files from the exported rows."""

    def __init__(self, data: Export) -> None:
        self.data = data
        self.zone = _zone(data.user.get("timezone")) or UTC
        self.spaces = {s["id"]: s["name"] for s in data.tables["spaces"]}

    def local(self, value: datetime | None) -> str:
        """ISO 8601 in the owner's time zone, with its offset (unambiguous)."""
        if value is None:
            return ""
        return value.astimezone(self.zone).isoformat(timespec="seconds")

    def space(self, row: dict[str, Any]) -> str:
        return neutral(self.spaces.get(row.get("space_id"), ""))

    # --- CSV (RFC 4180: comma, double quotes, CRLF; UTF-8 with a BOM for spreadsheets) ---

    @staticmethod
    def csv(header: Iterable[str], rows: Iterable[Iterable[str]]) -> bytes:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows(rows)
        return ("﻿" + buffer.getvalue()).encode()

    def tasks_csv(self) -> bytes:
        goals = {g["id"]: g["title"] for g in self.data.tables["goals"]}
        waits: dict[uuid.UUID, list[str]] = {}
        for link in self.data.tables["task_dependencies"]:
            waits.setdefault(link["task_id"], []).append(str(link["depends_on_id"]))
        rows = (
            [
                str(t["id"]),
                self.space(t),
                neutral(t["title"]),
                t["status"],
                t["priority"],
                self.local(t["due_at"]),
                self.local(t["remind_at"]),
                self.local(t["completed_at"]),
                neutral(", ".join(t["tags"] or [])),
                " ".join(waits.get(t["id"], [])),
                str(t["goal_id"] or ""),
                neutral(goals.get(t["goal_id"], "")),
                neutral(t["notes"]),
                t["source_type"],
                self.local(t["created_at"]),
                self.local(t["updated_at"]),
            ]
            for t in self.data.tables["tasks"]
        )
        return self.csv(TASK_COLUMNS, rows)

    def transactions_csv(self) -> bytes:
        categories = {c["id"]: c for c in self.data.tables["finance_categories"]}
        rows = []
        for tx in self.data.tables["transactions"]:
            category = categories.get(tx["category_id"]) or {}
            amount = str(tx["amount"])
            rows.append(
                [
                    str(tx["id"]),
                    self.space(tx),
                    tx["tx_date"].isoformat(),
                    tx["direction"],
                    amount,
                    f"-{amount}" if tx["direction"] == "expense" else amount,
                    tx["currency"],
                    neutral(tx["merchant"]),
                    neutral(tx["label"]),
                    neutral(category.get("name")),
                    category.get("kind", ""),
                    "true" if tx["category_confirmed"] else "false",
                    neutral(tx["notes"]),
                    tx["source_type"],
                    str(tx["document_id"] or ""),
                    self.local(tx["created_at"]),
                    self.local(tx["updated_at"]),
                ]
            )
        return self.csv(TRANSACTION_COLUMNS, rows)

    # --- iCalendar --------------------------------------------------------------------------

    def calendar_ics(self) -> bytes:
        events = []
        for row in self.data.tables["calendar_events"]:
            event = ics.Component("VEVENT").raw("UID", f"calendar-{row['id']}@lifeos")
            event.instant("DTSTAMP", self.data.exported_at)
            if row["starts_at"] is not None:
                event.instant("DTSTART", row["starts_at"]).instant("DTEND", row["ends_at"])
            else:
                event.on_day("DTSTART", row["start_date"]).on_day("DTEND", row["end_date"])
            event.text("SUMMARY", row["summary"]).text("DESCRIPTION", row["description"])
            event.text("LOCATION", row["location"])
            event.raw("STATUS", "TENTATIVE" if row["status"] == "tentative" else "CONFIRMED")
            event.raw("TRANSP", "OPAQUE" if row["busy"] else "TRANSPARENT")
            event.instant("LAST-MODIFIED", row["provider_updated_at"] or row["updated_at"])
            event.text("X-LIFEOS-SPACE", self.spaces.get(row["space_id"]))
            events.append(event)
        return ics.calendar(events, name="LifeOS — agenda").encode()

    def tasks_ics(self) -> bytes:
        waits: dict[uuid.UUID, list[uuid.UUID]] = {}
        for link in self.data.tables["task_dependencies"]:
            waits.setdefault(link["task_id"], []).append(link["depends_on_id"])
        todos = []
        for task in self.data.tables["tasks"]:
            todo = ics.Component("VTODO").raw("UID", f"task-{task['id']}@lifeos")
            todo.instant("DTSTAMP", self.data.exported_at)
            todo.instant("CREATED", task["created_at"])
            todo.instant("LAST-MODIFIED", task["updated_at"])
            todo.text("SUMMARY", task["title"]).text("DESCRIPTION", task["notes"])
            todo.instant("DUE", task["due_at"])
            todo.raw("STATUS", TASK_STATUS[task["status"]])
            todo.raw("PRIORITY", TASK_PRIORITY[task["priority"]])
            todo.instant("COMPLETED", task["completed_at"])
            todo.texts("CATEGORIES", task["tags"] or [])
            todo.text("X-LIFEOS-SPACE", self.spaces.get(task["space_id"]))
            for other in waits.get(task["id"], []):
                todo.raw("RELATED-TO;RELTYPE=DEPENDS-ON", f"task-{other}@lifeos")
            if task["remind_at"] is not None:
                alarm = ics.Component("VALARM").raw("ACTION", "DISPLAY")
                alarm.raw("TRIGGER;VALUE=DATE-TIME", ics.utc(task["remind_at"]))
                alarm.text("DESCRIPTION", task["title"])
                todo.add(alarm)
            todos.append(todo)
        return ics.calendar(todos, name="LifeOS — tâches").encode()

    # --- Markdown ---------------------------------------------------------------------------

    def journal(self) -> list[tuple[str, bytes]]:
        files = []
        for entry in self.data.tables["journal_entries"]:
            day: date = entry["entry_date"]
            front = {
                "id": str(entry["id"]),
                "date": day.isoformat(),
                "space": self.spaces.get(entry["space_id"], ""),
                "mood": entry["mood"],
                "created_at": self.local(entry["created_at"]),
                "updated_at": self.local(entry["updated_at"]),
            }
            # JSON scalars are valid YAML: the front matter reads in any Markdown tool.
            header = "\n".join(
                f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()
            )
            text = f"---\n{header}\n---\n\n# Journal du {day.isoformat()}\n\n{entry['content']}\n"
            files.append((f"journal/{day.isoformat()}-{str(entry['id'])[:8]}.md", text.encode()))
        return files


def readme(data: Export, written: Written, *, originals: bool, withheld: int) -> bytes:
    counts = {name: len(rows) for name, rows in data.tables.items()}
    excluded = "\n".join(f"- `{name}` : {why}" for name, why in EXPORT_EXCLUDED.items())
    if not originals:
        original_text = (
            "Les originaux des documents ne sont pas inclus. Chacun se télécharge, déchiffré, "
            "depuis la page Documents ; l'archive peut aussi les inclure (case « Inclure les "
            "originaux déchiffrés », confirmation par passkey)."
        )
    else:
        original_text = (
            f"Le dossier `originaux/` contient {len(written.originals)} original(aux) "
            "déchiffré(s), nommés `<date>-<identifiant>-<titre>.<extension>` (l'identifiant "
            "est le début de l'`id` du document dans le JSON)."
        )
        if written.missing:
            original_text += (
                f" {len(written.missing)} original(aux) n'ont pas pu être lus sur le serveur "
                "(fichier absent ou illisible) : " + ", ".join(written.missing) + "."
            )
        if withheld:
            original_text += (
                f" {withheld} image(s) jamais nettoyée(s) de leurs métadonnées (position GPS…) "
                "ne sont pas incluses, comme sur la page Documents."
            )
    text = f"""# Export LifeOS

Exporté le {data.exported_at.isoformat(timespec="seconds")} (UTC) pour {data.user["email"]}.

**Ces fichiers ne sont pas chiffrés.** Ils contiennent votre journal, vos conversations et
le texte de vos documents (y compris sensibles) : gardez-les sur un support chiffré, hors
d'un dossier synchronisé dans le cloud.

## Contenu

- `lifeos-export.json` (JSON UTF-8, format `lifeos-export` version {VERSION}) : toutes vos
  données, une clé par table ;
- `tasks.csv` (CSV) : les tâches ({counts["tasks"]}) ;
- `transactions.csv` (CSV) : les opérations ({counts["transactions"]}) ;
- `tasks.ics` (iCalendar, VTODO) : les tâches, avec échéances, rappels et dépendances ;
- `calendar.ics` (iCalendar, VEVENT) : les événements des agendas suivis
  ({counts["calendar_events"]}) ;
- `journal/*.md` (Markdown) : une entrée de journal par fichier
  ({counts["journal_entries"]}).

## Formats

- **JSON** : identifiants UUID, dates et instants ISO 8601 (UTC), montants et décimaux en
  chaînes exactes (`"12.50"`), jamais en nombres à virgule flottante. La clé `excluded`
  liste ce qui n'est pas exporté, et pourquoi.
- **CSV** : séparateur virgule, guillemets doubles, fins de ligne CRLF, UTF-8 avec BOM,
  point décimal ; instants dans votre fuseau horaire avec leur décalage. Une cellule de
  texte qui commencerait par `=`, `+`, `-` ou `@` est précédée d'une apostrophe, pour
  qu'un tableur ne l'exécute jamais comme une formule. Excel : Données > À partir d'un
  fichier texte/CSV ; LibreOffice : ouvrir le fichier, jeu de caractères UTF-8, séparateur
  virgule. `amount` est toujours positif, `signed_amount` négatif pour une dépense.
- **iCalendar** (RFC 5545) : instants en UTC. `calendar.ics` s'importe dans Google
  Agenda, Apple Calendrier ou Thunderbird ; `tasks.ics` dans un gestionnaire de tâches
  (Thunderbird, Apple Rappels…), Google Agenda ignorant les tâches. Les `UID` sont stables :
  importer un export plus récent met à jour au lieu de dupliquer.
- **Markdown** : un en-tête YAML (id, date, espace, humeur), puis le texte de l'entrée.

## Documents

{original_text}

## Non exporté

{excluded}

Les jetons des comptes connectés, les empreintes de session et le matériel de clé des
passkeys ne quittent jamais le serveur.
"""
    return text.encode()


def plan_originals(documents: Iterable[Document]) -> tuple[list[Original], int]:
    """The originals to include, and how many pictures are withheld (never re-encoded)."""
    planned: list[Original] = []
    withheld = 0
    for document in documents:
        if document.storage_key is None:
            continue
        if original_withheld(document):
            withheld += 1
            continue
        name = (
            f"originaux/{document.created_at:%Y-%m-%d}-{str(document.id)[:8]}-"
            f"{safe_name(document.title)}.{EXTENSIONS.get(document.mime, 'bin')}"
        )
        planned.append(Original(document.id, document.storage_key, name))
    return planned, withheld


class _Sink:
    """Write-only, non-seekable buffer: zipfile writes data descriptors and the stream
    hands out what was written so far."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        self._buffer += data
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return None

    def take(self) -> bytes:
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk


def _put(archive: zipfile.ZipFile, path: str, data: bytes, when: datetime, stored: bool) -> None:
    info = zipfile.ZipInfo(path, date_time=when.timetuple()[:6])
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


async def stream(
    data: Export,
    *,
    store: DocumentStore,
    originals: list[Original] | None,
    withheld: int = 0,
    written: Written | None = None,
) -> AsyncIterator[bytes]:
    """The archive, file by file. `originals` None: not requested."""
    written = written or Written()
    formats = _Formats(data)
    root = root_name(data.exported_at)
    when = data.exported_at.astimezone(formats.zone)
    sink = _Sink()
    files: list[tuple[str, bytes]] = [
        ("lifeos-export.json", data.to_json()),
        ("tasks.csv", formats.tasks_csv()),
        ("transactions.csv", formats.transactions_csv()),
        ("tasks.ics", formats.tasks_ics()),
        ("calendar.ics", formats.calendar_ics()),
        *formats.journal(),
    ]
    with zipfile.ZipFile(cast(IO[bytes], sink), "w") as archive:
        for path, content in files:
            await asyncio.to_thread(_put, archive, f"{root}/{path}", content, when, False)
            yield sink.take()
        for original in originals or []:
            try:
                content = await asyncio.to_thread(
                    store.read, original.storage_key, original.document_id
                )
            except StorageError as exc:
                log.error(
                    "export_original_unreadable", document=str(original.document_id), error=str(exc)
                )
                written.missing.append(str(original.document_id))
                continue
            # Media files are already compressed: stored as they are.
            await asyncio.to_thread(_put, archive, f"{root}/{original.path}", content, when, True)
            written.originals.append(original.path)
            yield sink.take()
        text = readme(data, written, originals=originals is not None, withheld=withheld)
        _put(archive, f"{root}/README.md", text, when, False)
    yield sink.take()
