"""French temporal expressions -> date ranges, deterministically (no LLM).

Used by retrieval to honour questions such as « qu'ai-je décidé la semaine dernière ? ».
Only common, unambiguous expressions are recognised; anything else yields None.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

MONTHS = {
    "janvier": 1,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
}
WEEKDAYS = {
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}
_NUMBERS = {
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
    "quinze": 15,
}


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # inclusive
    expression: str


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\u2019']", " ", stripped)


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    following = date(year + (month == 12), month % 12 + 1, 1)
    return start, following - timedelta(days=1)


def _week(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _amount(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _NUMBERS.get(token)


def parse(text: str, today: date) -> DateRange | None:
    """Returns the first recognised period in `text`, relative to `today`."""
    t = _normalize(text)

    def found(start: date, end: date, expression: str) -> DateRange:
        return DateRange(start=start, end=end, expression=expression)

    if re.search(r"\bavant[- ]hier\b", t):
        day = today - timedelta(days=2)
        return found(day, day, "avant-hier")
    if re.search(r"\bhier\b", t):
        day = today - timedelta(days=1)
        return found(day, day, "hier")
    if re.search(r"\baujourd hui\b|\bce matin\b|\bce soir\b", t):
        return found(today, today, "aujourd'hui")

    match = re.search(
        r"\bil y a (\d+|\w+) (jour|jours|semaine|semaines|mois|an|ans|annee|annees)\b", t
    )
    if match and (amount := _amount(match.group(1))):
        unit = match.group(2)
        if unit.startswith("jour"):
            day = today - timedelta(days=amount)
            return found(day, day, match.group(0))
        if unit.startswith("semaine"):
            return found(*_week(today - timedelta(weeks=amount)), match.group(0))
        if unit == "mois":
            month_index = today.year * 12 + today.month - 1 - amount
            return found(*_month_range(month_index // 12, month_index % 12 + 1), match.group(0))
        year = today.year - amount
        return found(date(year, 1, 1), date(year, 12, 31), match.group(0))

    match = re.search(r"\b(ces|les) (\d+|\w+) derniers jours\b", t)
    if match and (amount := _amount(match.group(2))):
        return found(today - timedelta(days=amount), today, match.group(0))

    if re.search(r"\b(la semaine derniere|la semaine passee|semaine derniere)\b", t):
        return found(*_week(today - timedelta(weeks=1)), "la semaine dernière")
    if re.search(r"\bcette semaine\b", t):
        start, _ = _week(today)
        return found(start, today, "cette semaine")
    if re.search(r"\b(le mois dernier|le mois passe)\b", t):
        index = today.year * 12 + today.month - 2
        return found(*_month_range(index // 12, index % 12 + 1), "le mois dernier")
    if re.search(r"\bce mois[- ]ci\b|\bce mois\b", t):
        return found(today.replace(day=1), today, "ce mois-ci")
    if re.search(r"\b(l an dernier|l annee derniere|l an passe|l annee passee)\b", t):
        year = today.year - 1
        return found(date(year, 1, 1), date(year, 12, 31), "l'an dernier")
    if re.search(r"\bcette annee\b", t):
        return found(date(today.year, 1, 1), today, "cette année")

    match = re.search(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) dernier\b", t)
    if match:
        delta = (today.weekday() - WEEKDAYS[match.group(1)]) % 7 or 7
        day = today - timedelta(days=delta)
        return found(day, day, match.group(0))

    month_names = "|".join(MONTHS)
    match = re.search(rf"\b(?:le )?(\d{{1,2}})(?:er)? ({month_names})(?: (\d{{4}}))?\b", t)
    if match:
        month = MONTHS[match.group(2)]
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            day = date(year, month, int(match.group(1)))
        except ValueError:
            return None
        if not match.group(3) and day > today:
            day = day.replace(year=year - 1)
        return found(day, day, match.group(0))

    match = re.search(rf"\b(?:en|au mois de|depuis) ({month_names})(?: (\d{{4}}))?\b", t)
    if match:
        month = MONTHS[match.group(1)]
        year = int(match.group(2)) if match.group(2) else today.year
        if not match.group(2) and date(year, month, 1) > today:
            year -= 1
        start, end = _month_range(year, month)
        if match.group(0).startswith("depuis"):
            end = today
        return found(start, end, match.group(0))

    match = re.search(r"\ben (20\d{2}|19\d{2})\b", t)
    if match:
        year = int(match.group(1))
        return found(date(year, 1, 1), date(year, 12, 31), match.group(0))
    return None
