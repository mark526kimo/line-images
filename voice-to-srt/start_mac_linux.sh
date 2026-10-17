#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "第一次執行，正在建立環境並安裝套件，請稍候..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
fi
echo "檢查套件..."
.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python voice_to_srt.py "$@"
