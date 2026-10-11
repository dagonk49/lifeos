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
    ],
)
def test_french_expressions(text: str, start: date, end: date) -> None:
    result = parse(text, TODAY)
    assert result is not None, text
    assert (result.start, result.end) == (start, end)


@pytest.mark.parametrize("text", ["Bonjour", "le 31 février", "mes 2 enfants", "mai-je ?"])
def test_no_false_positives(text: str) -> None:
    assert parse(text, TODAY) is None
