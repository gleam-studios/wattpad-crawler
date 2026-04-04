#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_BIN="$SCRIPT_DIR/dist/WattpadTool.app/Contents/MacOS/WattpadTool"

if [[ ! -x "$APP_BIN" ]]; then
  osascript -e 'display alert "启动失败" message "没有找到可执行文件：'"$APP_BIN"'" as critical'
  exit 1
fi

exec "$APP_BIN"
