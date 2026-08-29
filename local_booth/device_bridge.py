#!/usr/bin/env python3
"""Independent MP4 -> four-view AVI -> Waveshare USB bridge.

This module deliberately does not import or modify Holo Video Uploader.app.
It implements the same proven on-wire protocol directly so the local booth can
run as a separate entry point.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


ProgressHook = Callable[[str, float], None]
CHUNK_BYTES = 128


class DeviceBridgeError(RuntimeError):
    pass


def _noop(_stage: str, _progress: float) -> None:
    return


def find_ffmpeg() -> str:
    configured = os.environ.get("FFMPEG_PATH", "").strip()
    candidates = [
        configured,
        shutil.which("ffmpeg") or "",
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise DeviceBridgeError(
        "找不到 FFmpeg。请先运行 setup_local_booth.command，或设置 FFMPEG_PATH。"
    )


def find_port(explicit: str | None = None) -> str:
    if explicit:
        if not Path(explicit).exists():
            raise DeviceBridgeError(f"USB 串口不存在：{explicit}")
        return explicit
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise DeviceBridgeError("没有找到微雪设备（/dev/cu.usbmodem*）")
    return ports[0]


def convert_to_quad(source: str | Path, output: str | Path) -> Path:
    """Make the exact 360x360/10fps/MJPEG AVI accepted by the firmware."""
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if not source_path.is_file():
        raise DeviceBridgeError(f"输入视频不存在：{source_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # This orientation was verified on the physical four-sided acrylic rig:
    # every animal head points away from the centre after reflection.
    video_filter = (
        "[0:v]fps=10,scale=360:360:force_original_aspect_ratio=increase,"
        "crop=360:360,hflip,split=4[a][b][c][d];"
        "[a]scale=150:150[top];"
        "[b]scale=150:150,hflip,vflip[bottom];"
        "[c]scale=150:150,transpose=2[left];"
        "[d]scale=150:150,transpose=1[right];"
        "color=c=black:s=360x360:r=10[canvas];"
        "[canvas][top]overlay=105:0:shortest=1[q1];"
        "[q1][bottom]overlay=105:210:shortest=1[q2];"
        "[q2][left]overlay=0:105:shortest=1[q3];"
        "[q3][right]overlay=210:105:shortest=1[out]"
    )
    command = [
        find_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-filter_complex",
        video_filter,
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "mjpeg",
        "-q:v",
        "7",
        "-pix_fmt",
        "yuvj420p",
        "-vtag",
        "MJPG",
        "-f",
        "avi",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise DeviceBridgeError(result.stderr.strip() or "FFmpeg 视频转换失败")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise DeviceBridgeError("FFmpeg 没有生成 AVI 文件")
    return output_path


def _wait_for_line(device, marker: bytes, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    recent = bytearray()
    while time.monotonic() < deadline:
        line = device.readline()
        if not line:
            continue
        recent.extend(line)
        if marker in line:
            return line
        if len(recent) > 2048:
            del recent[:-2048]
    tail = recent.decode("utf-8", "replace").strip()
    raise DeviceBridgeError(
        f"等待设备响应超时：{marker.decode('ascii', 'replace')}"
        + (f"；最后日志：{tail}" if tail else "")
    )


def upload_avi(
    avi: str | Path,
    port: str | None = None,
    on_progress: ProgressHook = _noop,
) -> str:
    try:
        import serial
    except ImportError as exc:
        raise DeviceBridgeError(
            "缺少 pyserial。请双击 setup_local_booth.command 完成安装。"
        ) from exc

    path = Path(avi).expanduser().resolve()
    if not path.is_file():
        raise DeviceBridgeError(f"AVI 不存在：{path}")
    total = path.stat().st_size
    device_path = find_port(port)
    on_progress("连接微雪", 0.0)

    try:
        with serial.Serial(device_path, 115200, timeout=0.25, write_timeout=10) as device:
            device.reset_input_buffer()
            device.write(f"UPLOAD2 {total}\n".encode("ascii"))
            device.flush()
            _wait_for_line(device, b"[USB] ready2", 10)

            sent = 0
            with path.open("rb") as source:
                while True:
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    device.write(chunk)
                    device.flush()
                    sent += len(chunk)
                    _wait_for_line(device, f"[USB] ack {sent}".encode("ascii"), 10)
                    on_progress("USB 推送", sent / total)
            _wait_for_line(device, b"[USB] upload ok", 30)
    except DeviceBridgeError:
        raise
    except Exception as exc:  # serial errors vary by pyserial version
        raise DeviceBridgeError(f"USB 推送失败：{exc}") from exc

    on_progress("微雪正在播放", 1.0)
    return device_path


def convert_and_upload(
    source: str | Path,
    output: str | Path,
    port: str | None = None,
    on_progress: ProgressHook = _noop,
) -> tuple[Path, str]:
    on_progress("四面视频合成", 0.0)
    avi = convert_to_quad(source, output)
    on_progress("四面视频合成", 1.0)
    device = upload_avi(avi, port=port, on_progress=on_progress)
    return avi, device


def main() -> int:
    parser = argparse.ArgumentParser(description="独立微雪视频转换和 USB 推送")
    parser.add_argument("video", help="输入 MP4/MOV 视频")
    parser.add_argument("--output", default="/tmp/local_booth_current.avi")
    parser.add_argument("--port", default="")
    parser.add_argument("--convert-only", action="store_true")
    args = parser.parse_args()

    try:
        print("[LOCAL BOOTH] 四面视频合成")
        avi = convert_to_quad(args.video, args.output)
        print(f"[LOCAL BOOTH] AVI READY {avi} ({avi.stat().st_size} bytes)")
        if not args.convert_only:
            last_percent = {"value": -1, "stage": ""}

            def report(stage: str, value: float) -> None:
                percent = int(value * 100)
                if stage == last_percent["stage"] and percent == last_percent["value"]:
                    return
                last_percent.update({"stage": stage, "value": percent})
                print(f"\r[LOCAL BOOTH] {stage} {percent:3d}%", end="", flush=True)

            device = upload_avi(
                avi,
                port=args.port or None,
                on_progress=report,
            )
            print(f"\n[LOCAL BOOTH] UPLOAD OK {device}")
    except DeviceBridgeError as exc:
        print(f"[LOCAL BOOTH] ERROR {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
