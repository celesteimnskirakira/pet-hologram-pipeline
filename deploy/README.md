# Petta backend service deployment

The current production baseline remains the standard-library `ThreadingHTTPServer` in `api_server.py`. This deployment does not install FastAPI, Uvicorn, Nginx, Certbot, or the frontend.

## Runtime layout

```text
Cloudflare HTTPS
        -> Cloudflare Tunnel
        -> 127.0.0.1:8000
        -> api_server.py
```

Ports 8000 and 8001 must not be opened in the cloud security group or host firewall.

## Install the backend service

1. Synchronize source to `/opt/petta/backend`, excluding `.env`, generated media, caches, `runs/`, and Git metadata.
2. Prepare `/root/petta-backend.env` from `backend.env.example` with mode `0600`.
3. Run:

```bash
PETTA_ENV_FILE=/root/petta-backend.env \
bash /opt/petta/backend/deploy/install_backend_service.sh
```

The installer creates the unprivileged `petta` user, virtual environment, loopback-only generation service, and the legacy loopback projection simulator used only by staging checks.

## Acceptance checks

1. `curl http://127.0.0.1:8000/healthz` returns `{ "ok": true }`.
2. `GET /api/v1/jobs` without `X-Generation-Backend-Secret` returns 401.
3. `ss -lntup` shows 8000 and 8001 only on `127.0.0.1`.
4. A controlled service restart returns both services to `active`.
5. No Nginx, Certbot, FastAPI, Uvicorn, or public 8000 listener is installed.

## Cloudflare Tunnel

Install `cloudflared` from Cloudflare's official Ubuntu Noble package source and use the dashboard-generated token command to install its systemd service. Never copy the real token into this repository or deployment documentation.

Install `cloudflared-origin.conf` as `/etc/systemd/system/cloudflared.service.d/origin.conf`, then reload systemd and restart `cloudflared`. This sets the token-free origin target to `http://127.0.0.1:8000` without modifying either Petta service.

The public hostname is `genpichong.dpdns.org`. Its Cloudflare DNS record must target `653a6233-d4f6-4725-9d29-93eb06c1e0f7.cfargotunnel.com`, or the equivalent Public Hostname must be created in the Tunnel dashboard. Cloudflare handles public HTTPS; the origin remains loopback-only.

The independent frontend is published as `app.genpichong.dpdns.org -> http://127.0.0.1:3000`. Both image and callback allowlists use that exact hostname.

## Holo Video Uploader device delivery

The real projection computer runs Holo Video Uploader and polls the generation host over HTTPS:

```text
GET  https://genpichong.dpdns.org/api/device/next
POST https://genpichong.dpdns.org/api/device/ack
Authorization: Bearer <PROJECTION_AGENT_SECRET>
```

The generation job remains in `delivering` until the Mac app has downloaded and converted the MP4, the ESP32 has acknowledged the USB upload, and the app posts `status=played`. Only then does the backend callback the frontend with `completed`.
