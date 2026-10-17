# 語音轉 SRT 字幕

在自己電腦上把音訊／影片（mp3、wav、m4a、mp4、mkv…）轉成 `.srt` 字幕檔。
使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)（OpenAI Whisper 的加速版），**完全離線辨識**，檔案不會上傳。

## 安裝與啟動

1. 安裝 [Python 3.11 以上](https://www.python.org/downloads/)（Windows 安裝第一個畫面記得勾選 **Add python.exe to PATH**）。
2. 下載程式：打開 <https://github.com/mark526kimo/line-images/archive/refs/heads/claude/voice-to-srt-program-t7aeg7.zip>，解壓縮後進入 `voice-to-srt` 資料夾。
3. 啟動：
   - **Windows**：雙擊 `start_windows.bat`
   - **macOS / Linux**：在終端機執行 `./start_mac_linux.sh`

第一次啟動會自動安裝套件；第一次轉換時會下載模型（small 約 500 MB），之後就不用再下載。

> Linux 若出現 `No module named tkinter`，請先安裝：`sudo apt install python3-tk`

## 使用方式（視窗）

1. 按「新增檔案…」選擇一個或多個音訊／影片
2. 選模型、語言，是否轉繁體、每行最多字數
3. 按「開始轉換」，完成後 `.srt` 會存在原檔案旁邊（同檔名）

## 使用方式（命令列）

```bash
python voice_to_srt.py 會議錄音.m4a -l zh -t            # 中文、轉繁體
python voice_to_srt.py video.mp4 -m medium --max-chars 20
python voice_to_srt.py a.mp3 b.mp3 c.mp3                # 一次轉多個
```

| 參數 | 說明 |
|---|---|
| `-m` | 模型：`tiny` `base` `small`（預設）`medium` `large-v3` `large-v3-turbo` |
| `-l` | 語言代碼（`zh`、`en`、`ja`…），不填自動偵測 |
| `-t` | 中文輸出轉繁體（台灣用語） |
| `-g` | 專業詞庫：`biology`、`chemistry`，多個用逗號分隔，例如 `-g biology,chemistry` |
| `-k` | 關鍵字（人名、專有名詞），逗號分隔 |
| `-c` | 自動更正清單檔（每行「錯字=正確」） |
| `--max-chars` | 每行字幕最多字數，過長會自動拆行並分配時間 |
| `--ai` | 辨識完用 Gemini AI 校稿（金鑰放環境變數 `GEMINI_API_KEY`） |
| `--ai-model` | Gemini 模型，預設 `gemini-3.5-flash-lite` |
| `--topic` | 課程主題，給 AI 校稿參考 |
| `-o` | 指定輸出檔名（單一檔案時） |

## 提高準確度

1. **用大一點的模型**：影響最大。電腦版要更準請選 `large-v3-turbo`（有 NVIDIA 顯示卡會很快；只有 CPU 也能跑，但比較慢）。
2. **專業詞庫**：目前有「生物學」（約 360 個名詞、150 組大陸→台灣用語對照）和「化學」（約 180 個名詞、80 組對照），可以同時勾選。勾選後會
   - **讀音比對校正**：讀音和詞庫名詞相同的錯字自動換回來，例如 林酸基、淋酸雞→磷酸基；磷酸跟、鄰酸根→磷酸根；光和作用→光合作用（網頁版、電腦版都有，3 個字以上的詞）
   - 有開 AI 校稿時，詞庫名詞會交給 AI 當參考
   - 自動把大陸用語換成台灣課本用語，例如 線粒體→粒線體、氨基酸→胺基酸、等位基因→對偶基因、自然選擇→天擇（電腦版、手機版都有）
3. **關鍵字**：填老師名字、這堂課的特殊名詞，用逗號分隔。會用在讀音比對和 AI 校稿；電腦版另外會把前幾個（約 40 字內）提示給模型。
4. **自動更正**：發現某個詞一直被聽錯，就加一行「錯字=正確」，以後都會自動改。
5. **AI 校稿（Gemini，網頁版、電腦版都有）**：辨識完把逐字稿交給 Google Gemini，依上下文修正聽錯的字和專有名詞。
   - 到 Google AI Studio 申請自己的 Gemini API 金鑰
   - 網頁版：展開「AI 校稿（Gemini）」貼上金鑰；金鑰只存在你裝置的瀏覽器
   - 電腦版：勾選「AI 校稿」並貼上金鑰；金鑰存在這台電腦的 `~/.voice_to_srt.json`。命令列用 `--ai`（金鑰放環境變數 `GEMINI_API_KEY`）
   - 模型預設 gemini-3.5-flash-lite，也可選 3.6 flash、3.6 flash lite、3.8 flash；選的模型不能用或一直忙碌（503）時，會等一下重試，再依這個順序自動換
   - 金鑰不會上傳到 GitHub；**不要把金鑰寫進程式資料夾裡的檔案或貼在公開的地方**
   - 只傳文字、不傳錄音；改了哪些地方會列出來，不滿意可以按「復原 AI 校稿」
   - AI 改動太大（長度差很多）的行會自動保留原文，避免 AI 亂加內容
6. **自動過濾幻覺**：Whisper 在靜音時常亂編「請不吝點贊訂閱…」或一直重複同一句，程式會自動略過。

### 自己做詞庫

詞庫在 `glossaries/` 資料夾，是一般文字檔，打開 `biology.txt` 就能看到格式：

```
粒線體              ← 沒有「=」：關鍵字
线粒体=粒線體       ← 有「=」：自動更正
```

可以直接往 `biology.txt` 加詞，或另存一份（例如 `physics.txt`）當新的詞庫，電腦版會自動出現在選單裡。
命令列用 `-g biology` 或 `-g 詞庫檔路徑`：

```bash
python voice_to_srt.py 生物課.m4a -l zh -t -g biology,chemistry -k "陳老師, 草履蟲"
```

## 模型怎麼選

| 模型 | 速度 | 準確度 | 建議 |
|---|---|---|---|
| tiny / base | 很快 | 普通 | 先試試看 |
| small | 快 | 不錯 | 一般電腦首選 |
| medium | 慢 | 好 | 對準確度有要求 |
| large-v3-turbo | 中 | 很好 | 有 NVIDIA 顯示卡推薦 |
| large-v3 | 最慢 | 最好 | 有 NVIDIA 顯示卡 |

有 NVIDIA 顯示卡且裝好 CUDA 會自動用 GPU 加速，否則用 CPU。

## 手機版（`mobile/` 資料夾）

不用安裝 App，用手機瀏覽器打開就能用，一樣在手機上離線辨識，檔案不會上傳。

- 可以選手機裡的音訊／影片，或直接按「錄音」
- 結果可以直接修改，再「下載 SRT」或「分享」到 LINE、雲端硬碟等
- 在瀏覽器選「加到主畫面」就會像 App 一樣出現在桌面
- 支援 iPhone（Safari）與 Android（Chrome），新手機會自動用 GPU 加速

### 怎麼放到網路上給手機開

手機版必須透過 **https 網址**開啟（麥克風和離線功能需要）。最簡單是用 GitHub Pages：

1. 到 GitHub 儲存庫的 **Settings → Pages**
2. Source 選 **Deploy from a branch**，選分支和根目錄 `/ (root)`，按 Save
3. 等一兩分鐘，手機開 `https://<你的帳號>.github.io/<儲存庫名稱>/voice-to-srt/mobile/`

> 手機效能有限：建議用 tiny 或 base 模型；一小時的錄音可能要轉十幾分鐘以上，轉換時請保持畫面開著。
