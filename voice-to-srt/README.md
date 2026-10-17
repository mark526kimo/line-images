# 語音轉 SRT 字幕

在自己電腦上把音訊／影片（mp3、wav、m4a、mp4、mkv…）轉成 `.srt` 字幕檔。
使用 [faster-whisper](https://github.com/SYSTRAN/faster-whisper)（OpenAI Whisper 的加速版），**完全離線辨識**，檔案不會上傳。

## 安裝與啟動

1. 安裝 [Python 3.9 以上](https://www.python.org/downloads/)（Windows 安裝時記得勾選 **Add Python to PATH**）。
2. 下載這個資料夾。
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
| `--max-chars` | 每行字幕最多字數，過長會自動拆行並分配時間 |
| `-o` | 指定輸出檔名（單一檔案時） |

## 模型怎麼選

| 模型 | 速度 | 準確度 | 建議 |
|---|---|---|---|
| tiny / base | 很快 | 普通 | 先試試看 |
| small | 快 | 不錯 | 一般電腦首選 |
| medium | 慢 | 好 | 對準確度有要求 |
| large-v3-turbo | 中 | 很好 | 有 NVIDIA 顯示卡推薦 |
| large-v3 | 最慢 | 最好 | 有 NVIDIA 顯示卡 |

有 NVIDIA 顯示卡且裝好 CUDA 會自動用 GPU 加速，否則用 CPU。
