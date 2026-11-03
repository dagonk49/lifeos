"""Image clean-up before a second OCR pass (F10, UC02, ADR 0008).

Phone photos of receipts are rarely flat scans: the ticket is a few degrees off, unevenly
lit, sometimes a little blurred. Tesseract reads straight, evenly contrasted text best.
These helpers measure and correct that, with Pillow and numpy only (no OpenCV):

- `estimate_skew`: the angle of the text lines, by projection profiles;
- `sharpness`: variance of the Laplacian (low = blurred);
- `normalize_illumination`: divides by the estimated paper background, then stretches the
  contrast, and sharpens a blurred picture;
- `prepare`: all of it, for the second pass of `extract.extract_image`.

They never decide alone: the extraction keeps the prepared picture's text only when its
OCR confidence is clearly better than the plain reading (`extract.MIN_GAIN`), so an
upright, clean document reads exactly as before. The stored picture is never warped.

Memory: everything that loops runs on a downscaled copy; the full-size work (division by
the background) goes by bands of rows, so a 4096 px picture stays far below the isolated
child's limit (`DEFAULT_MEMORY_MB`).
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter, ImageOps, ImageStat

# Skew search range and steps (degrees): a coarse sweep, then a fine one around the best.
# Beyond 15°, the picture is held sideways, which is OSD's business (90° steps).
MAX_SKEW_DEG = 15.0
COARSE_STEP_DEG = 1.0
FINE_STEP_DEG = 0.2
# Below this angle, Tesseract copes on its own: the picture is not turned (each rotation
# resamples the text a little).
SKEW_MIN_DEG = 0.8
# The skew is measured on a copy at most this large: projection profiles only need the
# text lines, and 31 + 11 rotations of a 4096 px picture would cost seconds.
SKEW_SIDE = 1000
# Sharpness (variance of the Laplacian on the picture as OCRed) under which a picture is
# treated as blurred and gets an unsharp mask. Crisp rendered text measures about 1000,
# the same text blurred by 2 px about 130 (synthetic receipts); to be confirmed on real
# photos with `lifeos-ml bench`.
BLUR_THRESHOLD = 300.0
# The paper background is estimated on a copy reduced by this factor: a maximum filter
# erases the text strokes, a blur smooths the shading of the light.
BACKGROUND_REDUCTION = 8
BACKGROUND_MAX_FILTER = 7
BACKGROUND_BLUR = 4.0
# Rows processed at once by the full-size division: bounds the float buffers to a few MB.
BAND_ROWS = 256


@dataclass(frozen=True)
class Applied:
    """What `prepare` did, for the record (`Document.analysis["reading"]`)."""

    skew: float  # degrees turned back (counterclockwise positive), 0.0 when not turned
    enhanced: bool  # illumination normalised and contrast stretched (sharpened if blurred)
    sharpened: bool


def _small(gray: Image.Image, side: int) -> Image.Image:
    if max(gray.size) <= side:
        return gray
    factor = side / max(gray.size)
    size = (max(1, round(gray.width * factor)), max(1, round(gray.height * factor)))
    return gray.resize(size, Image.Resampling.BILINEAR)


def _ink(gray: Image.Image) -> np.ndarray:
    """Dark pixels (text) as 1.0, the rest 0.0, on a contrast-stretched copy."""
    pixels = np.asarray(ImageOps.autocontrast(gray, cutoff=1), dtype=np.float32)
    # Ink is clearly darker than the paper around it: a global threshold between the mean
    # and the darkest values is enough on a background-normalised picture.
    threshold = min(128.0, float(pixels.mean()) - float(pixels.std()) * 0.5)
    return (pixels < threshold).astype(np.float32)


def _profile_score(ink: Image.Image, angle: float) -> float:
    """How sharply the rows separate into text lines and gaps once turned by `angle`."""
    turned = np.asarray(
        ink.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=0),
        dtype=np.float32,
    )
    rows = turned.sum(axis=1)
    # Squared differences between neighbouring rows: maximal when every line of text sits
    # exactly in its own band of rows.
    return float(np.square(np.diff(rows)).sum())


def estimate_skew(gray: Image.Image) -> float:
    """Angle (degrees, counterclockwise positive, within ±MAX_SKEW_DEG) by which the text
    lines are turned; `gray.rotate(-angle)` straightens them. 0.0 when there is no text."""
    small = _small(gray.convert("L"), SKEW_SIDE)
    ink = _ink(small)
    if ink.sum() < 50:  # blank picture: nothing to measure
        return 0.0
    # Only the central region: the edges of a photo show the table, fabric, fingers.
    height, width = ink.shape
    top, left = height // 10, width // 10
    central = ink[top : height - top, left : width - left]
    if central.sum() >= 50:
        ink = central
    picture = Image.fromarray((ink * 255).astype(np.uint8), mode="L")

    def best(start: float, stop: float, step: float) -> float:
        count = round((stop - start) / step)
        angles = [start + step * index for index in range(count + 1)]
        scores = [_profile_score(picture, angle) for angle in angles]
        return angles[int(np.argmax(scores))]

    coarse = best(-MAX_SKEW_DEG, MAX_SKEW_DEG, COARSE_STEP_DEG)
    fine = best(coarse - COARSE_STEP_DEG, coarse + COARSE_STEP_DEG, FINE_STEP_DEG)
    # The picture is turned back by `fine`: the text was turned by its opposite.
    return round(-fine, 1) + 0.0  # no -0.0


def sharpness(gray: Image.Image) -> float:
    """Variance of the Laplacian: high for crisp edges, low for a blurred picture."""
    laplacian = gray.convert("L").filter(
        ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)
    )
    return float(ImageStat.Stat(laplacian).var[0])


def normalize_illumination(gray: Image.Image) -> Image.Image:
    """Evens out the lighting (shadow of the phone, lamp gradient), then stretches the
    contrast. Same size and mode ("L") as the input."""
    gray = gray.convert("L")
    small = gray.reduce(BACKGROUND_REDUCTION) if min(gray.size) >= 64 else gray
    background = (
        small.filter(ImageFilter.MaxFilter(BACKGROUND_MAX_FILTER))
        .filter(ImageFilter.GaussianBlur(BACKGROUND_BLUR))
        .resize(gray.size, Image.Resampling.BILINEAR)
    )
    source = np.asarray(gray)
    paper = np.asarray(background)
    out = np.empty_like(source)
    for start in range(0, source.shape[0], BAND_ROWS):
        rows = slice(start, start + BAND_ROWS)
        ratio = source[rows].astype(np.float32) * 255.0 / np.maximum(paper[rows], 1)
        out[rows] = np.minimum(ratio, 255.0).astype(np.uint8)
    return ImageOps.autocontrast(Image.fromarray(out, mode="L"), cutoff=0.5)


def prepare(gray: Image.Image, skew: float | None = None) -> tuple[Image.Image, Applied]:
    """The picture for the second OCR pass: lighting evened out, contrast stretched,
    sharpened when blurred, turned straight when skewed. `skew` reuses an estimate."""
    blurred = sharpness(gray) < BLUR_THRESHOLD
    image = normalize_illumination(gray)
    if blurred:
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    angle = estimate_skew(image) if skew is None else skew
    turned = 0.0
    if abs(angle) >= SKEW_MIN_DEG:
        # White fill: the corners uncovered by the rotation must not read as ink.
        image = image.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
        turned = angle
    return image, Applied(skew=turned, enhanced=True, sharpened=blurred)
