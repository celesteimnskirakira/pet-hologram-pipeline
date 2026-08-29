#!/usr/bin/env python3
"""Independent LAN photo booth that generates and pushes to the Waveshare."""

from __future__ import annotations

import argparse
import json
import queue
import secrets
import socket
import subprocess
import sys
import threading
import traceback
import uuid
import webbrowser
from datetime import datetime
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
LOCAL_ROOT = Path(__file__).resolve().parent
RUNS_DIR = LOCAL_ROOT / "runs"
UPLOADS_DIR = RUNS_DIR / "_uploads"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(LOCAL_ROOT))

from device_bridge import DeviceBridgeError, convert_and_upload, find_port  # noqa: E402
from petloop import config, imaging, pipeline, prompts, providers  # noqa: E402

TOKEN = secrets.token_urlsafe(10)
JOBS: dict[str, dict] = {}
JOB_ORDER: list[str] = []
LOCK = threading.Lock()
WORK: "queue.Queue[str]" = queue.Queue()
SETTINGS: dict[str, object] = {
    "port": None,
    "provider": "ark",
    "resolution": "480p",
    "no_device": False,
    "http_port": 8793,
    "advertise": "",
}

POSES = {
    "curled_side": "侧卧蜷睡",
    "loaf": "趴卧收爪",
    "side_stretch": "侧身伸展",
    "curled_tight": "团成球",
    "sprawl": "摊开趴睡",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _job_copy(job_id: str) -> dict | None:
    with LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def _persist(job: dict) -> None:
    directory = RUNS_DIR / job["id"]
    directory.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in job.items() if k != "events"}
    (directory / "job.json").write_text(
        json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def update(job_id: str, **values) -> None:
    with LOCK:
        job = JOBS[job_id]
        job.update(values)
        job["updated_at"] = _now()
        snapshot = dict(job)
    _persist(snapshot)


def event(job_id: str, stage: str, _payload: dict) -> None:
    labels = {
        "upload": "检查照片",
        "traits": "识别宠物特征",
        "still_attempt": "抠除主体并生成黑底图",
        "still_final": "黑底图完成",
        "actions_start": "生成动态视频",
        "video_status": "云端正在生成视频",
        "action_done": "动态视频完成",
        "actions_done": "准备推送到微雪",
    }
    with LOCK:
        job = JOBS[job_id]
        job["stage"] = labels.get(stage, stage.replace("_", " "))
        job["events"] = (job.get("events", []) + [stage])[-60:]
        job["updated_at"] = _now()


def enqueue(image: Path, pet_name: str, pose: str) -> str:
    job_id = uuid.uuid4().hex[:10]
    job = {
        "id": job_id,
        "pet_name": pet_name.strip() or "我的宠物",
        "pose": pose,
        "pose_name": POSES[pose],
        "image": str(image),
        "status": "queued",
        "stage": "等待生成",
        "progress": 0.0,
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
        "events": [],
        "avi": None,
        "device": None,
    }
    with LOCK:
        JOBS[job_id] = job
        JOB_ORDER.append(job_id)
    _persist(job)
    WORK.put(job_id)
    return job_id


def process_job(job_id: str) -> None:
    job = _job_copy(job_id)
    if not job:
        return
    update(job_id, status="generating", stage="开始处理", progress=0.03)
    spec = config.PipelineSpec(
        poses=(job["pose"],),
        parallel=False,
        provider=str(SETTINGS["provider"]),
    )
    spec.hologram.rig = "single"
    spec.video.resolution = str(SETTINGS["resolution"])
    work_dir = RUNS_DIR / job_id / "pipeline"
    artifacts = pipeline.run(
        job["image"],
        spec=spec,
        run_dir=work_dir,
        on_step=lambda stage, payload: event(job_id, stage, payload),
    )
    clips = [clip for clip in artifacts.clips if clip.get("ok")]
    if not clips:
        raise RuntimeError("没有生成可用的视频")
    source = Path(clips[0]["loop"])
    avi = RUNS_DIR / job_id / "device" / "current.avi"
    last_device_update = {"stage": "", "percent": -1}

    def device_progress(stage: str, value: float) -> None:
        percent = int(value * 100)
        if (
            stage == last_device_update["stage"]
            and percent == last_device_update["percent"]
        ):
            return
        last_device_update.update({"stage": stage, "percent": percent})
        update(
            job_id,
            status="sending",
            stage=stage,
            progress=round(0.82 + value * 0.17, 3),
        )

    if SETTINGS["no_device"]:
        from device_bridge import convert_to_quad

        device_progress("四面视频合成", 0.0)
        convert_to_quad(source, avi)
        device = "跳过 USB（测试模式）"
    else:
        avi, device = convert_and_upload(
            source,
            avi,
            port=SETTINGS["port"] or None,
            on_progress=device_progress,
        )
    update(
        job_id,
        status="done",
        stage="微雪正在循环播放" if not SETTINGS["no_device"] else "本地测试完成",
        progress=1.0,
        avi=str(avi),
        device=device,
    )


def worker() -> None:
    while True:
        job_id = WORK.get()
        try:
            process_job(job_id)
        except (providers.ProviderError, imaging.ImageError, DeviceBridgeError) as exc:
            update(job_id, status="error", stage="失败", error=str(exc))
        except Exception as exc:  # keep the next visitor moving
            traceback.print_exc()
            update(job_id, status="error", stage="失败", error=f"{type(exc).__name__}: {exc}")
        finally:
            WORK.task_done()


def parse_multipart(raw: bytes, content_type: str) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
    )
    fields: dict[str, str] = {}
    upload = None
    if not message.is_multipart():
        return fields, upload
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True) or b""
        if name == "image" and part.get_filename():
            upload = (part.get_filename() or "photo.jpg", payload)
        elif name:
            fields[str(name)] = payload.decode("utf-8", "replace").strip()
    return fields, upload


def lan_ip() -> str:
    # On macOS en0 is normally Wi-Fi. Reading it directly avoids VPN/proxy
    # routes causing the QR code to advertise an unreachable virtual address.
    try:
        result = subprocess.run(
            ["/usr/sbin/ipconfig", "getifaddr", "en0"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        value = result.stdout.strip()
        if value and not value.startswith(("127.", "169.254.")):
            return value
    except (OSError, subprocess.SubprocessError):
        pass

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        value = probe.getsockname()[0]
        if not value.startswith(("198.18.", "198.19.")):
            return value
    except OSError:
        pass
    finally:
        probe.close()
    return "127.0.0.1"


def visitor_url() -> str:
    advertised = str(SETTINGS.get("advertise") or "").strip() or lan_ip()
    port = int(SETTINGS.get("http_port") or 8793)
    return f"http://{advertised}:{port}/u?k={TOKEN}"


def qr_svg(value: str) -> bytes:
    import io
    import segno

    stream = io.BytesIO()
    segno.make(value, error="m").save(stream, kind="svg", scale=7, border=2)
    return stream.getvalue()


VISITOR_HTML = """<!doctype html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>宠物全息体验</title><style>
:root{color-scheme:dark;--bg:#080b10;--card:#121824;--line:#273047;--blue:#69a7ff;--ok:#63dfa4}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 50% -20%,#213456,var(--bg) 45%);color:#f5f7fb;font:16px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
main{max-width:520px;margin:auto;padding:28px 18px 60px}h1{font-size:30px;margin:8px 0}p{color:#aeb8cb}.card{background:rgba(18,24,36,.94);border:1px solid var(--line);border-radius:20px;padding:18px;margin-top:20px;box-shadow:0 20px 70px #0007}
label{display:block;margin:14px 0 6px;color:#c8d0df}input,select,button{width:100%;font:inherit;border-radius:12px;padding:14px;border:1px solid var(--line);background:#0c111a;color:white}input[type=file]{padding:22px 12px;border-style:dashed}button{margin-top:20px;border:0;background:linear-gradient(135deg,#397ef2,#8759ef);font-weight:700}button:disabled{opacity:.5}.bar{height:9px;background:#242b39;border-radius:99px;overflow:hidden}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--ok));transition:width .4s}.ok{color:var(--ok)}.bad{color:#ff8585}.small{font-size:13px}
</style></head><body><main><div class=small>本地运行 · 无需云服务器</div><h1>让宠物出现在全息屏里</h1><p>上传一张主体清晰的照片。完成后视频会自动发送到现场的微雪设备。</p>
<section class=card id=form><label>宠物照片</label><input id=image type=file accept="image/*" capture=environment><label>宠物名字（可选）</label><input id=name maxlength=30 placeholder="例如：奶糖"><label>动态姿势</label><select id=pose><option value=curled_side>侧卧蜷睡</option><option value=loaf>趴卧收爪</option><option value=side_stretch>侧身伸展</option><option value=curled_tight>团成球</option><option value=sprawl>摊开趴睡</option></select><button id=go>开始生成</button><p id=err class=bad></p></section>
<section class=card id=status hidden><h2 id=title>已加入队列</h2><p id=stage>等待生成</p><div class=bar><i id=fill></i></div><p class=small>请保留此页面。完成后现场屏幕会自动切换。</p></section></main>
<script>const key=new URLSearchParams(location.search).get('k')||'';let id='';
const petName=document.getElementById('name');
go.onclick=async()=>{err.textContent='';if(!image.files[0]){err.textContent='请先选择照片';return}go.disabled=true;const data=new FormData();data.append('image',image.files[0]);data.append('pet_name',petName.value);data.append('pose',pose.value);const r=await fetch('/api/enqueue?k='+encodeURIComponent(key),{method:'POST',body:data});const d=await r.json();if(!r.ok){err.textContent=d.error||'提交失败';go.disabled=false;return}id=d.job_id;form.hidden=true;status.hidden=false;poll()};
async function poll(){if(!id)return;const r=await fetch('/api/job/'+id+'?k='+encodeURIComponent(key));const d=await r.json();if(!r.ok)return;stage.textContent=d.stage;fill.style.width=Math.round((d.progress||0)*100)+'%';if(d.status==='done'){title.textContent='完成';title.className='ok';return}if(d.status==='error'){title.textContent='处理失败';title.className='bad';stage.textContent=d.error||'未知错误';return}setTimeout(poll,1800)}
</script></body></html>"""

CONSOLE_HTML = """<!doctype html><html lang=zh-CN><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>本地全息体验台</title><style>
body{margin:0;background:#090c11;color:#edf2fa;font:15px/1.5 -apple-system,"PingFang SC",sans-serif}main{max-width:1050px;margin:auto;padding:30px}header{display:flex;gap:28px;align-items:center;flex-wrap:wrap}.qr{background:#fff;padding:10px;border-radius:12px;width:190px}h1{font-size:30px;margin:0}.muted{color:#96a1b4}.badge{display:inline-block;padding:5px 10px;border-radius:99px;background:#192131;margin:5px 4px 5px 0}.jobs{margin-top:28px;display:grid;gap:12px}.job{background:#121824;border:1px solid #283149;border-radius:14px;padding:15px;display:grid;grid-template-columns:1.2fr 2fr .7fr;gap:12px}.ok{color:#62dda3}.bad{color:#ff8585}@media(max-width:650px){.job{grid-template-columns:1fr}}
</style></head><body><main><header><img class=qr src=/api/qr.svg><div><div class=muted>独立入口 · 不影响 Holo Video Uploader.app</div><h1>本地全息体验台</h1><p id=link></p><span class=badge id=device>检查微雪…</span><span class=badge id=queue>队列 0</span></div></header><section class=jobs id=jobs></section></main><script>
async function refresh(){const r=await fetch('/api/status');const d=await r.json();link.textContent=d.visitor_url;device.textContent=d.device.connected?'微雪已连接：'+d.device.port:'微雪未连接';device.className='badge '+(d.device.connected?'ok':'bad');queue.textContent='队列 '+d.pending;jobs.innerHTML=d.jobs.length?d.jobs.map(j=>'<div class=job><b>'+(j.pet_name||'我的宠物')+' · '+j.pose_name+'</b><span>'+j.stage+(j.error?'<br><span class=bad>'+j.error+'</span>':'')+'</span><span class='+(j.status==='done'?'ok':j.status==='error'?'bad':'')+'>'+Math.round((j.progress||0)*100)+'%</span></div>').join(''):'<p class=muted>等待第一位访客扫码上传。</p>'}setInterval(refresh,1800);refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "local-holo-booth/1.0"

    def log_message(self, _format: str, *_args) -> None:
        return

    def send_bytes(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, code: int, value: dict) -> None:
        self.send_bytes(code, json.dumps(value, ensure_ascii=False).encode(), "application/json")

    def token_ok(self, query: dict) -> bool:
        return secrets.compare_digest(query.get("k", [""])[0], TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            self.send_bytes(200, CONSOLE_HTML.encode(), "text/html; charset=utf-8")
            return
        if parsed.path == "/u":
            if not self.token_ok(query):
                self.send_bytes(403, "链接已失效，请重新扫码。".encode(), "text/plain; charset=utf-8")
                return
            self.send_bytes(200, VISITOR_HTML.encode(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/qr.svg":
            try:
                image = qr_svg(visitor_url())
            except ImportError:
                self.send_json(503, {"error": "缺少 segno，请先运行 setup_local_booth.command"})
                return
            self.send_bytes(200, image, "image/svg+xml")
            return
        if parsed.path.startswith("/api/job/"):
            if not self.token_ok(query):
                self.send_json(403, {"error": "链接已失效"})
                return
            job = _job_copy(parsed.path.rsplit("/", 1)[-1])
            if not job:
                self.send_json(404, {"error": "任务不存在"})
                return
            job.pop("events", None)
            self.send_json(200, job)
            return
        if parsed.path == "/api/status":
            try:
                device = find_port(SETTINGS["port"] or None)
            except DeviceBridgeError:
                device = None
            with LOCK:
                jobs = [dict(JOBS[j]) for j in reversed(JOB_ORDER)]
            for job in jobs:
                job.pop("events", None)
            self.send_json(200, {
                "visitor_url": visitor_url(),
                "device": {"connected": bool(device), "port": device},
                "pending": sum(j["status"] not in {"done", "error"} for j in jobs),
                "jobs": jobs,
            })
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path != "/api/enqueue":
            self.send_json(404, {"error": "not found"})
            return
        if not self.token_ok(query):
            self.send_json(403, {"error": "链接已失效，请重新扫码"})
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > MAX_UPLOAD_BYTES + 8192:
            self.send_json(413, {"error": "照片为空或超过 30 MB"})
            return
        fields, upload = parse_multipart(
            self.rfile.read(size), self.headers.get("Content-Type", "")
        )
        if not upload or not upload[1]:
            self.send_json(400, {"error": "没有收到照片"})
            return
        filename, payload = upload
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".jpg"
        image_path = UPLOADS_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
        image_path.write_bytes(payload)
        try:
            imaging.normalize_input(image_path)
        except imaging.ImageError as exc:
            image_path.unlink(missing_ok=True)
            self.send_json(400, {"error": str(exc)})
            return
        pose = fields.get("pose", "curled_side")
        if pose not in POSES or pose not in prompts.POSE_TEXT:
            pose = "curled_side"
        job_id = enqueue(image_path, fields.get("pet_name", ""), pose)
        self.send_json(200, {"job_id": job_id})


def main() -> int:
    parser = argparse.ArgumentParser(description="独立本地全息体验台")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--advertise", default="")
    parser.add_argument("--serial-port", default="")
    parser.add_argument("--provider", choices=("ark", "agnes"), default="ark")
    parser.add_argument("--resolution", choices=("480p", "720p"), default="480p")
    parser.add_argument("--no-device", action="store_true", help="只转换，不通过 USB 发送")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    SETTINGS.update({
        "port": args.serial_port or None,
        "provider": args.provider,
        "resolution": args.resolution,
        "no_device": args.no_device,
        "http_port": args.port,
        "advertise": args.advertise,
    })
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=worker, daemon=True, name="local-booth-worker").start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    console = f"http://127.0.0.1:{args.port}/"
    print("\n本地全息体验台已启动")
    print(f"电脑控制台：{console}")
    print(f"手机扫码地址：{visitor_url()}")
    print("原 Holo Video Uploader.app 未被启动或修改。\n")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(console)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n本地体验台已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
