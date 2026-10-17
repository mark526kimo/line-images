#!/usr/bin/env python3
"""語音轉 SRT 字幕工具。

使用 faster-whisper 在本機離線辨識語音，輸出 .srt 字幕檔。
不帶參數執行會開啟圖形介面；帶檔案路徑則以命令列模式執行：

    python voice_to_srt.py                       # 開啟視窗
    python voice_to_srt.py audio.mp3             # 命令列轉換
    python voice_to_srt.py a.mp3 b.mp4 -m small -l zh --traditional
"""

import argparse
import json
import os
import re
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
# 明顯不是音訊／影片的副檔名，選到時直接擋下
NOT_MEDIA = {".txt", ".srt", ".vtt", ".json", ".md", ".csv", ".doc", ".docx", ".pdf", ".ppt", ".pptx",
             ".xls", ".xlsx", ".py", ".bat", ".sh", ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif"}


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


def parse_corrections(text):
    """解析「錯字=正確」的更正清單（一行一組），回傳 [(錯, 對), ...]。"""
    pairs = []
    for line in (text or "").splitlines():
        if "=" in line:
            wrong, right = line.split("=", 1)
            if wrong.strip():
                pairs.append((wrong.strip(), right.strip()))
    return pairs


GLOSSARY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glossaries")
GLOSSARY_NAMES = {"biology": "生物學", "chemistry": "化學"}
# 給 Whisper 的提示只放自己填的少數關鍵字。提示太長（例如塞進整個詞庫）會讓 Whisper
# 把一整段 30 秒壓縮成幾個字直接跳過，詞庫只用在讀音比對、自動更正和 AI 校稿。
MAX_PROMPT_KEYWORD_CHARS = 40


def list_glossaries():
    """回傳 {顯示名稱: 檔案路徑}。"""
    out = {}
    if os.path.isdir(GLOSSARY_DIR):
        for name in sorted(os.listdir(GLOSSARY_DIR)):
            if name.endswith(".txt"):
                stem = name[:-4]
                out[GLOSSARY_NAMES.get(stem, stem)] = os.path.join(GLOSSARY_DIR, name)
    return out


def read_glossary(path):
    """讀取詞庫檔，回傳 (關鍵字清單, 更正文字)。"""
    keywords, corrections = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            (corrections if "=" in line else keywords).append(line)
    return keywords, "\n".join(corrections)


def replace_safe(text, wrong, right):
    """替換錯字；如果這個位置本來就是正確寫法的一部分（例如「磷脂=磷脂質」遇到「磷脂質」）就不換。"""
    k = right.find(wrong)
    out, i = [], 0
    while True:
        j = text.find(wrong, i)
        if j < 0:
            out.append(text[i:])
            return "".join(out)
        if k >= 0 and j - k >= 0 and text.startswith(right, j - k):
            out.append(text[i:j + len(wrong)])
        else:
            out.append(text[i:j] + right)
        i = j + len(wrong)


def apply_corrections(text, pairs):
    for wrong, right in pairs:
        text = replace_safe(text, wrong, right)
    return text


# ---------- 讀音比對校正 ----------
# 把辨識結果裡和關鍵字「讀音相同」的字串換成關鍵字，例如 林酸基→磷酸基、磷酸跟→磷酸根。
# 3 個字的詞要聲調也相同；4 個字以上不比聲調。差異只是詞頭或詞尾的「和、的、是…」這類虛字時不換，
# 避免「細胞和組織」被改成「細胞核組織」。
FUNCTION_CHARS = set("和與与及的了是在也就都有或而呢嗎吗吧啊")


def _is_cjk(c):
    return "\u3400" <= c <= "\u9fff"


def _pinyin(text):
    try:
        from pypinyin import lazy_pinyin, Style
    except ImportError:
        return None
    py = lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True, errors=lambda s: list(s))
    return py if len(py) == len(text) else None


def phonetic_terms(keywords):
    terms = []
    for term in dict.fromkeys(keywords):
        if len(term) < 3 or not all(_is_cjk(c) for c in term):
            continue
        py = _pinyin(term)
        if not py:
            continue
        toned = len(term) == 3
        terms.append((term, [p if toned else p.rstrip("012345") for p in py], toned))
    return sorted(terms, key=lambda t: -len(t[0]))


def phonetic_fix(text, terms):
    if not terms:
        return text
    py = _pinyin(text)
    if not py:
        return text
    used = [False] * len(text)
    repl = []
    for term, key, toned in terms:
        n = len(term)
        for i in range(len(text) - n + 1):
            diffs = []
            for k in range(n):
                c = text[i + k]
                p = py[i + k] if toned else py[i + k].rstrip("012345")
                if used[i + k] or not _is_cjk(c) or p != key[k]:
                    break
                if c != term[k]:
                    diffs.append((k, c))
            else:
                for k in range(n):
                    used[i + k] = True  # 已經正確的詞也佔住位置，避免被別的詞改掉
                if diffs and not all(c in FUNCTION_CHARS and k in (0, n - 1) for k, c in diffs):
                    repl.append((i, term))
    chars = list(text)
    for i, term in sorted(repl, reverse=True):
        chars[i:i + len(term)] = list(term)
    return "".join(chars)


# Whisper 在靜音或雜音時常編出的固定句子
HALLUCINATION_RE = re.compile(r"明鏡與點點|明镜与点点|Amara\.org|字幕志願者|字幕志愿者|請不吝點贊|请不吝点赞|訂閱.*轉發.*打賞|订阅.*转发.*打赏")


def is_hallucination(seg, prev_text):
    """過濾 Whisper 常見的幻覺：靜音時亂編的句子、卡住一直重複同一句。"""
    if HALLUCINATION_RE.search(seg.text):
        return True
    if seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0:
        return True
    if seg.compression_ratio > 2.4:  # 同一段文字裡大量重複
        return True
    return bool(prev_text) and seg.text.strip() == prev_text


def decode_audio(path, sampling_rate=16000):
    """把音訊／影片解碼成 16kHz 單聲道 float32。

    faster-whisper 內建的解碼用了新版 PyAV 已移除的參數（metadata_errors），
    所以自己解碼，再把聲音資料交給模型。
    """
    import gc
    import io
    import av
    import numpy as np
    from faster_whisper.audio import _group_frames, _ignore_invalid_frames, _resample_frames

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    raw = io.BytesIO()
    try:
        container = av.open(path, mode="r")
    except Exception as e:
        raise RuntimeError(f"無法開啟這個檔案：{os.path.basename(path)}（{e}）") from e
    with container:
        if not container.streams.audio:
            raise RuntimeError(f"這個檔案沒有聲音：{os.path.basename(path)}")
        frames = _ignore_invalid_frames(container.decode(audio=0))
        frames = _resample_frames(_group_frames(frames, 500000), resampler)
        for frame in frames:
            raw.write(frame.to_ndarray())
    del resampler
    gc.collect()
    return np.frombuffer(raw.getbuffer(), dtype=np.int16).astype(np.float32) / 32768.0


# ---------- AI 校稿（Gemini） ----------
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
AI_MODELS = ["gemini-3.6-flash", "gemini-3.6-flash-lite", "gemini-3.8-flash"]  # 預設選項，依序優先
AI_BATCH = 60    # 每次送出的字幕段數
AI_CONTEXT = 5   # 附上前一批最後幾段當上下文
AI_RETRY_CODES = {429, 500, 502, 503, 504}  # 忙碌、暫時性錯誤：等一下重試
AI_RETRY_WAITS = (3, 8, 20)                  # 每次重試前等幾秒


class GeminiError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def gemini_request(key, path, body=None):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{GEMINI_URL}/{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
        except ValueError:
            msg = e.reason
        if e.code == 400 and "API key" in msg:
            raise GeminiError("Gemini API 金鑰無效，請檢查是否貼對。", e.code) from None
        if e.code == 403:
            raise GeminiError(f"這組金鑰沒有權限使用 Gemini API：{msg}", e.code) from None
        if e.code == 404:
            raise GeminiError(f"找不到 Gemini 模型：{msg}", e.code) from None
        if e.code == 429:
            raise GeminiError("Gemini 用量超過限制（免費額度或每分鐘上限），請稍後再試。", e.code) from None
        if e.code == 503:
            raise GeminiError("Gemini 模型目前太忙（Google 端暫時性問題），請稍後再試。", e.code) from None
        raise GeminiError(f"Gemini 錯誤 {e.code}：{msg}", e.code) from None
    except urllib.error.URLError as e:
        raise GeminiError(f"連不上 Gemini，請檢查網路：{e.reason}") from None
    except TimeoutError:
        raise GeminiError("Gemini 回應逾時", 504) from None


def gemini_generate(key, models, body, log=print, sleep=None):
    """送出校稿請求。忙碌時等一下重試；一直忙就換 models 裡的下一個模型（會直接修改 models，後面的批次沿用）。"""
    import time
    sleep = sleep or time.sleep
    while True:
        model = models[0]
        last = None
        for attempt in range(len(AI_RETRY_WAITS) + 1):
            try:
                return gemini_request(key, f"models/{model}:generateContent", body)
            except GeminiError as e:
                if e.code not in AI_RETRY_CODES:
                    raise
                last = e
                if attempt < len(AI_RETRY_WAITS):
                    wait = AI_RETRY_WAITS[attempt]
                    log(f"Gemini 忙碌（{e.code}），{wait} 秒後重試…")
                    sleep(wait)
        if len(models) > 1:
            models.pop(0)
            log(f"{model} 一直忙碌，改用 {models[0]}。")
        else:
            raise last


def resolve_ai_models(key, wanted, log=print):
    """回傳要依序嘗試的模型清單：先用選的模型，忙碌時再依 AI_MODELS 順序換。選的模型不能用就直接跳過。"""
    data = gemini_request(key, "models?pageSize=200")
    names = [m["name"].split("/", 1)[-1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    names = [n for n in names if n.startswith("gemini")]
    if not names:
        return [wanted]
    preferred = [next((n for n in names if n == m or n.startswith(m + "-")), None) for m in AI_MODELS]
    preferred = [n for n in preferred if n]
    if not preferred:
        preferred = [next((n for n in names if "flash" in n and "lite" not in n), names[0])]
    if wanted in names:
        return [wanted] + [n for n in preferred if n != wanted]
    log(f"模型 {wanted} 不能用，改用 {preferred[0]}。")
    return preferred


def ai_prompt(topic, keywords):
    lines = [
        "你是台灣的逐字稿校對員。輸入是語音辨識產生的上課字幕，每行有編號，可能有同音錯字、聽錯的專有名詞、簡體字或中國大陸用語。",
        "請逐行修正：",
        "1. 依上下文把聽錯的字、詞改成正確的，特別是生物、化學等專有名詞。",
        "2. 一律使用台灣繁體中文與台灣課本用語（例如 粒線體、胺基酸、對偶基因、莫耳）。",
        "3. 只改錯的地方，不要改寫語氣、不要潤飾、不要增刪內容、不要加標點以外的東西；口語贅字照留。",
        "4. 不確定的地方保持原樣。英文術語（DNA、ATP 等）保持英文。",
        "5. 行數與編號必須和輸入完全一樣，不可合併或拆開。「上下文」只是參考，不要輸出。",
    ]
    if topic:
        lines.append(f"課程主題：{topic}")
    if keywords:
        lines.append("可能出現的專有名詞：" + "、".join(keywords))
    return "\n".join(lines)


def accept_ai_line(orig, fixed):
    """防止 AI 改太多：長度差太多或變空白就保留原文。"""
    return bool(fixed) and len(orig) * 0.5 <= len(fixed) <= len(orig) * 1.6 + 4


def ai_proofread(texts, key, models, topic="", keywords=(), log=print, postprocess=lambda t: t):
    """回傳 (校稿後的文字清單, 修改清單 [(序號, 原文, 新文)])。失敗時拋出例外，已完成的批次寫在 e.partial。"""
    system = ai_prompt(topic, list(keywords))
    out = list(texts)
    changes = []
    total = (len(texts) + AI_BATCH - 1) // AI_BATCH
    try:
        for b in range(total):
            log(f"AI 校稿中… {b + 1} / {total}")
            lo, hi = b * AI_BATCH, min((b + 1) * AI_BATCH, len(texts))
            context = texts[max(0, lo - AI_CONTEXT):lo]
            prompt = ""
            if context:
                prompt += "上下文（不用輸出）：\n" + "\n".join(context) + "\n\n"
            prompt += "請校對以下字幕（格式：編號|文字）：\n" + "\n".join(f"{i + 1}|{texts[i]}" for i in range(lo, hi))
            data = gemini_generate(key, models, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                    "responseSchema": {
                        "type": "ARRAY",
                        "items": {"type": "OBJECT",
                                  "properties": {"id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
                                  "required": ["id", "text"]},
                    },
                },
            }, log)
            cands = data.get("candidates") or []
            if not cands:
                raise RuntimeError("Gemini 沒有回傳結果")
            raw = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []) if not p.get("thought"))
            try:
                rows = json.loads(raw)
            except ValueError:
                raise RuntimeError("Gemini 回傳的格式看不懂，請再試一次。") from None
            fixed = {r["id"]: r["text"].strip() for r in rows if isinstance(r, dict)
                     and isinstance(r.get("id"), int) and isinstance(r.get("text"), str)}
            for i in range(lo, hi):
                t = fixed.get(i + 1)
                if t is None or not accept_ai_line(texts[i], t):
                    continue
                t = postprocess(t)
                if t != texts[i]:
                    out[i] = t
                    changes.append((i + 1, texts[i], t))
    except RuntimeError as e:
        e.partial = (out, changes)
        raise
    return out, changes


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
                      max_chars=0, output=None, log=print, progress=None, stop_event=None,
                      keywords="", corrections="", glossary=None, phonetic=True, ai=None):
    """轉換單一檔案，回傳輸出的 srt 路徑。

    ai：要 AI 校稿時傳 {"key": 金鑰, "model": 模型, "topic": 課程主題}。
    """
    if os.path.splitext(path)[1].lower() in NOT_MEDIA:
        raise RuntimeError(f"這不是音訊或影片檔：{os.path.basename(path)}")
    log(f"讀取聲音：{os.path.basename(path)}")
    audio = decode_audio(path)
    if audio.size == 0:
        raise RuntimeError(f"讀不到聲音：{os.path.basename(path)}")

    log(f"載入模型 {model_name}（第一次使用會自動下載，請稍候）…")
    model = load_model(model_name)
    convert = make_converter(traditional)

    keyword_list = [k.strip() for k in re.split(r"[,，、\n]", keywords or "") if k.strip()]
    own_keywords = list(keyword_list)
    if isinstance(glossary, str):
        glossary = [glossary]
    for g_path in glossary or []:
        g_keywords, g_corrections = read_glossary(g_path)
        keyword_list += [k for k in g_keywords if k not in keyword_list]
        corrections = g_corrections + "\n" + (corrections or "")
        log(f"使用詞庫：{os.path.basename(g_path)}")

    # 長的錯字先換，避免「淋巴細胞」被「細胞」之類的短規則先換掉
    pairs = sorted(parse_corrections(corrections), key=lambda p: -len(p[0]))
    # 繁簡轉換可能把「台」變「臺」等，錯字也轉一份，才對得上轉換後的文字
    pairs += [(convert(w), r) for w, r in pairs if convert(w) != w]
    pairs.sort(key=lambda p: -len(p[0]))

    terms = phonetic_terms(keyword_list) if phonetic else []
    if phonetic and keyword_list and not terms and _pinyin("測") is None:
        log("提示：未安裝 pypinyin，略過讀音比對校正。執行 pip install pypinyin")

    # 提示詞：只放自己填的少數關鍵字（見 MAX_PROMPT_KEYWORD_CHARS 的說明）；繁體提示能降低輸出簡體的機率
    used, total = [], 0
    for k in own_keywords:
        if total + len(k) + 1 > MAX_PROMPT_KEYWORD_CHARS:
            break
        used.append(k)
        total += len(k) + 1
    keywords = "、".join(used)
    prompt_parts = []
    if keywords:
        prompt_parts.append(f"相關詞彙：{keywords}。")
    if traditional and language == "zh":
        prompt_parts.append("以下是普通話的句子，使用繁體中文。")
    prompt = "".join(prompt_parts) or None

    log(f"開始辨識：{os.path.basename(path)}")
    segments, info = model.transcribe(
        audio,
        language=language,
        vad_filter=True,
        initial_prompt=prompt,
        beam_size=5,
        # 每段獨立辨識（和網頁版一樣），避免前一段的錯誤或重複一路影響下去
        condition_on_previous_text=False,
    )
    if language is None:
        log(f"偵測到語言：{info.language}（信心 {info.language_probability:.0%}）")

    duration = info.duration or 0
    output = output or os.path.splitext(path)[0] + ".srt"
    prev_text = ""
    skipped = 0
    cancelled = False
    items = []  # [開始, 結束, 文字]
    for seg in segments:
        if stop_event is not None and stop_event.is_set():
            log("已取消。")
            cancelled = True
            break
        if is_hallucination(seg, prev_text):
            skipped += 1
            continue
        prev_text = seg.text.strip()
        text = phonetic_fix(apply_corrections(convert(seg.text), pairs), terms).strip()
        if text:
            items.append([seg.start, seg.end, text])
        log(f"[{format_timestamp(seg.start)}] {text}")
        if progress and duration:
            progress(min(seg.end / duration, 1.0))
    if progress:
        progress(1.0)
    if skipped:
        log(f"已略過 {skipped} 段疑似幻覺／重複的內容。")

    if ai and ai.get("key") and items and not cancelled:
        texts = [t for _, _, t in items]
        postprocess = lambda t: phonetic_fix(apply_corrections(convert(t), pairs), terms)
        try:
            models = resolve_ai_models(ai["key"], ai.get("model") or AI_MODELS[0], log)
            new, changes = ai_proofread(texts, ai["key"], models, ai.get("topic", ""), keyword_list, log, postprocess)
        except RuntimeError as e:
            new, changes = getattr(e, "partial", (texts, []))
            msg = str(e).rstrip("。")
            log(f"AI 校稿失敗：{msg}" + (f"（已修改 {len(changes)} 段）" if changes else "，輸出未校稿的版本。"))
        else:
            log(f"AI 校稿完成，修改了 {len(changes)} 段。" if changes else "AI 校稿完成，沒有需要修改的地方。")
        for n, old, fixed in changes:
            log(f"  {old}\n  → {fixed}")
        for item, t in zip(items, new):
            item[2] = t

    index = 1
    with open(output, "w", encoding="utf-8") as f:
        for start, end, text in items:
            for a, b, line in split_long_segment(text, start, end, max_chars):
                if not line:
                    continue
                f.write(f"{index}\n{format_timestamp(a)} --> {format_timestamp(b)}\n{line}\n\n")
                index += 1
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
    p.add_argument("-k", "--keywords", default="", help="人名、專有名詞等關鍵字，用逗號分隔，可提高這些詞的準確度")
    p.add_argument("-g", "--glossary", help="專業詞庫：biology（生物學）、chemistry（化學）或詞庫檔路徑，多個用逗號分隔")
    p.add_argument("-c", "--corrections", help="自動更正清單檔，每行一組「錯字=正確」")
    p.add_argument("--ai", action="store_true", help="辨識完用 Gemini AI 校稿（金鑰放在環境變數 GEMINI_API_KEY，或先在視窗版設定）")
    p.add_argument("--ai-model", default=None, help=f"Gemini 模型（預設 {AI_MODELS[0]}）")
    p.add_argument("--topic", default="", help="課程主題，給 AI 校稿參考")
    p.add_argument("-o", "--output", help="輸出檔名（僅限單一輸入檔）")
    args = p.parse_args(argv)
    ai = None
    if args.ai:
        settings = load_settings()
        key = os.environ.get("GEMINI_API_KEY") or settings.get("ai_key", "")
        if not key:
            p.error("要用 --ai 請先設定環境變數 GEMINI_API_KEY，或在視窗版填入金鑰")
        ai = {"key": key, "model": args.ai_model or settings.get("ai_model") or AI_MODELS[0], "topic": args.topic}
    if args.output and len(args.files) > 1:
        p.error("-o 只能搭配單一輸入檔")
    corrections = ""
    if args.corrections:
        with open(args.corrections, encoding="utf-8") as f:
            corrections = f.read()
    glossary = []
    for g in filter(None, (args.glossary or "").split(",")):
        g = g.strip()
        path = g if os.path.isfile(g) else os.path.join(GLOSSARY_DIR, g + ".txt")
        if not os.path.isfile(path):
            p.error(f"找不到詞庫：{g}")
        glossary.append(path)
    for path in args.files:
        transcribe_to_srt(path, args.model, args.language, args.traditional,
                          args.max_chars, args.output,
                          keywords=args.keywords, corrections=corrections, glossary=glossary, ai=ai)


# ---------------------------------------------------------------- 圖形介面

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".voice_to_srt.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("語音轉 SRT 字幕")
    root.geometry("780x860")

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
        skipped = []
        for p in filedialog.askopenfilenames(title="選擇音訊或影片", filetypes=MEDIA_TYPES):
            if os.path.splitext(p)[1].lower() in NOT_MEDIA:
                skipped.append(os.path.basename(p))
                continue
            if p not in files:
                files.append(p)
                listbox.insert("end", p)
        if skipped:
            messagebox.showwarning("略過", "這些不是音訊或影片檔，已略過：\n" + "\n".join(skipped)
                                   + "\n\n詞庫請用下方「專業詞庫」勾選，不用加到這裡。")

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
    ttk.Label(opts, text="越大越準但越慢；要更準請選 large-v3-turbo", foreground="gray").grid(row=0, column=2, sticky="w", padx=8)

    ttk.Label(opts, text="語言：").grid(row=1, column=0, sticky="w", pady=4)
    lang_var = tk.StringVar(value="中文")
    ttk.Combobox(opts, textvariable=lang_var, values=list(LANGUAGES), state="readonly", width=16).grid(row=1, column=1, sticky="w")

    trad_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opts, text="中文轉繁體（台灣用語）", variable=trad_var).grid(row=1, column=2, sticky="w", padx=8)

    ttk.Label(opts, text="每行最多字數：").grid(row=2, column=0, sticky="w")
    max_var = tk.IntVar(value=20)
    ttk.Spinbox(opts, from_=0, to=100, textvariable=max_var, width=6).grid(row=2, column=1, sticky="w")
    ttk.Label(opts, text="0 = 不切行", foreground="gray").grid(row=2, column=2, sticky="w", padx=8)

    settings = load_settings()
    glossaries = list_glossaries()
    saved = settings.get("glossary", ["生物學"])
    if isinstance(saved, str):  # 舊版設定只存一個
        saved = [saved]
    ttk.Label(opts, text="專業詞庫：").grid(row=7, column=0, sticky="w", pady=4)
    gl_row = ttk.Frame(opts)
    gl_row.grid(row=7, column=1, columnspan=2, sticky="w")
    gl_vars = {}
    gl_checks = ttk.Frame(gl_row)
    gl_checks.pack(side="left")

    def add_glossary_check(name, checked):
        gl_vars[name] = tk.BooleanVar(value=checked)
        ttk.Checkbutton(gl_checks, text=name, variable=gl_vars[name]).pack(side="left", padx=(0, 6))

    for name in glossaries:
        add_glossary_check(name, name in saved)

    def load_glossary_file():
        p = filedialog.askopenfilename(title="選擇詞庫檔", filetypes=[("詞庫", "*.txt")])
        if p:
            name = os.path.splitext(os.path.basename(p))[0]
            if name in gl_vars:
                gl_vars[name].set(True)
            else:
                add_glossary_check(name, True)
            glossaries[name] = p

    def open_glossary_dir():
        os.makedirs(GLOSSARY_DIR, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(GLOSSARY_DIR)
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", GLOSSARY_DIR])

    ttk.Button(gl_row, text="載入其他詞庫…", command=load_glossary_file).pack(side="left", padx=4)
    ttk.Button(gl_row, text="開啟詞庫資料夾", command=open_glossary_dir).pack(side="left")

    ttk.Label(opts, text="關鍵字：").grid(row=3, column=0, sticky="w", pady=4)
    kw_var = tk.StringVar(value=settings.get("keywords", ""))
    ttk.Entry(opts, textvariable=kw_var).grid(row=3, column=1, columnspan=2, sticky="we")
    ttk.Label(opts, text="人名、公司名、專有名詞，用逗號分隔，例如：王小明, 台積電, ChatGPT",
              foreground="gray").grid(row=4, column=1, columnspan=2, sticky="w")

    ttk.Label(opts, text="自動更正：").grid(row=5, column=0, sticky="nw", pady=4)
    corr_box = tk.Text(opts, height=3, width=40)
    corr_box.grid(row=5, column=1, columnspan=2, sticky="we", pady=4)
    corr_box.insert("1.0", settings.get("corrections", ""))
    ttk.Label(opts, text="每行一組「錯字=正確」，例如：台機電=台積電；轉完會自動替換",
              foreground="gray").grid(row=6, column=1, columnspan=2, sticky="w")
    opts.columnconfigure(2, weight=1)

    # AI 校稿
    ai_frame = ttk.LabelFrame(frm, text="AI 校稿（Gemini）", padding=8)
    ai_frame.pack(fill="x", pady=(0, 6))
    ai_on_var = tk.BooleanVar(value=settings.get("ai_on", False))
    ttk.Checkbutton(ai_frame, text="辨識完用 Gemini 依上下文修正錯字（需要網路，只傳文字不傳錄音）",
                    variable=ai_on_var).grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(ai_frame, text="API 金鑰：").grid(row=1, column=0, sticky="w", pady=4)
    ai_key_var = tk.StringVar(value=settings.get("ai_key", ""))
    ttk.Entry(ai_frame, textvariable=ai_key_var, show="*", width=44).grid(row=1, column=1, sticky="we")
    ttk.Label(ai_frame, text="模型：").grid(row=2, column=0, sticky="w")
    ai_model_var = tk.StringVar(value=settings.get("ai_model", AI_MODELS[0]))
    ttk.Combobox(ai_frame, textvariable=ai_model_var, values=AI_MODELS, width=24).grid(row=2, column=1, sticky="w")
    ttk.Label(ai_frame, text="課程主題：").grid(row=3, column=0, sticky="w", pady=4)
    ai_topic_var = tk.StringVar(value=settings.get("ai_topic", ""))
    ttk.Entry(ai_frame, textvariable=ai_topic_var).grid(row=3, column=1, columnspan=2, sticky="we")
    ttk.Label(ai_frame, text="金鑰到 Google AI Studio 申請；只存在這台電腦，不要分享給別人。",
              foreground="gray").grid(row=4, column=0, columnspan=3, sticky="w")
    ai_frame.columnconfigure(1, weight=1)

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

    def worker(job_files, model, lang, trad, max_chars, keywords, corrections, glossary, ai):
        failed = []
        try:
            for i, path in enumerate(job_files, 1):
                if stop_event.is_set():
                    break
                log(f"── ({i}/{len(job_files)}) ──")
                set_progress(0)
                try:
                    transcribe_to_srt(path, model, lang, trad, max_chars,
                                      log=log, progress=set_progress, stop_event=stop_event,
                                      keywords=keywords, corrections=corrections, glossary=glossary, ai=ai)
                except Exception as e:  # 一個檔案失敗，繼續轉下一個
                    log(f"錯誤：{e}")
                    failed.append(f"{os.path.basename(path)}：{e}")
            if stop_event.is_set():
                pass
            elif failed:
                msg = "有些檔案沒有轉成功：\n\n" + "\n".join(failed)
                root.after(0, lambda: messagebox.showwarning("部分失敗", msg))
            else:
                root.after(0, lambda: messagebox.showinfo("完成", "全部轉換完成！SRT 已存在原檔案旁邊。"))
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
        keywords = kw_var.get()
        corrections = corr_box.get("1.0", "end").strip()
        chosen = [name for name, var in gl_vars.items() if var.get()]
        ai_key = ai_key_var.get().strip()
        if ai_on_var.get() and not ai_key:
            messagebox.showwarning("提醒", "要用 AI 校稿，請先填入 Gemini API 金鑰，或取消勾選 AI 校稿。")
            return
        ai = {"key": ai_key, "model": ai_model_var.get().strip(), "topic": ai_topic_var.get().strip()} if ai_on_var.get() else None
        save_settings({"keywords": keywords, "corrections": corrections, "glossary": chosen,
                       "ai_on": ai_on_var.get(), "ai_key": ai_key,
                       "ai_model": ai_model_var.get().strip(), "ai_topic": ai_topic_var.get().strip()})
        stop_event.clear()
        start_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        threading.Thread(
            target=worker,
            args=(list(files), model_var.get(), LANGUAGES[lang_var.get()], trad_var.get(), max_chars,
                  keywords, corrections, [glossaries[n] for n in chosen], ai),
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
