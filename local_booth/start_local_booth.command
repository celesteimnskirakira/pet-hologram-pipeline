#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

cd "$REPO_DIR"

# Reuse a project-level FFmpeg tool when this checkout sits next to the
# firmware workspace. This is separate from Holo Video Uploader.app.
if [[ -z "${FFMPEG_PATH:-}" && -x "$REPO_DIR/../tools/bin/ffmpeg" ]]; then
  export FFMPEG_PATH="$REPO_DIR/../tools/bin/ffmpeg"
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "首次使用请先双击 local_booth/setup_local_booth.command"
  read -k 1 "?按任意键关闭…"
  exit 1
fi

if [[ ! -f "$REPO_DIR/.env" ]]; then
  echo "缺少 .env。请把 .env.example 复制为 .env，并填入 ARK_API_KEY 或 AGNES_API_KEY。"
  read -k 1 "?按任意键关闭…"
  exit 1
fi

echo "启动独立的本地全息体验台…"
echo "关闭这个窗口即可停止；原 Holo Video Uploader.app 不受影响。"
exec "$PYTHON" "$SCRIPT_DIR/server.py"
