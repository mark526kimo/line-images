@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
    echo 第一次執行，正在建立環境並安裝套件，請稍候...
    python -m venv .venv || (echo 找不到 Python，請先到 https://www.python.org 安裝 Python 3.9 以上，安裝時勾選 Add to PATH & pause & exit /b 1)
    .venv\Scripts\python -m pip install --upgrade pip
)
echo 檢查套件...
.venv\Scripts\python -m pip install -q -r requirements.txt || (pause & exit /b 1)
start "" .venv\Scripts\pythonw voice_to_srt.py
