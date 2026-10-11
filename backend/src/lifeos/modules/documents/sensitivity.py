"""Local detection of sensitive documents (F10: « extraction locale préalable des
informations sensibles »).

Deterministic and explainable: weighted signals per category, a category is retained
above a threshold. Strong signals (MRZ line, valid IBAN, NIR, valid card number) decide
alone. A sensitive document's content is never sent to the cloud (politique de données
§3); detection errs on the side of caution. Reasons never contain the sensitive values.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from lifeos.modules.guardian.privacy import _CARD, _IBAN, _NIR, _iban_ok, _luhn_ok

THRESHOLD = 3

# MRZ (machine-readable zone) of passports and identity cards.
_MRZ = re.compile(r"^[A-Z0-9<]{28,44}$", re.MULTILINE)
_BIC = re.compile(r"\b(?:bic|swift)\b\s*:?\s*[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", re.I)

# (pattern on normalised text, weight, label)
_SIGNALS: dict[str, list[tuple[str, int, str]]] = {
    "identity": [
        (r"carte nationale d.?identite", 3, "carte d'identité"),
        (r"\bpasseport\b|\bpassport\b", 3, "passeport"),
        (r"titre de sejour|residence permit", 3, "titre de séjour"),
        (r"permis de conduire|driving licen[cs]e", 3, "permis de conduire"),
        (r"date de naissance|ne\(e\) le|nee? le \d", 1, "date de naissance"),
        (r"lieu de naissance", 1, "lieu de naissance"),
        (r"nationalite", 1, "nationalité"),
        (r"republique francaise", 1, "République française"),
    ],
    "health": [
        (r"\bordonnance\b", 3, "ordonnance"),
        (r"\bposologie\b|\bcomprimes?\b.*\bpar jour\b", 2, "posologie"),
        (r"carte vitale|assurance maladie|\bcpam\b|\bameli\b", 2, "assurance maladie"),
        (r"\bdiagnostic\b|\bpathologie\b|\bantecedents\b", 2, "diagnostic"),
        (
            r"compte.rendu (?:operatoire|medical|d.hospitalisation|de consultation)",
            3,
            "compte rendu",
        ),
        (r"\bmedecin\b|\bdocteur\b|\bdr\.? [a-z]", 1, "médecin"),
        (r"\bhopital\b|\bclinique\b|\burgences\b", 1, "établissement de santé"),
        (r"\bmutuelle\b|\btiers payant\b", 1, "mutuelle"),
        (r"resultats? d.analyses?|\bbilan sanguin\b", 2, "analyses"),
        (r"\bglycemie\b|\bcholesterol\b|\bhemoglobine\b|\bcreatinine\b", 2, "biologie"),
    ],
    "bank": [
        (r"releve d.identite bancaire|\brib\b", 3, "RIB"),
        (r"releve de compte|releve bancaire|extrait de compte", 3, "relevé de compte"),
        (r"\bsolde\b.*\b(crediteur|debiteur)\b|\bdecouvert autorise\b", 2, "solde"),
        (r"\bnumero de compte\b|\bcode banque\b|\bcode guichet\b", 2, "coordonnées bancaires"),
        (r"\bvirement\b|\bprelevement\b", 1, "opérations bancaires"),
    ],
}
_COMPILED = {
    category: [(re.compile(p), w, label) for p, w, label in signals]
    for category, signals in _SIGNALS.items()
}


@dataclass
class Sensitivity:
    categories: list[str] = field(default_factory=list)
    # Category -> human-readable reasons (never the detected values).
    reasons: dict[str, list[str]] = field(default_factory=dict)

    @property
    def sensitive(self) -> bool:
        return bool(self.categories)


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def classify(text: str) -> Sensitivity:
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    def add(category: str, weight: int, reason: str) -> None:
        scores[category] = scores.get(category, 0) + weight
        if reason not in reasons.setdefault(category, []):
            reasons[category].append(reason)

    upper_lines = "\n".join(line.strip().replace(" ", "") for line in text.upper().splitlines())
    for line in _MRZ.findall(upper_lines):
        if line.count("<") >= 3:
            add("identity", THRESHOLD, "bande MRZ")
            break
    if any(_iban_ok(m.group(0)) for m in _IBAN.finditer(text.upper())):
        add("bank", THRESHOLD, "IBAN")
    if _BIC.search(text):
        add("bank", 2, "BIC")
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            add("bank", THRESHOLD, "numéro de carte")
            break
    if _NIR.search(text):
        # A social security number: health (carte Vitale, soins) or identity.
        add("health", THRESHOLD, "numéro de sécurité sociale")

    normalised = _normalise(text)
    for category, signals in _COMPILED.items():
        for pattern, weight, label in signals:
            if pattern.search(normalised):
                add(category, weight, label)

    kept = [c for c in ("identity", "health", "bank") if scores.get(c, 0) >= THRESHOLD]
    return Sensitivity(categories=kept, reasons={c: reasons[c] for c in kept})
