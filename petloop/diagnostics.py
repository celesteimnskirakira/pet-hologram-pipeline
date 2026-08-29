"""Preflight checks.

Most first-run failures are environmental rather than code bugs: a missing API
key, a stale proxy pointing at a dead port, or no ffmpeg. Each of those surfaces
as a confusing network error deep inside a generation call, so check up front.
"""

from __future__ import annotations

import os
import shutil
import socket
import urllib.parse
from dataclasses import dataclass

from . import config

PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "hint": self.hint}


def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_proxy() -> Check:
    """A configured but unreachable proxy silently breaks every API call."""
    configured = {var: os.environ[var] for var in PROXY_VARS if os.environ.get(var)}
    if not configured:
        return Check("proxy", True, "No proxy configured; requests go direct.")

    dead: list[str] = []
    for var, value in configured.items():
        parsed = urllib.parse.urlparse(value if "://" in value else f"http://{value}")
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if host and not _port_open(host, port):
            dead.append(f"{var}={host}:{port}")

    if dead:
        return Check(
            "proxy",
            False,
            "Proxy configured but not reachable: " + ", ".join(dead),
            "Start the proxy, or clear it for this run: "
            "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy",
        )
    return Check("proxy", True, "Proxy reachable: " + ", ".join(configured))


def check_keys() -> Check:
    ark = config.ark_key()
    agnes = config.agnes_key()
    source = ".env file" if config.ENV_FILE.is_file() else "shell environment"
    if ark:
        return Check(
            "api_key",
            True,
            f"ARK_API_KEY found via {source} (cheapest Seedance 2.0 mini route).",
        )
    if agnes:
        return Check(
            "api_key",
            True,
            f"AGNES_API_KEY found via {source}; ARK_API_KEY missing so the fallback provider will be used.",
            "Set ARK_API_KEY to use Seedance 2.0 mini directly at the promo price.",
        )
    return Check(
        "api_key",
        False,
        "No API key found.",
        "cp .env.example .env and fill in ARK_API_KEY, or export ARK_API_KEY=...",
    )


def check_ffmpeg() -> Check:
    have_ffmpeg = shutil.which("ffmpeg") is not None
    have_ffprobe = shutil.which("ffprobe") is not None
    if have_ffmpeg and have_ffprobe:
        return Check("ffmpeg", True, "ffmpeg and ffprobe available; loop finishing enabled.")
    missing = [name for name, ok in (("ffmpeg", have_ffmpeg), ("ffprobe", have_ffprobe)) if not ok]
    return Check(
        "ffmpeg",
        False,
        f"Missing: {', '.join(missing)}. The raw clip will be delivered without seam repair.",
        "brew install ffmpeg",
    )


def check_endpoint() -> Check:
    """Reachability of the primary API host, honouring proxy settings."""
    host = urllib.parse.urlparse(config.ARK_BASE_URL).hostname or "ark.cn-beijing.volces.com"
    proxy = next((os.environ[var] for var in PROXY_VARS if os.environ.get(var)), None)
    if proxy:
        return Check("endpoint", True, f"Skipped direct probe of {host} because a proxy is configured.")
    if _port_open(host, 443, timeout=4.0):
        return Check("endpoint", True, f"{host}:443 reachable.")
    return Check("endpoint", False, f"Cannot reach {host}:443.", "Check network access or DNS.")


def run_all() -> list[Check]:
    return [check_keys(), check_proxy(), check_endpoint(), check_ffmpeg()]


def blocking_failures(checks: list[Check]) -> list[Check]:
    """Checks that will stop generation outright, as opposed to degrading it."""
    return [check for check in checks if not check.ok and check.name in {"api_key", "proxy", "endpoint"}]
