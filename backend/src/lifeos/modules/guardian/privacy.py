"""Privacy Engine: local masking before any cloud transmission (politique de données §3).

Detection is best effort: it reduces exposure, it is not a guarantee. Consent per space
and minimal context packets remain the primary safeguards.
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
# French social security number (NIR), with optional spaces.
_NIR = re.compile(
    r"\b[12][ ]?\d{2}[ ]?(?:0[1-9]|1[0-2]|[2-9]\d)[ ]?(?:\d{2}|2A|2B)"
    r"[ ]?\d{3}[ ]?\d{3}(?:[ ]?\d{2})?\b"
)
_PHONE = re.compile(r"(?<![\w+])(?:\+33[ .-]?|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)|\+\d{8,14}\b")
_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|token|password|mot de passe|mdp|secret)\b(\s*[:=]\s*)(\S+)"
)


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
    compact = value.replace(" ", "")
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


@dataclass
class MaskResult:
    text: str
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def mask(text: str) -> MaskResult:
    counts: Counter[str] = Counter()

    def sub(
        pattern: re.Pattern[str], label: str, check: Callable[[str], bool] | None = None
    ) -> None:
        nonlocal text

        def repl(match: re.Match[str]) -> str:
            if check is not None and not check(match.group(0)):
                return match.group(0)
            counts[label] += 1
            return f"[{label}]"

        text = pattern.sub(repl, text)

    def secret_repl(match: re.Match[str]) -> str:
        counts["SECRET"] += 1
        return f"{match.group(1)}{match.group(2)}[SECRET]"

    text = _SECRET.sub(secret_repl, text)
    sub(_EMAIL, "EMAIL")
    sub(_IBAN, "IBAN", _iban_ok)
    sub(_NIR, "NIR")
    sub(_CARD, "CARTE", lambda m: _luhn_ok(re.sub(r"[ -]", "", m)))
    sub(_PHONE, "TÉLÉPHONE")
    return MaskResult(text=text, counts=counts)
