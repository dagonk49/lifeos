"""Privacy Engine: local masking before any cloud transmission (politique de données §3).

Detection is best effort: it reduces exposure, it is not a guarantee. Consent per space
and minimal context packets remain the primary safeguards.

Identifiers are recognised in the forms found in real French documents: grouped with
spaces, non-breaking or thin spaces, dots or dashes, in lower case, and read by OCR with
the usual letter/digit confusions (O for 0, l or I for 1). Only the identifiers are
replaced: the rest of the text is sent as it was written.
"""

import bisect
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

# Separators found inside grouped identifiers: space, non-breaking space (U+00A0), narrow
# no-break space (U+202F, French typography), figure space (U+2007), thin space (U+2009),
# dot and dash.
_SPACES = " \u00a0\u202f\u2007\u2009"
_SEP = rf"[{_SPACES}.\-]"
_GAP = f"{_SEP}?"

# Placeholder label -> stable code used by the API (the web client translates codes).
MASK_KINDS: dict[str, str] = {
    "SECRET": "secret",
    "EMAIL": "email",
    "IBAN": "iban",
    "NIR": "nir",
    "CARTE": "card",
    "PASSEPORT": "passport",
    "IDENTITÉ": "id_number",
    "FISCAL": "tax_number",
    "TÉLÉPHONE": "phone",
}

# E-mail addresses, also written to escape robots: « jean . dupont @ example . fr »,
# « jean.dupont(at)example.fr », « jean.dupont [arobase] example [point] fr ». Spaces are
# accepted around a dot or an at sign only on both sides (a sentence ending in « .  »
# is not an address).
_DOT = r"(?:\.|\s\.\s|\s?[(\[]\s?(?:dot|point)\s?[)\]]\s?)"
_AT = r"(?:@|\s@\s|\s?[(\[{]\s?(?:at|arobase)\s?[)\]}]\s?|\sarobase\s)"
# Accented letters are accepted (« prénom.nom@… »: better masked than sent). Bounded
# repetitions (RFC 5321 sizes, a few dotted parts): an unbounded chain such as
# « a . a . a . … » would be rescanned from every position, in quadratic time.
_EMAIL = re.compile(
    rf"(?i)(?<![\w.%+-])[\w%+-]{{1,64}}(?:{_DOT}[\w%+-]{{1,64}}){{0,6}}{_AT}"
    rf"[\w-]{{1,63}}(?:{_DOT}[\w-]{{1,63}}){{0,6}}{_DOT}[a-z]{{2,24}}\b"
)
_IBAN = re.compile(
    rf"(?i)\b[A-Z]{{2}}\d{{2}}(?:{_GAP}[A-Z0-9]{{4}}){{2,7}}(?:{_GAP}[A-Z0-9]{{1,4}})?\b"
)
_CARD = re.compile(rf"\b(?:\d{_GAP}){{12,18}}\d\b")
# French social security number (NIR): sex, year, month (or 20-99 for unknown months),
# department (2A/2B for Corsica), commune, order, then the optional key.
_NIR = re.compile(
    rf"\b[12]{_GAP}\d{{2}}{_GAP}(?:0[1-9]|1[0-2]|[2-9]\d){_GAP}(?:\d{{2}}|2A|2B)"
    rf"{_GAP}\d{{3}}{_GAP}\d{{3}}(?:{_GAP}\d{{2}})?\b"
)
# French numbers (+33, 0033, +33 (0), (+33), 0) and other international numbers.
_PHONE = re.compile(
    rf"(?<![\w+])(?:(?:\+|00)33{_GAP}(?:\(0\){_GAP})?|\(\+33\){_GAP}|0)[1-9](?:{_GAP}\d{{2}}){{4}}(?!\d)"
    rf"|(?<![\w+])\+\d{{8,14}}\b"
    rf"|(?<![\w+])\+\d{{1,3}}(?:{_SEP}\(?\d{{1,4}}\)?){{2,6}}(?!\d)"
)
# A secret announced by its name. With « : » or « = », the next word is the secret; without
# (« mot de passe hunter2 », « code PIN 1234 »), only a word that looks like one (a digit
# or a symbol in it), so that « mot de passe oublié » stays readable.
_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|cl[ée] (?:d.)?api|token|jeton|password|passwd|passphrase"
    r"|mot de passe|mdp|secret|code (?:pin|secret|confidentiel|d.acc[eè]s))\b"
    r"(?:\s*[:=]\s*(?P<value>\S+)|\s+(?:est\s+)?(?P<bare>\S+))"
)
# Numbers of identity documents and tax identifiers: only right after the name of the
# document (they have no checksum, a bare 9 to 13 character code is too common).
_ID_CONTEXT = re.compile(
    r"(?i)\b(?:(?P<passport>passeport|passport)"
    r"|(?P<identity>carte nationale d.identit[ée]|carte d.identit[ée]|\bcni\b"
    r"|titre de s[ée]jour|permis de conduire|pi[èe]ce d.identit[ée])"
    r"|(?P<tax>num[ée]ro fiscal|n[°o]\s?fiscal|identifiant fiscal|\bspi\b"
    r"|r[ée]f[ée]rence de l.avis))\b"
)
# A number: groups of letters and digits, each with a digit (« 99XX99999 », « 30 99 999 999
# 999 », « 26 99 A12 345 678 »), 8 to 20 characters with at least 2 digits (`_id_value_ok`).
_ID_GROUP = r"(?=[A-Z]*\d)[0-9A-Z]{1,13}"
_ID_VALUE = re.compile(rf"(?<![\w]){_ID_GROUP}(?:{_SEP}{_ID_GROUP}){{0,6}}(?![\w])", re.IGNORECASE)
# The number follows the document name within a few words (« N° : », « numéro »...).
_ID_WINDOW = 48

# OCR confusions inside digit groups: « 3OOO 6OOO » is « 3000 6000 ».
_DIGIT_RUN = re.compile(r"[0-9OolI|]{2,}")
_DIGIT_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})


def ocr_digits(text: str) -> str:
    """The text with O/o read as 0 and l/I/| read as 1 inside digit groups.

    Same length as `text` (one character for one): positions found in the result are
    valid in the original.
    """

    def fold(match: re.Match[str]) -> str:
        run = match.group(0)
        if not any(c.isdigit() for c in run):
            return run
        return run.translate(_DIGIT_LOOKALIKES)

    return _DIGIT_RUN.sub(fold, text)


def _compact(value: str) -> str:
    return re.sub(rf"[{_SPACES}.\-]", "", value).upper()


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, char in enumerate(reversed(digits)):
        n = int(char)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _iban_ok(value: str) -> bool:
    compact = _compact(value)
    if not 15 <= len(compact) <= 34 or not (compact.isascii() and compact.isalnum()):
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


# A check returns the length of the accepted match (None: rejected).
Check = Callable[[str], int | None]
_GROUP_END = re.compile(rf"{_SEP}(?=[A-Z0-9]+$)", re.IGNORECASE)


def _iban_length(value: str) -> int | None:
    """The valid IBAN at the start of the match: the regex may have taken the next
    group of characters too (« BE68 5390 0754 7034 2026 »)."""
    while True:
        if _iban_ok(value):
            return len(value)
        cut = [m.start() for m in _GROUP_END.finditer(value)]
        if not cut:
            return None
        value = value[: cut[-1]]


def _card_length(value: str) -> int | None:
    digits = re.sub(r"\D", "", value)
    return len(value) if 13 <= len(digits) <= 19 and _luhn_ok(digits) else None


# A company's SIRET (14 digits) passes the Luhn check too: not a card when announced.
_COMPANY_NUMBER = re.compile(r"(?i)\b(?:siret|siren)\b\W{0,5}$")


def _phone_length(value: str) -> int | None:
    return len(value) if 8 <= sum(c.isdigit() for c in value) <= 15 else None


_APOSTROPHES = "'\u2019-"
_TRAILING = ".,;!?)»\"'\u2019"


def _looks_secret(value: str) -> bool:
    """Without « : », only a value that looks like a secret: a digit or a symbol in it
    (« hunter2 », « 1234 »), not an ordinary word (« oublié », « l'ancien »)."""
    return any(c.isdigit() or not (c.isalpha() or c in _APOSTROPHES) for c in value)


@dataclass(frozen=True)
class Found:
    label: str
    start: int
    end: int


# Priority order: on overlapping candidates, the first label wins.
_PATTERNS: list[tuple[str, re.Pattern[str], Check | None]] = [
    ("EMAIL", _EMAIL, None),
    ("IBAN", _IBAN, _iban_length),
    ("NIR", _NIR, None),
    ("CARTE", _CARD, _card_length),
]


def _secrets(text: str) -> list[Found]:
    found = []
    for match in _SECRET.finditer(text):
        if match.group("value") is not None:
            # Announced: the whole next word, punctuation included (it may be part of it).
            found.append(Found("SECRET", match.start("value"), match.end("value")))
            continue
        # Not announced: a final full stop or comma ends the sentence.
        bare = match.group("bare").rstrip(_TRAILING)
        if bare and _looks_secret(bare):
            start = match.start("bare")
            found.append(Found("SECRET", start, start + len(bare)))
    return found


def _ocr_word(group: str) -> bool:
    """A word OCR read with a 1 or a 0 for a letter (« REPUBL1QUE »), not a code."""
    digits = [c for c in group if c.isdigit()]
    return bool(digits) and set(digits) <= {"0", "1"} and len(group) - len(digits) > 4 * len(digits)


def _id_value_ok(value: str) -> bool:
    groups = re.split(_SEP, value)
    if any(_ocr_word(group) for group in groups):
        return False
    compact = "".join(groups)
    return 8 <= len(compact) <= 20 and sum(c.isdigit() for c in compact) >= 2


def _identity_numbers(text: str) -> list[Found]:
    found = []
    for context in _ID_CONTEXT.finditer(text):
        if context.group("passport"):
            label = "PASSEPORT"
        elif context.group("identity"):
            label = "IDENTITÉ"
        else:
            label = "FISCAL"
        # A few words further, on the same line or the next one (scanned forms put the
        # label above the value).
        window = "\n".join(text[context.end() : context.end() + _ID_WINDOW].split("\n")[:2])
        for value in _ID_VALUE.finditer(window):
            if _id_value_ok(value.group(0)):
                start = context.end() + value.start()
                found.append(Found(label, start, start + len(value.group(0))))
                break
    return found


def _matches(label: str, pattern: re.Pattern[str], check: Check | None, text: str) -> list[Found]:
    found = []
    for match in pattern.finditer(text):
        length = len(match.group(0)) if check is None else check(match.group(0))
        if not length:
            continue
        if label == "CARTE" and _COMPANY_NUMBER.search(
            text[max(0, match.start() - 12) : match.start()]
        ):
            continue
        found.append(Found(label, match.start(), match.start() + length))
    return found


def detect(text: str, *, resolve: bool = True) -> list[Found]:
    """Identifiers in `text`, as written and as OCR may have misread them.

    `resolve`: drop the candidates overlapping an earlier (higher priority) one, as
    masking does; otherwise every valid candidate is returned (a « mot de passe :
    FR76… » is both a secret and an IBAN).
    """
    variants = [text]
    folded = ocr_digits(text)
    if folded != text:
        variants.append(folded)
    candidates: list[Found] = []
    for variant in variants:
        candidates.extend(_secrets(variant))
    for label, pattern, check in _PATTERNS:
        for variant in variants:
            candidates.extend(_matches(label, pattern, check, variant))
    # Document numbers come before phone numbers (a 10-digit residence permit number).
    for variant in variants:
        candidates.extend(_identity_numbers(variant))
    for variant in variants:
        candidates.extend(_matches("TÉLÉPHONE", _PHONE, _phone_length, variant))
    if not resolve:
        return candidates
    starts: list[int] = []  # sorted, non-overlapping kept spans
    kept: list[Found] = []
    for candidate in candidates:
        index = bisect.bisect_right(starts, candidate.start)
        before = kept[index - 1] if index > 0 else None
        after = kept[index] if index < len(kept) else None
        if (before is None or before.end <= candidate.start) and (
            after is None or candidate.end <= after.start
        ):
            starts.insert(index, candidate.start)
            kept.insert(index, candidate)
    return kept


@dataclass
class MaskResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def kinds(self) -> dict[str, int]:
        """Counts by stable code (`MASK_KINDS`), for the API: never the values."""
        return {MASK_KINDS[label]: count for label, count in self.counts.items()}


def mask(text: str) -> MaskResult:
    """Replaces each identifier by its label in brackets (« [IBAN] »)."""
    counts: Counter[str] = Counter()
    parts: list[str] = []
    position = 0
    for found in detect(text):
        parts.append(text[position : found.start])
        parts.append(f"[{found.label}]")
        counts[found.label] += 1
        position = found.end
    parts.append(text[position:])
    return MaskResult(text="".join(parts), counts=counts)
