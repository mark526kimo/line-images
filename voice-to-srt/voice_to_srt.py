#!/usr/bin/env python3
"""語音轉 SRT 字幕工具。

使用 faster-whisper 在本機離線辨識語音，輸出 .srt 字幕檔。
不帶參數執行會開啟圖形介面；帶檔案路徑則以命令列模式執行：

    python voice_to_srt.py                       # 開啟視窗
    python voice_to_srt.py audio.mp3             # 命令列轉換
    python voice_to_srt.py a.mp3 b.mp4 -m small -l zh --traditional
"""

import argparse
import os
import sys
import threading

MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
LANGUAGES = {
    "自動偵測": None,
    "中文": "zh",
    "英文": "en",
    "日文": "ja",
    "韓文": "ko",
}
MEDIA_TYPES = [
    ("音訊/影片", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.mkv *.mov *.avi *.webm"),
    ("所有檔案", "*.*"),
]


def format_timestamp(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_long_segment(text, start, end, max_chars):
    """把過長的字幕依字數平均切成多行，時間按比例分配。"""
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return [(start, end, text)]
    n = -(-len(text) // max_chars)  # 需要幾行
    size = -(-len(text) // n)       # 平均每行字數，避免最後剩一兩個字
    if " " in text:
        # 英文等有空白的語言：以單字為單位組行，不把單字切斷
        pieces, cur = [], ""
        for w in text.split():
            if cur and (len(cur) >= size or len(cur) + 1 + len(w) > max_chars):
                pieces.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}" if cur else w
        pieces.append(cur)
    else:
        pieces = [text[i:i + size] for i in range(0, len(text), size)]
    total = sum(len(p) for p in pieces)
    out, t = [], start
    for p in pieces:
        dur = (end - start) * len(p) / total
        out.append((t, t + dur, p.strip()))
        t += dur
    return out


def make_converter(traditional):
    if not traditional:
        return lambda s: s
    try:
        from opencc import OpenCC
    except ImportError:
        print("提示：未安裝 opencc，無法轉成繁體。執行 pip install opencc-python-reimplemented", file=sys.stderr)
        return lambda s: s
    cc = OpenCC("s2twp")
    return cc.convert


_model_cache = {}


def load_model(name, device="auto"):
    key = (name, device)
    if key not in _model_cache:
        from faster_whisper import WhisperModel
        compute = "int8" if device == "cpu" else "default"
        try:
            _model_cache[key] = WhisperModel(name, device=device, compute_type=compute)
        except Exception:
            # GPU 不可用時退回 CPU
            _model_cache[key] = WhisperModel(name, device="cpu", compute_type="int8")
    return _model_cache[key]


def transcribe_to_srt(path, model_name="small", language=None, traditional=False,
                      max_chars=0, output=None, log=print, progress=None, stop_event=None):
    """轉換單一檔案，回傳輸出的 srt 路徑。"""
    log(f"載入模型 {model_name}（第一次使用會自動下載，請稍候）…")
    model = load_model(model_name)
    convert = make_converter(traditional)

    # 給中文一段繁體提示，能降低 Whisper 輸出簡體的機率
    prompt = "以下是普通話的句子，使用繁體中文。" if traditional and language == "zh" else None

    log(f"開始辨識：{os.path.basename(path)}")
    segments, info = model.transcribe(
        path,
        language=language,
        vad_filter=True,
        initial_prompt=prompt,
    )
    if language is None:
        log(f"偵測到語言：{info.language}（信心 {info.language_probability:.0%}）")

    duration = info.duration or 0
    output = output or os.path.splitext(path)[0] + ".srt"
    index = 1
    with open(output, "w", encoding="utf-8") as f:
        for seg in segments:
            if stop_event is not None and stop_event.is_set():
                log("已取消。")
                break
            text = convert(seg.text)
            for start, end, line in split_long_segment(text, seg.start, seg.end, max_chars):
                if not line:
                    continue
                f.write(f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{line}\n\n")
                index += 1
            log(f"[{format_timestamp(seg.start)}] {text.strip()}")
            if progress and duration:
                progress(min(seg.end / duration, 1.0))
    if progress:
        progress(1.0)
    log(f"完成！已輸出：{output}")
    return output


# ---------------------------------------------------------------- 命令列

def run_cli(argv):
    p = argparse.ArgumentParser(description="語音/影片轉 SRT 字幕（faster-whisper，本機離線）")
    p.add_argument("files", nargs="+", help="音訊或影片檔")
    p.add_argument("-m", "--model", default="small", choices=MODELS, help="模型大小（預設 small）")
    p.add_argument("-l", "--language", default=None, help="語言代碼，如 zh、en、ja；不填則自動偵測")
    p.add_argument("-t", "--traditional", action="store_true", help="中文輸出轉為繁體（台灣用語）")
    p.add_argument("--max-chars", type=int, default=0, help="每行字幕最多字數，0 為不切")
    p.add_argument("-o", "--output", help="輸出檔名（僅限單一輸入檔）")
    args = p.parse_args(argv)
    if args.output and len(args.files) > 1:
        p.error("-o 只能搭配單一輸入檔")
    for path in args.files:
        transcribe_to_srt(path, args.model, args.language, args.traditional,
                          args.max_chars, args.output)


# ---------------------------------------------------------------- 圖形介面

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("語音轉 SRT 字幕")
    root.geometry("720x540")

    files = []
    stop_event = threading.Event()

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)

    # 檔案清單
    top = ttk.Frame(frm)
    top.pack(fill="x")
    ttk.Label(top, text="要轉換的檔案：").pack(side="left")
    listbox = tk.Listbox(frm, height=5)
    listbox.pack(fill="x", pady=4)

    def add_files():
        for p in filedialog.askopenfilenames(title="選擇音訊或影片", filetypes=MEDIA_TYPES):
            if p not in files:
                files.append(p)
                listbox.insert("end", p)

    def clear_files():
        files.clear()
        listbox.delete(0, "end")

    ttk.Button(top, text="新增檔案…", command=add_files).pack(side="right")
    ttk.Button(top, text="清除", command=clear_files).pack(side="right", padx=4)

    # 選項
    opts = ttk.LabelFrame(frm, text="設定", padding=8)
    opts.pack(fill="x", pady=6)

    ttk.Label(opts, text="模型：").grid(row=0, column=0, sticky="w")
    model_var = tk.StringVar(value="small")
    ttk.Combobox(opts, textvariable=model_var, values=MODELS, state="readonly", width=16).grid(row=0, column=1, sticky="w")
    ttk.Label(opts, text="越大越準但越慢；一般電腦建議 small", foreground="gray").grid(row=0, column=2, sticky="w", padx=8)

    ttk.Label(opts, text="語言：").grid(row=1, column=0, sticky="w", pady=4)
    lang_var = tk.StringVar(value="中文")
    ttk.Combobox(opts, textvariable=lang_var, values=list(LANGUAGES), state="readonly", width=16).grid(row=1, column=1, sticky="w")

    trad_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opts, text="中文轉繁體（台灣用語）", variable=trad_var).grid(row=1, column=2, sticky="w", padx=8)

    ttk.Label(opts, text="每行最多字數：").grid(row=2, column=0, sticky="w")
    max_var = tk.IntVar(value=20)
    ttk.Spinbox(opts, from_=0, to=100, textvariable=max_var, width=6).grid(row=2, column=1, sticky="w")
    ttk.Label(opts, text="0 = 不切行", foreground="gray").grid(row=2, column=2, sticky="w", padx=8)

    # 進度與紀錄
    progress = ttk.Progressbar(frm, maximum=1.0)
    progress.pack(fill="x", pady=4)
    log_box = tk.Text(frm, height=12, state="disabled", wrap="word")
    log_box.pack(fill="both", expand=True)

    btns = ttk.Frame(frm)
    btns.pack(fill="x", pady=6)
    start_btn = ttk.Button(btns, text="開始轉換")
    stop_btn = ttk.Button(btns, text="取消", state="disabled")
    start_btn.pack(side="right")
    stop_btn.pack(side="right", padx=4)

    # 背景執行緒透過 after() 更新介面
    def log(msg):
        def _do():
            log_box.configure(state="normal")
            log_box.insert("end", msg + "\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, _do)

    def set_progress(v):
        root.after(0, lambda: progress.configure(value=v))

    def worker(job_files, model, lang, trad, max_chars):
        try:
            for i, path in enumerate(job_files, 1):
                if stop_event.is_set():
                    break
                log(f"── ({i}/{len(job_files)}) ──")
                set_progress(0)
                transcribe_to_srt(path, model, lang, trad, max_chars,
                                  log=log, progress=set_progress, stop_event=stop_event)
            if not stop_event.is_set():
                root.after(0, lambda: messagebox.showinfo("完成", "全部轉換完成！SRT 已存在原檔案旁邊。"))
        except Exception as e:
            log(f"錯誤：{e}")
            root.after(0, lambda: messagebox.showerror("錯誤", str(e)))
        finally:
            root.after(0, lambda: (start_btn.configure(state="normal"), stop_btn.configure(state="disabled")))

    def start():
        if not files:
            messagebox.showwarning("提醒", "請先新增要轉換的檔案")
            return
        try:
            max_chars = int(max_var.get())
        except (tk.TclError, ValueError):
            max_chars = 0
        stop_event.clear()
        start_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        threading.Thread(
            target=worker,
            args=(list(files), model_var.get(), LANGUAGES[lang_var.get()], trad_var.get(), max_chars),
            daemon=True,
        ).start()

    start_btn.configure(command=start)
    stop_btn.configure(command=stop_event.set)

    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_cli(sys.argv[1:])
    else:
        run_gui()
