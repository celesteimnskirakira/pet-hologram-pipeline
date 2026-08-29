#!/usr/bin/env python3
"""Command line entry point for the pet loop pipeline.

    python cli.py price
    python cli.py run photo.jpg --pose curled_side
    python cli.py run photo.jpg --dry-run
    python cli.py still photo.jpg
    python cli.py loop still.png
    python cli.py seam clip.mp4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from petloop import (  # noqa: E402
    config,
    diagnostics,
    imaging,
    looping,
    pipeline,
    pricing,
    prompts,
    providers,
)


def log(stage: str, payload: dict) -> None:
    detail = " ".join(f"{key}={value}" for key, value in payload.items() if value is not None)
    print(f"[{stage}] {detail}".rstrip(), flush=True)


def build_spec(args: argparse.Namespace) -> config.PipelineSpec:
    spec = config.PipelineSpec(
        pet_kind=getattr(args, "pet", "auto"),
        pose=getattr(args, "pose", "curled_side"),
        provider=getattr(args, "provider", "ark"),
        dry_run=getattr(args, "dry_run", False),
        sleep_bridge=getattr(args, "sleep_bridge", True),
    )
    spec.video.resolution = getattr(args, "resolution", spec.video.resolution)
    spec.video.ratio = getattr(args, "ratio", spec.video.ratio)
    spec.video.loop_mode = getattr(args, "loop_mode", spec.video.loop_mode)
    if getattr(args, "seed", None) is not None:
        spec.video.seed = args.seed
    if getattr(args, "attempts", None):
        spec.still.max_attempts = args.attempts
    return spec


def cmd_price(args: argparse.Namespace) -> int:
    options = pricing.cheapest_options(seconds=args.seconds, fps=args.fps)
    promo = options[0].promo_active
    print(f"Seedance 2.0 mini, {args.seconds}s @ {args.fps}fps, image-to-video (no video input)")
    print(f"Promo active right now: {promo}  (40% of list, 2026-08-07 14:00 -> 2026-09-07 14:00 UTC+8)")
    print()
    print(f"{'resolution':<11}{'ratio':<8}{'pixels':<12}{'tokens':>10}{'list CNY':>11}{'you pay':>11}")
    for option in options:
        pixels = f"{option.width}x{option.height}"
        print(
            f"{option.resolution:<11}{option.ratio:<8}{pixels:<12}"
            f"{option.tokens:>10,}{option.list_cny:>11.3f}{option.effective_cny:>11.3f}"
        )
    print()
    best = options[0]
    print(f"Cheapest usable spec: {best.resolution} {best.ratio} -> {best.effective_cny:.3f} CNY per clip")
    print("Note: final billing uses usage.completion_tokens returned by the API.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    spec = build_spec(args)

    if not spec.dry_run:
        blockers = diagnostics.blocking_failures(diagnostics.run_all())
        if blockers:
            print("preflight failed:", file=sys.stderr)
            for check in blockers:
                print(f"  [{check.name}] {check.detail}", file=sys.stderr)
                if check.hint:
                    print(f"           hint: {check.hint}", file=sys.stderr)
            print("  run `python cli.py doctor` for the full report.", file=sys.stderr)
            return 2

    try:
        artifacts = pipeline.run(args.image, spec=spec, run_dir=args.out, on_step=log)
    except (providers.ProviderError, imaging.ImageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print()
    print("--- artifacts ---")
    for key, value in artifacts.as_dict().items():
        if key in {"traits", "metrics"} or value is None:
            continue
        print(f"{key}: {value}")
    print()
    print("--- metrics ---")
    print(json.dumps(artifacts.metrics, ensure_ascii=False, indent=2))
    return 0


def cmd_still(args: argparse.Namespace) -> int:
    spec = build_spec(args)
    run_dir = Path(args.out) if args.out else pipeline.make_run_dir(tag="still")
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        provider = providers.resolve(spec.provider)
        source = imaging.normalize_input(args.image)
        imaging.save(source, run_dir / "step1_source.png")
        traits = pipeline.extract_traits(provider, source)
        if spec.pet_kind != "auto":
            traits.raw["species"] = spec.pet_kind
        (run_dir / "traits.json").write_text(
            json.dumps(traits.raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        still_path, _raw, score, _ = pipeline.generate_still(
            provider, source, traits, spec.still, run_dir, log
        )
    except (providers.ProviderError, imaging.ImageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"still: {still_path}")
    print(f"background: {json.dumps(score.as_dict())}")
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    spec = build_spec(args)
    run_dir = Path(args.out) if args.out else pipeline.make_run_dir(tag="loop")
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        provider = providers.resolve(spec.provider)
        still = imaging.load_rgb(args.still)
        traits_path = Path(args.traits) if args.traits else None
        traits = prompts.PetTraits(
            raw=json.loads(traits_path.read_text(encoding="utf-8")) if traits_path else {}
        )
        if spec.pet_kind != "auto":
            traits.raw["species"] = spec.pet_kind
        raw_video, result = pipeline.generate_loop(
            provider, still, traits, spec.video, spec.pose, run_dir, log
        )
        final, before, after = looping.finish_loop(
            raw_video, run_dir / "step3_loop_5s.mp4", mode=spec.video.loop_mode
        )
    except (providers.ProviderError, imaging.ImageError, looping.FFmpegMissing) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"video: {final}")
    print(f"task: {result.task_id}  usage: {json.dumps(result.usage)}")
    print(f"seam before: {json.dumps(before.as_dict())}")
    if after:
        print(f"seam after:  {json.dumps(after.as_dict())}")
    return 0


def cmd_seam(args: argparse.Namespace) -> int:
    try:
        info = looping.probe(args.video)
        report = looping.measure_seam(args.video)
        motion = looping.measure_motion(args.video)
    except (looping.FFmpegMissing, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"probe": info, "seam": report.as_dict(), "motion": motion.as_dict()}, indent=2))
    if motion.is_static:
        print(
            "\nwarning: this clip is effectively static. The seam looks perfect only "
            "because nothing moves.",
            file=sys.stderr,
        )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = diagnostics.run_all()
    for check in checks:
        mark = "ok  " if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        if check.hint and not check.ok:
            print(f"         hint: {check.hint}")
    blockers = diagnostics.blocking_failures(checks)
    print()
    if blockers:
        print(f"{len(blockers)} blocking issue(s); generation will fail until resolved.")
        return 1
    print("Ready to generate.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="petloop", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    price = subparsers.add_parser("price", help="show Seedance 2.0 mini cost table")
    price.add_argument("--seconds", type=int, default=5)
    price.add_argument("--fps", type=int, default=24)
    price.set_defaults(func=cmd_price)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--provider", choices=["ark", "agnes"], default="ark")
        sub.add_argument("--pet", choices=["auto", "cat", "dog"], default="auto")
        sub.add_argument(
            "--pose",
            choices=[
                "curled_side", "loaf", "sprawl", "side_stretch", "curled_tight",
                "scratch_neck", "sleep", "groom", "walk",
            ],
            default="curled_side",
        )
        sub.add_argument("--resolution", choices=["480p", "720p"], default="480p")
        sub.add_argument("--ratio", choices=["1:1", "16:9", "9:16", "4:3", "3:4"], default="1:1")
        sub.add_argument("--loop-mode", choices=["trim", "xfade", "none"], default="trim", dest="loop_mode")
        sub.add_argument(
            "--no-sleep-bridge",
            action="store_false",
            dest="sleep_bridge",
            help="feed the front view straight to the video model instead of generating a sleeping still first",
        )
        sub.add_argument("--seed", type=int, default=None)
        sub.add_argument("--attempts", type=int, default=None, help="max still retries")
        sub.add_argument("--out", default=None, help="run directory")

    run_cmd = subparsers.add_parser("run", help="full three-step pipeline")
    run_cmd.add_argument("image")
    run_cmd.add_argument("--dry-run", action="store_true", dest="dry_run")
    add_common(run_cmd)
    run_cmd.set_defaults(func=cmd_run)

    still_cmd = subparsers.add_parser("still", help="step 2 only: black background front view")
    still_cmd.add_argument("image")
    add_common(still_cmd)
    still_cmd.set_defaults(func=cmd_still)

    loop_cmd = subparsers.add_parser("loop", help="step 3 only: looping sleep clip from a still")
    loop_cmd.add_argument("still")
    loop_cmd.add_argument("--traits", default=None, help="traits.json from a previous run")
    add_common(loop_cmd)
    loop_cmd.set_defaults(func=cmd_loop)

    seam_cmd = subparsers.add_parser("seam", help="measure first/last frame difference of a clip")
    seam_cmd.add_argument("video")
    seam_cmd.set_defaults(func=cmd_seam)

    doctor_cmd = subparsers.add_parser("doctor", help="check keys, proxy, endpoint, and ffmpeg")
    doctor_cmd.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
