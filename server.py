#!/usr/bin/env python3
"""Local upload UI for the pet loop pipeline.

Stdlib only, so it runs without installing anything:

    python server.py --port 8770

Security note: this binds to 127.0.0.1 and has no authentication. It is a local
operator tool. Do not expose it on a public interface or bind 0.0.0.0 without
putting an authenticating proxy in front of it.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import threading
import traceback
import uuid
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from petloop import config, diagnostics, imaging, pipeline, pricing, providers  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "runs" / "_uploads"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def parse_multipart(raw: bytes, content_type: str) -> tuple[dict[str, str], tuple[str, bytes] | None]:
    """Parse a multipart/form-data body without the removed `cgi` module."""
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
    )
    if not message.is_multipart():
        return {}, None

    fields: dict[str, str] = {}
    upload: tuple[str, bytes] | None = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            if name == "image":
                upload = (filename, payload)
        else:
            fields[str(name)] = payload.decode("utf-8", "replace").strip()
    return fields, upload


def new_job() -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "status": "queued", "events": [], "artifacts": None, "error": None}
    return job_id


def push_event(job_id: str, stage: str, payload: dict) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job["events"].append({"stage": stage, "payload": payload})
        job["events"] = job["events"][-80:]


def set_job(job_id: str, **fields) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return json.loads(json.dumps(job)) if job else None


def run_job(job_id: str, image_path: Path, spec: config.PipelineSpec) -> None:
    set_job(job_id, status="running")
    try:
        artifacts = pipeline.run(
            image_path,
            spec=spec,
            on_step=lambda stage, payload: push_event(job_id, stage, payload),
        )
        set_job(job_id, status="done", artifacts=artifacts.as_dict())
    except (providers.ProviderError, imaging.ImageError) as exc:
        set_job(job_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface unexpected failures to the UI
        traceback.print_exc()
        set_job(job_id, status="error", error=f"{type(exc).__name__}: {exc}")


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>宠物睡眠循环视频流水线</title>
<style>
  :root {
    --bg: #0f1113;
    --panel: #171a1d;
    --panel-2: #1d2125;
    --line: #2b3136;
    --text: #e7eaec;
    --muted: #949ca3;
    --accent: #4a9d7f;
    --accent-dim: #2f6350;
    --warn: #c9853f;
    --err: #c25a52;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
    letter-spacing: 0;
  }
  header {
    border-bottom: 1px solid var(--line); padding: 14px 22px;
    display: flex; align-items: baseline; gap: 14px; background: var(--panel);
  }
  header h1 { font-size: 15px; font-weight: 600; margin: 0; }
  header .sub { color: var(--muted); font-size: 12px; }
  main { display: grid; grid-template-columns: 340px 1fr; min-height: calc(100vh - 50px); }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
  .side { border-right: 1px solid var(--line); padding: 18px; background: var(--panel); }
  .work { padding: 18px 22px; }
  fieldset { border: 1px solid var(--line); border-radius: 6px; padding: 12px; margin: 0 0 14px; }
  legend { color: var(--muted); font-size: 11px; padding: 0 5px; text-transform: uppercase; }
  label { display: block; font-size: 12px; color: var(--muted); margin: 9px 0 4px; }
  select, input[type=number] {
    width: 100%; background: var(--panel-2); color: var(--text);
    border: 1px solid var(--line); border-radius: 4px; padding: 7px 8px; font-size: 13px;
  }
  .drop {
    border: 1px dashed var(--line); border-radius: 6px; padding: 22px 14px; text-align: center;
    color: var(--muted); cursor: pointer; background: var(--panel-2); transition: border-color .15s, color .15s;
  }
  .drop:hover, .drop.hot { border-color: var(--accent); color: var(--text); }
  .drop img { max-width: 100%; max-height: 190px; border-radius: 4px; display: block; margin: 0 auto; }
  button {
    width: 100%; margin-top: 12px; padding: 9px 12px; border-radius: 4px; cursor: pointer;
    background: var(--accent); border: 1px solid var(--accent); color: #08110d; font-size: 13px; font-weight: 600;
    display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  }
  button:disabled { background: var(--panel-2); border-color: var(--line); color: var(--muted); cursor: not-allowed; }
  button.ghost { background: transparent; border-color: var(--line); color: var(--text); font-weight: 500; }
  svg.icon { width: 15px; height: 15px; stroke: currentColor; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
  .cost { font-size: 12px; color: var(--muted); margin-top: 9px; }
  .cost b { color: var(--accent); font-weight: 600; }
  .steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
  @media (max-width: 700px) { .steps { grid-template-columns: 1fr; } }
  .step { border: 1px solid var(--line); border-radius: 6px; background: var(--panel); padding: 11px 12px; }
  .step .n { font-size: 11px; color: var(--muted); text-transform: uppercase; }
  .step .t { font-size: 13px; margin: 3px 0 7px; }
  .step .state { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 6px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--line); flex: none; }
  .dot.active { background: var(--warn); }
  .dot.ok { background: var(--accent); }
  .dot.bad { background: var(--err); }
  .media { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 700px) { .media { grid-template-columns: 1fr; } }
  .frame { border: 1px solid var(--line); border-radius: 6px; overflow: hidden; background: #000; aspect-ratio: 1 / 1; display: grid; place-items: center; }
  .frame img, .frame video { width: 100%; height: 100%; object-fit: contain; display: block; }
  .frame .ph { color: var(--muted); font-size: 12px; }
  .cap { font-size: 12px; color: var(--muted); margin: 6px 0 0; }
  pre {
    background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 11px;
    font-size: 11.5px; max-height: 240px; overflow: auto; color: var(--muted); margin: 14px 0 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .row { display: flex; gap: 10px; }
  .row > * { flex: 1; }
  .err { color: var(--err); font-size: 12.5px; margin-top: 10px; }
  .env {
    margin-top: 12px; padding: 9px 10px; border-radius: 4px; font-size: 12px; line-height: 1.5;
    border: 1px solid var(--warn); color: var(--warn); background: rgba(201, 133, 63, 0.08);
  }
  .env code { color: var(--text); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; word-break: break-all; }
</style>
</head>
<body>
<header>
  <h1>宠物睡眠循环视频流水线</h1>
  <span class="sub">上传正面照 &rarr; 黑底正视图 &rarr; 5 秒头尾循环</span>
</header>
<main>
  <section class="side">
    <fieldset>
      <legend>第 1 步 上传</legend>
      <div class="drop" id="drop">
        <div id="dropHint">点击或拖入宠物正面照<br /><span style="font-size:11px">jpg / png / webp，短边 ≥ 300px</span></div>
        <img id="preview" alt="已选择的宠物照片预览" hidden />
      </div>
      <input type="file" id="file" accept="image/*" hidden />
    </fieldset>
    <fieldset>
      <legend>生成参数</legend>
      <label for="pet">物种</label>
      <select id="pet">
        <option value="auto">自动识别</option>
        <option value="cat">猫</option>
        <option value="dog">狗</option>
      </select>
      <label for="pose">睡姿</label>
      <select id="pose">
        <option value="curled_side">侧卧蜷睡</option>
        <option value="loaf">趴卧收爪</option>
        <option value="sprawl">摊开趴睡</option>
      </select>
      <label for="resolution">分辨率</label>
      <select id="resolution">
        <option value="480p">480p（最省）</option>
        <option value="720p">720p</option>
      </select>
      <label for="ratio">画幅</label>
      <select id="ratio">
        <option value="1:1">1:1</option>
        <option value="9:16">9:16</option>
        <option value="16:9">16:9</option>
      </select>
      <label for="loopMode">循环处理</label>
      <select id="loopMode">
        <option value="trim">裁掉重复尾帧</option>
        <option value="xfade">尾首交叉淡化</option>
        <option value="none">不处理</option>
      </select>
      <div class="cost" id="cost">预估成本：<b>--</b></div>
    </fieldset>
    <div class="row">
      <button id="go" disabled>
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3l14 9-14 9V3z"/></svg>
        开始生成
      </button>
      <button id="reset" class="ghost" type="button">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
        重置
      </button>
    </div>
    <div class="err" id="err" hidden></div>
    <div class="env" id="env" hidden></div>
  </section>
  <section class="work">
    <div class="steps">
      <div class="step"><div class="n">Step 1</div><div class="t">照片校验</div><div class="state"><span class="dot" id="d1"></span><span id="s1">等待上传</span></div></div>
      <div class="step"><div class="n">Step 2</div><div class="t">黑底正视图</div><div class="state"><span class="dot" id="d2"></span><span id="s2">未开始</span></div></div>
      <div class="step"><div class="n">Step 3</div><div class="t">5 秒循环视频</div><div class="state"><span class="dot" id="d3"></span><span id="s3">未开始</span></div></div>
    </div>
    <div class="media">
      <div>
        <div class="frame"><img id="stillOut" alt="生成的黑底正视图" hidden /><span class="ph" id="stillPh">黑底正视图</span></div>
        <p class="cap" id="stillCap">纯黑背景，保留原宠物特征</p>
      </div>
      <div>
        <div class="frame"><video id="videoOut" loop muted autoplay playsinline hidden></video><span class="ph" id="videoPh">循环视频</span></div>
        <p class="cap" id="videoCap">5 秒，头尾无缝</p>
      </div>
    </div>
    <pre id="logView">就绪。</pre>
  </section>
</main>
<script>
const fileInput = document.getElementById('file');
const drop = document.getElementById('drop');
const preview = document.getElementById('preview');
const dropHint = document.getElementById('dropHint');
const go = document.getElementById('go');
const errBox = document.getElementById('err');
const logView = document.getElementById('logView');
let picked = null, timer = null;

const dots = { 1: document.getElementById('d1'), 2: document.getElementById('d2'), 3: document.getElementById('d3') };
const labels = { 1: document.getElementById('s1'), 2: document.getElementById('s2'), 3: document.getElementById('s3') };

function setStep(n, cls, text) {
  dots[n].className = 'dot' + (cls ? ' ' + cls : '');
  labels[n].textContent = text;
}

function showError(message) {
  errBox.hidden = !message;
  errBox.textContent = message || '';
}

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('hot'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hot'));
drop.addEventListener('drop', (e) => {
  e.preventDefault(); drop.classList.remove('hot');
  if (e.dataTransfer.files && e.dataTransfer.files[0]) choose(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => fileInput.files[0] && choose(fileInput.files[0]));

function choose(file) {
  if (!file.type.startsWith('image/')) { showError('请选择图片文件。'); return; }
  if (file.size > 30 * 1024 * 1024) { showError('图片超过 30 MB，请先压缩。'); return; }
  showError('');
  picked = file;
  preview.src = URL.createObjectURL(file);
  preview.hidden = false;
  dropHint.hidden = true;
  go.disabled = false;
  setStep(1, 'ok', file.name.length > 22 ? file.name.slice(0, 20) + '…' : file.name);
}

async function refreshCost() {
  const params = new URLSearchParams({
    resolution: document.getElementById('resolution').value,
    ratio: document.getElementById('ratio').value
  });
  const res = await fetch('/api/price?' + params);
  const data = await res.json();
  document.getElementById('cost').innerHTML =
    '预估成本：<b>' + data.effective_cny.toFixed(3) + ' 元 / 条</b>' +
    (data.promo_active ? '（促销价，刊例 ' + data.list_cny.toFixed(3) + ' 元）' : '') +
    '<br />' + data.tokens_est.toLocaleString() + ' tokens · ' + data.spec;
}
['resolution', 'ratio'].forEach((id) => document.getElementById(id).addEventListener('change', refreshCost));
refreshCost();

async function checkEnv() {
  const box = document.getElementById('env');
  try {
    const res = await fetch('/api/doctor');
    const data = await res.json();
    if (data.ready) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<b>环境未就绪</b><br />' + data.blocking.map(function (c) {
      return c.detail + (c.hint ? '<br /><code>' + c.hint + '</code>' : '');
    }).join('<br />');
  } catch (e) {
    box.hidden = true;
  }
}
checkEnv();

document.getElementById('reset').addEventListener('click', () => {
  picked = null; go.disabled = true; preview.hidden = true; dropHint.hidden = false;
  fileInput.value = ''; showError(''); logView.textContent = '就绪。';
  document.getElementById('stillOut').hidden = true; document.getElementById('stillPh').hidden = false;
  document.getElementById('videoOut').hidden = true; document.getElementById('videoPh').hidden = false;
  setStep(1, '', '等待上传'); setStep(2, '', '未开始'); setStep(3, '', '未开始');
  if (timer) { clearInterval(timer); timer = null; }
});

go.addEventListener('click', async () => {
  if (!picked) return;
  go.disabled = true; showError('');
  setStep(1, 'active', '校验中'); setStep(2, '', '排队'); setStep(3, '', '排队');
  const body = new FormData();
  body.append('image', picked);
  ['pet', 'pose', 'resolution', 'ratio'].forEach((id) => body.append(id, document.getElementById(id).value));
  body.append('loop_mode', document.getElementById('loopMode').value);
  const res = await fetch('/api/run', { method: 'POST', body });
  const data = await res.json();
  if (!res.ok) { showError(data.error || '提交失败'); go.disabled = false; setStep(1, 'bad', '失败'); return; }
  poll(data.job_id);
});

function poll(jobId) {
  if (timer) clearInterval(timer);
  timer = setInterval(async () => {
    const res = await fetch('/api/job/' + jobId);
    if (!res.ok) return;
    const job = await res.json();
    const lines = job.events.map((e) => '[' + e.stage + '] ' + JSON.stringify(e.payload));
    logView.textContent = lines.join('\\n') || '排队中…';
    logView.scrollTop = logView.scrollHeight;

    const stages = job.events.map((e) => e.stage);
    if (stages.includes('upload')) setStep(1, 'ok', '已校验');
    if (stages.some((s) => s.startsWith('still'))) setStep(2, 'active', '生成中');
    if (stages.includes('still_final')) setStep(2, 'ok', '已完成');
    if (stages.some((s) => s.startsWith('video'))) setStep(3, 'active', '渲染中');

    if (job.status === 'done') {
      clearInterval(timer); timer = null; go.disabled = false;
      setStep(2, 'ok', '已完成'); setStep(3, 'ok', '已完成');
      const a = job.artifacts || {};
      if (a.still) {
        const img = document.getElementById('stillOut');
        img.src = '/api/file?path=' + encodeURIComponent(a.still) + '&t=' + Date.now();
        img.hidden = false; document.getElementById('stillPh').hidden = true;
      }
      if (a.video) {
        const vid = document.getElementById('videoOut');
        vid.src = '/api/file?path=' + encodeURIComponent(a.video) + '&t=' + Date.now();
        vid.hidden = false; document.getElementById('videoPh').hidden = true;
        vid.play().catch(() => {});
      }
      const m = a.metrics || {};
      if (m.seam_after || m.seam_before) {
        const seam = m.seam_after || m.seam_before;
        document.getElementById('videoCap').textContent =
          '5 秒循环 · 接缝 ' + seam.rating + '（帧差 ' + seam.mean_abs_diff + '）';
      }
      if (m.background) {
        document.getElementById('stillCap').textContent =
          '黑底检测 · 边缘均值亮度 ' + m.background.mean_luma + ' · 纯黑占比 ' + (m.background.black_ratio * 100).toFixed(1) + '%';
      }
    } else if (job.status === 'error') {
      clearInterval(timer); timer = null; go.disabled = false;
      showError(job.error || '生成失败');
      setStep(3, 'bad', '失败');
    }
  }, 1500);
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "petloop/1.0"

    def log_message(self, fmt: str, *args) -> None:  # keep the console readable
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        route = parsed.path

        if route in {"/", "/index.html"}:
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if route == "/api/price":
            query = parse_qs(parsed.query)
            try:
                estimate = pricing.estimate(
                    config.VIDEO_MODEL or config.ARK_VIDEO_MODEL,
                    resolution=query.get("resolution", ["480p"])[0],
                    ratio=query.get("ratio", ["1:1"])[0],
                    seconds=5,
                )
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, estimate.as_dict())
            return

        if route.startswith("/api/job/"):
            job = get_job(route.rsplit("/", 1)[-1])
            self._json(200 if job else 404, job or {"error": "unknown job"})
            return

        if route == "/api/doctor":
            checks = diagnostics.run_all()
            blockers = diagnostics.blocking_failures(checks)
            self._json(
                200,
                {
                    "ready": not blockers,
                    "checks": [check.as_dict() for check in checks],
                    "blocking": [check.as_dict() for check in blockers],
                },
            )
            return

        if route == "/api/file":
            raw = parse_qs(parsed.query).get("path", [""])[0]
            if not raw:
                self._json(400, {"error": "missing path"})
                return
            target = Path(raw).resolve()
            allowed = (BASE_DIR / "runs").resolve()
            # Only serve files this pipeline produced.
            if not str(target).startswith(str(allowed)) or not target.is_file():
                self._json(403, {"error": "path not allowed"})
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send(200, target.read_bytes(), ctype)
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path != "/api/run":
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES + 4096:
            self._json(413, {"error": "upload too large or empty"})
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json(400, {"error": "expected multipart/form-data"})
            return

        fields, upload = parse_multipart(self.rfile.read(length), content_type)
        if upload is None:
            self._json(400, {"error": "no image uploaded"})
            return

        filename, payload = upload
        if not payload:
            self._json(400, {"error": "uploaded image is empty"})
            return
        if len(payload) > MAX_UPLOAD_BYTES:
            self._json(413, {"error": "image exceeds 30 MB"})
            return

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".png"
        upload_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
        upload_path.write_bytes(payload)

        try:
            imaging.normalize_input(upload_path)
        except imaging.ImageError as exc:
            upload_path.unlink(missing_ok=True)
            self._json(400, {"error": str(exc)})
            return

        def field(name: str, default: str) -> str:
            value = fields.get(name, default)
            return value if isinstance(value, str) and value else default

        spec = config.PipelineSpec(
            pet_kind=field("pet", "auto"),
            pose=field("pose", "curled_side"),
            provider="ark",
        )
        spec.video.resolution = field("resolution", "480p")
        spec.video.ratio = field("ratio", "1:1")
        spec.video.loop_mode = field("loop_mode", "trim")

        job_id = new_job()
        threading.Thread(target=run_job, args=(job_id, upload_path, spec), daemon=True).start()
        self._json(200, {"job_id": job_id})


def main() -> int:
    parser = argparse.ArgumentParser(description="Local UI for the pet loop pipeline")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Pet loop UI: http://{args.host}:{args.port}")
    if args.host not in {"127.0.0.1", "localhost"}:
        print("WARNING: this server has no authentication. Do not expose it publicly.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
