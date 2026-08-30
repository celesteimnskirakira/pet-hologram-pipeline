#!/usr/bin/env python3
"""Local generation API that bridges the Next.js frontend to petloop."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from petloop import config, imaging, pipeline, providers

ROOT = Path(__file__).resolve().parent
config.load_env_file(ROOT / ".env")
RUNS = Path(os.environ.get("GENERATION_RUNS_DIR", ROOT / "runs"))
JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
BACKEND_SECRET = os.environ.get("GENERATION_BACKEND_SECRET", "")
CALLBACK_SECRET = os.environ.get("GENERATION_CALLBACK_SECRET", "")
ARTIFACT_SECRET = os.environ.get("GENERATION_ARTIFACT_SECRET", "")
PROJECTION_URL = os.environ.get("PROJECTION_AGENT_URL", "http://127.0.0.1:8001")
PROJECTION_SECRET = os.environ.get("PROJECTION_AGENT_SECRET", "")
PUBLIC_BASE_URL = os.environ.get("GENERATION_PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ARTIFACT_TTL_S = max(60, int(os.environ.get("GENERATION_ARTIFACT_TTL_S", "3600")))
MAX_CONCURRENT_JOBS = max(1, int(os.environ.get("GENERATION_MAX_CONCURRENT", "1")))
JOB_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
TASK_ID_RE = re.compile(r"^task_[A-Za-z0-9_-]{6,58}$")
DISPLAY_CODE_RE = re.compile(r"^\d{6}$")
ALLOW_LOCAL_HTTP = os.environ.get("GENERATION_ALLOW_LOCAL_HTTP", "0") == "1"


def _host_allowlist(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip().lower().rstrip(".")
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    )


ALLOWED_IMAGE_HOSTS = _host_allowlist("GENERATION_ALLOWED_IMAGE_HOSTS")
ALLOWED_CALLBACK_HOSTS = _host_allowlist("GENERATION_ALLOWED_CALLBACK_HOSTS")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _secret_matches(candidate: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


def _host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        normalized == allowed
        or (allowed.startswith(".") and normalized.endswith(allowed))
        for allowed in allowlist
    )


def _validate_remote_url(
    value: str,
    *,
    allowlist: tuple[str, ...],
    purpose: str,
) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    local = host in {"127.0.0.1", "::1", "localhost"}
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"invalid {purpose} URL")
    if parsed.scheme != "https" and not (ALLOW_LOCAL_HTTP and local and parsed.scheme == "http"):
        raise ValueError(f"{purpose} URL must use HTTPS")
    if not host or not parsed.path.startswith("/"):
        raise ValueError(f"invalid {purpose} URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not (ALLOW_LOCAL_HTTP and local):
        if not address.is_global:
            raise ValueError(f"{purpose} URL cannot target a private address")
    if not (ALLOW_LOCAL_HTTP and local) and not _host_allowed(host, allowlist):
        raise ValueError(f"{purpose} host is not allowed")
    return value


def _artifact_signature(job_id: str, name: str, expires: int) -> str:
    if not ARTIFACT_SECRET:
        raise RuntimeError("GENERATION_ARTIFACT_SECRET is not configured")
    message = f"{job_id}\n{name}\n{expires}".encode()
    return hmac.new(ARTIFACT_SECRET.encode(), message, hashlib.sha256).hexdigest()


def _artifact_authorized(job_id: str, name: str, query: dict[str, list[str]]) -> bool:
    try:
        expires = int(query.get("expires", [""])[0])
        supplied = query.get("signature", [""])[0]
    except (TypeError, ValueError):
        return False
    if expires < int(time.time()) or expires > int(time.time()) + ARTIFACT_TTL_S + 60:
        return False
    expected = _artifact_signature(job_id, name, expires)
    return _secret_matches(supplied, expected)


def _artifact_request_allowed(
    job_id: str, name: str, query: dict[str, list[str]]
) -> bool:
    return bool(
        TASK_ID_RE.fullmatch(job_id)
        and name
        and name == Path(name).name
        and _artifact_authorized(job_id, name, query)
    )


def _reserve_job_slot() -> bool:
    return JOB_SLOTS.acquire(blocking=False)


class _SafeImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_remote_url(newurl, allowlist=ALLOWED_IMAGE_HOSTS, purpose="image")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_image_request(request: urllib.request.Request, context=None):
    handlers: list[Any] = [_SafeImageRedirectHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(request, timeout=120)


def _callback(job: dict[str, Any], **payload: Any) -> None:
    with LOCK:
        job.update(payload)
        job["updatedAt"] = datetime.now(timezone.utc).isoformat()
    body = {"job_id": job["task_id"], **payload}
    request = urllib.request.Request(
        job["callback_url"],
        data=_json_bytes(body),
        headers={
            "Content-Type": "application/json",
            "X-Generation-Callback-Secret": CALLBACK_SECRET,
            # Cloudflare rejects urllib's default Python-urllib user agent.
            "User-Agent": "petloop-api/1.0",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            return
    except Exception as exc:  # noqa: BLE001 - generation status must remain visible locally
        with LOCK:
            job["last_callback_error"] = str(exc)


def _download_image(url: str, out: Path) -> Path:
    request = urllib.request.Request(url, headers={"User-Agent": "petloop-api/1.0"})
    try:
        with _open_image_request(request) as response:
            _validate_remote_url(
                response.geturl(), allowlist=ALLOWED_IMAGE_HOSTS, purpose="image"
            )
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise ValueError("downloaded object is not an image")
            data = response.read(30 * 1024 * 1024 + 1)
    except urllib.error.URLError as exc:
        # Some local Python installations lack the macOS root CA bundle while
        # browsers can still open the same object-storage URL. Keep normal TLS
        # verification first, then allow an explicit local-only fallback.
        reason = getattr(exc, "reason", None)
        insecure_allowed = os.environ.get("GENERATION_ALLOW_INSECURE_TLS", "0") == "1"
        if not insecure_allowed or not isinstance(reason, ssl.SSLCertVerificationError):
            raise
        print("warning: image URL certificate verification failed; retrying with local TLS compatibility mode", flush=True)
        context = ssl._create_unverified_context()
        with _open_image_request(request, context=context) as response:
            _validate_remote_url(
                response.geturl(), allowlist=ALLOWED_IMAGE_HOSTS, purpose="image"
            )
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise ValueError("downloaded object is not an image")
            data = response.read(30 * 1024 * 1024 + 1)
    if len(data) > 30 * 1024 * 1024:
        raise ValueError("image exceeds 30 MB")
    out.write_bytes(data)
    return out


def _artifact_url(job_id: str, name: str) -> str:
    expires = int(time.time()) + ARTIFACT_TTL_S
    query = urlencode(
        {"expires": expires, "signature": _artifact_signature(job_id, name, expires)}
    )
    return f"{PUBLIC_BASE_URL}/api/v1/artifacts/{quote(job_id, safe='')}/{quote(name, safe='')}?{query}"


def _run_job(job: dict[str, Any]) -> None:
    task_id = job["task_id"]
    run_dir = RUNS / f"frontend-{task_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    with LOCK:
        job["run_dir"] = str(run_dir)
    try:
        _callback(job, status="processing", stage="validating", progress=5, message="正在校验图片")
        # Object-storage URLs often have no image extension; use a known image
        # suffix so the pipeline's extension guard can proceed to real decoding.
        source_path = _download_image(job["image_url"], run_dir / "upload.jpg")
        _callback(
            job,
            status="processing",
            stage="validating",
            progress=10,
            message="图片已校验",
            event="image_validated",
        )

        def on_step(stage: str, payload: dict[str, Any]) -> None:
            mapping = {
                "upload": ("validating", 8, "图片已接收"),
                "traits_skipped": ("validating", 12, "图片校验完成"),
                "still_attempt": ("generating_still", 20, "正在生成黑底正视图"),
                "still_scored": ("generating_still", 35, "正在检查黑底效果"),
                "still_final": ("generating_still", 45, "黑底正视图已完成"),
                "video_status": ("generating_video", 60, "正在生成动作视频"),
                "loop_finished": ("post_processing", 82, "视频后处理已完成"),
                "loop_skipped": ("post_processing", 82, "视频生成完成，跳过可选后处理"),
                "motion_checked": ("post_processing", 85, "视频验收已完成"),
            }
            if stage not in mapping:
                return
            current, progress, message = mapping[stage]
            update: dict[str, Any] = {"status": "processing", "stage": current, "progress": progress, "message": message}
            if stage == "video_status" and payload.get("status") in {"succeeded", "success", "completed"}:
                update["progress"] = 72
            if stage == "still_final":
                update["event"] = "still_completed"
            elif stage == "video_status" and payload.get("status") in {"succeeded", "success", "completed"}:
                update["event"] = "video_completed"
            elif stage in {"loop_finished", "loop_skipped", "motion_checked"}:
                update["event"] = "post_processing_completed"
            _callback(job, **update)

        spec = config.PipelineSpec()
        artifacts = pipeline.run(source_path, spec=spec, run_dir=run_dir, on_step=on_step)
        video = artifacts.video or artifacts.video_raw
        if video is None or not Path(video).is_file():
            raise RuntimeError("pipeline produced no video artifact")
        video = Path(video)
        selected = artifacts.metrics.get("selected_action") or spec.pose
        video_url = _artifact_url(task_id, video.name)
        _callback(
            job,
            status="processing",
            stage="post_processing",
            progress=88,
            message="视频已生成，准备发送到投影电脑",
            event="post_processing_completed",
            artifacts={"videoUrl": video_url, "selectedAction": selected},
            selectedAction=selected,
        )

        _callback(job, status="processing", stage="delivering", progress=92, message="正在发送到投影电脑", event="delivery_started")
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        projection_payload = {
            "jobId": task_id,
            "artifactUrl": video_url,
            "sha256": digest,
            "displayCode": job["display_code"],
        }
        request = urllib.request.Request(
            PROJECTION_URL.rstrip("/") + "/api/v1/projection/jobs",
            data=_json_bytes(projection_payload),
            headers={"Content-Type": "application/json", "X-Projection-Secret": PROJECTION_SECRET},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            delivered = json.loads(response.read().decode("utf-8"))
        if not (delivered.get("received") and delivered.get("ready") and delivered.get("sha256") == digest):
            raise RuntimeError("projection computer did not confirm file integrity")
        _callback(
            job,
            status="completed",
            stage="completed",
            progress=100,
            message="已完成",
            videoUrl=video_url,
            artifacts={"videoUrl": video_url, "selectedAction": selected},
            selectedAction=selected,
            deliveryStatus="ready",
            event="delivery_completed",
        )
        with LOCK:
            job.update({"status": "completed", "run_dir": str(run_dir), "video": str(video)})
    except Exception as exc:  # noqa: BLE001 - convert failures into user-visible task state
        traceback.print_exc()
        delivery = "delivery_failed" if job.get("stage") == "delivering" else "failed"
        _callback(
            job,
            status=delivery,
            stage=delivery,
            progress=0 if delivery == "failed" else 92,
            message="处理失败，请稍后重试",
            errorCode=type(exc).__name__,
            error="generation_failed",
        )
        with LOCK:
            job.update({"status": delivery, "internal_error": str(exc)})
    finally:
        JOB_SLOTS.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "petloop-api/1.0"

    def _send(self, code: int, body: Any, content_type: str = "application/json; charset=utf-8") -> None:
        data = body if isinstance(body, bytes) else _json_bytes(body)
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:
            raise ValueError("invalid request body")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("request body must be an object")
        return body

    def _backend_authorized(self) -> bool:
        return _secret_matches(
            self.headers.get("X-Generation-Backend-Secret", ""), BACKEND_SECRET
        )

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route != "/api/v1/jobs":
            self._send(404, {"error": "not_found"})
            return
        if not self._backend_authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            body = self._read_json()
            task_id = str(body["task_id"])
            if not TASK_ID_RE.fullmatch(task_id):
                raise ValueError("invalid task_id")
            image_url = _validate_remote_url(
                str(body["image_url"]),
                allowlist=ALLOWED_IMAGE_HOSTS,
                purpose="image",
            )
            callback_url = _validate_remote_url(
                str(body["callback_url"]),
                allowlist=ALLOWED_CALLBACK_HOSTS,
                purpose="callback",
            )
            expected_callback_path = f"/api/generation/{task_id}"
            if urlparse(callback_url).path.rstrip("/") != expected_callback_path:
                raise ValueError("callback path does not match task_id")
            display_code = str(body.get("display_code") or uuid.uuid4().int % 1_000_000).zfill(6)
            if not DISPLAY_CODE_RE.fullmatch(display_code):
                raise ValueError("invalid display_code")
        except Exception as exc:
            self._send(400, {"error": "invalid_body", "message": str(exc)})
            return
        with LOCK:
            if task_id in JOBS:
                self._send(409, {"error": "job_exists"})
                return
        if not _reserve_job_slot():
            self._send(
                429,
                {"error": "generation_busy", "message": "generation capacity is full"},
            )
            return
        job = {
            "task_id": task_id,
            "image_url": image_url,
            "callback_url": callback_url,
            "display_code": display_code,
            "status": "queued",
            "stage": "queued",
        }
        with LOCK:
            JOBS[task_id] = job
        _callback(job, status="processing", stage="queued", progress=0, message="已进入生成队列")
        threading.Thread(target=_run_job, args=(job,), daemon=True).start()
        self._send(202, {"job_id": task_id, "display_code": job["display_code"]})

    def do_GET(self) -> None:  # noqa: N802
        parsed_request = urlparse(self.path)
        route = parsed_request.path
        prefix = "/api/v1/artifacts/"
        if route.startswith(prefix):
            parts = route[len(prefix):].split("/", 1)
            if len(parts) != 2:
                self._send(404, {"error": "not_found"})
                return
            task_id, name = (unquote(parts[0]), unquote(parts[1]))
            if not _artifact_request_allowed(task_id, name, parse_qs(parsed_request.query)):
                self._send(403, {"error": "invalid_or_expired_artifact_url"})
                return
            with LOCK:
                job = JOBS.get(task_id)
            # Jobs are currently kept in memory, but completed artifacts must
            # remain readable after a local API restart. Fall back to the
            # deterministic per-task run directory when the job record is no
            # longer present in this process.
            root = Path(job["run_dir"]).resolve() if job and job.get("run_dir") else (
                RUNS / f"frontend-{task_id}"
            ).resolve()
            if not root.is_dir():
                self._send(404, {"error": "not_found"})
                return
            target = (root / name).resolve()
            if root not in target.parents or not target.is_file():
                self._send(404, {"error": "not_found"})
                return
            self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            return
        job_prefix = "/api/v1/jobs/"
        if route.startswith(job_prefix):
            if not self._backend_authorized():
                self._send(401, {"error": "unauthorized"})
                return
            task_id = unquote(route[len(job_prefix):])
            if not TASK_ID_RE.fullmatch(task_id):
                self._send(404, {"error": "not_found"})
                return
            with LOCK:
                job = JOBS.get(task_id)
                payload = (
                    {
                        "taskId": task_id,
                        "displayCode": job.get("display_code"),
                        "status": job.get("status"),
                        "stage": job.get("stage"),
                        "progress": job.get("progress", 0),
                        "videoUrl": job.get("videoUrl"),
                        "message": job.get("message"),
                        "errorCode": job.get("errorCode"),
                        "error": job.get("error"),
                        "selectedAction": job.get("selectedAction"),
                        "deliveryStatus": job.get("deliveryStatus"),
                        "artifacts": job.get("artifacts", {}),
                        "updatedAt": job.get("updatedAt"),
                    }
                    if job
                    else None
                )
            if payload is None:
                self._send(404, {"error": "not_found"})
                return
            self._send(200, payload)
            return
        if route == "/api/v1/jobs":
            if not self._backend_authorized():
                self._send(401, {"error": "unauthorized"})
                return
            with LOCK:
                jobs = [
                    {k: job.get(k) for k in ("task_id", "display_code", "status", "stage", "run_dir", "video", "error", "last_callback_error")}
                    for job in JOBS.values()
                ]
            self._send(200, {"jobs": jobs})
            return
        if route == "/healthz":
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not_found"})


def main() -> int:
    parser = argparse.ArgumentParser(description="petloop frontend generation bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    missing = [
        name
        for name, value in (
            ("GENERATION_BACKEND_SECRET", BACKEND_SECRET),
            ("GENERATION_CALLBACK_SECRET", CALLBACK_SECRET),
            ("GENERATION_ARTIFACT_SECRET", ARTIFACT_SECRET),
            ("PROJECTION_AGENT_SECRET", PROJECTION_SECRET),
            ("GENERATION_ALLOWED_IMAGE_HOSTS", ALLOWED_IMAGE_HOSTS),
            ("GENERATION_ALLOWED_CALLBACK_HOSTS", ALLOWED_CALLBACK_HOSTS),
        )
        if not value
    ]
    if missing:
        parser.error("missing required configuration: " + ", ".join(missing))
    RUNS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Generation API: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
