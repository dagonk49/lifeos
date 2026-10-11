from datetime import date

import pytest

from lifeos.modules.retrieval.temporal import parse

TODAY = date(2026, 10, 11)  # a Sunday


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Qu'ai-je fait hier ?", date(2026, 10, 10), date(2026, 10, 10)),
        ("avant-hier soir", date(2026, 10, 9), date(2026, 10, 9)),
        ("aujourd'hui", TODAY, TODAY),
        ("la semaine dernière", date(2026, 9, 28), date(2026, 10, 4)),
        ("Cette semaine", date(2026, 10, 5), TODAY),
        ("le mois dernier", date(2026, 9, 1), date(2026, 9, 30)),
        ("ce mois-ci", date(2026, 10, 1), TODAY),
        ("l'année dernière", date(2025, 1, 1), date(2025, 12, 31)),
        ("cette année", date(2026, 1, 1), TODAY),
        ("il y a 3 jours", date(2026, 10, 8), date(2026, 10, 8)),
        ("il y a deux semaines", date(2026, 9, 21), date(2026, 9, 27)),
        ("il y a un mois", date(2026, 9, 1), date(2026, 9, 30)),
        ("ces 7 derniers jours", date(2026, 10, 4), TODAY),
        ("vendredi dernier", date(2026, 10, 9), date(2026, 10, 9)),
        ("dimanche dernier", date(2026, 10, 4), date(2026, 10, 4)),
        ("le 3 mars", date(2026, 3, 3), date(2026, 3, 3)),
        ("le 1er décembre", date(2025, 12, 1), date(2025, 12, 1)),  # future -> last year
        ("le 14 juillet 2024", date(2024, 7, 14), date(2024, 7, 14)),
        ("en février", date(2026, 2, 1), date(2026, 2, 28)),
        ("en novembre", date(2025, 11, 1), date(2025, 11, 30)),
        ("depuis août", date(2026, 8, 1), TODAY),
        ("en 2024", date(2024, 1, 1), date(2024, 12, 31)),
        # « depuis » opens the period up to today.
        ("Qu'ai-je fait depuis le 1er octobre ?", date(2026, 10, 1), TODAY),
        ("depuis hier", date(2026, 10, 10), TODAY),
        ("depuis la semaine dernière", date(2026, 9, 28), TODAY),
        ("depuis 3 jours", date(2026, 10, 8), TODAY),
        ("depuis deux semaines", date(2026, 9, 27), TODAY),
        ("depuis 2 mois", date(2026, 8, 11), TODAY),
        ("depuis un an", date(2025, 10, 11), TODAY),
    ],
)
def test_french_expressions(text: str, start: date, end: date) -> None:
    result = parse(text, TODAY)
    assert result is not None, text
    assert (result.start, result.end) == (start, end)


@pytest.mark.parametrize(
    "text",
    [
        "Bonjour",
        "le 31 février",
        "mes 2 enfants",
        "mai-je ?",
        # Dates that cannot exist never crash the assistant.
        "Les pyramides ont été construites il y a 4500 ans",
        "il y a 2026 ans",
        "il y a 99999999999 jours",
        "le 29 février",  # 2026 is not a leap year
    ],
)
def test_no_false_positives(text: str) -> None:
    assert parse(text, TODAY) is None
