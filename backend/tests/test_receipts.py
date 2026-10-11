from datetime import date
from decimal import Decimal

import pytest

from lifeos.modules.finance.receipts import parse_amount, parse_receipt

TODAY = date(2026, 10, 11)

CARREFOUR = """CARREFOUR MARKET
12 rue de la Paix
75002 PARIS
Tel : 01 23 45 67 89
LAIT DEMI ECREME 1L        1,15
PAIN DE CAMPAGNE           2,30
POMMES GOLDEN 1,2 KG       3,48
SOUS-TOTAL                 6,93
REMISE FIDELITE           -0,50
TOTAL TTC                  6,43 EUR
TVA 5,5 %   6,09   0,34
CB EMV  **** **** **** 1234
10/10/2026 18:42  CAISSE 3
MERCI DE VOTRE VISITE"""

BOULANGERIE = """** BOULANGERIE DU MARCHÉ **
Ticket n° 4521
2 Croissants 2,40
1 Baguette 1,20
TOTAL
3,60 €
Espèces 5,00
Rendu 1,40
09.10.26"""

RESTAURANT = """Le Petit Bistrot
SIRET 123 456 789 00012
Plat du jour 14,50
Café 2,20
Total HT 15,18
TVA 10 % 1,52
NET A PAYER 16,70 €
Visa ****5678
Le 08/10/2026"""


@pytest.mark.parametrize(
    ("text", "merchant", "day", "total", "vat"),
    [
        (CARREFOUR, "CARREFOUR MARKET", date(2026, 10, 10), "6.43", ["5,5"]),
        (BOULANGERIE, "BOULANGERIE DU MARCHÉ", date(2026, 10, 9), "3.60", []),
        (RESTAURANT, "Le Petit Bistrot", date(2026, 10, 8), "16.70", ["10"]),
    ],
)
def test_french_receipts(text: str, merchant: str, day: date, total: str, vat: list[str]) -> None:
    proposal = parse_receipt(text, TODAY)
    assert proposal.merchant is not None and proposal.merchant.value == merchant
    assert proposal.tx_date is not None and proposal.tx_date.value == day
    assert proposal.total is not None and proposal.total.value == Decimal(total)
    assert proposal.vat_rates == vat
    assert proposal.confidence >= 0.85
    assert proposal.as_dict()["total"] == total


def test_unreadable_receipt_says_what_is_missing() -> None:
    proposal = parse_receipt("~~~ illisible ~~~\n12", TODAY)
    assert proposal.total is None and proposal.confidence < 0.5
    assert "Montant total non trouvé." in proposal.warnings


def test_future_and_ancient_dates_are_ignored() -> None:
    proposal = parse_receipt("SHOP\nTOTAL 3,00\n01/01/2031\n02/01/2019\n05/10/2026", TODAY)
    assert proposal.tx_date is not None and proposal.tx_date.value == date(2026, 10, 5)


@pytest.mark.parametrize(
    ("text", "value"),
    [("TOTAL 1 234,56 €", "1234.56"), ("12.5", "12.50"), ("Montant 0,00", None), ("n° 42", None)],
)
def test_amounts(text: str, value: str | None) -> None:
    assert parse_amount(text) == (Decimal(value) if value else None)
