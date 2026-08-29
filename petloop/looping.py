"""Loop finishing with ffmpeg.

Even with first_frame == last_frame, a diffusion video model lands *close* to
the opening pose rather than exactly on it, and the duplicated end frame itself
causes a one-frame stutter. This module measures the seam objectively and then
repairs it, so "head/tail looping" is a verified property instead of a hope.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


class FFmpegMissing(RuntimeError):
    pass


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        configured = os.environ.get("FFMPEG_PATH", "").strip()
        if configured:
            candidate = Path(configured).with_name(tool)
            if candidate.is_file():
                path = str(candidate)
    if not path:
        raise FFmpegMissing(f"{tool} not found on PATH. Install ffmpeg to enable loop finishing.")
    return path


def probe(path: str | Path) -> dict:
    ffprobe = _require("ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_packets,duration",
            "-count_packets",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or [{}]
    stream = streams[0]
    rate = stream.get("r_frame_rate", "0/1")
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    frames = stream.get("nb_read_packets")
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": round(fps, 3),
        "frames": int(frames) if frames is not None else None,
        "duration_s": float(stream["duration"]) if stream.get("duration") else None,
    }


def extract_frame(video: str | Path, index: int, out_path: str | Path) -> Path:
    """Pull a single frame by index using the select filter."""
    ffmpeg = _require("ffmpeg")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{index})",
            "-frames:v",
            "1",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out_path


def render_for_hologram(
    video: str | Path,
    out_path: str | Path,
    rig: str = "single",
    mirror: bool = True,
    flip_vertical: bool = False,
    margin_frac: float = 0.06,
    crush_black: int = 12,
) -> Path:
    """Shape a clip for a Pepper's ghost acrylic rig.

    Three corrections happen here:

    1. Mirror, because a 45-degree pane flips the image left-to-right. Skipping
       this puts asymmetric markings on the wrong side.
    2. Crush near-black to true black, because panel backlight bleed reflects as
       visible haze and breaks the floating illusion.
    3. Pad, so the subject never touches the pane edge where the reflection
       geometry falls apart.

    For a four-sided pyramid the frame is tiled into four rotated copies so one
    screen feeds all viewing directions.
    """
    ffmpeg = _require("ffmpeg")
    info = probe(video)
    width = info.get("width") or 640
    height = info.get("height") or 640

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Lift the black floor first; do it before scaling so resampling cannot
    # smear crushed pixels back into dark grey.
    steps = []
    if crush_black > 0:
        level = crush_black / 255.0
        steps.append(f"lutyuv=y='if(lt(val,{crush_black}),0,val)'")
        steps.append(f"colorlevels=rimin={level:.4f}:gimin={level:.4f}:bimin={level:.4f}")
    if mirror:
        steps.append("hflip")
    if flip_vertical:
        steps.append("vflip")

    if margin_frac > 0:
        inner_w = max(2, int(round(width * (1 - 2 * margin_frac))) // 2 * 2)
        inner_h = max(2, int(round(height * (1 - 2 * margin_frac))) // 2 * 2)
        steps.append(f"scale={inner_w}:{inner_h}")
        steps.append(
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )

    base = ",".join(steps) if steps else "null"

    if rig == "quad":
        # Quad tiling is done frame by frame in build_quad_frames instead of a
        # filtergraph: exact paste coordinates matter more here than compactness,
        # and rotation swapping width/height makes overlay offsets easy to get
        # wrong in a way that silently crops the subject.
        raise ValueError("rig='quad' is handled by render_quad_hologram, not this function")

    args = ["-vf", base]

    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            *args,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out_path


def measure_black_floor(video: str | Path, border_frac: float = 0.05) -> dict:
    """Check how black the frame edges are in the finished clip.

    On a hologram rig any non-black edge reflects as a visible rectangle, so
    this is a functional check rather than a cosmetic one.
    """
    return _sample_edges(video, border_frac)


def render_quad_hologram(
    video: str | Path,
    out_path: str | Path,
    mirror: bool = True,
    crush_black: int = 12,
    subject_frac: float = 0.42,
) -> Path:
    """Tile a clip four ways for a pyramid rig, compositing frame by frame.

    Done in Pillow rather than an ffmpeg filtergraph on purpose. Rotating a copy
    swaps its width and height, and getting overlay offsets subtly wrong crops
    the subject without any error message. Explicit paste coordinates are worth
    more here than a compact one-liner.

    Layout: each copy sits against one edge of a square canvas, rotated so its
    feet point outward. Reflected off a four-sided pyramid, the four copies
    converge into one figure viewable from any side.
    """
    ffmpeg = _require("ffmpeg")
    info = probe(video)
    fps = info.get("fps") or 24.0
    src_w = info.get("width") or 640

    canvas_edge = max(2, src_w // 2 * 2)
    cell = max(2, int(canvas_edge * subject_frac) // 2 * 2)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frames_in = tmp_dir / "in"
        frames_out = tmp_dir / "out"
        frames_in.mkdir()
        frames_out.mkdir()

        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error", "-i", str(video),
                str(frames_in / "f%05d.png"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        sources = sorted(frames_in.glob("f*.png"))
        if not sources:
            raise ValueError("No frames extracted from the source clip.")

        for index, frame_path in enumerate(sources):
            frame = Image.open(frame_path).convert("RGB")
            if mirror:
                frame = frame.transpose(Image.FLIP_LEFT_RIGHT)
            if crush_black > 0:
                frame = frame.point(lambda v: 0 if v <= crush_black else v)

            tile = frame.resize((cell, cell), Image.LANCZOS)
            canvas = Image.new("RGB", (canvas_edge, canvas_edge), (0, 0, 0))
            centre = canvas_edge // 2
            half = cell // 2

            # Top copy is upright; the others are rotated so every copy's feet
            # face the outer edge of the canvas.
            canvas.paste(tile.rotate(180, expand=False), (centre - half, 0))
            canvas.paste(tile, (centre - half, canvas_edge - cell))
            canvas.paste(tile.rotate(270, expand=False), (0, centre - half))
            canvas.paste(tile.rotate(90, expand=False), (canvas_edge - cell, centre - half))

            canvas.save(frames_out / f"q{index:05d}.png")

        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-framerate", str(fps),
                "-i", str(frames_out / "q%05d.png"),
                "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    return out_path


def _sample_edges(video: str | Path, border_frac: float = 0.05) -> dict:
    info = probe(video)
    frames = info.get("frames") or 1
    with tempfile.TemporaryDirectory() as tmp:
        mid = extract_frame(video, max(0, frames // 2), Path(tmp) / "mid.png")
        image = Image.open(mid).convert("L")
        w, h = image.size
        band_w = max(1, int(w * border_frac))
        band_h = max(1, int(h * border_frac))
        pixels = image.load()
        values = []
        for y in range(0, band_h):
            for x in range(0, w, 4):
                values.append(pixels[x, y])
        for y in range(h - band_h, h):
            for x in range(0, w, 4):
                values.append(pixels[x, y])
        for x in range(0, band_w):
            for y in range(0, h, 4):
                values.append(pixels[x, y])
        for x in range(w - band_w, w):
            for y in range(0, h, 4):
                values.append(pixels[x, y])

    peak = max(values) if values else 255
    mean = sum(values) / len(values) if values else 255.0
    return {
        "edge_mean": round(mean, 3),
        "edge_peak": int(peak),
        "hologram_ready": peak <= 8,
        "sampled": len(values),
    }


@dataclass
class SeamReport:
    mean_abs_diff: float
    max_channel_diff: int
    first_index: int
    last_index: int

    @property
    def rating(self) -> str:
        if self.mean_abs_diff <= 2.0:
            return "seamless"
        if self.mean_abs_diff <= 6.0:
            return "good"
        if self.mean_abs_diff <= 14.0:
            return "visible"
        return "poor"

    def as_dict(self) -> dict:
        return {
            "mean_abs_diff": round(self.mean_abs_diff, 3),
            "max_channel_diff": self.max_channel_diff,
            "first_index": self.first_index,
            "last_index": self.last_index,
            "rating": self.rating,
        }


def measure_seam(video: str | Path, total_frames: int | None = None) -> SeamReport:
    """Compare frame 0 against the final frame."""
    info = probe(video)
    frames = total_frames or info.get("frames") or 0
    if not frames or frames < 2:
        raise ValueError(f"Cannot measure seam: video reports {frames} frames.")
    last_index = frames - 1

    with tempfile.TemporaryDirectory() as tmp:
        first_path = extract_frame(video, 0, Path(tmp) / "first.png")
        last_path = extract_frame(video, last_index, Path(tmp) / "last.png")
        first = Image.open(first_path).convert("RGB")
        last = Image.open(last_path).convert("RGB")
        if first.size != last.size:
            last = last.resize(first.size, Image.LANCZOS)
        diff = ImageChops.difference(first, last)
        stat = ImageStat.Stat(diff)
        mean = sum(stat.mean) / len(stat.mean)
        peak = max(int(value) for value in diff.getextrema()[0]) if diff.mode == "L" else max(
            int(hi) for _lo, hi in diff.getextrema()
        )

    return SeamReport(
        mean_abs_diff=mean,
        max_channel_diff=peak,
        first_index=0,
        last_index=last_index,
    )


@dataclass
class MotionReport:
    """How much the clip actually moves.

    A seamless loop is trivially achievable by not moving at all, so seam
    quality alone cannot tell a good breathing loop from a frozen frame. This
    measures mid-clip motion so that failure mode is visible instead of being
    reported as a success.

    Scope limit worth knowing: this detects *whether* the clip moves, not
    whether the pet is in the pose you asked for. A sitting, eyes-open animal
    that breathes slightly scores the same as a correctly sleeping one, so pose
    correctness still needs a look at the frames.
    """

    peak_diff: float
    mean_diff: float
    sampled_pairs: int

    @property
    def is_static(self) -> bool:
        # Calibrated against a losslessly frozen 640x640 h264 clip, which
        # measures ~0.007. Anything below 0.3 is encoder noise, not animation.
        return self.peak_diff < 0.3

    @property
    def rating(self) -> str:
        if self.is_static:
            return "static"
        if self.peak_diff < 3.0:
            return "subtle"
        if self.peak_diff < 12.0:
            return "alive"
        return "busy"

    def as_dict(self) -> dict:
        return {
            "peak_diff": round(self.peak_diff, 3),
            "mean_diff": round(self.mean_diff, 3),
            "sampled_pairs": self.sampled_pairs,
            "rating": self.rating,
            "is_static": self.is_static,
        }


def measure_motion(video: str | Path, samples: int = 6) -> MotionReport:
    """Compare the first frame against frames spread through the clip."""
    info = probe(video)
    frames = info.get("frames") or 0
    if frames < 3:
        raise ValueError(f"Cannot measure motion: video reports {frames} frames.")

    step = max(1, frames // (samples + 1))
    indices = list(range(step, frames - 1, step))[:samples]
    if not indices:
        indices = [frames // 2]

    diffs: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:
        base_path = extract_frame(video, 0, Path(tmp) / "base.png")
        base = Image.open(base_path).convert("RGB")
        for index in indices:
            other_path = extract_frame(video, index, Path(tmp) / f"f{index}.png")
            other = Image.open(other_path).convert("RGB")
            if other.size != base.size:
                other = other.resize(base.size, Image.LANCZOS)
            stat = ImageStat.Stat(ImageChops.difference(base, other))
            diffs.append(sum(stat.mean) / len(stat.mean))

    return MotionReport(
        peak_diff=max(diffs) if diffs else 0.0,
        mean_diff=sum(diffs) / len(diffs) if diffs else 0.0,
        sampled_pairs=len(diffs),
    )


def trim_tail(video: str | Path, out_path: str | Path, drop_frames: int = 1) -> Path:
    """Drop the trailing duplicate frame(s) so playback wraps without a stutter."""
    ffmpeg = _require("ffmpeg")
    info = probe(video)
    frames = info.get("frames")
    if not frames:
        raise ValueError("Cannot trim: frame count unavailable.")
    keep = max(1, frames - max(0, drop_frames))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-frames:v",
            str(keep),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out_path


def crossfade_loop(video: str | Path, out_path: str | Path, blend_frames: int = 6) -> Path:
    """Blend the clip's tail into its own head.

    Cuts the source into body + tail, then xfades the tail over the head. The
    result is shorter by `blend_frames`, and the wrap point becomes continuous
    even when the model drifted away from the opening pose.
    """
    ffmpeg = _require("ffmpeg")
    info = probe(video)
    frames = info.get("frames")
    fps = info.get("fps") or 24.0
    if not frames or frames <= blend_frames * 2:
        raise ValueError(f"Clip too short to crossfade: {frames} frames.")

    blend_s = blend_frames / fps
    duration = frames / fps
    body_end = duration - blend_s

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    filtergraph = (
        f"[0:v]trim=start=0:end={body_end:.6f},setpts=PTS-STARTPTS[body];"
        f"[1:v]trim=start={body_end:.6f},setpts=PTS-STARTPTS[tail];"
        f"[body][tail]xfade=transition=fade:duration={blend_s:.6f}:offset={body_end - blend_s:.6f}[out]"
    )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(video),
            "-filter_complex",
            filtergraph,
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out_path


def make_preview_gif(video: str | Path, out_path: str | Path, width: int = 360, fps: int = 12) -> Path:
    """Small looping gif so the seam and pose can be eyeballed without a player."""
    ffmpeg = _require("ffmpeg")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-loop",
            "0",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out_path


def finish_loop(
    video: str | Path,
    out_path: str | Path,
    mode: str = "trim",
    blend_frames: int = 6,
) -> tuple[Path, SeamReport, SeamReport | None]:
    """Measure the seam, repair it, and measure again."""
    before = measure_seam(video)

    if mode == "none":
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(video, out)
        return out, before, None

    if mode == "xfade":
        result = crossfade_loop(video, out_path, blend_frames=blend_frames)
    else:
        result = trim_tail(video, out_path, drop_frames=1)

    after = measure_seam(result)
    return result, before, after
