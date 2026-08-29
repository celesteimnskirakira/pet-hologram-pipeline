"""Local image helpers: validation, normalization, black-background scoring."""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

# Ark input constraints for Seedance / Seedream reference images.
MIN_EDGE = 300
MAX_EDGE = 6000
MIN_RATIO = 0.4
MAX_RATIO = 2.5
MAX_BYTES = 30 * 1024 * 1024

SUPPORTED_INPUT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif"}


class ImageError(ValueError):
    """Raised when user input cannot be used by the generation APIs."""


@dataclass
class BackgroundScore:
    mean_luma: float
    black_ratio: float
    sampled_pixels: int

    def passes(self, luma_max: float, black_ratio_min: float) -> bool:
        return self.mean_luma <= luma_max and self.black_ratio >= black_ratio_min

    def as_dict(self) -> dict:
        return {
            "mean_luma": round(self.mean_luma, 3),
            "black_ratio": round(self.black_ratio, 4),
            "sampled_pixels": self.sampled_pixels,
        }


def load_rgb(path: str | Path) -> Image.Image:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_INPUT:
        raise ImageError(f"Unsupported image type: {path.suffix or 'unknown'}")
    if path.stat().st_size > MAX_BYTES:
        raise ImageError("Image is larger than 30 MB; please downscale before upload.")
    try:
        image = Image.open(path)
        image.load()
    except Exception as exc:  # noqa: BLE001 - Pillow raises many decode errors
        raise ImageError(f"Cannot decode image: {exc}") from exc
    # Honour EXIF orientation so a phone portrait shot is not sideways.
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def validate_for_upload(image: Image.Image) -> None:
    width, height = image.size
    ratio = width / height
    if min(width, height) < MIN_EDGE:
        raise ImageError(f"Shortest edge is {min(width, height)}px; needs at least {MIN_EDGE}px.")
    if max(width, height) > MAX_EDGE:
        raise ImageError(f"Longest edge is {max(width, height)}px; must stay under {MAX_EDGE}px.")
    if not MIN_RATIO <= ratio <= MAX_RATIO:
        raise ImageError(f"Aspect ratio {ratio:.2f} is outside the supported [0.4, 2.5] range.")


def normalize_input(path: str | Path, max_edge: int = 2048) -> Image.Image:
    """Load, orient, and clamp a user upload so the APIs accept it."""
    image = load_rgb(path)
    width, height = image.size

    if max(width, height) > max_edge:
        scale = max_edge / max(width, height)
        image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)

    width, height = image.size
    if min(width, height) < MIN_EDGE:
        scale = MIN_EDGE / min(width, height)
        image = image.resize((round(width * scale), round(height * scale)), Image.LANCZOS)

    validate_for_upload(image)
    return image


def fit_max_edge(image: Image.Image, max_edge: int) -> Image.Image:
    width, height = image.size
    if max(width, height) <= max_edge:
        return image
    scale = max_edge / max(width, height)
    return image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.LANCZOS)


def center_square(image: Image.Image) -> Image.Image:
    """Crop to 1:1 so the cheapest Seedance ratio needs no server-side cropping."""
    width, height = image.size
    edge = min(width, height)
    left = (width - edge) // 2
    top = (height - edge) // 2
    return image.crop((left, top, left + edge, top + edge))


def to_data_url(image: Image.Image, fmt: str = "png") -> str:
    fmt = fmt.lower()
    buffer = io.BytesIO()
    save_fmt = "JPEG" if fmt in {"jpg", "jpeg"} else fmt.upper()
    save_kwargs = {"quality": 95} if save_fmt == "JPEG" else {}
    image.save(buffer, format=save_fmt, **save_kwargs)
    mime = "jpeg" if save_fmt == "JPEG" else fmt
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def score_background(image: Image.Image, border_frac: float = 0.06, near_black: int = 18) -> BackgroundScore:
    """Measure how black the background is, ignoring the subject.

    Sampling a fixed outer ring is not enough. A well-composed portrait often
    runs the subject to the bottom edge, so the lower band measures fur instead
    of backdrop and a perfectly good image gets rejected. Bigger animals failed
    more often, which is backwards.

    So locate the subject first, then score only pixels outside its bounding
    box. `border_frac` is kept for callers but no longer bounds the sample.
    """
    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()

    # Anything clearly brighter than the near-black floor is subject, not backdrop.
    subject_level = max(near_black * 2, 40)
    step = max(1, min(width, height) // 200)

    left, right, top, bottom = width, -1, height, -1
    for y in range(0, height, step):
        for x in range(0, width, step):
            if pixels[x, y] > subject_level:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y

    if right < left or bottom < top:
        # Nothing bright anywhere: treat the whole frame as background.
        left = right = width // 2
        top = bottom = height // 2
    else:
        # Margin so anti-aliased fur edges are not scored as backdrop.
        pad = max(2, round(min(width, height) * 0.015))
        left, right = max(0, left - pad), min(width - 1, right + pad)
        top, bottom = max(0, top - pad), min(height - 1, bottom + pad)

    total = 0
    accum = 0
    black = 0

    for y in range(0, height, step):
        for x in range(0, width, step):
            if left <= x <= right and top <= y <= bottom:
                continue
            value = pixels[x, y]
            total += 1
            accum += value
            if value <= near_black:
                black += 1

    if total == 0:
        return BackgroundScore(mean_luma=255.0, black_ratio=0.0, sampled_pixels=0)
    return BackgroundScore(mean_luma=accum / total, black_ratio=black / total, sampled_pixels=total)


def hard_clean_background(image: Image.Image, threshold: int = 26) -> Image.Image:
    """Clamp near-black pixels to exact #000000.

    Used as a deterministic finishing pass after the model returns a dark
    background, so the still is truly pure black instead of near black.
    """
    result = image.convert("RGB").copy()
    pixels = result.load()
    width, height = result.size
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            if r <= threshold and g <= threshold and b <= threshold:
                pixels[x, y] = (0, 0, 0)
    return result


def save(image: Image.Image, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path
