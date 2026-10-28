"""Bank statement CSV parsing (F04, task 25): pure functions, no database.

A CSV file is untrusted input. It is decoded strictly (UTF-8, else Windows-1252), never
guessed further; amounts are parsed exactly (Decimal, never rounded: a third decimal is
refused); dates follow the format the owner chose for the whole file. Preamble lines
(account number, balance) before the header are dropped and never stored.

French bank exports are the reference: `;` separator, comma decimal, dd/mm/yyyy dates,
debit and credit in one signed column or in two columns.
"""

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from lifeos.modules.documents.sniff import opens_as_markup

ENCODINGS = ("utf-8", "cp1252")
DELIMITERS = (";", "\t", ",")
DATE_FORMATS = ("DD/MM/YYYY", "DD/MM/YY", "YYYY-MM-DD", "DD.MM.YYYY", "DD-MM-YYYY")
# The header is looked for in the first lines only (preamble of a few lines at most).
HEADER_SCAN = 30
MAX_CELL = 500
MAX_COLUMNS = 40
# numeric(14, 2): 12 digits before the decimal separator.
MAX_INTEGER_DIGITS = 12
KEY_VERSION = "v1"
# Characters a spreadsheet reads as the start of a formula (CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@")
MINUS = "\u2212"


class CsvError(Exception):
    """The file cannot be read as a CSV statement; `code` is a stable API code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CellError(ValueError):
    """One cell cannot be read; `reason` is a stable row reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- Decoding and dialect ---------------------------------------------------------------------


def decode(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    """(text, encoding): strict UTF-8 (BOM removed), else Windows-1252. Binary, markup or
    undecodable content is refused (`import_unreadable`)."""
    if b"\x00" in data:
        raise CsvError("import_unreadable")
    candidates = (encoding,) if encoding else ENCODINGS
    for candidate in candidates:
        try:
            text = data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
        text = text.removeprefix("﻿")
        if opens_as_markup(text):
            raise CsvError("import_unreadable")
        printable = sum(
            1 for c in text if c.isprintable() or c in "\n\r\t" or unicodedata.category(c) == "Zs"
        )
        if text and printable / len(text) < 0.97:
            raise CsvError("import_unreadable")
        return text, candidate
    raise CsvError("unsupported_encoding" if encoding else "import_unreadable")


def _rows(text: str, delimiter: str, limit: int | None = None) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"')
    rows: list[list[str]] = []
    try:
        for row in reader:
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    except csv.Error as exc:
        raise CsvError("import_unreadable") from exc
    return rows


def detect_delimiter(text: str) -> str:
    """The separator giving the most lines with the same (>= 2) number of fields; `;` wins
    ties (French exports)."""
    best, best_score = DELIMITERS[0], (0, 0)
    for delimiter in DELIMITERS:
        counts = Counter(len(r) for r in _rows(text, delimiter, 60) if any(c.strip() for c in r))
        if not counts:
            continue
        width, lines = max(counts.items(), key=lambda item: (item[1], item[0]))
        score = (lines, width) if width >= 2 else (0, 0)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _cell(value: str) -> str:
    return " ".join(value.split())[:MAX_CELL]


_DATE_LIKE = re.compile(r"^\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}$")
_NUMBER_LIKE = re.compile("^[+\\-\u2212]?[\\d\\s\u00a0\u202f.,]+(?:\u20ac|EUR)?-?$", re.IGNORECASE)


def _looks_like_header(row: list[str]) -> bool:
    cells = [c for c in (_cell(v) for v in row) if c]
    if len(cells) < 2:
        return False
    textual = [c for c in cells if not _DATE_LIKE.match(c) and not _NUMBER_LIKE.match(c)]
    return len(textual) == len(cells)


def _looks_like_data(row: list[str]) -> bool:
    cells = [_cell(v) for v in row]
    return any(_DATE_LIKE.match(c) for c in cells) and any(
        _NUMBER_LIKE.match(c) and not _DATE_LIKE.match(c) for c in cells if c
    )


@dataclass
class ParsedFile:
    """Header and data rows of a statement (the preamble is dropped)."""

    delimiter: str
    header: list[str]
    # (line number in the file, starting at 1; cells)
    rows: list[tuple[int, list[str]]] = field(default_factory=list)
    preamble_lines: int = 0
    has_header: bool = True


def parse_file(text: str, delimiter: str | None = None) -> ParsedFile:
    """Splits the statement: preamble (dropped), header, data rows."""
    delimiter = delimiter or detect_delimiter(text)
    rows = _rows(text, delimiter)
    counts = Counter(len(r) for r in rows if any(c.strip() for c in r))
    if not counts:
        raise CsvError("import_empty")
    width = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    if width < 2:
        raise CsvError("import_unreadable")
    if width > MAX_COLUMNS:
        raise CsvError("import_unreadable")
    header_at: int | None = None
    first_data: int | None = None
    for index, row in enumerate(rows[:HEADER_SCAN]):
        if len(row) != width or sum(1 for c in row if c.strip()) < 2:
            continue  # preamble: account number, owner, balance...
        if _looks_like_header(row):
            header_at = index
            break
        if _looks_like_data(row):
            first_data = index
            break
    if header_at is not None:
        header = [_cell(c) or f"Colonne {i + 1}" for i, c in enumerate(rows[header_at])]
        start = header_at + 1
    else:
        # No header line: columns are numbered, the first full-width line is data.
        start = first_data if first_data is not None else 0
        header = [f"Colonne {i + 1}" for i in range(width)]
    parsed = ParsedFile(
        delimiter=delimiter,
        header=header,
        preamble_lines=start - (1 if header_at is not None else 0),
        has_header=header_at is not None,
    )
    for index in range(start, len(rows)):
        row = rows[index]
        if not any(c.strip() for c in row):
            continue
        cells = [_cell(c) for c in row]
        # A trailing separator adds empty cells: they are not data.
        while len(cells) > width and cells[-1] == "":
            cells.pop()
        parsed.rows.append((index + 1, cells[:MAX_COLUMNS]))
    return parsed


# --- Column mapping ----------------------------------------------------------------------------


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return " ".join(
        re.findall(r"[a-z0-9]+", "".join(c for c in decomposed if not unicodedata.combining(c)))
    )


_DATE_NAMES = ("date operation", "date d operation", "date", "date comptable", "jour")
_VALUE_DATE = ("date de valeur", "date valeur", "valeur")
# LifeOS' own `transactions.csv` (privacy archive) is recognised too: `date`, `label`,
# `signed_amount`, `currency`, `id` (never its unsigned `amount` column).
_LABEL_NAMES = (
    "libelle",
    "label",
    "libelle operation",
    "libelle de l operation",
    "description",
    "intitule",
    "detail",
    "operation",
    "nature de l operation",
)
_AMOUNT_NAMES = (
    "signed amount",
    "montant",
    "montant eur",
    "montant euros",
    "montant en euros",
    "somme",
)
_DEBIT_NAMES = ("debit", "debit eur", "debit euros", "debits")
_CREDIT_NAMES = ("credit", "credit eur", "credit euros", "credits")
_REFERENCE_NAMES = ("reference", "ref", "numero", "n operation", "identifiant", "id")
_CURRENCY_NAMES = ("devise", "monnaie", "currency")


def _find(header: list[str], names: tuple[str, ...], exclude: set[int]) -> int | None:
    folded = [fold(h) for h in header]
    for name in names:
        for index, column in enumerate(folded):
            if index not in exclude and column == name:
                return index
    for name in names:
        for index, column in enumerate(folded):
            if index not in exclude and column.startswith(name):
                return index
    return None


@dataclass(frozen=True)
class Mapping:
    date_column: int
    label_column: int
    amount_mode: str  # signed | debit_credit
    amount_column: int | None = None
    debit_column: int | None = None
    credit_column: int | None = None
    date_format: str = "DD/MM/YYYY"
    decimal_separator: str = ","
    reference_column: int | None = None
    currency_column: int | None = None
    currency: str = "EUR"


def suggest_mapping(header: list[str], sample: list[list[str]]) -> Mapping | None:
    """A first mapping from French header names (operation date before value date); the
    date format and the decimal separator come from the sample. None if unsure."""
    used: set[int] = set()
    value_date = _find(header, _VALUE_DATE, used)
    if value_date is not None:
        used.add(value_date)
    date_column = _find(header, _DATE_NAMES, used)
    if date_column is None:
        date_column = value_date
    if date_column is None:
        return None
    used.add(date_column)
    label = _find(header, _LABEL_NAMES, used)
    if label is None:
        return None
    used.add(label)
    amount = _find(header, _AMOUNT_NAMES, used)
    debit = _find(header, _DEBIT_NAMES, used)
    credit = _find(header, _CREDIT_NAMES, used)
    reference = _find(header, _REFERENCE_NAMES, used | {c for c in (amount, debit, credit) if c})
    currency = _find(header, _CURRENCY_NAMES, used)
    dates = [row[date_column] for row in sample if date_column < len(row)]
    if amount is not None:
        mode, amount_cells = "signed", [r[amount] for r in sample if amount < len(r)]
    elif debit is not None and credit is not None:
        mode = "debit_credit"
        amount_cells = [r[c] for r in sample for c in (debit, credit) if c < len(r)]
    else:
        return None
    return Mapping(
        date_column=date_column,
        label_column=label,
        amount_mode=mode,
        amount_column=amount if mode == "signed" else None,
        debit_column=debit if mode == "debit_credit" else None,
        credit_column=credit if mode == "debit_credit" else None,
        date_format=guess_date_format(dates),
        decimal_separator=guess_decimal_separator(amount_cells),
        reference_column=reference,
        currency_column=currency,
    )


def guess_date_format(cells: list[str]) -> str:
    """The first format every non-empty sample date parses with (DD/MM/YYYY by default)."""
    values = [c for c in cells if c.strip()]
    for fmt in DATE_FORMATS:
        try:
            for value in values:
                parse_date(value, fmt)
        except CellError:
            continue
        if values:
            return fmt
    return DATE_FORMATS[0]


def guess_decimal_separator(cells: list[str]) -> str:
    """The separator every sample amount parses with (the comma by default)."""
    for separator in (",", "."):
        try:
            for cell in cells:
                parse_csv_amount(cell, separator)
        except CellError:
            continue
        return separator
    return ","


# --- Cells -------------------------------------------------------------------------------------

_DATE_PATTERNS = {
    "DD/MM/YYYY": re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"),
    "DD/MM/YY": re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$"),
    "YYYY-MM-DD": re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"),
    "DD.MM.YYYY": re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"),
    "DD-MM-YYYY": re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"),
}


def parse_date(cell: str, fmt: str) -> date:
    """A calendar date in the chosen format; anything else (or an impossible date) is
    refused, never guessed."""
    pattern = _DATE_PATTERNS.get(fmt)
    value = cell.strip()
    # Some exports add a time after the date: "12/10/2026 00:00".
    value = value.split(" ")[0] if value else value
    match = pattern.match(value) if pattern else None
    if match is None:
        raise CellError("date_invalid")
    first, second, third = (int(g) for g in match.groups())
    if fmt == "YYYY-MM-DD":
        year, month, day = first, second, third
    else:
        day, month, year = first, second, third
        if fmt == "DD/MM/YY":
            year += 2000
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise CellError("date_invalid") from exc


_SPACES = re.compile("[\\s\u00a0\u202f\u2009']")
_CURRENCY_AFFIX = re.compile(r"^(?:€|EUR)|(?:€|EUR)$", re.IGNORECASE)


def parse_csv_amount(cell: str, decimal_separator: str = ",") -> Decimal | None:
    """Signed exact amount of a CSV cell; None for an empty cell.

    Accepts "1 234,56", "1.234,56" (with ","), "-12,50" (hyphen or minus sign), "+3",
    "12,50-", "12,5",
    and a "€" or "EUR" before or after. Refuses more than two decimals (never rounds) and
    more than 12 integer digits (numeric(14, 2)).
    """
    text = cell.strip()
    if not text:
        return None
    text = _CURRENCY_AFFIX.sub("", text).strip()
    text = _CURRENCY_AFFIX.sub("", text).strip()
    negative = False
    if text[:1] in ("-", MINUS, "+"):
        negative = text[0] != "+"
        text = text[1:].strip()
    elif text[-1:] in ("-", MINUS):
        negative = True
        text = text[:-1].strip()
    text = _CURRENCY_AFFIX.sub("", text).strip()
    text = _SPACES.sub("", text)
    if not text:
        raise CellError("amount_invalid")
    thousands = "." if decimal_separator == "," else ","
    grouped = re.fullmatch(
        rf"(\d{{1,3}}(?:{re.escape(thousands)}\d{{3}})+|\d+)(?:{re.escape(decimal_separator)}(\d+))?",
        text,
    )
    if grouped is None:
        raise CellError("amount_invalid")
    whole, fraction = grouped.group(1).replace(thousands, ""), grouped.group(2) or ""
    if len(fraction) > 2:
        raise CellError("amount_precision")
    whole = whole.lstrip("0") or "0"
    if len(whole) > MAX_INTEGER_DIGITS:
        raise CellError("amount_too_large")
    try:
        amount = Decimal(f"{whole}.{fraction.ljust(2, '0')}")
    except InvalidOperation as exc:  # pragma: no cover - the pattern only lets digits in
        raise CellError("amount_invalid") from exc
    return -amount if negative else amount


# Bank prefixes before the merchant name: card payments, direct debits, transfers, cash.
_BANK_PREFIX = re.compile(
    r"^(?:(?:paiement|achat|pmt)\s+(?:par\s+)?(?:cb|carte)|cb|carte(?:\s+x?\d{4})?|"
    r"prlv(?:\s+sepa)?|prelevement(?:\s+sepa)?|prélèvement(?:\s+sepa)?|"
    r"vir(?:\s+sepa)?(?:\s+(?:recu|emis|inst))?|virement(?:\s+sepa)?(?:\s+(?:recu|emis))?(?:\s+de)?|"
    r"retrait(?:\s+dab)?|dab|ech(?:\s+pret)?)\b[\s*:.-]*",
    re.IGNORECASE,
)
_CARD_DATE = re.compile(r"\b\d{2}[/.]\d{2}(?:[/.]\d{2,4})?\b")
_CARD_NUMBER = re.compile(r"\b(?:x+\d{2,4}|\d{4,}|\*+\d{2,4})\b", re.IGNORECASE)


def clean_bank_label(label: str) -> str | None:
    """The merchant inside a bank label: « CB CARREFOUR MARKET 12/10 » -> « CARREFOUR MARKET »,
    « PRLV SEPA EDF » -> « EDF ». None when nothing is left."""
    text = " ".join(label.split())
    for _ in range(2):
        text = _BANK_PREFIX.sub("", text).strip()
    text = _CARD_DATE.sub(" ", text)
    text = _CARD_NUMBER.sub(" ", text)
    text = " ".join(text.split()).strip(" -*:.")
    return text[:120] or None


def import_key(
    *,
    account: str | None,
    tx_date: date,
    direction: str,
    amount: Decimal,
    currency: str,
    label: str | None,
    reference: str | None,
    occurrence: int,
) -> str:
    """Stable identity of one bank operation, so that importing it again is detected.

    A bank reference identifies the operation alone; without one, the occurrence index
    (the n-th identical operation of that day in the file) tells two identical coffees
    apart, and stays the same when an overlapping export covers the whole day again.
    """
    parts = [
        KEY_VERSION,
        fold(account or ""),
        tx_date.isoformat(),
        direction,
        f"{amount:.2f}",
        currency,
        fold(label or ""),
        f"ref:{fold(reference)}" if reference and fold(reference) else f"n:{occurrence}",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# --- Rows --------------------------------------------------------------------------------------


@dataclass
class RowReading:
    """What one data row says under a mapping (or why it cannot be imported)."""

    line_no: int
    tx_date: date | None = None
    amount: Decimal | None = None  # positive; the sign is `direction`
    direction: str | None = None
    currency: str | None = None
    label: str | None = None
    reference: str | None = None
    reason: str | None = None


def _get(cells: list[str], column: int | None) -> str:
    if column is None or column >= len(cells):
        return ""
    return cells[column]


def read_row(line_no: int, cells: list[str], mapping: Mapping, width: int) -> RowReading:
    reading = RowReading(line_no=line_no)
    if len(cells) != width:
        reading.reason = "column_count"
        return reading
    try:
        reading.tx_date = parse_date(_get(cells, mapping.date_column), mapping.date_format)
        if mapping.amount_mode == "signed":
            signed = parse_csv_amount(_get(cells, mapping.amount_column), mapping.decimal_separator)
            if signed is None:
                raise CellError("amount_missing")
        else:
            debit = parse_csv_amount(_get(cells, mapping.debit_column), mapping.decimal_separator)
            credit = parse_csv_amount(_get(cells, mapping.credit_column), mapping.decimal_separator)
            debit = abs(debit) if debit else None
            credit = abs(credit) if credit else None
            if debit and credit:
                raise CellError("amount_ambiguous")
            if debit:
                signed = -debit
            elif credit:
                signed = credit
            else:
                raise CellError("amount_missing")
        if signed == 0:
            raise CellError("amount_zero")
        currency = _get(cells, mapping.currency_column).strip().upper() or mapping.currency
        if currency == "€":
            currency = "EUR"
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise CellError("currency_invalid")
    except CellError as error:
        reading.reason = error.reason
        return reading
    reading.amount = abs(signed)
    reading.direction = "expense" if signed < 0 else "income"
    reading.currency = currency
    label = " ".join(_get(cells, mapping.label_column).split())
    # A spreadsheet-safe export writes « '=… » for a text starting like a formula.
    if label[:1] == "'" and label[1:2] in FORMULA_PREFIXES:
        label = label[1:]
    reading.label = label[:200] or None
    reference = _get(cells, mapping.reference_column).strip()
    reading.reference = reference[:120] or None
    return reading
