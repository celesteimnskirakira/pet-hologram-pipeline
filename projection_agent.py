#!/usr/bin/env python3
"""Local projection-computer receiver simulator."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
if ENV_FILE.is_file():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

STORE = Path(os.environ.get("PROJECTION_STORE", Path(__file__).resolve().parent / "projection_store"))
SECRET = os.environ.get("PROJECTION_AGENT_SECRET", "local-projection-secret")
TASK_ID_RE = re.compile(r"^task_[A-Za-z0-9_-]{6,58}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT_BYTES = int(os.environ.get("PROJECTION_MAX_ARTIFACT_BYTES", str(200 * 1024 * 1024)))
ALLOW_LOCAL_HTTP = os.environ.get("PROJECTION_ALLOW_LOCAL_HTTP", "0") == "1"


def _secret_matches(candidate: str) -> bool:
    return bool(SECRET) and hmac.compare_digest(candidate.encode(), SECRET.encode())


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/v1/projection/jobs":
            self.send_json(404, {"error": "not_found"})
            return
        if not _secret_matches(self.headers.get("X-Projection-Secret", "")):
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode())
            job_id = str(body["jobId"])
            url = str(body["artifactUrl"])
            expected = str(body["sha256"])
            if not TASK_ID_RE.fullmatch(job_id):
                raise ValueError("invalid jobId")
            if not SHA256_RE.fullmatch(expected):
                raise ValueError("invalid sha256")
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or ""
            try:
                private_host = ipaddress.ip_address(host).is_private
            except ValueError:
                private_host = host == "localhost"
            local_http = ALLOW_LOCAL_HTTP and parsed.scheme == "http" and private_host
            if parsed.scheme != "https" and not local_http:
                raise ValueError("artifactUrl must use HTTPS")
        except Exception as exc:
            self.send_json(400, {"error": "invalid_body", "message": str(exc)})
            return
        STORE.mkdir(parents=True, exist_ok=True)
        target = STORE / f"{job_id}.mp4"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "projection-agent/1.0"}), timeout=180) as response:
                data = response.read(MAX_ARTIFACT_BYTES + 1)
            if len(data) > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact exceeds configured size limit")
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected:
                self.send_json(422, {"received": False, "sha256": digest, "ready": False})
                return
            with tempfile.NamedTemporaryFile(dir=STORE, prefix=f".{job_id}-", delete=False) as tmp:
                tmp.write(data)
                temp_path = Path(tmp.name)
            temp_path.replace(target)
        except Exception as exc:
            self.send_json(502, {"received": False, "ready": False, "error": str(exc)})
            return
        self.send_json(200, {"received": True, "sha256": digest, "ready": True, "jobId": job_id})


def main() -> int:
    parser = argparse.ArgumentParser(description="local projection receiver")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    STORE.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Projection agent: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
