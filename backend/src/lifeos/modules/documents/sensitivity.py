"""Local detection of sensitive documents (F10: « extraction locale préalable des
informations sensibles »).

Deterministic and explainable: weighted signals per category, a category is retained
above a threshold. Strong signals (MRZ line, valid IBAN, NIR, valid card number) decide
alone. A sensitive document's content is never sent to the cloud (politique de données
§3); detection errs on the side of caution. Reasons never contain the sensitive values.

Identifiers are found by the Privacy Engine (`guardian.privacy.detect`), in all the
forms it knows (spaces, non-breaking spaces, dots, dashes, OCR confusions). Keywords are
also looked for as OCR may have read them: « 0RD0NNANCE », « CARTE NATI0NALE ».

The owner can add protection (`Document.local_only`, « Toujours garder en local ») but
cannot remove a category found here: correcting the text runs the detection again.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from lifeos.modules.documents.models import Document
from lifeos.modules.guardian.privacy import detect

THRESHOLD = 3
# Version of the rules below: documents classified by an older version are classified
# again in the background (`processing.reclassify_documents`). Bump it on every change
# that can find more.
CLASSIFIER_VERSION = 2

# MRZ (machine-readable zone) of passports and identity cards.
_MRZ = re.compile(r"^[A-Z0-9<]{28,44}$", re.MULTILINE)
# OCR often reads the MRZ filler « < » as « « » or « K ».
_MRZ_FILLERS = str.maketrans({"\u00ab": "<", "\u2039": "<"})
_BIC = re.compile(r"\b(?:bic|swift)\b\s*:?\s*[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b", re.I)
# Identifier found by the Privacy Engine -> (category, reason).
_IDENTIFIERS = {
    "IBAN": ("bank", "IBAN"),
    "CARTE": ("bank", "numéro de carte"),
    # A social security number: health (carte Vitale, soins) or identity.
    "NIR": ("health", "numéro de sécurité sociale"),
    "PASSEPORT": ("identity", "numéro de passeport"),
    "IDENTITÉ": ("identity", "numéro de pièce d'identité"),
    "FISCAL": ("identity", "numéro fiscal"),
}

# (pattern on normalised text, weight, label). Health and bank documents rarely say what
# they are: several weak signals add up, and an ambiguous document is treated as sensitive.
_SIGNALS: dict[str, list[tuple[str, int, str]]] = {
    "identity": [
        (r"carte nationale d.?identite", 3, "carte d'identité"),
        (r"\bpasseport\b|\bpassport\b", 3, "passeport"),
        (r"titre de sejour|residence permit", 3, "titre de séjour"),
        (r"permis de conduire|driving licen[cs]e", 3, "permis de conduire"),
        (r"acte de (?:naissance|mariage|deces)|livret de famille", 3, "état civil"),
        (r"certificat d.immatriculation|carte grise", 3, "carte grise"),
        (
            r"avis d.(?:impot|imposition)|avis de (?:taxe|situation declarative)"
            r"|taxe fonciere|taxe d.habitation|numero fiscal|revenu fiscal de reference"
            r"|declaration (?:des|de) revenus|impots\.gouv",
            3,
            "fiscal",
        ),
        (r"date de naissance|ne\(e\) le|nee? le \d", 1, "date de naissance"),
        (r"lieu de naissance", 1, "lieu de naissance"),
        (r"nationalite", 1, "nationalité"),
        (r"republique francaise", 1, "République française"),
        (r"\bprefecture\b|\bants\b", 1, "administration"),
    ],
    "health": [
        (r"\bordonnance\b", 3, "ordonnance"),
        (r"certificat medical|arret de travail|avis d.arret", 3, "certificat ou arrêt"),
        (
            r"compte.rendu (?:operatoire|medical|d.hospitalisation|de consultation"
            r"|d.echographie|d.imagerie|de radiologie|d.irm|d.examen|de biologie)",
            3,
            "compte rendu",
        ),
        (
            r"decompte (?:de |des )?(?:mutuelle|remboursements?|prestations|soins)"
            r"|releve (?:de |des )?(?:remboursements|prestations)",
            3,
            "remboursement de soins",
        ),
        (r"\bposologie\b|\bcomprimes?\b.*\bpar jour\b", 2, "posologie"),
        (r"carte vitale|assurance maladie|\bcpam\b|\bameli\b", 2, "assurance maladie"),
        (r"attestation de droits|affection de longue duree|\bald\b", 2, "droits santé"),
        (r"\bdiagnostic\b|\bpathologie\b|\bantecedents\b", 2, "diagnostic"),
        (r"\brpps\b|\badeli\b|\bprescripteur\b|\bprescrit\b", 2, "prescription"),
        (
            r"\bechographie\b|\bradiographie\b|\bmammographie\b|\bscintigraphie\b|\birm\b"
            r"|imagerie medicale",
            2,
            "imagerie",
        ),
        (r"psychiatr|psycholog|idees? suicidaires?|suicid", 2, "santé mentale"),
        (r"hospitalis|centre hospitalier|\bchu\b", 2, "hospitalisation"),
        (r"resultats? d.analyses?|\bbilan sanguin\b|biologie medicale", 2, "analyses"),
        (
            r"part (?:secu|securite sociale|obligatoire|complementaire|mutuelle)"
            r"|base de remboursement|reste a charge|ticket moderateur",
            2,
            "remboursement de soins",
        ),
        (r"\bmedecin\b|\bdocteur\b|\bdr\.? [a-z]", 1, "médecin"),
        (r"\bhopital\b|\bclinique\b|\burgences\b", 1, "établissement de santé"),
        (r"\bmutuelle\b|\btiers payant\b", 1, "mutuelle"),
        (r"\bpatiente?\b", 1, "patient"),
        (r"\b\d+(?:[.,]\d+)? ?(?:mg|µg|mcg|ui)\b", 1, "dosage"),
        (r"\b(?:comprimes?|gelules?|sachets?|ampoules?|injections?)\b", 1, "traitement"),
    ],
    "bank": [
        (r"releve d.identite bancaire|\brib\b", 3, "RIB"),
        (
            r"releve (?:de |d.)?(?:vos |votre |mes |mon )?comptes?|releve bancaire"
            r"|extrait de compte",
            3,
            "relevé de compte",
        ),
        (r"bulletin de (?:paie|salaire)|fiche de (?:paie|salaire)", 3, "bulletin de paie"),
        (
            r"offre de (?:pret|credit)|contrat de (?:pret|credit)|tableau d.amortissement",
            3,
            "prêt",
        ),
        (r"salaire (?:brut|de base)|net a payer|net imposable|cotisations salariales", 2, "paie"),
        (r"\btaeg\b|\bmensualites?\b|capital emprunte|pret immobilier", 2, "prêt"),
        (r"\bsolde\b.*\b(crediteur|debiteur)\b|\bdecouvert autorise\b", 2, "solde"),
        (r"(?:nouveau|ancien) solde|solde (?:au|en date)", 2, "solde"),
        (r"\bnumero de compte\b|\bcode banque\b|\bcode guichet\b", 2, "coordonnées bancaires"),
        (r"\bcompte (?:cheques?|courant|joint)\b", 2, "compte bancaire"),
        (r"\bvirement\b|\bprelevement\b|\bprlv\b|\bvir sepa\b", 1, "opérations bancaires"),
    ],
}
# Laboratory results: each distinct analyte counts (a lab report lists many).
_ANALYTES = re.compile(
    r"\b(glycemie|cholesterol|hemoglobine|creatinine|hba1c|triglycerides|ferritine|tsh"
    r"|leucocytes|plaquettes|hematies|serologie|transaminases|vih|crp)\b"
)
_COMPILED = {
    category: [(re.compile(p), w, label) for p, w, label in signals]
    for category, signals in _SIGNALS.items()
}
# Every reason `classify` may give: fixed labels, never a value read in the document.
REASONS = frozenset(
    {label for signals in _SIGNALS.values() for _, _, label in signals}
    | {reason for _, reason in _IDENTIFIERS.values()}
    | {"bande MRZ", "BIC", "biologie"}
)
# A word read by OCR with digits for letters: more letters than digits, 0 and 1 only.
_OCR_WORD = re.compile(r"\b(?=\w*[a-z])(?=\w*[01])[a-z01]{3,}\b")


@dataclass
class Sensitivity:
    categories: list[str] = field(default_factory=list)
    # Category -> human-readable reasons (never the detected values).
    reasons: dict[str, list[str]] = field(default_factory=dict)

    @property
    def sensitive(self) -> bool:
        return bool(self.categories)


def keeps_local(document: Document) -> bool:
    """Whether a document's content must stay on the server: sensitive (detected
    locally) or kept local by the owner. The single rule for every path to the cloud."""
    return bool(document.sensitivity) or bool(document.local_only)


def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _ocr_words(normalised: str, one: str) -> str:
    """Words OCR read with digits: 0 -> o, 1 -> `one` (« i » or « l »)."""

    def fold(match: re.Match[str]) -> str:
        word = match.group(0)
        digits = sum(c in "01" for c in word)
        if digits * 2 >= len(word):
            return word  # mostly digits: a number, not a word
        return word.replace("0", "o").replace("1", one)

    return _OCR_WORD.sub(fold, normalised)


def classify_document(title: str | None, text: str | None) -> Sensitivity:
    """A document is classified on its title and its text (« Ordonnance Dr X »)."""
    body = text or ""
    return classify(f"{title}\n{body}" if title else body)


def classify(text: str) -> Sensitivity:
    scores: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}

    def add(category: str, weight: int, reason: str) -> None:
        scores[category] = scores.get(category, 0) + weight
        if reason not in reasons.setdefault(category, []):
            reasons[category].append(reason)

    upper_lines = "\n".join(
        line.strip().replace(" ", "") for line in text.upper().translate(_MRZ_FILLERS).splitlines()
    )
    for line in _MRZ.findall(upper_lines):
        if line.count("<") >= 3:
            add("identity", THRESHOLD, "bande MRZ")
            break
    for found in detect(text, resolve=False):
        if found.label in _IDENTIFIERS:
            category, reason = _IDENTIFIERS[found.label]
            if reason not in reasons.get(category, []):
                add(category, THRESHOLD, reason)
    if _BIC.search(text):
        add("bank", 2, "BIC")

    normalised = _normalise(text)
    variants = {normalised, _ocr_words(normalised, "i"), _ocr_words(normalised, "l")}
    for category, signals in _COMPILED.items():
        for pattern, weight, label in signals:
            if any(pattern.search(variant) for variant in variants):
                add(category, weight, label)
    analytes = {m.group(1) for variant in variants for m in _ANALYTES.finditer(variant)}
    if analytes:
        add("health", min(len(analytes), THRESHOLD), "biologie")

    kept = [c for c in ("identity", "health", "bank") if scores.get(c, 0) >= THRESHOLD]
    return Sensitivity(categories=kept, reasons={c: reasons[c] for c in kept})
