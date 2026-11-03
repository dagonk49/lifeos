"""Picture clean-up before the second OCR pass (`lifeos.ml.preprocess`): pure Pillow and
numpy, no Tesseract needed. Pictures are generated in code (`ml_samples.render`)."""

import time

import pytest

pytest.importorskip("numpy")

from ml_samples import render
from PIL import Image, ImageChops, ImageFilter, ImageStat

from lifeos.ml.preprocess import (
    estimate_skew,
    normalize_illumination,
    prepare,
    sharpness,
)

RECEIPT = [
    "CARREFOUR MARKET",
    "12 rue de la Paix 75002 Paris",
    "Le 10/10/2026 a 18:42",
    "PAIN COMPLET          2,15",
    "LAIT DEMI ECREME      1,29",
    "POMMES 1KG            2,99",
    "TOTAL TTC             6,43",
    "CB                    6,43",
]


def _turned(image: Image.Image, angle: float) -> Image.Image:
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")


@pytest.mark.parametrize("angle", [-10, -5, -2, 2, 5, 10])
def test_skew_is_measured_within_half_a_degree(angle: int) -> None:
    gray = _turned(render(RECEIPT), angle).convert("L")
    assert abs(estimate_skew(gray) - angle) <= 0.5


def test_upright_and_blank_pictures_have_no_skew() -> None:
    assert abs(estimate_skew(render(RECEIPT).convert("L"))) <= 0.2
    assert abs(estimate_skew(render(["Facture", "Total 12,00"], width=1200).convert("L"))) <= 0.2
    assert estimate_skew(Image.new("L", (800, 600), 255)) == 0.0


def test_blurred_pictures_are_less_sharp() -> None:
    gray = render(RECEIPT).convert("L")
    blurred = gray.filter(ImageFilter.GaussianBlur(2))
    assert sharpness(blurred) < sharpness(gray) / 3


def test_illumination_is_evened_out_without_changing_the_picture_shape() -> None:
    gray = render(RECEIPT).convert("L")
    # A lamp on one side: the paper fades to dark grey on the other.
    shade = Image.linear_gradient("L").rotate(90).resize(gray.size).point(lambda v: 70 + v * 0.7)
    shaded = ImageChops.multiply(gray, shade)
    normalized = normalize_illumination(shaded)
    assert (normalized.size, normalized.mode) == (shaded.size, "L")
    # The paper is about as light on both sides again.
    width, _ = normalized.size
    corners = [(5, 5, 30, 30), (width - 30, 5, width - 5, 30)]

    def darkest_paper(image: Image.Image) -> float:
        return min(ImageStat.Stat(image.crop(box)).mean[0] for box in corners)

    assert darkest_paper(shaded) < 120
    assert darkest_paper(normalized) > 220


def test_prepare_straightens_only_skewed_pictures() -> None:
    _, applied = prepare(render(RECEIPT).convert("L"))
    assert applied.skew == 0.0 and applied.enhanced
    skewed, applied = prepare(_turned(render(RECEIPT), 7).convert("L"))
    assert abs(applied.skew - 7) <= 0.5
    assert abs(estimate_skew(skewed)) <= 0.5


def test_preparing_a_full_size_picture_is_bounded_in_time() -> None:
    # The largest picture OCRed (MAX_IMAGE_SIDE): the second pass adds at most a few
    # seconds before Tesseract runs again.
    photo = _turned(render(RECEIPT, size=120, width=3800), 4).convert("L")
    assert max(photo.size) > 3800
    started = time.monotonic()
    prepared, applied = prepare(photo)
    assert time.monotonic() - started < 8.0
    assert abs(applied.skew - 4) <= 0.5
    assert max(prepared.size) <= 4096 * 1.1
