#!/bin/zsh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  exec python3 app.py --open
fi
echo '需要已有的 Python 3.10 或更高版本。'
read -r '?按回车退出'
