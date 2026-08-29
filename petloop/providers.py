"""Provider adapters.

Two backends implement the same three calls the pipeline needs:

    describe(image)  -> trait JSON text
    still(prompt)    -> one image URL or data URL
    loop(prompt)     -> one mp4 URL

`ark` is the primary path: Seedance 2.0 mini is billed per token there and
supports first_frame + last_frame, which is what makes a clean head/tail loop
possible. `agnes` is a fallback for when no Ark key is configured.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from PIL import Image

from . import config, imaging

USER_AGENT = "petloop/1.0"

# Providers rate limit video task creation per account, not per connection.
# Agnes measured at 1 request / minute. Serialising only the submission call
# still lets several actions overlap during the long poll, which is where
# nearly all the wall clock goes.
_SUBMIT_LOCK = threading.Lock()
_LAST_SUBMIT = {"at": 0.0}


def pace_video_submit(min_gap_s: float, on_wait: Callable[[float], None] | None = None) -> None:
    """Block until at least `min_gap_s` has passed since the last submission."""
    if min_gap_s <= 0:
        return
    with _SUBMIT_LOCK:
        wait = _LAST_SUBMIT["at"] + min_gap_s - time.monotonic()
        if wait > 0:
            if on_wait:
                on_wait(wait)
            time.sleep(wait)
        _LAST_SUBMIT["at"] = time.monotonic()


def _submit_with_pacing(
    submit: Callable[[], dict[str, Any]],
    spec: Any,
    on_status: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Create a video task, pacing submissions and waiting out rate limits.

    Without this, running several actions concurrently trips the provider's
    per-account submission limit and the extra actions are simply lost.
    """
    gap = float(getattr(spec, "submit_gap_s", 0.0) or 0.0)
    attempts = int(getattr(spec, "rate_limit_retries", 3) or 0) + 1

    last: RateLimited | None = None
    for attempt in range(attempts):
        pace_video_submit(
            gap,
            on_wait=lambda w: on_status("pacing", {"wait_s": round(w, 1)}) if on_status else None,
        )
        try:
            return submit()
        except RateLimited as exc:
            last = exc
            if attempt >= attempts - 1:
                break
            if on_status:
                on_status(
                    "rate_limited",
                    {"wait_s": round(exc.retry_after_s, 1), "attempt": attempt + 1},
                )
            # Mark the clock so other threads also hold off.
            with _SUBMIT_LOCK:
                _LAST_SUBMIT["at"] = time.monotonic()
            time.sleep(exc.retry_after_s)
    raise last or ProviderError("Video submission failed for an unknown reason.")


class ProviderError(RuntimeError):
    """API call failed in a way the caller should surface to the user."""


class RateLimited(ProviderError):
    """Provider refused the request because of a rate limit.

    Separate from ProviderError so callers can wait and retry instead of
    treating the action as lost.
    """

    def __init__(self, message: str, retry_after_s: float = 60.0) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass
class VideoResult:
    url: str
    task_id: str | None
    usage: dict[str, Any]
    raw: dict[str, Any]


def _request(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 180.0,
    retries: int = 3,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }

    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", "replace")
            return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            if exc.code == 429:
                # Surface rate limits distinctly so a scheduler can pace itself
                # rather than discarding the work.
                hinted = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(hinted) if hinted else 65.0
                except ValueError:
                    wait = 65.0
                raise RateLimited(f"HTTP 429 from {url}: {detail}", retry_after_s=wait) from exc
            # 429/5xx are worth another try; 4xx client errors are not.
            if exc.code in {500, 502, 503, 504} and attempt < retries - 1:
                last_error = ProviderError(f"HTTP {exc.code}: {detail}")
                time.sleep(2 ** attempt * 2)
                continue
            raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
        # ssl.SSLError is not a URLError subclass, so a mid-transfer TLS drop
        # used to abort the whole action even though the video had already been
        # generated. Venue WiFi makes these transient drops common.
        except (
            urllib.error.URLError,
            ssl.SSLError,
            TimeoutError,
            ConnectionError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise ProviderError(f"Request to {url} failed: {exc}") from exc
    raise ProviderError(f"Request to {url} failed after {retries} attempts: {last_error}")


def _first_image_url(data: dict[str, Any]) -> str:
    items = data.get("data")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("error"):
                raise ProviderError(f"Image generation rejected: {item['error']}")
            url = item.get("url")
            if isinstance(url, str) and url:
                return url
            b64 = item.get("b64_json")
            if isinstance(b64, str) and b64:
                return f"[image omitted]{b64}"
    for key in ("url", "image_url", "image"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    raise ProviderError(f"No image in response: {json.dumps(data)[:400]}")


class ArkProvider:
    """Volcengine Ark. Cheapest verified route for Seedance 2.0 mini."""

    name = "ark"
    # Ark allows 3 concurrent video tasks for individual accounts, so the actions
    # for one pet can be submitted back to back.
    submit_gap_s = 0.0

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or config.ARK_BASE_URL).rstrip("/")

    def describe(self, image: Image.Image, instruction: str, system: str) -> str:
        data_url = imaging.to_data_url(imaging.fit_max_edge(image, 1024), "jpeg")
        payload = {
            "model": config.ARK_VISION_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": instruction},
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }
        data = _request("POST", f"{self.base_url}/chat/completions", self.api_key, payload)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"Vision model returned no choices: {json.dumps(data)[:300]}")
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Vision model returned empty content.")
        return content

    def still(self, prompt: str, reference: Image.Image, spec: config.StillSpec) -> str:
        data_url = imaging.to_data_url(imaging.fit_max_edge(reference, 1536), "jpeg")
        payload = {
            "model": config.ARK_IMAGE_MODEL,
            "prompt": prompt,
            "image": data_url,
            "size": spec.size,
            "sequential_image_generation": "disabled",
            "response_format": "url",
            "watermark": spec.watermark,
        }
        data = _request("POST", f"{self.base_url}/images/generations", self.api_key, payload)
        return _first_image_url(data)

    def loop(
        self,
        prompt: str,
        first_frame: Image.Image,
        spec: config.VideoSpec,
        negative: str | None = None,
        on_status: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> VideoResult:
        frame = imaging.fit_max_edge(first_frame, spec.frame_max_edge)
        data_url = imaging.to_data_url(frame, "jpeg")

        flags = [
            f"--rs {spec.resolution}",
            f"--rt {spec.ratio}",
            f"--dur {spec.duration_s}",
            f"--cf {'true' if spec.camera_fixed else 'false'}",
            f"--wm {'true' if spec.watermark else 'false'}",
        ]
        if spec.seed is not None:
            flags.append(f"--seed {spec.seed}")
        text = f"{prompt}\n\n{' '.join(flags)}"

        # Same image as first AND last frame: the model must land back on the
        # opening pose, which is exactly the head/tail loop requirement.
        content: list[dict[str, Any]] = [
            {"type": "text", "text": text},
            {"type": "image_url", "role": "first_frame", "image_url": {"url": data_url}},
            {"type": "image_url", "role": "last_frame", "image_url": {"url": data_url}},
        ]

        payload: dict[str, Any] = {"model": config.ARK_VIDEO_MODEL, "content": content}
        if negative:
            payload["negative_prompt"] = negative

        created = _submit_with_pacing(
            lambda: _request(
                "POST", f"{self.base_url}/contents/generations/tasks", self.api_key, payload
            ),
            spec,
            on_status,
        )
        task_id = created.get("id") or created.get("task_id")
        if not task_id:
            raise ProviderError(f"No task id in create response: {json.dumps(created)[:400]}")

        deadline = time.monotonic() + spec.poll_timeout_s
        while time.monotonic() < deadline:
            time.sleep(spec.poll_interval_s)
            # A lost poll must not discard an already-generated video, so keep
            # polling until the deadline instead of failing the action.
            try:
                task = _request(
                    "GET",
                    f"{self.base_url}/contents/generations/tasks/{urllib.parse.quote(str(task_id))}",
                    self.api_key,
                )
            except ProviderError as exc:
                if on_status:
                    on_status("poll_retry", {"task_id": task_id, "detail": str(exc)[:120]})
                continue
            status = str(task.get("status", "")).lower()
            if on_status:
                on_status(status, task)
            if status in {"succeeded", "success", "completed"}:
                url = (task.get("content") or {}).get("video_url") or task.get("video_url")
                if not url:
                    raise ProviderError(f"Task succeeded without a video url: {json.dumps(task)[:400]}")
                return VideoResult(
                    url=url,
                    task_id=str(task_id),
                    usage=task.get("usage") or {},
                    raw=task,
                )
            if status in {"failed", "cancelled", "canceled"}:
                reason = task.get("error") or task.get("failure_reason") or task
                raise ProviderError(f"Video task {task_id} {status}: {json.dumps(reason, ensure_ascii=False)[:400]}")
        raise ProviderError(f"Video task {task_id} did not finish within {spec.poll_timeout_s:.0f}s.")


class AgnesProvider:
    """Fallback backend. Used when no Ark key is present."""

    name = "agnes"
    # Measured limit: 1 video task per minute. Submitting three actions at once
    # returns 429 for the second and third.
    submit_gap_s = 62.0

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = (base_url or config.AGNES_BASE_URL).rstrip("/")

    def describe(self, image: Image.Image, instruction: str, system: str) -> str:
        data_url = imaging.to_data_url(imaging.fit_max_edge(image, 1024), "jpeg")
        payload = {
            "model": config.AGNES_TEXT_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": instruction},
                    ],
                },
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }
        data = _request("POST", f"{self.base_url}/v1/chat/completions", self.api_key, payload)
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"Text model returned no choices: {json.dumps(data)[:300]}")
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ProviderError("Text model returned no content.")
        return content

    def still(self, prompt: str, reference: Image.Image, spec: config.StillSpec) -> str:
        data_url = imaging.to_data_url(imaging.fit_max_edge(reference, 1536), "jpeg")
        payload = {
            "model": config.AGNES_IMAGE_MODEL,
            "prompt": prompt,
            "size": "1024x1024",
            "extra_body": {"image": [data_url], "response_format": "url"},
        }
        data = _request("POST", f"{self.base_url}/v1/images/generations", self.api_key, payload)
        return _first_image_url(data)

    def loop(
        self,
        prompt: str,
        first_frame: Image.Image,
        spec: config.VideoSpec,
        negative: str | None = None,
        on_status: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> VideoResult:
        frame = imaging.fit_max_edge(first_frame, spec.frame_max_edge)
        data_url = imaging.to_data_url(frame, "jpeg")
        # num_frames must satisfy 8n + 1; 121 frames at 24fps is the 5s slot.
        num_frames = 121
        payload: dict[str, Any] = {
            "model": config.AGNES_VIDEO_MODEL,
            "prompt": prompt,
            "width": 640,
            "height": 640,
            "num_frames": num_frames,
            "frame_rate": spec.fps,
            "extra_body": {"image": [data_url, data_url], "mode": "keyframes"},
        }
        if negative:
            payload["negative_prompt"] = negative
        if spec.seed is not None:
            payload["seed"] = spec.seed

        created = _submit_with_pacing(
            lambda: _request("POST", f"{self.base_url}/v1/videos", self.api_key, payload),
            spec,
            on_status,
        )
        video_id = created.get("video_id") or created.get("task_id") or created.get("id")
        if not video_id:
            raise ProviderError(f"No video id in create response: {json.dumps(created)[:400]}")

        deadline = time.monotonic() + spec.poll_timeout_s
        while time.monotonic() < deadline:
            time.sleep(spec.poll_interval_s)
            # Transient TLS or DNS failures on venue WiFi should not throw away a
            # video that the provider has already finished rendering.
            try:
                if str(video_id).startswith("video_"):
                    query = urllib.parse.urlencode(
                        {"video_id": video_id, "model_name": config.AGNES_VIDEO_MODEL}
                    )
                    task = _request("GET", f"{self.base_url}/agnesapi?{query}", self.api_key)
                else:
                    task = _request(
                        "GET",
                        f"{self.base_url}/v1/videos/{urllib.parse.quote(str(video_id))}",
                        self.api_key,
                    )
            except ProviderError as exc:
                if on_status:
                    on_status("poll_retry", {"video_id": video_id, "detail": str(exc)[:120]})
                continue
            status = str(task.get("status", "")).lower()
            if on_status:
                on_status(status, task)
            if status in {"completed", "succeeded", "success"}:
                url = task.get("video_url") or task.get("url")
                if not url:
                    raise ProviderError(f"Video completed without a url: {json.dumps(task)[:400]}")
                return VideoResult(url=url, task_id=str(video_id), usage=task.get("usage") or {}, raw=task)
            if status in {"failed", "cancelled", "canceled"}:
                raise ProviderError(f"Video task {video_id} {status}: {json.dumps(task, ensure_ascii=False)[:400]}")
        raise ProviderError(f"Video task {video_id} did not finish within {spec.poll_timeout_s:.0f}s.")


def resolve(preferred: str = "ark") -> ArkProvider | AgnesProvider:
    """Pick a backend, falling back when the preferred key is missing."""
    ark = config.ark_key()
    agnes = config.agnes_key()

    if preferred == "ark":
        if ark:
            return ArkProvider(ark)
        if agnes:
            return AgnesProvider(agnes)
        raise ProviderError(
            "No API key found. Set ARK_API_KEY for the cheapest Seedance 2.0 mini route, "
            "or AGNES_API_KEY to use the fallback provider."
        )
    if preferred == "agnes":
        if agnes:
            return AgnesProvider(agnes)
        if ark:
            return ArkProvider(ark)
        raise ProviderError("No API key found. Set AGNES_API_KEY or ARK_API_KEY.")
    raise ProviderError(f"Unknown provider: {preferred}")
