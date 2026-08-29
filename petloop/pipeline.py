"""Three-step orchestrator: upload -> black-background front view -> looping clip."""

from __future__ import annotations

import base64
import json
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from . import config, imaging, looping, pricing, prompts, providers

StepHook = Callable[[str, dict[str, Any]], None]


@dataclass
class Artifacts:
    run_dir: Path
    source: Path | None = None
    still: Path | None = None
    still_raw: Path | None = None
    sleep_still: Path | None = None
    video_raw: Path | None = None
    video: Path | None = None
    preview_gif: Path | None = None
    report: Path | None = None
    # Roadshow mode: one entry per generated action.
    clips: list[dict[str, Any]] = field(default_factory=list)
    playlist: Path | None = None
    traits: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        def rel(path: Path | None) -> str | None:
            return str(path) if path else None

        return {
            "run_dir": str(self.run_dir),
            "source": rel(self.source),
            "still": rel(self.still),
            "still_raw": rel(self.still_raw),
            "sleep_still": rel(self.sleep_still),
            "video_raw": rel(self.video_raw),
            "video": rel(self.video),
            "preview_gif": rel(self.preview_gif),
            "clips": self.clips,
            "playlist": rel(self.playlist),
            "traits": self.traits,
            "metrics": self.metrics,
        }


def _noop(stage: str, payload: dict[str, Any]) -> None:  # pragma: no cover - default hook
    return None


def download(url: str, out_path: str | Path, timeout: float = 300.0) -> Path:
    """Fetch a generated asset. Handles both http(s) URLs and data URLs."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if url.startswith("data:"):
        header, _, encoded = url.partition(",")
        payload = base64.b64decode(encoded) if "base64" in header else encoded.encode("utf-8")
        out_path.write_bytes(payload)
        return out_path

    request = urllib.request.Request(url, headers={"User-Agent": providers.USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                out_path.write_bytes(response.read())
            return out_path
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt * 2)
    raise providers.ProviderError(f"Could not download {url[:120]}: {last_error}")


def make_run_dir(base: str | Path | None = None, tag: str = "run") -> Path:
    base_path = Path(base) if base else config.DEFAULT_WORKDIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = base_path / f"{stamp}-{tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def extract_traits(
    provider: providers.ArkProvider | providers.AgnesProvider,
    image: Image.Image,
) -> prompts.PetTraits:
    """Step 1.5: read the pet's identity off the upload."""
    text = provider.describe(image, prompts.VISION_INSTRUCTION, prompts.VISION_SYSTEM)
    traits = prompts.PetTraits.from_text(text)
    if not traits.raw:
        # Keep going with an empty trait set; the reference image still anchors identity.
        traits = prompts.PetTraits(raw={"species": "pet", "_note": "trait extraction returned no JSON"})
    return traits


def generate_still(
    provider: providers.ArkProvider | providers.AgnesProvider,
    reference: Image.Image,
    traits: prompts.PetTraits,
    spec: config.StillSpec,
    run_dir: Path,
    on_step: StepHook = _noop,
    prompt: str | None = None,
    stem: str = "step2_front_view_black",
    label: str = "still",
) -> tuple[Path, Path, imaging.BackgroundScore, int]:
    """Generate a black-background still, retried until the background validates.

    Used for both the front view (step 2) and the sleeping bridge frame that
    feeds the video step.
    """
    prompt = prompt or prompts.still_prompt(traits, has_reference=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"prompt_{label}.txt").write_text(prompt, encoding="utf-8")

    best: tuple[Path, imaging.BackgroundScore] | None = None

    for attempt in range(1, spec.max_attempts + 1):
        on_step(f"{label}_attempt", {"attempt": attempt, "max": spec.max_attempts})
        nudge = prompt
        if attempt > 1:
            nudge = (
                prompt
                + "\n\nCRITICAL RETRY NOTE\n"
                "- The previous attempt did not have a pure black background.\n"
                "- The background must be absolute black RGB (0,0,0) across every edge and corner.\n"
                "- Remove all backdrop lighting, gradients, and glow entirely."
            )
        url = provider.still(nudge, reference, spec)
        raw_path = download(url, run_dir / f"{label}_attempt{attempt}.png")
        image = imaging.load_rgb(raw_path)
        score = imaging.score_background(image)
        on_step(f"{label}_scored", {"attempt": attempt, **score.as_dict()})

        if best is None or score.mean_luma < best[1].mean_luma:
            best = (raw_path, score)

        if score.passes(spec.background_luma_max, spec.background_black_ratio_min):
            break

    if best is None:  # pragma: no cover - loop always assigns
        raise providers.ProviderError("Still generation produced no candidates.")

    raw_path, score = best
    cleaned = imaging.hard_clean_background(imaging.load_rgb(raw_path))
    still_path = imaging.save(cleaned, run_dir / f"{stem}.png")
    final_score = imaging.score_background(imaging.load_rgb(still_path))
    on_step(f"{label}_final", final_score.as_dict())
    return still_path, raw_path, final_score, spec.max_attempts


def generate_loop(
    provider: providers.ArkProvider | providers.AgnesProvider,
    still: Image.Image,
    traits: prompts.PetTraits,
    spec: config.VideoSpec,
    pose: str,
    run_dir: Path,
    on_step: StepHook = _noop,
    tag: str = "",
) -> tuple[Path, providers.VideoResult]:
    """Step 3: sleeping pet, 5s, head/tail loop."""
    prompt = prompts.video_prompt(traits, pose=pose, loop_hint=True)
    suffix = f"_{tag}" if tag else ""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"prompt_video{suffix}.txt").write_text(prompt, encoding="utf-8")

    # Square-crop the still so a 1:1 request needs no server-side cropping.
    frame = imaging.center_square(still) if spec.ratio == "1:1" else still

    def status_hook(status: str, task: dict[str, Any]) -> None:
        on_step("video_status", {"status": status, "task_id": task.get("id"), "pose": pose})

    result = provider.loop(
        prompt,
        frame,
        spec,
        negative=prompts.negative_prompt(),
        on_status=status_hook,
    )
    raw_video = download(result.url, run_dir / f"step3_raw{suffix}.mp4")
    return raw_video, result


def build_action(
    provider: providers.ArkProvider | providers.AgnesProvider,
    front_view: Image.Image,
    traits: prompts.PetTraits,
    spec: config.PipelineSpec,
    pose: str,
    run_dir: Path,
    on_step: StepHook = _noop,
) -> dict[str, Any]:
    """Produce one finished action: sleep still -> clip -> loop -> hologram render.

    Self-contained so several actions can run concurrently for the same pet.
    Failures are captured rather than raised: one bad pose should not lose the
    others while a visitor is standing at the rig.
    """
    entry: dict[str, Any] = {"pose": pose, "ok": False}
    try:
        bridge_path, _raw, bridge_score, _ = generate_still(
            provider,
            front_view,
            traits,
            spec.still,
            run_dir,
            on_step,
            prompt=prompts.sleep_still_prompt(traits, pose=pose),
            stem=f"pose_{pose}_still",
            label=f"pose_{pose}",
        )
        entry["still"] = str(bridge_path)
        entry["still_background"] = bridge_score.as_dict()

        raw_video, result = generate_loop(
            provider,
            imaging.load_rgb(bridge_path),
            traits,
            spec.video,
            pose,
            run_dir,
            on_step,
            tag=pose,
        )
        entry["task_id"] = result.task_id
        entry["usage"] = result.usage

        looped, seam_before, seam_after = looping.finish_loop(
            raw_video,
            run_dir / f"pose_{pose}_loop.mp4",
            mode=spec.video.loop_mode,
            blend_frames=spec.video.xfade_frames,
        )
        entry["loop"] = str(looped)
        entry["seam"] = (seam_after or seam_before).as_dict()

        motion = looping.measure_motion(looped)
        entry["motion"] = motion.as_dict()

        holo = spec.hologram
        holo_path = run_dir / f"pose_{pose}_hologram.mp4"
        if holo.rig == "quad":
            looping.render_quad_hologram(
                looped, holo_path, mirror=holo.mirror, crush_black=holo.crush_black
            )
        else:
            looping.render_for_hologram(
                looped,
                holo_path,
                rig="single",
                mirror=holo.mirror,
                flip_vertical=holo.flip_vertical,
                margin_frac=holo.margin_frac,
                crush_black=holo.crush_black,
            )
        entry["hologram"] = str(holo_path)
        entry["edges"] = looping.measure_black_floor(holo_path)
        entry["ok"] = True
        on_step("action_done", {"pose": pose, "seam": entry["seam"]["rating"]})
    except Exception as exc:  # noqa: BLE001 - keep the other actions alive
        entry["error"] = f"{type(exc).__name__}: {exc}"
        on_step("action_failed", {"pose": pose, "error": entry["error"]})
    return entry


def run(
    image_path: str | Path,
    spec: config.PipelineSpec | None = None,
    run_dir: str | Path | None = None,
    on_step: StepHook = _noop,
) -> Artifacts:
    """Run all steps end to end. See also run_actions for roadshow mode."""
    spec = spec or config.PipelineSpec()
    work_dir = Path(run_dir) if run_dir else make_run_dir()
    work_dir.mkdir(parents=True, exist_ok=True)
    artifacts = Artifacts(run_dir=work_dir)

    # Step 1: accept and normalise the upload.
    on_step("upload", {"path": str(image_path)})
    source = imaging.normalize_input(image_path)
    artifacts.source = imaging.save(source, work_dir / "step1_source.png")

    estimate = pricing.estimate(
        config.ARK_VIDEO_MODEL,
        resolution=spec.video.resolution,
        ratio=spec.video.ratio,
        seconds=spec.video.duration_s,
        fps=spec.video.fps,
    )
    artifacts.metrics["cost_estimate"] = estimate.as_dict()
    on_step("cost_estimate", estimate.as_dict())

    if spec.dry_run:
        artifacts.metrics["dry_run"] = True
        artifacts.report = _write_report(artifacts, spec)
        return artifacts

    provider = providers.resolve(spec.provider)
    artifacts.metrics["provider"] = provider.name
    on_step("provider", {"name": provider.name})

    # Adopt the provider's submission limit unless the caller set one. Agnes
    # allows 1 video task per minute; Ark allows 3 concurrent. Without this the
    # extra actions come back 429 and are lost.
    if not spec.video.submit_gap_s:
        spec.video.submit_gap_s = float(getattr(provider, "submit_gap_s", 0.0) or 0.0)
    artifacts.metrics["submit_gap_s"] = spec.video.submit_gap_s

    # Step 1.5: lock identity into structured traits.
    traits = extract_traits(provider, source)
    if spec.pet_kind != "auto":
        traits.raw["species"] = spec.pet_kind
    artifacts.traits = traits.raw
    (work_dir / "traits.json").write_text(
        json.dumps(traits.raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    on_step("traits", {"species": traits.species, "keys": len(traits.raw)})

    # Step 2: black-background front view.
    still_path, still_raw, still_score, _ = generate_still(
        provider, source, traits, spec.still, work_dir, on_step
    )
    artifacts.still = still_path
    artifacts.still_raw = still_raw
    artifacts.metrics["background"] = still_score.as_dict()
    artifacts.metrics["background_ok"] = still_score.passes(
        spec.still.background_luma_max, spec.still.background_black_ratio_min
    )

    # Step 2.5: sleeping bridge frame.
    #
    # Roadshow mode branches here: when several poses are requested, each one is
    # a self-contained action built concurrently, and the single-pose path below
    # is skipped entirely.
    if spec.poses:
        front_view = imaging.load_rgb(still_path)
        poses = list(dict.fromkeys(spec.poses))
        on_step("actions_start", {"poses": poses, "parallel": spec.parallel})
        started = time.monotonic()

        if spec.parallel and len(poses) > 1:
            # Wall clock is dominated by provider queue time, so running the
            # poses together turns 3x latency into roughly 1x.
            with ThreadPoolExecutor(max_workers=min(spec.max_workers, len(poses))) as pool:
                futures = {
                    pool.submit(
                        build_action,
                        provider,
                        front_view,
                        traits,
                        spec,
                        pose,
                        work_dir,
                        on_step,
                    ): pose
                    for pose in poses
                }
                results = {futures[f]: f.result() for f in as_completed(futures)}
            clips = [results[pose] for pose in poses]
        else:
            clips = [
                build_action(provider, front_view, traits, spec, pose, work_dir, on_step)
                for pose in poses
            ]

        artifacts.clips = clips
        elapsed = time.monotonic() - started
        ok = [c for c in clips if c.get("ok")]
        artifacts.metrics["actions"] = {
            "requested": len(poses),
            "succeeded": len(ok),
            "failed": [c["pose"] for c in clips if not c.get("ok")],
            "wall_clock_s": round(elapsed, 1),
            "parallel": bool(spec.parallel and len(poses) > 1),
        }

        if not ok:
            raise providers.ProviderError(
                "Every requested action failed: "
                + "; ".join(c.get("error", "unknown") for c in clips)
            )

        # Point the single-clip fields at the first success so existing consumers
        # and the web UI keep working unchanged.
        artifacts.video = Path(ok[0]["loop"])
        artifacts.sleep_still = Path(ok[0]["still"])
        artifacts.playlist = _write_playlist(artifacts, spec, ok)
        try:
            artifacts.preview_gif = looping.make_preview_gif(
                artifacts.video, work_dir / "preview_loop.gif"
            )
        except Exception as exc:  # noqa: BLE001 - preview is optional
            artifacts.metrics["preview_error"] = str(exc)

        on_step("actions_done", artifacts.metrics["actions"])
        artifacts.report = _write_report(artifacts, spec)
        return artifacts

    # Single-pose path.
    #
    # The front view is what the user asked for as a deliverable, but it is a
    # poor first frame for the video: turning a sitting, eyes-open portrait into
    # a curled sleeping animal is a big pose change, and first/last frame
    # conditioning actively resists it. Doing the pose change in image space
    # leaves the video model with only breathing to add.
    video_source_path = still_path
    if spec.sleep_bridge:
        sleep_path, _sleep_raw, sleep_score, _ = generate_still(
            provider,
            imaging.load_rgb(still_path),
            traits,
            spec.still,
            work_dir,
            on_step,
            prompt=prompts.sleep_still_prompt(traits, pose=spec.pose),
            stem="step2b_sleep_pose_black",
            label="sleep_still",
        )
        artifacts.sleep_still = sleep_path
        artifacts.metrics["sleep_background"] = sleep_score.as_dict()
        video_source_path = sleep_path

    # Step 3: 5s looping sleep clip.
    still_image = imaging.load_rgb(video_source_path)
    raw_video, video_result = generate_loop(
        provider, still_image, traits, spec.video, spec.pose, work_dir, on_step
    )
    artifacts.video_raw = raw_video
    artifacts.metrics["video_task_id"] = video_result.task_id
    artifacts.metrics["video_usage"] = video_result.usage

    completion_tokens = (video_result.usage or {}).get("completion_tokens")
    if isinstance(completion_tokens, (int, float)) and completion_tokens > 0:
        family = pricing.family_of(config.ARK_VIDEO_MODEL)
        unit = pricing.CNY_PER_M_TOKEN.get(family, 23.0)
        billed_list = completion_tokens / 1_000_000 * unit
        discount = estimate.discount or 1.0
        artifacts.metrics["actual_cost"] = {
            "completion_tokens": int(completion_tokens),
            "list_cny": round(billed_list, 4),
            "effective_cny": round(billed_list * discount, 4),
        }

    # Loop finishing.
    try:
        final_video, before, after = looping.finish_loop(
            raw_video,
            work_dir / "step3_loop_5s.mp4",
            mode=spec.video.loop_mode,
            blend_frames=spec.video.xfade_frames,
        )
        artifacts.video = final_video
        artifacts.metrics["seam_before"] = before.as_dict()
        artifacts.metrics["seam_after"] = after.as_dict() if after else None
        artifacts.metrics["video_probe"] = looping.probe(final_video)

        # A frozen clip loops perfectly, so seam quality alone would call this a
        # success. Check that the pet actually breathes.
        try:
            motion = looping.measure_motion(final_video)
            artifacts.metrics["motion"] = motion.as_dict()
            if motion.is_static:
                artifacts.metrics["motion_warning"] = (
                    "Clip is effectively static: the video model did not animate the pet. "
                    "The loop is seamless only because nothing moves."
                )
            on_step("motion_checked", motion.as_dict())
        except (ValueError, subprocess.CalledProcessError) as exc:
            artifacts.metrics["motion_error"] = str(exc)

        on_step("loop_finished", {"seam": (after or before).as_dict()})
        try:
            artifacts.preview_gif = looping.make_preview_gif(final_video, work_dir / "preview_loop.gif")
        except Exception as exc:  # noqa: BLE001 - preview is optional
            artifacts.metrics["preview_error"] = str(exc)
    except looping.FFmpegMissing as exc:
        artifacts.video = raw_video
        artifacts.metrics["loop_warning"] = str(exc)
        on_step("loop_skipped", {"reason": str(exc)})

    artifacts.report = _write_report(artifacts, spec)
    if not spec.keep_intermediate:
        for stale in work_dir.glob("still_attempt*.png"):
            if artifacts.still_raw and stale.samefile(artifacts.still_raw):
                continue
            stale.unlink(missing_ok=True)
    return artifacts


def _write_report(artifacts: Artifacts, spec: config.PipelineSpec) -> Path:
    payload: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "spec": {
            "pet_kind": spec.pet_kind,
            "pose": spec.pose,
            "provider": spec.provider,
            "video": {
                "duration_s": spec.video.duration_s,
                "resolution": spec.video.resolution,
                "ratio": spec.video.ratio,
                "fps": spec.video.fps,
                "loop_mode": spec.video.loop_mode,
            },
        },
        "artifacts": artifacts.as_dict(),
    }
    path = artifacts.run_dir / "report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_playlist(
    artifacts: Artifacts,
    spec: config.PipelineSpec,
    clips: list[dict[str, Any]],
) -> Path:
    """Manifest the display page polls to know what to play."""
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "species": artifacts.traits.get("species", "pet"),
        "rig": spec.hologram.rig,
        "mirrored": spec.hologram.mirror,
        "loop_seconds": spec.video.duration_s,
        "items": [
            {
                "pose": clip["pose"],
                "hologram": clip["hologram"],
                "loop": clip["loop"],
                "still": clip["still"],
                "seam": clip.get("seam", {}).get("rating"),
                "motion": clip.get("motion", {}).get("rating"),
            }
            for clip in clips
        ],
    }
    path = artifacts.run_dir / "playlist.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
