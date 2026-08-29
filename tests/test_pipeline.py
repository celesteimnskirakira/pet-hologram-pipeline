"""Tests for the local, deterministic parts of the pipeline.

Nothing here calls a paid API. Generation is exercised through a fake provider
so the orchestration, retry, and loop-finishing logic can be verified for free.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import urllib.error
from pathlib import Path

from PIL import Image, ImageDraw

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

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
from server import parse_multipart  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_pet_photo(path: Path, size: int = 900, bg=(140, 160, 175)) -> Path:
    """A crude stand-in for a user upload: colored background, blob subject."""
    image = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(image)
    draw.ellipse((size * 0.22, size * 0.22, size * 0.78, size * 0.82), fill=(216, 168, 96))
    draw.ellipse((size * 0.36, size * 0.42, size * 0.44, size * 0.50), fill=(30, 30, 30))
    draw.ellipse((size * 0.56, size * 0.42, size * 0.64, size * 0.50), fill=(30, 30, 30))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def make_black_bg_still(path: Path, size: int = 512, edge_value: int = 0) -> Path:
    image = Image.new("RGB", (size, size), (edge_value, edge_value, edge_value))
    draw = ImageDraw.Draw(image)
    draw.ellipse((size * 0.25, size * 0.25, size * 0.75, size * 0.75), fill=(210, 165, 95))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


class PricingTests(unittest.TestCase):
    def test_cheapest_spec_is_480p_square(self):
        options = pricing.cheapest_options(seconds=5, fps=24)
        best = options[0]
        self.assertEqual((best.resolution, best.ratio), ("480p", "1:1"))

    def test_matches_published_480p_square_list_price(self):
        # Ark docs: 480p 1:1 -> 640x640; (0+5)*640*640*24/1024 = 48000 tokens.
        estimate = pricing.estimate("doubao-seedance-2-0-mini", "480p", "1:1", seconds=5, fps=24)
        self.assertEqual(estimate.tokens, 48000)
        self.assertAlmostEqual(estimate.list_cny, 48000 / 1_000_000 * 23.00, places=6)

    def test_matches_published_720p_example(self):
        # Ark price example table lists 720p 16:9 5s at 2.48 CNY for mini.
        estimate = pricing.estimate("doubao-seedance-2-0-mini", "720p", "16:9", seconds=5, fps=24)
        self.assertAlmostEqual(estimate.list_cny, 2.484, places=3)

    def test_promo_applies_forty_percent(self):
        estimate = pricing.estimate(
            "doubao-seedance-2-0-mini",
            "480p",
            "1:1",
            seconds=5,
            now=pricing.PROMO_START,
        )
        self.assertTrue(estimate.promo_active)
        self.assertAlmostEqual(estimate.effective_cny, estimate.list_cny * 0.40, places=6)

    def test_promo_expires(self):
        after = pricing.PROMO_END.replace(day=8)
        estimate = pricing.estimate("doubao-seedance-2-0-mini", "480p", "1:1", seconds=5, now=after)
        self.assertFalse(estimate.promo_active)
        self.assertAlmostEqual(estimate.effective_cny, estimate.list_cny, places=6)

    def test_mini_is_cheaper_than_fast_and_standard(self):
        args = ("480p", "1:1")
        mini = pricing.estimate("doubao-seedance-2-0-mini", *args, seconds=5)
        fast = pricing.estimate("doubao-seedance-2-0-fast", *args, seconds=5)
        std = pricing.estimate("doubao-seedance-2-0", *args, seconds=5)
        self.assertLess(mini.list_cny, fast.list_cny)
        self.assertLess(fast.list_cny, std.list_cny)

    def test_unsupported_combo_rejected(self):
        with self.assertRaises(ValueError):
            pricing.estimate("doubao-seedance-2-0-mini", "1080p", "16:9", seconds=5)


class ImagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = BASE_DIR / "runs" / "_test_imaging"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalize_rejects_tiny_then_upscales_to_minimum(self):
        small = self.tmp / "small.png"
        Image.new("RGB", (120, 120), (10, 10, 10)).save(small)
        normalized = imaging.normalize_input(small)
        self.assertGreaterEqual(min(normalized.size), imaging.MIN_EDGE)

    def test_normalize_clamps_large_edge(self):
        big = self.tmp / "big.png"
        Image.new("RGB", (4000, 3000), (10, 10, 10)).save(big)
        normalized = imaging.normalize_input(big, max_edge=2048)
        self.assertLessEqual(max(normalized.size), 2048)

    def test_extreme_aspect_ratio_rejected(self):
        wide = self.tmp / "wide.png"
        Image.new("RGB", (3000, 320), (10, 10, 10)).save(wide)
        with self.assertRaises(imaging.ImageError):
            imaging.normalize_input(wide, max_edge=3000)

    def test_unsupported_extension_rejected(self):
        bogus = self.tmp / "not_image.txt"
        bogus.write_text("nope", encoding="utf-8")
        with self.assertRaises(imaging.ImageError):
            imaging.load_rgb(bogus)

    def test_human_portrait_is_rejected_before_generation(self):
        traits = prompts.PetTraits(
            raw={"species": "other", "breed_guess": "human", "coat_length": "not applicable"}
        )
        with self.assertRaisesRegex(imaging.ImageError, "请上传猫、狗或其他宠物"):
            pipeline.ensure_pet_subject(traits)

    def test_other_nonhuman_pet_is_allowed(self):
        traits = prompts.PetTraits(raw={"species": "other", "breed_guess": "rabbit"})
        pipeline.ensure_pet_subject(traits)

    def test_background_score_separates_black_from_colored(self):
        black = imaging.load_rgb(make_black_bg_still(self.tmp / "black.png"))
        colored = imaging.load_rgb(make_pet_photo(self.tmp / "colored.png", size=512))
        black_score = imaging.score_background(black)
        colored_score = imaging.score_background(colored)
        spec = config.StillSpec()
        self.assertTrue(black_score.passes(spec.background_luma_max, spec.background_black_ratio_min))
        self.assertFalse(colored_score.passes(spec.background_luma_max, spec.background_black_ratio_min))

    def test_hard_clean_forces_pure_black_edges(self):
        near = imaging.load_rgb(make_black_bg_still(self.tmp / "near.png", edge_value=14))
        self.assertGreater(imaging.score_background(near).mean_luma, 0)
        cleaned = imaging.hard_clean_background(near)
        self.assertAlmostEqual(imaging.score_background(cleaned).mean_luma, 0.0, places=6)

    def test_subject_touching_bottom_edge_still_passes(self):
        """Regression: a well-composed portrait was rejected for being too big.

        The old scorer sampled a fixed outer ring, so a subject running to the
        bottom edge made the lower band measure fur instead of backdrop. Larger
        animals failed more often, which is backwards.
        """
        path = self.tmp / "tall.png"
        size = 512
        image = Image.new("RGB", (size, size), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        # Body fills the lower half and reaches the bottom edge.
        draw.ellipse((size * 0.2, size * 0.35, size * 0.8, size + 40), fill=(210, 165, 95))
        image.save(path)

        spec = config.StillSpec()
        score = imaging.score_background(imaging.load_rgb(path))
        self.assertTrue(
            score.passes(spec.background_luma_max, spec.background_black_ratio_min),
            f"pure black backdrop rejected: {score.as_dict()}",
        )

    def test_gradient_backdrop_still_fails(self):
        """The relaxed scorer must still catch a non-black background."""
        path = self.tmp / "gradient.png"
        size = 512
        image = Image.new("RGB", (size, size))
        pixels = image.load()
        for y in range(size):
            shade = int(60 * y / size) + 20
            for x in range(size):
                pixels[x, y] = (shade, shade, shade)
        ImageDraw.Draw(image).ellipse(
            (size * 0.3, size * 0.3, size * 0.7, size * 0.7), fill=(210, 165, 95)
        )
        image.save(path)

        spec = config.StillSpec()
        score = imaging.score_background(imaging.load_rgb(path))
        self.assertFalse(score.passes(spec.background_luma_max, spec.background_black_ratio_min))

    def test_center_square_is_square(self):
        image = Image.new("RGB", (1200, 800), (0, 0, 0))
        self.assertEqual(imaging.center_square(image).size, (800, 800))

    def test_data_url_round_trip(self):
        image = Image.new("RGB", (320, 320), (5, 5, 5))
        url = imaging.to_data_url(image, "jpeg")
        self.assertTrue(url.startswith("data:image/jpeg;base64,"))


class PromptTests(unittest.TestCase):
    def test_traits_parse_from_fenced_json(self):
        text = """```json
        {"species": "cat", "eye_color": "amber", "markings": ["white chest patch"]}
        ```"""
        traits = prompts.PetTraits.from_text(text)
        self.assertEqual(traits.species, "cat")
        self.assertIn("white chest patch", traits.identity_block())

    def test_traits_parse_from_surrounding_prose(self):
        traits = prompts.PetTraits.from_text('Sure! {"species": "dog", "ear_shape": "floppy"} Hope that helps.')
        self.assertEqual(traits.species, "dog")

    def test_traits_tolerate_garbage(self):
        self.assertEqual(prompts.PetTraits.from_text("not json at all").raw, {})

    def test_not_visible_traits_are_dropped(self):
        traits = prompts.PetTraits(raw={"species": "cat", "tail_notes": "not visible"})
        self.assertNotIn("tail notes", traits.identity_block())

    def test_still_prompt_demands_pure_black_and_front_view(self):
        prompt = prompts.still_prompt(prompts.PetTraits(raw={"species": "cat"}))
        for needle in ("Pure solid black background", "RGB (0, 0, 0)", "front view", "no gradient"):
            self.assertIn(needle.lower(), prompt.lower())

    def test_video_prompt_locks_loop_and_stillness(self):
        prompt = prompts.video_prompt(prompts.PetTraits(raw={"species": "dog"}), pose="loaf")
        for needle in ("loops seamlessly", "Locked-off static camera", "Eyes stay closed", "loaf"):
            self.assertIn(needle.lower(), prompt.lower())

    def test_negative_prompt_covers_common_failures(self):
        negative = prompts.negative_prompt()
        for needle in ("camera movement", "eyes opening", "changing markings", "watermark"):
            self.assertIn(needle, negative)

    def test_supplied_action_prompts_are_loaded(self):
        expected = {
            "scratch_neck": "后爪连续轻挠同侧脖子",
            "sleep": "保持安静睡眠状态",
            "groom": "低头连续舔舐前爪",
            "walk": "自然的原地行走循环",
        }
        for action, phrase in expected.items():
            self.assertIn(phrase, prompts.action_video_prompt(action))

    def test_walk_prompt_is_not_overridden_by_sleep_rules(self):
        traits = prompts.PetTraits(raw={"species": "cat"})
        prompt = prompts.video_prompt(traits, pose="walk")
        self.assertIn("自然的原地行走循环", prompt)
        self.assertNotIn("Eyes stay closed", prompt)
        negative = prompts.negative_prompt("walk")
        self.assertNotIn("standing up", negative)
        self.assertNotIn(", walking,", negative)

    def test_action_bridge_frames_match_selected_action(self):
        traits = prompts.PetTraits(raw={"species": "dog"})
        self.assertIn("hind paws", prompts.action_still_prompt(traits, "scratch_neck"))
        self.assertIn("Eyes fully closed", prompts.action_still_prompt(traits, "sleep"))
        self.assertIn("front paws", prompts.action_still_prompt(traits, "groom"))
        self.assertIn("in-place walking cycle", prompts.action_still_prompt(traits, "walk"))


class MultipartTests(unittest.TestCase):
    def test_parses_fields_and_file(self):
        boundary = "----petloopTEST"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="pose"\r\n\r\nloaf\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="image"; filename="cat.png"\r\n'
            "Content-Type: image/png\r\n\r\nBINARYDATA\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        fields, upload = parse_multipart(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(fields["pose"], "loaf")
        self.assertIsNotNone(upload)
        self.assertEqual(upload[0], "cat.png")
        self.assertEqual(upload[1], b"BINARYDATA")

    def test_missing_file_returns_none(self):
        boundary = "----petloopTEST"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="pose"\r\n\r\nloaf\r\n'
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        _fields, upload = parse_multipart(body, f"multipart/form-data; boundary={boundary}")
        self.assertIsNone(upload)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.saved = {var: os.environ.get(var) for var in diagnostics.PROXY_VARS}
        for var in diagnostics.PROXY_VARS:
            os.environ.pop(var, None)

    def tearDown(self):
        for var, value in self.saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    def test_no_proxy_is_ok(self):
        check = diagnostics.check_proxy()
        self.assertTrue(check.ok)

    def test_dead_proxy_is_flagged_with_hint(self):
        # Port 1 on loopback is reliably closed.
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"
        check = diagnostics.check_proxy()
        self.assertFalse(check.ok)
        self.assertIn("unset", check.hint)

    def test_dead_proxy_is_a_blocking_failure(self):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
        blockers = diagnostics.blocking_failures(diagnostics.run_all())
        self.assertIn("proxy", [check.name for check in blockers])

    def test_ffmpeg_missing_is_not_blocking(self):
        # ffmpeg only degrades loop finishing, so it must never block a run.
        degraded = diagnostics.Check("ffmpeg", False, "missing")
        self.assertEqual(diagnostics.blocking_failures([degraded]), [])

    def test_run_all_covers_every_check(self):
        names = {check.name for check in diagnostics.run_all()}
        self.assertEqual(names, {"api_key", "proxy", "endpoint", "ffmpeg"})


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = BASE_DIR / "runs" / "_test_env"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.path = self.tmp / ".env"
        self.saved = os.environ.get("PETLOOP_TEST_KEY")
        os.environ.pop("PETLOOP_TEST_KEY", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.saved is None:
            os.environ.pop("PETLOOP_TEST_KEY", None)
        else:
            os.environ["PETLOOP_TEST_KEY"] = self.saved

    def test_loads_plain_and_quoted_and_exported_lines(self):
        self.path.write_text(
            "# a comment\n"
            "\n"
            "PETLOOP_TEST_KEY='sk-quoted'\n",
            encoding="utf-8",
        )
        loaded = config.load_env_file(self.path)
        self.assertEqual(loaded["PETLOOP_TEST_KEY"], "sk-quoted")
        self.assertEqual(os.environ["PETLOOP_TEST_KEY"], "sk-quoted")

    def test_export_prefix_is_stripped(self):
        self.path.write_text("export PETLOOP_TEST_KEY=sk-exported\n", encoding="utf-8")
        loaded = config.load_env_file(self.path)
        self.assertEqual(loaded["PETLOOP_TEST_KEY"], "sk-exported")

    def test_real_environment_wins_by_default(self):
        os.environ["PETLOOP_TEST_KEY"] = "sk-from-shell"
        self.path.write_text("PETLOOP_TEST_KEY=sk-from-file\n", encoding="utf-8")
        config.load_env_file(self.path)
        self.assertEqual(os.environ["PETLOOP_TEST_KEY"], "sk-from-shell")

    def test_override_flag_forces_file_value(self):
        os.environ["PETLOOP_TEST_KEY"] = "sk-from-shell"
        self.path.write_text("PETLOOP_TEST_KEY=sk-from-file\n", encoding="utf-8")
        config.load_env_file(self.path, override=True)
        self.assertEqual(os.environ["PETLOOP_TEST_KEY"], "sk-from-file")

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(config.load_env_file(self.tmp / "nope.env"), {})

    def test_example_template_ships_and_gitignore_protects_env(self):
        self.assertTrue((BASE_DIR / ".env.example").is_file())
        ignored = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignored)


class RateLimitTests(unittest.TestCase):
    """Regression cover for the 429 that silently dropped two of three actions."""

    def setUp(self):
        providers._LAST_SUBMIT["at"] = 0.0

    def test_rate_limited_is_a_provider_error_subclass(self):
        # Callers that only catch ProviderError must still see it.
        self.assertTrue(issubclass(providers.RateLimited, providers.ProviderError))

    def test_submission_retries_after_a_rate_limit(self):
        calls = {"n": 0}

        def submit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise providers.RateLimited("429", retry_after_s=0.01)
            return {"id": "task-2"}

        spec = config.VideoSpec()
        spec.submit_gap_s = 0.0
        result = providers._submit_with_pacing(submit, spec)
        self.assertEqual(result["id"], "task-2")
        self.assertEqual(calls["n"], 2)

    def test_submission_gives_up_after_configured_retries(self):
        def submit():
            raise providers.RateLimited("429", retry_after_s=0.01)

        spec = config.VideoSpec()
        spec.submit_gap_s = 0.0
        spec.rate_limit_retries = 2
        with self.assertRaises(providers.RateLimited):
            providers._submit_with_pacing(submit, spec)

    def test_pacing_enforces_a_gap_between_submissions(self):
        providers.pace_video_submit(0.0)
        first = time.monotonic()
        providers.pace_video_submit(0.25)
        providers.pace_video_submit(0.25)
        self.assertGreaterEqual(time.monotonic() - first, 0.2)

    def test_zero_gap_does_not_block(self):
        started = time.monotonic()
        for _ in range(5):
            providers.pace_video_submit(0.0)
        self.assertLess(time.monotonic() - started, 0.1)

    def test_provider_defaults_reflect_measured_limits(self):
        # Agnes measured at 1 video task per minute; Ark allows 3 concurrent.
        self.assertGreater(providers.AgnesProvider.submit_gap_s, 60.0)
        self.assertEqual(providers.ArkProvider.submit_gap_s, 0.0)


class NetworkResilienceTests(unittest.TestCase):
    """Cover for the TLS drop that discarded an already-generated video."""

    def test_ssl_error_is_not_a_urlerror(self):
        # This is why the original handler missed it.
        import ssl

        self.assertFalse(issubclass(ssl.SSLError, urllib.error.URLError))

    def test_request_layer_handles_ssl_and_connection_errors(self):
        source = (BASE_DIR / "petloop" / "providers.py").read_text(encoding="utf-8")
        self.assertIn("ssl.SSLError", source)
        self.assertIn("ConnectionError", source)

    def test_polling_survives_a_transient_failure(self):
        calls = {"n": 0}

        def flaky(method, url, key, payload=None, timeout=180.0, retries=3):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"video_id": "video_abc"}
            if calls["n"] == 2:
                raise providers.ProviderError("SSL: UNEXPECTED_EOF_WHILE_READING")
            return {"status": "completed", "video_url": "https://example.test/v.mp4"}

        spec = config.VideoSpec()
        spec.poll_interval_s = 0.01
        spec.poll_timeout_s = 5.0
        spec.submit_gap_s = 0.0

        seen: list[str] = []
        original = providers._request
        providers._request = flaky
        try:
            result = providers.AgnesProvider("k").loop(
                "prompt",
                Image.new("RGB", (320, 320), (0, 0, 0)),
                spec,
                on_status=lambda s, t: seen.append(s),
            )
        finally:
            providers._request = original

        # The finished video is still delivered despite the lost poll.
        self.assertEqual(result.url, "https://example.test/v.mp4")
        self.assertIn("poll_retry", seen)


class FakeProvider:
    """Stand-in backend: no network, scriptable failure modes."""

    name = "fake"

    def __init__(self, tmp: Path, bad_bg_attempts: int = 0):
        self.tmp = tmp
        self.bad_bg_attempts = bad_bg_attempts
        self.still_calls = 0
        self.loop_calls = 0
        self.last_video_prompt = ""
        self.still_prompts: list[str] = []

    def describe(self, image, instruction, system):
        return json.dumps(
            {
                "species": "cat",
                "coat_pattern": "tabby",
                "primary_colors": ["ginger", "cream"],
                "markings": ["white chest patch", "white left front paw"],
                "eye_color": "amber",
                "ear_shape": "upright triangular",
            }
        )

    def still(self, prompt, reference, spec):
        self.still_calls += 1
        self.still_prompts.append(prompt)
        path = self.tmp / f"fake_still_{self.still_calls}.png"
        edge = 90 if self.still_calls <= self.bad_bg_attempts else 0
        make_black_bg_still(path, size=512, edge_value=edge)
        return imaging.to_data_url(imaging.load_rgb(path), "png")

    def loop(self, prompt, first_frame, spec, negative=None, on_status=None):
        self.loop_calls += 1
        self.last_video_prompt = prompt
        video = self.tmp / "fake_clip.mp4"
        synth_video(first_frame, video, seconds=spec.duration_s, fps=spec.fps)
        if on_status:
            on_status("succeeded", {"id": "fake-task"})
        from petloop.providers import VideoResult

        return VideoResult(
            url=video.resolve().as_uri(),
            task_id="fake-task",
            usage={"completion_tokens": 48000},
            raw={},
        )


def synth_video(frame: Image.Image, out_path: Path, seconds: int = 5, fps: int = 24) -> Path:
    """Build a breathing-like clip whose last frame repeats the first."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = out_path.parent / "_frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    base = imaging.center_square(frame).resize((320, 320), Image.LANCZOS)
    total = seconds * fps
    for index in range(total):
        # Full sine cycle: the final frame lands back on the opening pose,
        # then we append one exact duplicate to mimic model behaviour.
        import math

        phase = math.sin(index / (total - 1) * 2 * math.pi)
        shift = int(round(phase * 3))
        canvas = Image.new("RGB", base.size, (0, 0, 0))
        canvas.paste(base, (0, shift))
        canvas.save(frames_dir / f"f{index:04d}.png")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "f%04d.png"),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(frames_dir, ignore_errors=True)
    return out_path


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
class LoopingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = BASE_DIR / "runs" / "_test_loop"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        still = imaging.load_rgb(make_black_bg_still(self.tmp / "still.png"))
        self.video = synth_video(still, self.tmp / "clip.mp4", seconds=5, fps=24)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_probe_reports_expected_shape(self):
        info = looping.probe(self.video)
        self.assertEqual(info["frames"], 120)
        self.assertAlmostEqual(info["fps"], 24.0, places=1)

    def test_seam_measurable_and_rated(self):
        report = looping.measure_seam(self.video)
        self.assertGreaterEqual(report.mean_abs_diff, 0.0)
        self.assertIn(report.rating, {"seamless", "good", "visible", "poor"})

    def test_trim_drops_one_frame(self):
        trimmed = looping.trim_tail(self.video, self.tmp / "trimmed.mp4", drop_frames=1)
        self.assertEqual(looping.probe(trimmed)["frames"], 119)

    def test_xfade_shortens_and_keeps_video_valid(self):
        faded = looping.crossfade_loop(self.video, self.tmp / "faded.mp4", blend_frames=6)
        info = looping.probe(faded)
        self.assertIsNotNone(info["frames"])
        self.assertLess(info["frames"], 120)

    def test_finish_loop_none_mode_copies_unchanged(self):
        out, before, after = looping.finish_loop(self.video, self.tmp / "same.mp4", mode="none")
        self.assertIsNone(after)
        self.assertEqual(looping.probe(out)["frames"], 120)
        self.assertIsInstance(before.as_dict()["rating"], str)

    def test_preview_gif_created(self):
        gif = looping.make_preview_gif(self.video, self.tmp / "preview.gif", width=160, fps=8)
        self.assertTrue(gif.exists() and gif.stat().st_size > 0)

    def test_motion_detected_in_a_moving_clip(self):
        motion = looping.measure_motion(self.video)
        self.assertFalse(motion.is_static)
        self.assertGreater(motion.peak_diff, 0.3)

    def test_frozen_clip_is_reported_static(self):
        # Encode a deliberately frozen clip; only encoder noise should register.
        frozen = self.tmp / "frozen.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-loop", "1",
                "-i", str(self.tmp / "still.png"),
                "-t", "3", "-r", "24", "-vf", "scale=320:320",
                "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                "-pix_fmt", "yuv420p", str(frozen),
            ],
            check=True,
            capture_output=True,
        )
        motion = looping.measure_motion(frozen)
        self.assertTrue(motion.is_static)
        self.assertEqual(motion.rating, "static")

    def test_static_threshold_sits_above_encoder_noise(self):
        # Measured baselines: frozen h264 ~0.007, real generated clip ~2.0.
        noise = looping.MotionReport(peak_diff=0.007, mean_diff=0.007, sampled_pairs=6)
        real = looping.MotionReport(peak_diff=2.0, mean_diff=1.4, sampled_pairs=6)
        self.assertTrue(noise.is_static)
        self.assertFalse(real.is_static)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not installed")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = BASE_DIR / "runs" / "_test_pipeline"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.photo = make_pet_photo(self.tmp / "upload.png")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, provider, spec=None):
        original = pipeline.providers.resolve
        pipeline.providers.resolve = lambda preferred="ark": provider
        try:
            return pipeline.run(self.photo, spec=spec or config.PipelineSpec(), run_dir=self.tmp / "run", on_step=lambda *_: None)
        finally:
            pipeline.providers.resolve = original

    def test_end_to_end_produces_all_artifacts(self):
        provider = FakeProvider(self.tmp)
        artifacts = self._run(provider)

        self.assertTrue(artifacts.still.exists())
        self.assertTrue(artifacts.video.exists())
        self.assertTrue(artifacts.report.exists())
        self.assertTrue(artifacts.metrics["background_ok"])
        self.assertEqual(artifacts.traits["species"], "cat")

        # Step 2 contract: the delivered still must be pure black at the edges.
        score = imaging.score_background(imaging.load_rgb(artifacts.still))
        self.assertAlmostEqual(score.mean_luma, 0.0, places=6)

        # Step 3 contract: 5 seconds, and the trailing duplicate frame is gone.
        info = artifacts.metrics["video_probe"]
        self.assertEqual(info["frames"], 119)
        self.assertIn("seam_after", artifacts.metrics)

        report = json.loads(artifacts.report.read_text(encoding="utf-8"))
        self.assertEqual(report["spec"]["video"]["duration_s"], 5)

    def test_still_retries_until_background_is_black(self):
        provider = FakeProvider(self.tmp, bad_bg_attempts=2)
        artifacts = self._run(provider)
        # Two rejected backgrounds, then a passing one: 3 calls for the front
        # view. The sleep bridge frame then succeeds first try, so 4 total.
        self.assertEqual(provider.still_calls, 4)
        self.assertTrue(artifacts.metrics["background_ok"])

    def test_still_retries_counted_per_stage(self):
        provider = FakeProvider(self.tmp, bad_bg_attempts=2)
        self._run(provider, config.PipelineSpec(sleep_bridge=False))
        # With the bridge off, only the front view runs: 2 failures + 1 pass.
        self.assertEqual(provider.still_calls, 3)

    def test_traits_flow_into_video_prompt(self):
        provider = FakeProvider(self.tmp)
        self._run(provider)
        self.assertIn("white left front paw", provider.last_video_prompt)
        self.assertIn("loops seamlessly", provider.last_video_prompt)

    def test_actual_cost_derived_from_usage_tokens(self):
        provider = FakeProvider(self.tmp)
        artifacts = self._run(provider)
        actual = artifacts.metrics["actual_cost"]
        self.assertEqual(actual["completion_tokens"], 48000)
        self.assertAlmostEqual(actual["list_cny"], 48000 / 1_000_000 * 23.0, places=4)

    def test_dry_run_skips_generation(self):
        spec = config.PipelineSpec(dry_run=True)
        artifacts = pipeline.run(self.photo, spec=spec, run_dir=self.tmp / "dry", on_step=lambda *_: None)
        self.assertTrue(artifacts.metrics["dry_run"])
        self.assertIsNone(artifacts.still)
        self.assertIn("cost_estimate", artifacts.metrics)

    def test_pet_kind_override_wins(self):
        provider = FakeProvider(self.tmp)
        spec = config.PipelineSpec(pet_kind="dog")
        artifacts = self._run(provider, spec)
        self.assertEqual(artifacts.traits["species"], "dog")

    def test_sleep_bridge_generates_a_second_still_and_feeds_the_video(self):
        provider = FakeProvider(self.tmp)
        artifacts = self._run(provider)

        # Two stills: the front-view deliverable, then the sleeping bridge frame.
        self.assertEqual(provider.still_calls, 2)
        self.assertIsNotNone(artifacts.sleep_still)
        self.assertTrue(artifacts.sleep_still.exists())
        self.assertNotEqual(artifacts.still, artifacts.sleep_still)

        # The front view stays a straight-on portrait; the bridge frame lies down.
        front, sleeping = provider.still_prompts
        self.assertIn("front-facing", front.lower())
        self.assertIn("lying", sleeping.lower())
        self.assertIn("eyes fully closed", sleeping.lower())

    def test_sleep_bridge_can_be_disabled(self):
        provider = FakeProvider(self.tmp)
        spec = config.PipelineSpec(sleep_bridge=False)
        artifacts = self._run(provider, spec)
        self.assertEqual(provider.still_calls, 1)
        self.assertIsNone(artifacts.sleep_still)

    def test_sleep_prompt_tracks_requested_pose(self):
        provider = FakeProvider(self.tmp)
        self._run(provider, config.PipelineSpec(pose="sprawl"))
        self.assertIn("splayed", provider.still_prompts[1].lower())

    def test_motion_metric_recorded_for_the_clip(self):
        provider = FakeProvider(self.tmp)
        artifacts = self._run(provider)
        self.assertIn("motion", artifacts.metrics)
        self.assertIn("rating", artifacts.metrics["motion"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
