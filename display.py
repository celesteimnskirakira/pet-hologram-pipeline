#!/usr/bin/env python3
"""Display server for the holographic rig.

Two pages, both stdlib only:

    /          operator console: upload, queue, per-visitor status
    /stage     full-bleed black playback surface for the screen under the acrylic

    python display.py --port 8792

The stage page is what sits under the pane. It is pure black with no chrome, so
nothing but the pet reflects. It polls for the newest finished pet and crossfades
between that pet's actions.

Security note: binds to 127.0.0.1 by default and has no authentication. It is a
booth tool. On a venue network, keep it on localhost and drive the screen from
the same machine.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import secrets
import socket
import sys
import threading
import traceback
import uuid
from datetime import datetime
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from petloop import config, diagnostics, imaging, pipeline, prompts, providers  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / "runs"
UPLOAD_DIR = RUNS_DIR / "_uploads"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024

# Visitor access token. Present in the QR URL so a passer-by cannot POST to the
# booth just by knowing the LAN address, while a scan still needs no typing.
ACCESS_TOKEN = secrets.token_urlsafe(8)
PUBLIC_BASE = {"url": ""}

# Booth state. A single worker thread drains the queue so concurrent visitors
# cannot saturate the provider's per-account concurrency limit.
JOBS: dict[str, dict] = {}
JOB_ORDER: list[str] = []
STATE_LOCK = threading.Lock()
WORK_QUEUE: "queue.Queue[str]" = queue.Queue()
CURRENT_STAGE: dict[str, object] = {"job_id": None, "revision": 0}


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


def detect_lan_ip() -> str:
    """Best guess at the address a phone on the same WiFi can reach.

    A UDP connect to a public address reveals the routing interface without
    sending traffic. That can pick a VPN or tunnel interface though, so private
    LAN ranges are preferred and the CGNAT range used by some VPN clients is
    rejected outright.
    """
    candidates: list[str] = []

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass

    def usable(addr: str) -> bool:
        if addr.startswith("127.") or addr.startswith("169.254."):
            return False
        # 198.18/15 is benchmarking space; VPN clients commonly squat here and a
        # phone on the venue WiFi will not reach it.
        if addr.startswith("198.18.") or addr.startswith("198.19."):
            return False
        return True

    def preferred(addr: str) -> bool:
        return (
            addr.startswith("192.168.")
            or addr.startswith("10.")
            or any(addr.startswith(f"172.{n}.") for n in range(16, 32))
        )

    viable = [a for a in candidates if usable(a)]
    for addr in viable:
        if preferred(addr):
            return addr
    return viable[0] if viable else "127.0.0.1"


def visitor_url() -> str:
    base = PUBLIC_BASE["url"] or "http://127.0.0.1:8792"
    return f"{base}/u?k={ACCESS_TOKEN}"


def qr_svg(data: str, scale: int = 6) -> bytes:
    """Render the QR as SVG so it stays crisp when scaled up on screen."""
    import io

    import segno

    buffer = io.BytesIO()
    # Medium error correction tolerates a little glare on a printed card.
    segno.make(data, error="m").save(
        buffer, kind="svg", scale=scale, border=2, dark="#0f1113", light="#ffffff"
    )
    return buffer.getvalue()


def enqueue(visitor: str, pet_name: str, image_path: Path, spec: config.PipelineSpec) -> str:
    job_id = uuid.uuid4().hex[:10]
    with STATE_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "visitor": visitor or "",
            "pet_name": pet_name or "",
            "status": "queued",
            "queued_at": datetime.now().isoformat(timespec="seconds"),
            "stage": "waiting in queue",
            "events": [],
            "clips": [],
            "playlist": None,
            "error": None,
            "image": str(image_path),
            "spec": {
                "poses": list(spec.poses),
                "rig": spec.hologram.rig,
                "resolution": spec.video.resolution,
            },
        }
        JOB_ORDER.append(job_id)
    WORK_QUEUE.put(job_id)
    return job_id


def update_job(job_id: str, **fields) -> None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def push_event(job_id: str, stage: str, payload: dict) -> None:
    # Translate internal step names into something an operator can read at a
    # glance while a visitor is standing next to them.
    labels = {
        "upload": "checking photo",
        "traits": "reading markings",
        "still_attempt": "drawing portrait",
        "still_scored": "checking background",
        "still_final": "portrait ready",
        "actions_start": "generating actions",
        "video_status": "rendering video",
        "action_done": "action ready",
        "action_failed": "action failed",
        "actions_done": "all actions ready",
    }
    friendly = labels.get(stage, stage.replace("_", " "))
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job["stage"] = friendly
        job["events"].append({"stage": stage, "payload": payload})
        job["events"] = job["events"][-60:]


def worker_loop() -> None:
    while True:
        job_id = WORK_QUEUE.get()
        try:
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if job is None:
                    continue
                image_path = Path(job["image"])
                poses = tuple(job["spec"]["poses"])
                rig = job["spec"]["rig"]
                resolution = job["spec"]["resolution"]

            spec = config.PipelineSpec(poses=poses, parallel=True)
            spec.hologram.rig = rig
            spec.video.resolution = resolution

            update_job(job_id, status="running", stage="starting")
            artifacts = pipeline.run(
                image_path,
                spec=spec,
                on_step=lambda s, p: push_event(job_id, s, p),
            )
            update_job(
                job_id,
                status="done",
                stage="ready to show",
                clips=[c for c in artifacts.clips if c.get("ok")],
                playlist=str(artifacts.playlist) if artifacts.playlist else None,
                traits=artifacts.traits,
                metrics=artifacts.metrics,
            )
            # Newest finished pet takes the stage automatically.
            with STATE_LOCK:
                CURRENT_STAGE["job_id"] = job_id
                CURRENT_STAGE["revision"] = int(CURRENT_STAGE["revision"]) + 1
        except (providers.ProviderError, imaging.ImageError) as exc:
            update_job(job_id, status="error", stage="failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the booth running
            traceback.print_exc()
            update_job(job_id, status="error", stage="failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            WORK_QUEUE.task_done()


def recover_finished_runs() -> int:
    """Reload previously finished pets from disk on startup.

    Booth state lives in memory, so restarting the server mid-event would
    otherwise wipe every pet already generated even though the files are still
    on disk. Rebuilding from report.json keeps the operator able to re-show any
    earlier visitor after a crash or a deliberate restart.
    """
    if not RUNS_DIR.is_dir():
        return 0

    found: list[tuple[float, str, dict]] = []
    for report_path in RUNS_DIR.glob("*/report.json"):
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifacts = payload.get("artifacts") or {}
        clips = [
            c
            for c in (artifacts.get("clips") or [])
            if c.get("ok") and c.get("hologram") and Path(c["hologram"]).is_file()
        ]
        if not clips:
            continue
        traits = artifacts.get("traits") or {}
        found.append(
            (
                report_path.stat().st_mtime,
                report_path.parent.name,
                {
                    "clips": clips,
                    "traits": traits,
                    "metrics": artifacts.get("metrics") or {},
                    "playlist": artifacts.get("playlist"),
                    "poses": [c["pose"] for c in clips],
                },
            )
        )

    found.sort(key=lambda item: item[0])
    with STATE_LOCK:
        for mtime, run_name, data in found:
            job_id = "r" + run_name[-9:].replace("-", "")[:9]
            JOBS[job_id] = {
                "id": job_id,
                "visitor": "",
                "pet_name": data["traits"].get("species", "") or "",
                "status": "done",
                "queued_at": datetime.fromtimestamp(mtime).isoformat(timespec="seconds"),
                "stage": "recovered from disk",
                "events": [],
                "clips": data["clips"],
                "playlist": data["playlist"],
                "error": None,
                "image": "",
                "traits": data["traits"],
                "metrics": data["metrics"],
                "spec": {"poses": data["poses"], "rig": "single", "resolution": "480p"},
                "recovered": True,
            }
            JOB_ORDER.append(job_id)
        # Put the most recent pet back on the stage so the rig is not blank.
        if JOB_ORDER:
            CURRENT_STAGE["job_id"] = JOB_ORDER[-1]
            CURRENT_STAGE["revision"] = int(CURRENT_STAGE["revision"]) + 1
    return len(found)


VISITOR_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="theme-color" content="#0f1113" />
<title>让你的宠物上屏</title>
<style>
  :root{--bg:#0f1113;--panel:#171a1d;--line:#2b3136;--text:#e7eaec;--muted:#949ca3;
    --accent:#4a9d7f;--err:#c25a52}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;background:var(--bg);color:var(--text);letter-spacing:0;
    font:16px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif;
    padding:max(18px,env(safe-area-inset-top)) 18px max(18px,env(safe-area-inset-bottom))}
  h1{font-size:20px;font-weight:600;margin:4px 0 4px}
  .sub{color:var(--muted);font-size:13.5px;margin-bottom:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;margin-bottom:14px}
  .drop{border:1px dashed var(--line);border-radius:8px;padding:30px 14px;text-align:center;
    color:var(--muted);font-size:14px}
  .drop img{max-width:100%;max-height:44vh;border-radius:6px;display:block;margin:0 auto}
  label{display:block;font-size:13px;color:var(--muted);margin:12px 0 5px}
  input[type=text]{width:100%;background:#1d2125;color:var(--text);border:1px solid var(--line);
    border-radius:6px;padding:11px 12px;font-size:16px}
  button{width:100%;padding:15px;border-radius:8px;border:1px solid var(--accent);
    background:var(--accent);color:#08110d;font-size:16px;font-weight:600;cursor:pointer;
    display:inline-flex;align-items:center;justify-content:center;gap:8px}
  button:disabled{background:#1d2125;border-color:var(--line);color:var(--muted)}
  button.ghost{background:transparent;border-color:var(--line);color:var(--text);font-weight:500;margin-top:10px}
  svg.icon{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  .chips{display:flex;gap:8px;flex-wrap:wrap}
  .chip{border:1px solid var(--line);border-radius:6px;padding:9px 13px;font-size:14px;
    color:var(--muted);background:#1d2125;user-select:none}
  .chip[aria-pressed=true]{border-color:var(--accent);color:var(--text)}
  .err{color:var(--err);font-size:14px;margin-top:10px}
  .steps{display:flex;flex-direction:column;gap:11px}
  .step{display:flex;align-items:center;gap:10px;font-size:14.5px;color:var(--muted)}
  .step.on{color:var(--text)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--line);flex:none}
  .dot.on{background:var(--accent)}
  .dot.run{background:#c9853f}
  .spin{width:15px;height:15px;border:2px solid var(--line);border-top-color:var(--accent);
    border-radius:50%;animation:sp 1s linear infinite;flex:none}
  @keyframes sp{to{transform:rotate(360deg)}}
  .hint{color:var(--muted);font-size:13px;margin-top:12px}
  video{width:100%;border-radius:8px;background:#000;display:block}
  .big{font-size:17px;color:var(--text);font-weight:600}
</style>
</head>
<body>

<div id="formView">
  <h1>让你的宠物上屏</h1>
  <div class="sub">传一张正面照，稍后会在展台的全息屏里看到 TA 在睡觉</div>

  <div class="card">
    <div class="drop" id="drop">
      <div id="hint">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true" style="width:26px;height:26px">
          <path d="M3 16.5V19a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-2.5"/><path d="M7 9l5-5 5 5"/><path d="M12 4v12"/>
        </svg>
        <div style="margin-top:8px">点这里选照片或拍一张</div>
      </div>
      <img id="prev" alt="已选宠物照片" hidden />
    </div>
    <input type="file" id="file" accept="image/*" hidden />
    <label for="petName">宠物名字（可选）</label>
    <input type="text" id="petName" placeholder="比如 橘子" autocomplete="off" />
  </div>

  <div class="card">
    <label style="margin-top:0">想看哪些睡姿</label>
    <div class="chips" id="poses" role="group" aria-label="选择睡姿">
      <span class="chip" role="button" tabindex="0" aria-pressed="true" data-pose="curled_side">侧卧蜷睡</span>
      <span class="chip" role="button" tabindex="0" aria-pressed="true" data-pose="loaf">趴卧收爬</span>
      <span class="chip" role="button" tabindex="0" aria-pressed="false" data-pose="side_stretch">侧身伸展</span>
      <span class="chip" role="button" tabindex="0" aria-pressed="false" data-pose="curled_tight">团成球</span>
    </div>
  </div>

  <button id="go" disabled>
    <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3l14 9-14 9V3z"/></svg>
    开始生成
  </button>
  <div class="err" id="err" hidden></div>
</div>

<div id="waitView" hidden>
  <h1 id="waitTitle">正在生成</h1>
  <div class="sub">大约需要 2-3 分钟，可以先听展台介绍。这个页面会自动更新</div>
  <div class="card">
    <div class="steps">
      <div class="step" id="s1"><span class="dot"></span><span>检查照片</span></div>
      <div class="step" id="s2"><span class="dot"></span><span>记住 TA 的毛色和花纹</span></div>
      <div class="step" id="s3"><span class="dot"></span><span>画出睡着的样子</span></div>
      <div class="step" id="s4"><span class="dot"></span><span>让 TA 动起来</span></div>
    </div>
    <div class="hint" id="queueHint"></div>
  </div>
</div>

<div id="doneView" hidden>
  <h1>完成了</h1>
  <div class="sub" id="doneSub">去展台的全息屏看看吧</div>
  <div class="card" id="resultCard"></div>
  <button class="ghost" id="again" type="button">
    <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
    再传一只
  </button>
</div>

<script>
const TOKEN=new URLSearchParams(location.search).get('k')||'';
const fileInput=document.getElementById('file'),drop=document.getElementById('drop');
const prev=document.getElementById('prev'),hint=document.getElementById('hint');
const go=document.getElementById('go'),errBox=document.getElementById('err');
let picked=null,jobId=null,timer=null;

function showErr(m){errBox.hidden=!m;errBox.textContent=m||''}
function view(name){
  for(const id of ['formView','waitView','doneView'])
    document.getElementById(id).hidden=(id!==name);
}

drop.addEventListener('click',()=>fileInput.click());
fileInput.addEventListener('change',()=>fileInput.files[0]&&choose(fileInput.files[0]));

function choose(f){
  if(!f.type.startsWith('image/')){showErr('请选图片文件。');return}
  if(f.size>30*1024*1024){showErr('图片超过 30 MB，换一张小一点的。');return}
  showErr('');picked=f;
  prev.src=URL.createObjectURL(f);prev.hidden=false;hint.hidden=true;go.disabled=false;
}

const poseBox=document.getElementById('poses');
poseBox.addEventListener('click',e=>{
  const c=e.target.closest('.chip');if(!c)return;
  c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');
});
poseBox.addEventListener('keydown',e=>{
  if(e.key===' '||e.key==='Enter'){e.preventDefault();e.target.click()}
});

go.addEventListener('click',async()=>{
  if(!picked)return;
  const poses=[...poseBox.querySelectorAll('.chip[aria-pressed=true]')].map(c=>c.dataset.pose);
  if(!poses.length){showErr('至少选一个睡姿。');return}
  go.disabled=true;showErr('');
  const b=new FormData();
  b.append('image',picked);
  b.append('poses',poses.join(','));
  b.append('pet_name',document.getElementById('petName').value);
  b.append('source','qr');
  let r,d;
  try{
    r=await fetch('/api/enqueue?k='+encodeURIComponent(TOKEN),{method:'POST',body:b});
    d=await r.json();
  }catch(e){showErr('网络不稳定，再试一次。');go.disabled=false;return}
  if(!r.ok){showErr(d.error||'提交失败');go.disabled=false;return}
  jobId=d.job_id;
  document.getElementById('waitTitle').textContent=
    (document.getElementById('petName').value||'你的宠物')+' 正在生成';
  view('waitView');
  if(timer)clearInterval(timer);
  timer=setInterval(poll,2500);poll();
});

function mark(id,state){
  const row=document.getElementById(id);
  const dot=row.querySelector('.dot,.spin');
  row.classList.toggle('on',state!=='');
  if(state==='run'){
    if(dot.className!=='spin'){const s=document.createElement('span');s.className='spin';dot.replaceWith(s)}
  }else{
    const nd=document.createElement('span');
    nd.className='dot'+(state==='ok'?' on':'');
    dot.replaceWith(nd);
  }
}

async function poll(){
  if(!jobId)return;
  let j;
  try{
    const r=await fetch('/api/my/'+jobId+'?k='+encodeURIComponent(TOKEN));
    if(!r.ok)return;
    j=await r.json();
  }catch(e){return}

  const st=j.stages||{};
  mark('s1',st.checked?'ok':'run');
  mark('s2',st.traits?'ok':(st.checked?'run':''));
  mark('s3',st.portrait?'ok':(st.traits?'run':''));
  mark('s4',st.video?'ok':(st.portrait?'run':''));

  const qh=document.getElementById('queueHint');
  if(j.status==='queued'&&j.ahead>0){
    qh.textContent='前面还有 '+j.ahead+' 位，马上就到你';
  }else if(j.status==='running'){
    qh.textContent='已经开始了';
  }else{qh.textContent=''}

  if(j.status==='done'){
    clearInterval(timer);timer=null;
    const card=document.getElementById('resultCard');
    card.innerHTML=(j.clips||[]).map(c=>
      '<video src="/api/file?path='+encodeURIComponent(c.loop)+'&k='+encodeURIComponent(TOKEN)
      +'" muted loop autoplay playsinline style="margin-bottom:10px"></video>').join('');
    document.getElementById('doneSub').textContent=
      '已经送到展台的全息屏了，去看看吧';
    view('doneView');
  }else if(j.status==='error'){
    clearInterval(timer);timer=null;
    view('formView');go.disabled=false;
    showErr('生成失败了，可以换一张照片再试。建议用光线充足、能看清正面的照片。');
  }
}

document.getElementById('again').addEventListener('click',()=>{
  picked=null;jobId=null;go.disabled=true;
  prev.hidden=true;hint.hidden=false;fileInput.value='';
  document.getElementById('petName').value='';
  document.getElementById('resultCard').innerHTML='';
  showErr('');view('formView');
});
</script>
</body>
</html>
"""

CONSOLE_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>全息宠物 · 操作台</title>
<style>
  :root {
    --bg:#0f1113; --panel:#171a1d; --panel2:#1d2125; --line:#2b3136;
    --text:#e7eaec; --muted:#949ca3; --accent:#4a9d7f; --warn:#c9853f; --err:#c25a52;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;letter-spacing:0}
  header{border-bottom:1px solid var(--line);padding:12px 20px;background:var(--panel);
    display:flex;align-items:baseline;gap:14px;justify-content:space-between}
  header h1{font-size:15px;font-weight:600;margin:0}
  header .right{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--muted)}
  header a{color:var(--accent);text-decoration:none}
  main{display:grid;grid-template-columns:330px 1fr;min-height:calc(100vh - 46px)}
  @media (max-width:900px){main{grid-template-columns:1fr}}
  .side{border-right:1px solid var(--line);padding:16px;background:var(--panel)}
  .work{padding:16px 20px;overflow:auto}
  fieldset{border:1px solid var(--line);border-radius:6px;padding:12px;margin:0 0 12px}
  legend{color:var(--muted);font-size:11px;padding:0 5px;text-transform:uppercase}
  label{display:block;font-size:12px;color:var(--muted);margin:8px 0 4px}
  select,input[type=text]{width:100%;background:var(--panel2);color:var(--text);
    border:1px solid var(--line);border-radius:4px;padding:7px 8px;font-size:13px}
  .drop{border:1px dashed var(--line);border-radius:6px;padding:20px 12px;text-align:center;
    color:var(--muted);cursor:pointer;background:var(--panel2)}
  .drop:hover,.drop.hot{border-color:var(--accent);color:var(--text)}
  .drop img{max-width:100%;max-height:150px;border-radius:4px;display:block;margin:0 auto}
  button{width:100%;margin-top:10px;padding:9px 12px;border-radius:4px;cursor:pointer;
    background:var(--accent);border:1px solid var(--accent);color:#08110d;font-size:13px;font-weight:600;
    display:inline-flex;align-items:center;justify-content:center;gap:7px}
  button:disabled{background:var(--panel2);border-color:var(--line);color:var(--muted);cursor:not-allowed}
  button.ghost{background:transparent;border-color:var(--line);color:var(--text);font-weight:500}
  svg.icon{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .chip{border:1px solid var(--line);border-radius:4px;padding:5px 9px;font-size:12px;
    cursor:pointer;color:var(--muted);background:var(--panel2);user-select:none}
  .chip[aria-pressed=true]{border-color:var(--accent);color:var(--text)}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
  th{color:var(--muted);font-size:11px;text-transform:uppercase;font-weight:500}
  tr.live{background:rgba(74,157,127,0.06)}
  .dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:7px;background:var(--line)}
  .dot.run{background:var(--warn)}
  .dot.ok{background:var(--accent)}
  .dot.bad{background:var(--err)}
  .thumbs{display:flex;gap:5px}
  .thumbs img{width:40px;height:40px;object-fit:cover;border-radius:3px;background:#000;border:1px solid var(--line)}
  .act{border:1px solid var(--line);background:transparent;color:var(--text);width:auto;
    margin:0;padding:5px 10px;font-size:12px;font-weight:500}
  .empty{color:var(--muted);padding:26px 10px;text-align:center;font-size:13px}
  .err{color:var(--err);font-size:12.5px;margin-top:9px}
  .env{margin-top:10px;padding:9px 10px;border-radius:4px;font-size:12px;
    border:1px solid var(--warn);color:var(--warn);background:rgba(201,133,63,.08)}
  .env code{color:var(--text);font-family:ui-monospace,Menlo,monospace;font-size:11px;word-break:break-all}
  .qinfo{font-size:12px;color:var(--muted);margin-top:8px}
</style>
</head>
<body>
<header>
  <h1>全息宠物 · 操作台</h1>
  <div class="right">
    <span id="qstat">队列 0</span>
    <a href="#" id="qrToggle">二维码</a>
    <a href="/stage" target="_blank" rel="noopener">打开投影页 &rarr;</a>
  </div>
</header>
<main>
  <section class="side">
    <fieldset id="qrPanel" hidden>
      <legend>访客扫码</legend>
      <div id="qrBox" style="background:#fff;border-radius:6px;padding:10px;display:grid;place-items:center"></div>
      <div class="qinfo" id="qrUrl" style="word-break:break-all"></div>
      <div class="qinfo" id="qrWarn"></div>
    </fieldset>
    <fieldset>
      <legend>访客照片</legend>
      <div class="drop" id="drop">
        <div id="hint">点击或拖入宠物正面照<br /><span style="font-size:11px">短边 ≥ 300px</span></div>
        <img id="prev" alt="已选照片预览" hidden />
      </div>
      <input type="file" id="file" accept="image/*" hidden />
      <label for="petName">宠物名字（可选）</label>
      <input type="text" id="petName" placeholder="比如 橘子" />
      <label for="visitor">访客备注（可选）</label>
      <input type="text" id="visitor" placeholder="方便叫号" />
    </fieldset>
    <fieldset>
      <legend>动作组合</legend>
      <div class="chips" id="poseChips" role="group" aria-label="选择动作">
        <span class="chip" role="button" tabindex="0" aria-pressed="true" data-pose="curled_side">侧卧蜷睡</span>
        <span class="chip" role="button" tabindex="0" aria-pressed="true" data-pose="loaf">趴卧收爪</span>
        <span class="chip" role="button" tabindex="0" aria-pressed="true" data-pose="side_stretch">侧身伸展</span>
        <span class="chip" role="button" tabindex="0" aria-pressed="false" data-pose="curled_tight">团成球</span>
        <span class="chip" role="button" tabindex="0" aria-pressed="false" data-pose="sprawl">摊开趴睡</span>
      </div>
      <label for="rig">投影结构</label>
      <select id="rig">
        <option value="single">单面 45 度</option>
        <option value="quad">四面锥形</option>
      </select>
      <label for="res">分辨率</label>
      <select id="res">
        <option value="480p">480p（最省）</option>
        <option value="720p">720p</option>
      </select>
      <div class="qinfo" id="cost">预估成本：--</div>
    </fieldset>
    <button id="go" disabled>
      <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
      加入队列
    </button>
    <button id="reset" class="ghost" type="button">
      <svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
      清空
    </button>
    <div class="err" id="err" hidden></div>
    <div class="env" id="env" hidden></div>
  </section>
  <section class="work">
    <table>
      <thead>
        <tr><th>状态</th><th>宠物</th><th>动作</th><th>进度</th><th></th></tr>
      </thead>
      <tbody id="rows">
        <tr><td colspan="5" class="empty">还没有访客。上传第一张照片试试。</td></tr>
      </tbody>
    </table>
  </section>
</main>
<script>
const fileInput=document.getElementById('file'),drop=document.getElementById('drop');
const prev=document.getElementById('prev'),hint=document.getElementById('hint');
const go=document.getElementById('go'),errBox=document.getElementById('err');
let picked=null;

function showErr(m){errBox.hidden=!m;errBox.textContent=m||''}
drop.addEventListener('click',()=>fileInput.click());
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('hot')});
drop.addEventListener('dragleave',()=>drop.classList.remove('hot'));
drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('hot');
  if(e.dataTransfer.files[0])choose(e.dataTransfer.files[0])});
fileInput.addEventListener('change',()=>fileInput.files[0]&&choose(fileInput.files[0]));

function choose(f){
  if(!f.type.startsWith('image/')){showErr('请选择图片文件。');return}
  if(f.size>30*1024*1024){showErr('图片超过 30 MB。');return}
  showErr('');picked=f;prev.src=URL.createObjectURL(f);prev.hidden=false;hint.hidden=true;go.disabled=false;
}

document.getElementById('poseChips').addEventListener('click',e=>{
  const c=e.target.closest('.chip');if(!c)return;
  c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');
  refreshCost();
});
document.getElementById('poseChips').addEventListener('keydown',e=>{
  if(e.key===' '||e.key==='Enter'){e.preventDefault();e.target.click()}
});

function selectedPoses(){
  return [...document.querySelectorAll('.chip[aria-pressed=true]')].map(c=>c.dataset.pose);
}

async function refreshCost(){
  const n=selectedPoses().length;
  const r=await fetch('/api/price?resolution='+document.getElementById('res').value);
  const d=await r.json();
  const vid=d.effective_cny*n, img=0.20*2*n*0.7;
  document.getElementById('cost').textContent=
    '预估成本：约 '+(vid+img).toFixed(2)+' 元 / 位（'+n+' 个动作，视频 '+vid.toFixed(2)+' + 图像 '+img.toFixed(2)+'）';
}
document.getElementById('res').addEventListener('change',refreshCost);
refreshCost();

document.getElementById('reset').addEventListener('click',()=>{
  picked=null;go.disabled=true;prev.hidden=true;hint.hidden=false;fileInput.value='';
  document.getElementById('petName').value='';document.getElementById('visitor').value='';showErr('');
});

go.addEventListener('click',async()=>{
  if(!picked)return;
  const poses=selectedPoses();
  if(!poses.length){showErr('至少选一个动作。');return}
  go.disabled=true;showErr('');
  const b=new FormData();
  b.append('image',picked);
  b.append('poses',poses.join(','));
  b.append('rig',document.getElementById('rig').value);
  b.append('resolution',document.getElementById('res').value);
  b.append('pet_name',document.getElementById('petName').value);
  b.append('visitor',document.getElementById('visitor').value);
  const r=await fetch('/api/enqueue',{method:'POST',body:b});
  const d=await r.json();
  if(!r.ok){showErr(d.error||'提交失败');go.disabled=false;return}
  document.getElementById('reset').click();
});

const POSE_CN={curled_side:'侧卧蜷睡',loaf:'趴卧收爪',side_stretch:'侧身伸展',
  curled_tight:'团成球',sprawl:'摊开趴睡'};

async function poll(){
  try{
    const r=await fetch('/api/jobs');const d=await r.json();
    document.getElementById('qstat').textContent='队列 '+d.pending+' · 已完成 '+d.done;
    const rows=document.getElementById('rows');
    if(!d.jobs.length){
      rows.innerHTML='<tr><td colspan="5" class="empty">还没有访客。上传第一张照片试试。</td></tr>';
    }else{
      rows.innerHTML=d.jobs.map(j=>{
        const cls=j.status==='done'?'ok':j.status==='error'?'bad':j.status==='running'?'run':'';
        const thumbs=(j.clips||[]).map(c=>
          '<img src="/api/file?path='+encodeURIComponent(c.still)+'" alt="'+(POSE_CN[c.pose]||c.pose)+'" />').join('');
        const poses=(j.spec.poses||[]).map(p=>POSE_CN[p]||p).join('、');
        const btn=j.status==='done'
          ? '<button class="act" onclick="show(\\''+j.id+'\\')">投到屏幕</button>':'';
        return '<tr class="'+(j.id===d.stage_job?'live':'')+'">'
          +'<td><span class="dot '+cls+'"></span>'+(j.status==='done'?'完成':j.status==='error'?'失败':j.status==='running'?'生成中':'排队')+'</td>'
          +'<td>'+(j.pet_name||'—')+(j.visitor?'<br /><span style="color:var(--muted);font-size:11px">'+j.visitor+'</span>':'')+'</td>'
          +'<td>'+poses+'<div class="thumbs">'+thumbs+'</div></td>'
          +'<td>'+(j.error?'<span style="color:var(--err)">'+j.error.slice(0,70)+'</span>':j.stage)+'</td>'
          +'<td>'+btn+'</td></tr>';
      }).join('');
    }
  }catch(e){}
}
async function show(id){await fetch('/api/stage/'+id,{method:'POST'});poll()}
setInterval(poll,2000);poll();

document.getElementById('qrToggle').addEventListener('click',async e=>{
  e.preventDefault();
  const panel=document.getElementById('qrPanel');
  panel.hidden=!panel.hidden;
  if(panel.hidden)return;
  const r=await fetch('/api/link');const d=await r.json();
  document.getElementById('qrBox').innerHTML=
    '<img src="/api/qr.svg?scale=7" alt="访客上传页二维码" style="width:100%;max-width:220px;display:block" />';
  document.getElementById('qrUrl').textContent=d.url;
  document.getElementById('qrWarn').textContent=
    d.url.includes('127.0.0.1')
      ? '当前只绑定本机，手机扫不到。重启时加 --lan 参数。'
      : '手机需连同一个 WiFi。';
});

(async()=>{
  try{
    const r=await fetch('/api/doctor');const d=await r.json();
    const box=document.getElementById('env');
    if(d.ready){box.hidden=true;return}
    box.hidden=false;
    box.innerHTML='<b>环境未就绪</b><br />'+d.blocking.map(c=>
      c.detail+(c.hint?'<br /><code>'+c.hint+'</code>':'')).join('<br />');
  }catch(e){}
})();
</script>
</body>
</html>
"""

STAGE_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Stage</title>
<style>
  /* Pure black, no chrome. Anything non-black reflects off the acrylic. */
  html,body{margin:0;height:100%;background:#000;overflow:hidden;cursor:none}
  #wrap{position:fixed;inset:0;background:#000}
  video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;
    background:#000;opacity:0;transition:opacity var(--fade,1.1s) ease-in-out}
  video.on{opacity:1}
  #idle{position:fixed;inset:0;display:grid;place-items:center;color:#1b1b1b;
    font:300 clamp(18px,2.4vw,30px)/1.4 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;
    letter-spacing:0;transition:opacity .8s ease}
  #idle.hide{opacity:0}
</style>
</head>
<body>
<div id="wrap">
  <video id="va" muted loop playsinline preload="auto"></video>
  <video id="vb" muted loop playsinline preload="auto"></video>
</div>
<div id="idle">待机中</div>
<script>
// Two stacked video elements. Both are visible during the handover, so the
// outgoing pose dissolves into the incoming one.
//
// The transition lives here rather than being burned into the video files. A
// hard cut between two different sleeping poses measures ~10.8 mean frame delta
// against ~0.8 for the loop point inside one clip, so some blend is needed; but
// opacity on two layers achieves it for free and stays adjustable at runtime.
const va=document.getElementById('va'),vb=document.getElementById('vb');
const idle=document.getElementById('idle');
let front=va,back=vb,items=[],cursor=0,revision=-1,timer=null;

// Crossfade length and how many loops to hold a pose before moving on.
const FADE_MS=1100, LOOPS_PER_POSE=3;
document.documentElement.style.setProperty('--fade',FADE_MS+'ms');

function swapTo(src){
  return new Promise(res=>{
    back.src=src;
    back.load();
    const start=()=>{
      // Let the incoming clip decode a frame before revealing it, otherwise the
      // fade briefly shows an empty element.
      back.play().catch(()=>{});
      back.classList.add('on');
      front.classList.remove('on');
      const tmp=front;front=back;back=tmp;
      setTimeout(res,FADE_MS+80);
    };
    let fired=false;
    const once=()=>{if(!fired){fired=true;start()}};
    back.oncanplay=once;
    // Never stall the show on a slow decode.
    setTimeout(()=>{if(fired)return;if(back.readyState>=2)once();else res()},2500);
  });
}

async function cycle(){
  if(!items.length)return;
  const item=items[cursor%items.length];
  cursor++;
  await swapTo('/api/file?path='+encodeURIComponent(item.hologram));
}

async function tick(){
  try{
    const r=await fetch('/api/stage');const d=await r.json();
    if(d.revision!==revision){
      revision=d.revision;
      items=d.items||[];
      cursor=0;
      if(items.length){
        idle.classList.add('hide');
        await cycle();
        if(timer)clearInterval(timer);
        // Hold each action for a few loops, then dissolve to the next. A single
        // action just keeps looping on its own and never needs a handover.
        if(items.length>1){
          timer=setInterval(cycle,(d.loop_seconds||5)*1000*LOOPS_PER_POSE);
        }
      }else{
        idle.classList.remove('hide');
      }
    }
  }catch(e){}
}
setInterval(tick,1500);tick();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "petloop-display/1.0"

    def log_message(self, fmt: str, *args) -> None:
        return  # keep the booth console quiet

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _serve_file(self, raw: str) -> None:
        if not raw:
            self._json(400, {"error": "missing path"})
            return
        target = Path(raw).resolve()
        allowed = RUNS_DIR.resolve()
        if not str(target).startswith(str(allowed)) or not target.is_file():
            self._json(403, {"error": "path not allowed"})
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        # Range support so the stage page can seek/loop video smoothly.
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                start_s, _, end_s = range_header[6:].partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(data) - 1
                end = min(end, len(data) - 1)
                chunk = data[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            except (ValueError, BrokenPipeError, ConnectionResetError):
                return
        self._send(200, data, ctype)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        # Visitor-facing surfaces require the token from the QR code. The operator
        # console stays open because it is only reachable from the booth machine
        # when bound to localhost.
        def token_ok() -> bool:
            supplied = query.get("k", [""])[0]
            return secrets.compare_digest(supplied, ACCESS_TOKEN)

        if route in {"/", "/index.html"}:
            self._send(200, CONSOLE_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if route == "/u":
            if not token_ok():
                self._send(
                    403,
                    "<!DOCTYPE html><meta charset=utf-8>"
                    "<body style='background:#0f1113;color:#949ca3;font:16px/1.6 -apple-system,sans-serif;"
                    "padding:40px 24px'>"
                    "<p>链接已失效。请重新扫展台上的二维码。</p>".encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            self._send(200, VISITOR_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if route == "/api/qr.svg":
            try:
                svg = qr_svg(visitor_url(), scale=int(query.get("scale", ["6"])[0]))
            except Exception as exc:  # noqa: BLE001 - report instead of crashing the booth
                self._json(500, {"error": f"qr render failed: {exc}"})
                return
            self._send(200, svg, "image/svg+xml")
            return

        if route == "/api/link":
            self._json(200, {"url": visitor_url(), "base": PUBLIC_BASE["url"]})
            return

        if route.startswith("/api/my/"):
            if not token_ok():
                self._json(403, {"error": "invalid token"})
                return
            job_id = route.rsplit("/", 1)[-1]
            with STATE_LOCK:
                job = dict(JOBS[job_id]) if job_id in JOBS else None
                waiting = [
                    j for j in JOB_ORDER if JOBS[j]["status"] in {"queued", "running"}
                ]
            if job is None:
                self._json(404, {"error": "unknown job"})
                return
            ahead = waiting.index(job_id) if job_id in waiting else 0
            stages = {name for e in job.get("events", []) for name in [e["stage"]]}
            self._json(
                200,
                {
                    "status": job["status"],
                    "ahead": max(0, ahead),
                    "stages": {
                        "checked": "upload" in stages,
                        "traits": "traits" in stages,
                        "portrait": "still_final" in stages,
                        "video": job["status"] == "done",
                    },
                    "clips": [
                        {"pose": c["pose"], "loop": c["loop"]}
                        for c in (job.get("clips") or [])
                    ],
                    "error": job.get("error"),
                },
            )
            return
        if route == "/stage":
            self._send(200, STAGE_PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if route == "/api/jobs":
            with STATE_LOCK:
                jobs = [dict(JOBS[j]) for j in reversed(JOB_ORDER)]
                stage_job = CURRENT_STAGE["job_id"]
            for job in jobs:
                job.pop("events", None)
            pending = sum(1 for j in jobs if j["status"] in {"queued", "running"})
            done = sum(1 for j in jobs if j["status"] == "done")
            self._json(200, {"jobs": jobs, "pending": pending, "done": done, "stage_job": stage_job})
            return

        if route == "/api/stage":
            with STATE_LOCK:
                job_id = CURRENT_STAGE["job_id"]
                revision = CURRENT_STAGE["revision"]
                job = dict(JOBS[job_id]) if job_id and job_id in JOBS else None
            items = [
                {"pose": c["pose"], "hologram": c["hologram"]}
                for c in (job or {}).get("clips", [])
                if c.get("hologram")
            ]
            self._json(200, {"revision": revision, "items": items, "loop_seconds": 5})
            return

        if route == "/api/price":
            from petloop import pricing

            query = parse_qs(parsed.query)
            try:
                estimate = pricing.estimate(
                    config.ARK_VIDEO_MODEL,
                    resolution=query.get("resolution", ["480p"])[0],
                    ratio="1:1",
                    seconds=5,
                )
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            self._json(200, estimate.as_dict())
            return

        if route == "/api/doctor":
            checks = diagnostics.run_all()
            blocking = diagnostics.blocking_failures(checks)
            self._json(
                200,
                {
                    "ready": not blocking,
                    "checks": [c.as_dict() for c in checks],
                    "blocking": [c.as_dict() for c in blocking],
                },
            )
            return

        if route == "/api/file":
            self._serve_file(parse_qs(parsed.query).get("path", [""])[0])
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        supplied = parse_qs(parsed.query).get("k", [""])[0]
        from_visitor = bool(supplied)

        if route.startswith("/api/stage/"):
            job_id = route.rsplit("/", 1)[-1]
            with STATE_LOCK:
                if job_id not in JOBS or JOBS[job_id]["status"] != "done":
                    self._json(404, {"error": "job not ready"})
                    return
                CURRENT_STAGE["job_id"] = job_id
                CURRENT_STAGE["revision"] = int(CURRENT_STAGE["revision"]) + 1
            self._json(200, {"ok": True})
            return

        if route != "/api/enqueue":
            self._json(404, {"error": "not found"})
            return

        # A request carrying a token must present the right one. Requests without
        # a token come from the operator console on the booth machine.
        if from_visitor and not secrets.compare_digest(supplied, ACCESS_TOKEN):
            self._json(403, {"error": "链接已失效，请重新扫码"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES + 8192:
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

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".png"
        image_path = UPLOAD_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
        image_path.write_bytes(payload)

        try:
            imaging.normalize_input(image_path)
        except imaging.ImageError as exc:
            image_path.unlink(missing_ok=True)
            self._json(400, {"error": str(exc)})
            return

        raw_poses = [p.strip() for p in fields.get("poses", "").split(",") if p.strip()]
        valid = [p for p in raw_poses if p in prompts.POSE_TEXT]
        if not valid:
            valid = list(prompts.ROADSHOW_POSES)

        spec = config.PipelineSpec(poses=tuple(valid), parallel=True)
        spec.hologram.rig = fields.get("rig", "single")
        spec.video.resolution = fields.get("resolution", "480p")

        job_id = enqueue(fields.get("visitor", ""), fields.get("pet_name", ""), image_path, spec)
        self._json(200, {"job_id": job_id, "poses": valid})


def main() -> int:
    parser = argparse.ArgumentParser(description="Holographic pet display booth")
    parser.add_argument("--port", type=int, default=8792)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="bind all interfaces so phones on the same WiFi can scan the QR code",
    )
    parser.add_argument(
        "--advertise",
        default="",
        help="override the address used in the QR code, e.g. 192.168.1.20",
    )
    args = parser.parse_args()

    host = "0.0.0.0" if args.lan else args.host  # noqa: S104 - opt-in booth mode
    advertised = args.advertise or (detect_lan_ip() if args.lan else args.host)
    PUBLIC_BASE["url"] = f"http://{advertised}:{args.port}"

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    recovered = recover_finished_runs()
    if recovered:
        print(f"Recovered {recovered} finished pet(s) from disk; newest is on the stage.")
    threading.Thread(target=worker_loop, daemon=True).start()

    server = ThreadingHTTPServer((host, args.port), Handler)
    print(f"Console: http://{args.host if not args.lan else advertised}:{args.port}/")
    print(f"Stage:   http://{args.host if not args.lan else advertised}:{args.port}/stage   (fullscreen on the rig screen)")
    print(f"Visitor: {visitor_url()}")
    if args.lan:
        print()
        print("LAN mode is on. Phones on the same WiFi can reach the visitor page.")
        print("The visitor link carries a per-session token; the operator console does not.")
        print("Anyone on this network who reaches the console URL can drive the booth,")
        print("so prefer a private hotspot over open venue WiFi.")
        if advertised.startswith("127."):
            print("WARNING: could not detect a LAN address. Pass --advertise <ip> explicitly.")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
