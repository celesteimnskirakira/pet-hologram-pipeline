#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
VENV_DIR="$SCRIPT_DIR/.venv"

cd "$REPO_DIR"
echo "正在配置独立的本地全息体验台…"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  /usr/bin/python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

LOCAL_FFMPEG="$REPO_DIR/../tools/bin/ffmpeg"
if ! command -v ffmpeg >/dev/null 2>&1 && [[ ! -x "$LOCAL_FFMPEG" ]]; then
  echo ""
  echo "未找到 FFmpeg。请先安装 Homebrew，然后运行：brew install ffmpeg"
  echo "也可以设置 FFMPEG_PATH 指向现有 ffmpeg。"
  read -k 1 "?按任意键关闭…"
  exit 1
fi

echo ""
echo "配置完成。以后只需双击 start_local_booth.command。"
read -k 1 "?按任意键关闭…"
