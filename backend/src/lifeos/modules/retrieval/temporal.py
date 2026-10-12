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


def _months_ago(today: date, months: int) -> date:
    """Same day `months` earlier (clamped to the end of a shorter month)."""
    index = today.year * 12 + today.month - 1 - months
    start, end = _month_range(index // 12, index % 12 + 1)
    return min(start.replace(day=min(today.day, end.day)), end)


def _amount(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _NUMBERS.get(token)


def parse(text: str, today: date) -> DateRange | None:
    """Returns the first recognised period in `text`, relative to `today`.

    « depuis <période> » extends the period up to today. Dates that cannot exist
    (« il y a 4500 ans », a 29 February in the wrong year) yield None.
    """
    t = _normalize(text)
    try:
        result = _parse(t, today)
    except (ValueError, OverflowError):
        return None
    if result is None:
        return None
    period, position = result
    if t[:position].rstrip().endswith("depuis") and period.start <= today:
        return DateRange(start=period.start, end=today, expression=f"depuis {period.expression}")
    return period


def _parse(t: str, today: date) -> tuple[DateRange, int] | None:
    """(period, position of the expression in `t`)."""

    def found(
        start: date, end: date, expression: str, match: re.Match[str]
    ) -> tuple[DateRange, int]:
        return DateRange(start=start, end=end, expression=expression), match.start()

    if m := re.search(r"\bavant[- ]hier\b", t):
        day = today - timedelta(days=2)
        return found(day, day, "avant-hier", m)
    if m := re.search(r"\bhier\b", t):
        day = today - timedelta(days=1)
        return found(day, day, "hier", m)
    if m := re.search(r"\baujourd hui\b|\bce matin\b|\bce soir\b", t):
        return found(today, today, "aujourd'hui", m)

    m = re.search(
        r"\b(?:il y a|depuis) (\d+|\w+) (jour|jours|semaine|semaines|mois|an|ans|annee|annees)\b", t
    )
    if m and (amount := _amount(m.group(1))):
        unit = m.group(2)
        since = m.group(0).startswith("depuis")
        if unit.startswith("jour"):
            day = today - timedelta(days=amount)
            return found(day, today if since else day, m.group(0), m)
        if unit.startswith("semaine"):
            if since:
                return found(today - timedelta(weeks=amount), today, m.group(0), m)
            return found(*_week(today - timedelta(weeks=amount)), m.group(0), m)
        if unit == "mois":
            if since:
                return found(_months_ago(today, amount), today, m.group(0), m)
            month_index = today.year * 12 + today.month - 1 - amount
            return found(*_month_range(month_index // 12, month_index % 12 + 1), m.group(0), m)
        if since:
            return found(_months_ago(today, 12 * amount), today, m.group(0), m)
        year = today.year - amount
        return found(date(year, 1, 1), date(year, 12, 31), m.group(0), m)

    m = re.search(r"\b(ces|les) (\d+|\w+) derniers jours\b", t)
    if m and (amount := _amount(m.group(2))):
        return found(today - timedelta(days=amount), today, m.group(0), m)

    if m := re.search(r"\b(la semaine derniere|la semaine passee|semaine derniere)\b", t):
        return found(*_week(today - timedelta(weeks=1)), "la semaine dernière", m)
    if m := re.search(r"\bcette semaine\b", t):
        start, _ = _week(today)
        return found(start, today, "cette semaine", m)
    if m := re.search(r"\b(le mois dernier|le mois passe)\b", t):
        index = today.year * 12 + today.month - 2
        return found(*_month_range(index // 12, index % 12 + 1), "le mois dernier", m)
    if m := re.search(r"\bce mois[- ]ci\b|\bce mois\b", t):
        return found(today.replace(day=1), today, "ce mois-ci", m)
    if m := re.search(r"\b(l an dernier|l annee derniere|l an passe|l annee passee)\b", t):
        year = today.year - 1
        return found(date(year, 1, 1), date(year, 12, 31), "l'an dernier", m)
    if m := re.search(r"\bcette annee\b", t):
        return found(date(today.year, 1, 1), today, "cette année", m)

    m = re.search(r"\b(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche) dernier\b", t)
    if m:
        delta = (today.weekday() - WEEKDAYS[m.group(1)]) % 7 or 7
        day = today - timedelta(days=delta)
        return found(day, day, m.group(0), m)

    month_names = "|".join(MONTHS)
    m = re.search(rf"\b(?:le )?(\d{{1,2}})(?:er)? ({month_names})(?: (\d{{4}}))?\b", t)
    if m:
        month = MONTHS[m.group(2)]
        day_number = int(m.group(1))
        if m.group(3):
            day = date(int(m.group(3)), month, day_number)
        else:
            # Without a year: the most recent such date (this year, else last year).
            day = (
                date(today.year, month, day_number)
                if (month, day_number)
                <= (
                    today.month,
                    today.day,
                )
                else date(today.year - 1, month, day_number)
            )
        return found(day, day, m.group(0), m)

    m = re.search(rf"\b(?:en|au mois de) ({month_names})(?: (\d{{4}}))?\b", t)
    if m:
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else today.year
        if not m.group(2) and date(year, month, 1) > today:
            year -= 1
        return found(*_month_range(year, month), m.group(0), m)
    m = re.search(rf"\bdepuis ({month_names})(?: (\d{{4}}))?\b", t)
    if m:
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else today.year
        if not m.group(2) and date(year, month, 1) > today:
            year -= 1
        return found(date(year, month, 1), today, m.group(0), m)

    m = re.search(r"\ben (20\d{2}|19\d{2})\b", t)
    if m:
        year = int(m.group(1))
        return found(date(year, 1, 1), date(year, 12, 31), m.group(0), m)
    return None
