#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

APP_DIR="/opt/petta/backend"
DEPLOY_DIR="$APP_DIR/deploy"
ENV_SOURCE="${PETTA_ENV_FILE:-/root/petta-backend.env}"

if [[ ! -f "$APP_DIR/api_server.py" || ! -f "$APP_DIR/requirements.txt" ]]; then
  echo "Backend source is incomplete under $APP_DIR." >&2
  exit 1
fi
if [[ ! -f "$ENV_SOURCE" ]]; then
  echo "Secure environment file not found: $ENV_SOURCE" >&2
  exit 1
fi

if ! id petta >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/petta --create-home --shell /usr/sbin/nologin petta
fi

install -d -m 0750 -o petta -g petta /var/lib/petta/runs /var/lib/petta/projection
install -d -m 0750 -o root -g petta /etc/petta
install -m 0600 -o root -g root "$ENV_SOURCE" /etc/petta/backend.env

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --requirement "$APP_DIR/requirements.txt"
cd "$APP_DIR"
"$APP_DIR/.venv/bin/python" -m unittest discover -s "$APP_DIR/tests" -q

chown -R root:petta "$APP_DIR"
chmod -R g+rX,o-rwx "$APP_DIR"

install -m 0644 "$DEPLOY_DIR/petta-generation.service" /etc/systemd/system/petta-generation.service
install -m 0644 "$DEPLOY_DIR/petta-projection.service" /etc/systemd/system/petta-projection.service

systemctl daemon-reload
systemctl enable --now petta-projection.service petta-generation.service

if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp
  ufw deny 8000/tcp
  ufw deny 8001/tcp
  ufw default deny incoming
  ufw default allow outgoing
  ufw --force enable
fi

curl --fail --silent http://127.0.0.1:8000/healthz
echo
echo "Petta backend is running on loopback. No public listener was created."
