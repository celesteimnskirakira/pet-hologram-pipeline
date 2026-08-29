#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_DIR=${SCRIPT_DIR:h}
APP_DIR="$PROJECT_DIR/Holo Video Uploader.app"
CONTENTS_DIR="$APP_DIR/Contents"

if [[ -n "${FFMPEG_PATH:-}" ]]; then
  FFMPEG_BINARY="$FFMPEG_PATH"
elif command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG_BINARY=$(command -v ffmpeg)
else
  echo "ERROR: ffmpeg not found. Install it with Homebrew or set FFMPEG_PATH."
  exit 1
fi

rm -rf "$APP_DIR"
mkdir -p "$CONTENTS_DIR/MacOS" "$CONTENTS_DIR/Resources"

xcrun swiftc \
  -swift-version 5 \
  -O \
  -framework AppKit \
  "$SCRIPT_DIR/Sources/HoloUploader/main.swift" \
  -o "$CONTENTS_DIR/MacOS/HoloVideoUploader"

cp "$SCRIPT_DIR/Info.plist" "$CONTENTS_DIR/Info.plist"
cp "$FFMPEG_BINARY" "$CONTENTS_DIR/Resources/ffmpeg"
chmod +x "$CONTENTS_DIR/MacOS/HoloVideoUploader" "$CONTENTS_DIR/Resources/ffmpeg"
codesign --force --deep --sign - "$APP_DIR"

echo "BUILD SUCCESS: $APP_DIR"
