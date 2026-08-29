#!/usr/bin/env python3
"""Probe the configured image and Ark video APIs with one real request each.

Usage:
    python scripts/api_probe.py ./pet.jpg
    python scripts/api_probe.py ./pet.jpg --action 睡觉 --poll

The probe is intentionally separate from the production pipeline. It prints
the request and response for both providers so endpoint/payload mismatches are
easy to diagnose. API keys and large base64 fields are redacted by default;
use --full only in a private terminal when the complete payload is required.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - depends on local environment
    raise SystemExit("Pillow is required: python3 -m pip install Pillow") from exc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from petloop import config, imaging  # noqa: E402

IMAGE_PROMPT = "生成图片中的宠物的完整全身正面图背景为纯黑色比例为一比一"
DEFAULT_VIDEO_PROMPT = (
    "严格参考输入的宠物图片，保持宠物身份和外观完全一致。"
    "生成一段写实风格、全身居中、纯黑背景、固定镜头的宠物动作视频。"
    "保持主体完整不出边，动作自然，首尾姿态尽量接近，适合循环播放。"
)


def load_dotenv() -> None:
    """Load the project .env using the same simple rules as config.py."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def jsonable(value: Any, full: bool = False, key: str = "") -> Any:
    """Make request data printable without leaking credentials or huge images."""
    if isinstance(value, dict):
        return {k: jsonable(v, full, k) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v, full, key) for v in value]
    if isinstance(value, str):
        if key.lower() in {"authorization", "api_key", "x-api-key"}:
            return "<redacted>" if not full else value
        if not full and (value.startswith("data:image/") or len(value) > 4096):
            return f"<redacted string: {len(value)} chars>"
    return value


def print_block(title: str, value: Any, full: bool) -> None:
    print(f"\n===== {title} =====")
    if isinstance(value, (dict, list)):
        print(json.dumps(jsonable(value, full), ensure_ascii=False, indent=2))
    else:
        print(value)


def request_json(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None,
    full: bool,
    timeout: float,
) -> tuple[int, dict[str, Any] | str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "petloop-api-probe/1.0",
    }
    print_block("REQUEST", {"method": method, "url": url, "headers": headers, "json": payload}, full)
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError) as exc:
        print_block("RESPONSE ERROR", repr(exc), full)
        raise SystemExit(2) from exc

    try:
        parsed: dict[str, Any] | str = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    print_block("RESPONSE", {"status": status, "body": parsed}, full)
    return status, parsed


def image_result_url(response: dict[str, Any]) -> str:
    items = response.get("data")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("url"):
                return str(item["url"])
            if item.get("b64_json"):
                return "data:image/png;base64," + str(item["b64_json"])
    for key in ("url", "image_url", "image"):
        if isinstance(response.get(key), str) and response[key]:
            return response[key]
    raise RuntimeError("图生图响应中没有 data[].url / data[].b64_json")


def image_to_data_url(url: str, timeout: float) -> str:
    if url.startswith("[image omitted]"):
        return "data:image/png;base64," + url[len("[image omitted]") :]
    if url.startswith("data:"):
        return url
    request = urllib.request.Request(url, headers={"User-Agent": "petloop-api-probe/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
        content_type = response.headers.get_content_type() or "image/png"
    return f"data:{content_type};base64," + base64.b64encode(payload).decode("ascii")


def load_action_prompt(action: str | None) -> str:
    if not action:
        action = random.choice(["舔毛", "走路", "睡觉", "挠脖子"])
    path = ROOT / "petloop" / "action_prompts" / f"{action}.txt"
    if not path.is_file():
        raise SystemExit(f"动作 prompt 不存在：{path}")
    return path.read_text(encoding="utf-8").strip()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="本地宠物图片")
    parser.add_argument("--action", choices=["舔毛", "走路", "睡觉", "挠脖子"], help="使用项目动作 prompt")
    parser.add_argument("--video-prompt", default=None, help="直接覆盖视频 prompt")
    parser.add_argument("--poll", action="store_true", help="创建任务后继续轮询，直到成功/失败/超时")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--poll-timeout", type=float, default=900.0)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--resolution", default="480p")
    parser.add_argument("--ratio", default="1:1")
    parser.add_argument("--full", action="store_true", help="输出完整 API key 和 base64，请只在私密终端使用")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    if not args.image.is_file():
        raise SystemExit(f"图片不存在：{args.image}")
    image_key = first_env("IMAGE_API_KEY", "IMAGE_TOKEN")
    video_key = first_env("VIDEO_API_KEY", "VIDEO_TOKEN")
    if not image_key or not video_key:
        raise SystemExit("请在 .env 或环境变量中同时填写 IMAGE_API_KEY 和 VIDEO_API_KEY")

    source = imaging.normalize_input(args.image)
    image_base = os.environ.get("IMAGE_BASE_URL", "https://api.openai-next.com").rstrip("/")
    image_prefix = os.environ.get("IMAGE_API_PREFIX", "/v1").strip("/")
    image_root = f"{image_base}/{image_prefix}" if image_prefix else image_base
    image_model = os.environ.get("IMAGE_MODEL", "doubao-seedream-5-0-260128")

    still_payload = {
        "model": image_model,
        "prompt": IMAGE_PROMPT,
        "image": imaging.to_data_url(imaging.fit_max_edge(source, 1536), "jpeg"),
        "size": "2048x2048",
        "response_format": "url",
        "watermark": False,
    }
    status, response = request_json(
        "POST", f"{image_root}/images/generations", image_key, still_payload, args.full, args.timeout
    )
    if status < 200 or status >= 300 or not isinstance(response, dict):
        raise SystemExit("图生图请求失败，未继续调用视频接口。")
    generated_url = image_result_url(response)
    first_frame = image_to_data_url(generated_url, args.timeout)

    video_base = os.environ.get("VIDEO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    video_model = os.environ.get("VIDEO_MODEL", "doubao-seedance-2-0-mini-260615")
    selected_action = args.action
    if not selected_action and not args.video_prompt:
        selected_action = random.choice(["舔毛", "走路", "睡觉", "挠脖子"])
    video_prompt = args.video_prompt or load_action_prompt(selected_action)
    print_block("SELECTED ACTION", selected_action or "custom", args.full)
    flags = [
        f"--rs {args.resolution}",
        f"--rt {args.ratio}",
        f"--dur {args.duration}",
        "--cf true",
        "--wm false",
    ]
    content = [
        {"type": "text", "text": video_prompt + "\n\n" + " ".join(flags)},
        {"type": "image_url", "role": "first_frame", "image_url": {"url": first_frame}},
    ]
    video_payload = {"model": video_model, "content": content}
    video_status, video_response = request_json(
        "POST",
        f"{video_base}/contents/generations/tasks",
        video_key,
        video_payload,
        args.full,
        args.timeout,
    )
    if video_status < 200 or video_status >= 300 or not isinstance(video_response, dict):
        raise SystemExit("Seedance 创建任务失败。")

    task_id = video_response.get("id") or video_response.get("task_id")
    if not args.poll or not task_id:
        return 0
    deadline = time.monotonic() + args.poll_timeout
    while time.monotonic() < deadline:
        time.sleep(args.poll_interval)
        poll_status, poll_response = request_json(
            "GET",
            f"{video_base}/contents/generations/tasks/{urllib.parse.quote(str(task_id))}",
            video_key,
            None,
            args.full,
            args.timeout,
        )
        if isinstance(poll_response, dict):
            state = str(poll_response.get("status", "")).lower()
            if state in {"succeeded", "success", "completed", "failed", "cancelled", "canceled"}:
                return 0 if state in {"succeeded", "success", "completed"} else 1
        if poll_status >= 400:
            print("轮询返回错误，继续等待或使用 --poll-timeout 调整。", file=sys.stderr)
    print("轮询超时。任务可能仍在服务商侧运行。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
