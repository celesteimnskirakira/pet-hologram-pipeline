"""Runtime configuration for the pet loop pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKDIR = PROJECT_ROOT / "runs"

ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path | None = None, override: bool = False) -> dict[str, str]:
    """Read KEY=value lines from a .env file into the process environment.

    Keeps secrets out of the shell history and out of a global rc file. Real
    environment variables win by default so an explicit export can still
    override the file for a one-off run.
    """
    target = path or ENV_FILE
    loaded: dict[str, str] = {}
    if not target.is_file():
        return loaded

    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if not key or (not override and os.environ.get(key)):
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded


# Load before the module-level model settings below read os.environ.
load_env_file()

ARK_BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
AGNES_BASE_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com")

# Ark model ids. Pinned on purpose: floating aliases change pricing tiers silently.
ARK_VISION_MODEL = os.environ.get("ARK_VISION_MODEL", "doubao-seed-1-6-flash-250828")
ARK_IMAGE_MODEL = os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-4-0-250828")
ARK_VIDEO_MODEL = os.environ.get("ARK_VIDEO_MODEL", "doubao-seedance-2-0-mini-260615")

AGNES_TEXT_MODEL = "agnes-2.0-flash"
AGNES_IMAGE_MODEL = "agnes-image-2.1-flash"
AGNES_VIDEO_MODEL = "agnes-video-v2.0"

ARK_KEY_ENV = ("ARK_API_KEY", "VOLC_ARK_API_KEY", "ARK_TOKEN")
AGNES_KEY_ENV = ("AGNES_API_KEY", "AGNES_API_TOKEN", "APIHUB_AGNES_API_KEY")


def first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return None


def ark_key() -> str | None:
    return first_env(ARK_KEY_ENV)


def agnes_key() -> str | None:
    return first_env(AGNES_KEY_ENV)


@dataclass
class StillSpec:
    """Step 2 output contract: pure black background front view."""

    size: str = "2048x2048"
    watermark: bool = False
    max_attempts: int = 3
    # Mean luminance (0-255) allowed on the sampled background ring.
    background_luma_max: float = 12.0
    # Share of background ring pixels that must be near black.
    background_black_ratio_min: float = 0.97


@dataclass
class VideoSpec:
    """Step 3 output contract: 5s head/tail looping clip."""

    duration_s: int = 5
    resolution: str = "480p"
    ratio: str = "1:1"
    fps: int = 24
    camera_fixed: bool = True
    watermark: bool = False
    seed: int | None = 42
    # Frame sent to the video model. Smaller keeps the request body light.
    frame_max_edge: int = 1024
    loop_mode: str = "trim"  # trim | xfade | none
    xfade_frames: int = 6
    poll_interval_s: float = 5.0
    poll_timeout_s: float = 900.0
    # Minimum gap between video task submissions, in seconds. Providers rate
    # limit task creation per account: Agnes measured at 1 per minute, Ark
    # allows 3 concurrent for individuals. 0 disables pacing.
    submit_gap_s: float = 0.0
    # How many times to wait out a 429 before giving up on an action.
    rate_limit_retries: int = 3


@dataclass
class HologramSpec:
    """Output shaping for a Pepper's ghost acrylic rig.

    The pure black background is not a style choice here: in a Pepper's ghost
    setup the acrylic reflects light but black emits none, so black reads as
    transparent and the pet appears to float.

    A single 45-degree pane mirrors the image left-to-right. Without a
    pre-flip, asymmetric markings land on the wrong side, which is exactly the
    detail an owner checks first.
    """

    # single: one 45-degree pane. quad: four-sided pyramid, viewable all round.
    rig: str = "single"
    # Mirror horizontally to cancel the reflection flip.
    mirror: bool = True
    # Some rigs sit the screen face-up under the pane and need a vertical flip.
    flip_vertical: bool = False
    # Pad the frame so the subject does not touch the pane edge.
    margin_frac: float = 0.06
    # Lift black toward true zero. Panel backlight bleed shows up as haze in the
    # reflection, so crushing near-black helps the float illusion.
    crush_black: int = 12


@dataclass
class PipelineSpec:
    pet_kind: str = "auto"  # auto | cat | dog
    pose: str = "curled_side"  # curled_side | loaf | sprawl
    # Roadshow mode: render several actions for one pet so the holographic
    # display can cycle through them. Empty means single-pose behaviour.
    poses: tuple[str, ...] = ()
    # Generate the extra poses concurrently. The wall clock is dominated by
    # provider queue time, so this is close to free in latency terms.
    parallel: bool = True
    max_workers: int = 3
    provider: str = "ark"  # ark | agnes
    # Generate a sleeping still before the video step. Without it the video
    # model must invent a large pose change while first/last frame conditioning
    # tells it to stay put, which in practice yields a static clip.
    sleep_bridge: bool = True
    still: StillSpec = field(default_factory=StillSpec)
    video: VideoSpec = field(default_factory=VideoSpec)
    hologram: HologramSpec = field(default_factory=HologramSpec)
    dry_run: bool = False
    keep_intermediate: bool = True
