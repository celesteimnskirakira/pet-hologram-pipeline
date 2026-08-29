"""Cost model for Seedance 2.0 mini on Volcengine Ark.

Official billing formula (Ark docs, 模型价格 page):

    tokens = (input_video_seconds + output_video_seconds) * width * height * fps / 1024

Seedance 2.0 series list price, output 480p/720p, input without video:
    2.0        46.00 CNY / M tokens
    2.0-fast   37.00 CNY / M tokens (limited-time 75% of list)
    2.0-mini   23.00 CNY / M tokens (limited-time 40% of list)

The mini promo runs 2026-08-07 14:00 -> 2026-09-07 14:00 (UTC+8) at 40% of list
for 480p and 720p. Verified against the Ark pricing page on 2026-08-28.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CNY_PER_M_TOKEN = {
    "doubao-seedance-2-0-mini": 23.00,
    "doubao-seedance-2-0-fast": 37.00,
    "doubao-seedance-2-0": 46.00,
}

PROMO_DISCOUNT = {
    "doubao-seedance-2-0-mini": 0.40,
    "doubao-seedance-2-0-fast": 0.75,
}

CN_TZ = timezone(timedelta(hours=8))
PROMO_START = datetime(2026, 8, 7, 14, 0, tzinfo=CN_TZ)
PROMO_END = datetime(2026, 9, 7, 14, 0, tzinfo=CN_TZ)

# Seedance 2.0 series pixel dimensions per resolution + aspect ratio.
PIXELS = {
    ("480p", "16:9"): (864, 496),
    ("480p", "4:3"): (752, 560),
    ("480p", "1:1"): (640, 640),
    ("480p", "3:4"): (560, 752),
    ("480p", "9:16"): (496, 864),
    ("480p", "21:9"): (992, 432),
    ("720p", "16:9"): (1280, 720),
    ("720p", "4:3"): (1112, 834),
    ("720p", "1:1"): (960, 960),
    ("720p", "3:4"): (834, 1112),
    ("720p", "9:16"): (720, 1280),
    ("720p", "21:9"): (1470, 630),
}


@dataclass
class CostEstimate:
    model_family: str
    resolution: str
    ratio: str
    width: int
    height: int
    fps: int
    seconds: int
    tokens: int
    list_cny: float
    effective_cny: float
    discount: float | None
    promo_active: bool

    def as_dict(self) -> dict:
        return {
            "model_family": self.model_family,
            "spec": f"{self.resolution} {self.ratio} {self.width}x{self.height}@{self.fps}fps {self.seconds}s",
            "tokens_est": self.tokens,
            "list_cny": round(self.list_cny, 4),
            "effective_cny": round(self.effective_cny, 4),
            "discount": self.discount,
            "promo_active": self.promo_active,
        }


def family_of(model_id: str) -> str:
    for family in CNY_PER_M_TOKEN:
        if model_id.startswith(family):
            return family
    return "doubao-seedance-2-0-mini"


def estimate(
    model_id: str,
    resolution: str = "480p",
    ratio: str = "1:1",
    seconds: int = 5,
    fps: int = 24,
    input_video_seconds: int = 0,
    now: datetime | None = None,
) -> CostEstimate:
    """Estimate the price of one clip. Actual billing uses usage.completion_tokens."""
    family = family_of(model_id)
    key = (resolution, ratio)
    if key not in PIXELS:
        raise ValueError(f"Unsupported resolution/ratio combo for Seedance 2.0: {resolution} {ratio}")
    width, height = PIXELS[key]

    tokens = round((input_video_seconds + seconds) * width * height * fps / 1024)
    unit = CNY_PER_M_TOKEN[family]
    list_cny = tokens / 1_000_000 * unit

    moment = now or datetime.now(CN_TZ)
    discount = PROMO_DISCOUNT.get(family)
    promo_active = discount is not None and PROMO_START <= moment <= PROMO_END
    effective = list_cny * discount if promo_active else list_cny

    return CostEstimate(
        model_family=family,
        resolution=resolution,
        ratio=ratio,
        width=width,
        height=height,
        fps=fps,
        seconds=seconds,
        tokens=tokens,
        list_cny=list_cny,
        effective_cny=effective,
        discount=discount if promo_active else None,
        promo_active=promo_active,
    )


def cheapest_options(seconds: int = 5, fps: int = 24) -> list[CostEstimate]:
    """Rank every mini-supported spec from cheapest to priciest."""
    out = []
    for resolution, ratio in PIXELS:
        out.append(
            estimate(
                "doubao-seedance-2-0-mini",
                resolution=resolution,
                ratio=ratio,
                seconds=seconds,
                fps=fps,
            )
        )
    return sorted(out, key=lambda e: e.effective_cny)
