"""French receipt parsing from OCR text, locally (UC02).

Heuristic and explainable: every field comes with the line it was read from, and an
overall confidence decides how insistently the owner is asked to check. Nothing is
recorded without the owner's validation.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

# Amount: 1 234,56 / 1234.56 / 12,5 ; optional € or EUR.
# The integer part is bounded: a longer number is not an amount on a receipt.
_AMOUNT = r"(?<![\d,.])(\d{1,3}(?:[ \u00a0.]\d{3}){0,3}|\d{1,12})[,.](\d{1,2})(?!\d)"
_AMOUNT_RE = re.compile(_AMOUNT)
# OCR often merges words ("TOTALTTC", "NETAPAYER"): spaces are optional.
_TOTAL_WORDS = re.compile(r"\b(total|montant|somme\s*due|net\s*a\s*payer|a\s*payer)")
# Payment lines: the amount paid by card equals the total, but cash tendered may exceed it.
_PAYMENT_WORDS = re.compile(r"\b(carte bancaire|cb|visa|mastercard|especes|paiement)\b")
# Lines that look like a total but are not the amount paid.
_NOT_TOTAL = re.compile(r"\b(sous.?total|total\s*ht|ht\b|tva|remise|economie|rendu|fidelite)")
_STRONG_TOTAL = re.compile(
    r"\b(net\s*a\s*payer|total\s*ttc|total\s*a\s*payer|montant\s*ttc|a\s*payer)"
)
_DATE_RE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})\b")
_VAT_RE = re.compile(r"\btva\b\D{0,12}(\d{1,2}(?:[,.]\d{1,2})?)\s*%")
_SKIP_MERCHANT = re.compile(
    r"^(ticket|facture|bienvenue|merci|caisse|tel|tél|siret|siren|tva|www\.|http|\d)", re.I
)


@dataclass
class ReceiptField[T]:
    value: T
    line: str


@dataclass
class ReceiptProposal:
    merchant: ReceiptField[str] | None = None
    tx_date: ReceiptField[date] | None = None
    total: ReceiptField[Decimal] | None = None
    vat_rates: list[str] = field(default_factory=list)
    currency: str = "EUR"
    card_payment: bool = False
    # 0..1: how much the parse can be trusted (the owner checks anyway).
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "merchant": self.merchant.value if self.merchant else None,
            "tx_date": self.tx_date.value.isoformat() if self.tx_date else None,
            "total": str(self.total.value) if self.total else None,
            "currency": self.currency,
            "vat_rates": self.vat_rates,
            "card_payment": self.card_payment,
            "confidence": round(self.confidence, 2),
            "warnings": self.warnings,
            "lines": {
                "merchant": self.merchant.line if self.merchant else None,
                "tx_date": self.tx_date.line if self.tx_date else None,
                "total": self.total.line if self.total else None,
            },
        }


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def parse_amount(text: str) -> Decimal | None:
    matches = list(_AMOUNT_RE.finditer(text))
    if not matches:
        return None
    integer, cents = matches[-1].groups()  # the rightmost amount on the line
    try:
        value = Decimal(re.sub(r"[ \u00a0.]", "", integer) + "." + cents.ljust(2, "0"))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _date(lines: list[str], today: date) -> ReceiptField[date] | None:
    for line in lines:
        for match in _DATE_RE.finditer(line):
            day, month, year = (int(g) for g in match.groups())
            if year < 100:
                year += 2000
            try:
                found = date(year, month, day)
            except ValueError:
                continue
            # A receipt is from the past, and rarely older than a few years.
            if found <= today and found.year >= today.year - 5:
                return ReceiptField(found, line.strip())
    return None


def _total(lines: list[str]) -> tuple[ReceiptField[Decimal] | None, bool]:
    """(total, found on a strong « net à payer / total TTC » line)."""
    candidates: list[tuple[int, int, Decimal, str]] = []
    for index, line in enumerate(lines):
        normalised = _norm(line)
        if _NOT_TOTAL.search(normalised):
            continue
        if _TOTAL_WORDS.search(normalised):
            strength = 2 if _STRONG_TOTAL.search(normalised) else 1
        elif _PAYMENT_WORDS.search(normalised):
            strength = 0  # only when no total line is readable
        else:
            continue
        amount = parse_amount(line)
        if amount is None and index + 1 < len(lines):
            # "TOTAL" alone on its line, the amount on the next one.
            amount = parse_amount(lines[index + 1])
        if amount is None:
            continue
        candidates.append((strength, index, amount, line.strip()))
    if not candidates:
        return None, False
    # Strongest wording first, then the largest amount (a total is >= its parts).
    strength, _, amount, line = max(candidates, key=lambda c: (c[0], c[2]))
    return ReceiptField(amount, line), strength == 2


def _merchant(lines: list[str]) -> ReceiptField[str] | None:
    for line in lines[:6]:
        cleaned = line.strip(" *-=_#.:")
        letters = sum(c.isalpha() for c in cleaned)
        if len(cleaned) < 3 or letters < 3 or _SKIP_MERCHANT.match(cleaned):
            continue
        if parse_amount(cleaned) is not None or _DATE_RE.search(cleaned):
            continue
        return ReceiptField(" ".join(cleaned.split())[:80], line.strip())
    return None


def parse_receipt(text: str, today: date) -> ReceiptProposal:
    lines = [line for line in (raw.strip() for raw in text.splitlines()) if line]
    proposal = ReceiptProposal()
    proposal.merchant = _merchant(lines)
    proposal.tx_date = _date(lines, today)
    proposal.total, strong = _total(lines)
    normalised = _norm(text)
    proposal.vat_rates = sorted(
        {m.group(1).replace(".", ",") for m in _VAT_RE.finditer(normalised)}
    )
    proposal.card_payment = bool(re.search(r"\b(cb|carte bancaire|visa|mastercard)\b", normalised))
    if re.search(r"\b(usd|\$)\b|\$", text) and "eur" not in normalised and "€" not in text:
        proposal.currency = "USD"
        proposal.warnings.append("Devise autre que l'euro : à vérifier.")

    score = 0.0
    if proposal.total:
        score += 0.5 if strong else 0.35
    else:
        proposal.warnings.append("Montant total non trouvé.")
    if proposal.tx_date:
        score += 0.25
    else:
        proposal.warnings.append("Date non trouvée.")
    if proposal.merchant:
        score += 0.25
    else:
        proposal.warnings.append("Commerçant non identifié.")
    proposal.confidence = score
    return proposal
