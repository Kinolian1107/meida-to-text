# 本地影片/音訊多模態分析系統：Agent 實作藍圖

*Generated: 2026-08-08 | Sources: 28+ | Confidence: High（硬體與既有 ASR 已實測；YouTube PO Token / Qwen3-VL GGUF 細節為 Medium）*

> 用途：交付給 Cursor / Claude Code 直接開工。  
> 對應規格：使用者提供的執行計畫 v1.2。  
> 本文件在 v1.2 之上**鎖定技術決策、目錄結構、建置順序、介面契約與已知坑**。

---

## Executive Summary

計畫整體可行，且與既有環境高度契合：`asr-kino` 已具備 WhisperX `large-v3-turbo` + int8 + hotwords/topic/diarization/OpenCC；硬體為 RTX 5070 Ti 16GB。關鍵修正有四：

1. **GPU 必須序列化**：WhisperX（約 6–10GB）與 Qwen3-VL-8B Q4（舒適區約 12GB）無法同時常駐 16GB VRAM；Phase 1 一律「先 ASR → 卸載 → 再 VL」。
2. **YouTube 下載比字幕更脆**：2025–2026 YouTube 正推行 PO Token；字幕可用 `--skip-download`，但無字幕時下載影片需預留 PO Token plugin / 定期更新 yt-dlp。
3. **任務佇列**：單機本機工具可用「SQLite 狀態 + 單 worker asyncio 佇列」取代純 `BackgroundTasks`（避免重啟丟任務）；Celery 延到真有多並行需求。
4. **ASR 不要重寫**：以 subprocess / thin wrapper 呼叫 `/home/kino/git/asr-kino` 的 `transcribe_whisperx.py`，保留 hotwords、corrections、OpenCC、diarization。

---

## 0. 鎖定決策（Agent 勿再辯論）

| 決策點 | 鎖定選擇 | 理由 |
|---|---|---|
| 專案根目錄 | `/home/kino/git/media2text` | 目前為空 repo，由此建立 |
| ASR | 呼叫既有 asr-kino WhisperX 腳本，不重實作 | 已驗證參數與中文流程 |
| VL 模型 | `Qwen/Qwen3-VL-8B-Instruct-GGUF`，LLM=`Q4_K_M`，mmproj=`F16` 或 `Q8_0` | 16GB 舒適；OCR/簡報文字夠用 |
| VL 服務 | `llama-server` OpenAI-compatible `/v1/chat/completions` | 規格已定；image_url 用 nested + base64 data URL |
| GPU 排程 | **全域 GPU 鎖，序列執行** | 16GB 不夠雙模常駐 |
| 任務佇列 Phase 1 | `asyncio.Queue` + 單一 worker + DB 持久化 status | 比 BackgroundTasks 耐重啟；比 Celery 輕 |
| 儲存 | LanceDB（videos/timelines/summaries/cross_analyses）+ SQLite（accounts/jobs） | 敏感憑證與 job 狀態不宜放 LanceDB |
| 前端 Phase 1 | Vite + React + TypeScript（極簡頁） | 後續編輯/多選較省事；勿做設計系統 |
| Google Drive OAuth | **Phase 2**；Phase 1 只做公開分享 + `gdown` | 使用者環境已有 `gdown` |
| YouTube cookies 帳號管理 | **Phase 2**；Phase 1 支援可選 `--cookies` 路徑 env | 先打通公開影片 |
| Diarization | Phase 1 可選 flag，預設關；會議類再開 | 額外 VRAM/時間 |
| Embedding | 欄位 nullable 預留，Phase 1 填 `null` | 不阻塞 MVP |

---

## 1. 既有資產（必須重用）

### 1.1 asr-kino WhisperX

| 項目 | 路徑 / 值 |
|---|---|
| Orchestrator | `/home/kino/git/asr-kino/CLAUDE.md` |
| Script | `/home/kino/git/asr-kino/.claude/skills/asr-local/whisperx/scripts/transcribe_whisperx.py` |
| Venv | `/home/kino/asr/.venv-whisperx/bin/python3` |
| Config | `.../asr-local/config/asr_config.json` |
| Hotwords | `.../config/hotwords.txt` |
| Corrections | `.../config/corrections.json` |
| Model | `large-v3-turbo` |
| Compute | `int8` |
| Batch | `16`（可經 `WHISPERX_BATCH_SIZE` 調低） |
| 預設語言 | `zh` + OpenCC `s2twp` |
| Output | `/home/kino/asr/output/{basename}.{srt,txt}`（腳本內部行為） |

**已驗證的 asr_options（直接沿用，勿改）**：

```python
asr_options = {
    "no_repeat_ngram_size": 3,
    "repetition_penalty": 1.2,
    "hallucination_silence_threshold": 2.0,
    "compression_ratio_threshold": 2.4,
    "condition_on_previous_text": False,
    # + initial_prompt=topic, hotwords=...
}
```

> 注意：社群回報部分 `asr_options`（如 `repetition_penalty`）在某些 WhisperX 版本可能無效；以 asr-kino 實測為準，不要為了「理論更優」亂改參數。

### 1.2 Host 工具

- `ffmpeg`、`python3`、`gdown` 已在 PATH（asr-kino 前提）
- HF cache：`/home/kino/ollama-models/huggingface-hub`
- 雲端 LLM：已有本機可呼叫 API（實作時用 env：`CLOUD_LLM_BASE_URL`、`CLOUD_LLM_API_KEY`）

---

## 2. 建議 Repo 結構

```
media2text/
├── README.md
├── CLAUDE.md / AGENTS.md          # Agent 入口（指向本藍圖 + 如何跑）
├── .env.example
├── pyproject.toml                 # 或 requirements.txt
├── prompts/
│   ├── frame_description.txt
│   ├── transcript_correction.txt
│   ├── frame_correction.txt
│   ├── timeline_merge.txt
│   ├── cross_analysis.txt
│   └── summary/
│       ├── bullet_points.txt
│       ├── meeting_minutes.txt
│       ├── tutorial_outline.txt
│       └── custom_default.txt
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI
│   │   ├── config.py
│   │   ├── api/
│   │   │   ├── videos.py
│   │   │   ├── accounts.py        # Phase 2 stub OK
│   │   │   ├── summaries.py
│   │   │   ├── cross_analysis.py
│   │   │   └── prompts.py
│   │   ├── models/                # Pydantic schemas
│   │   ├── db/
│   │   │   ├── lancedb_store.py
│   │   │   └── sqlite_store.py
│   │   ├── pipeline/
│   │   │   ├── orchestrator.py    # 狀態機
│   │   │   ├── source_normalize.py
│   │   │   ├── youtube.py
│   │   │   ├── direct_url.py
│   │   │   ├── google_drive.py
│   │   │   ├── extract.py         # ffmpeg + scenedetect
│   │   │   ├── asr.py             # wrap asr-kino
│   │   │   ├── frames_vl.py       # llama.cpp client
│   │   │   ├── cloud_llm.py       # adapter
│   │   │   ├── merge.py
│   │   │   └── summarize.py
│   │   ├── workers/
│   │   │   └── job_worker.py      # asyncio queue, concurrency=1
│   │   └── security/
│   │       └── credentials.py     # Fernet encrypt
│   └── tests/
├── frontend/                      # Vite React TS
│   └── src/pages/
│       ├── UploadPage.tsx
│       ├── LibraryPage.tsx
│       ├── ProgressPage.tsx
│       └── TimelinePage.tsx       # Phase 1 唯讀
├── scripts/
│   ├── start_llama_server.sh
│   ├── start_backend.sh
│   └── smoke_e2e.sh
└── data/                          # gitignore
    ├── media/
    ├── lancedb/
    └── sqlite/
```

---

## 3. Pipeline 狀態機（實作契約）

```
pending
  → fetching_source      # 下載 / 抓字幕
  → extracting           # ffmpeg + PySceneDetect（純音訊可跳過 frames）
  → transcribing         # WhisperX（若 caption_source != none 則跳過）
  → describing           # Qwen3-VL（純音訊跳過）
  → merging              # 雲端 C1/C2/C3（caption_source=manual 可降強度或跳過 C1）
  → ready
  → failed               # 必填 error_code + error_message
```

每個階段結束必須：

1. 寫入中繼檔到 `data/media/{video_id}/...`
2. 更新 `videos.status` + `videos.updated_at`
3. 寫 `jobs` 表 progress（0–100 與 stage label）

**中繼檔約定**：

```
data/media/{video_id}/
  meta.json                 # source_type, source_url, caption_source, ...
  media.{ext}               # 本地媒體
  audio.wav                 # 16kHz mono（若需 WhisperX）
  captions.raw.vtt|srt      # YouTube 字幕原始檔（若有）
  transcript.raw.json       # WhisperX 或字幕轉換後的統一格式
  frames/{timestamp_ms}.jpg
  frame_descriptions.json
  timeline.json             # C3 合併結果（也同步寫 LanceDB timelines）
  summary_latest.md
```

**統一 transcript JSON（下游只認這個）**：

```json
{
  "source": "whisperx | youtube_caption_manual | youtube_caption_auto",
  "language": "zh",
  "segments": [
    {"start": 12.4, "end": 18.9, "text": "...", "words": []}
  ]
}
```

---

## 4. 模組實作要點（依研究結論）

### 4.1 YouTube（`pipeline/youtube.py`）

**正確流程（禁止「單一命令同時 write-subs + write-auto-subs 然後猜優先序」）**：

1. `yt_dlp.YoutubeDL.extract_info(url, download=False)` 取 metadata + subtitles / automatic_captions
2. 語言優先：`zh-Hant` > `zh-TW` > `zh-Hans` > `zh` > `en`（可用設定覆寫）
3. 類型優先：**manual `subtitles` > `automatic_captions`**
4. 命中字幕：
   - `download=True` 但只抓字幕：`writethumbnail=False`，用 `writesubtitles` / `writeautomaticsub` + `skip_download=True` + `subtitlesformat=vtt` 或 convert to srt
   - 轉成統一 transcript JSON，`caption_source=manual|auto`
   - **仍下載影片（或至少可抽幀的視訊）以跑畫格分析**——規格要求「字幕只解決逐字稿」。可用較低畫質格式省空間，例如 `bv*[height<=720]+ba/b[height<=720]`
5. 無字幕：完整下載 → 走 WhisperX
6. 錯誤對應表（回傳前端）：

| 情況 | error_code |
|---|---|
| 影片不存在/下架 | `VIDEO_UNAVAILABLE` |
| 需登入/會員 | `AUTH_REQUIRED` |
| Cookie 失效 | `COOKIE_EXPIRED` |
| 地區限制 | `GEO_BLOCKED` |
| yt-dlp 壞掉 / PO Token | `YTDLP_EXTRACT_FAILED`（訊息提示更新 yt-dlp） |
| 字幕下載空檔 | `CAPTION_EMPTY`（fallback 改下載+WhisperX） |

**實作注意（2026 現況）**：

- Prefer `--sub-langs "zh.*"` 這類 wildcard；精確 `zh` 可能 miss `zh-Hant` / community codes
- 只使用 VTT/SRT；避開 json3/ttml（`_UnsafeExtensionError`）
- Auto VTT 含 word-level tag，轉換時要剝乾淨
- `caption_source=auto` 時，timeline API 回傳 flag `can_retranscribe_locally=true`
- Cookies：Phase 1 用 env `YOUTUBE_COOKIES_FILE`；Phase 2 才做多帳號加密庫
- Cookie 匯出指引寫進 README：incognito → 登入 → 開 `youtube.com/robots.txt` → 匯出 → 關掉視窗（避免 cookie 被旋轉）
- 下載間 sleep 5–10s；帳號下載有 ban 風險，README 寫明僅個人備份用途
- 啟動時 log `yt-dlp` 版本；提供 `scripts/update_ytdlp.sh`

**Python API 優於 CLI**：方便解析 `subtitles` vs `automatic_captions` 字典，避免 #9371 的優先序問題。

### 4.2 一般直連 URL（`pipeline/direct_url.py`）

1. HEAD（允許部分站不支援 HEAD → fallback GET stream 前幾 KB）
2. 檢查 `Content-Type` ∈ `{video/*, audio/*, application/octet-stream}` + 副檔名白名單
3. `Content-Length` ≤ `MAX_DOWNLOAD_BYTES`（建議預設 4GB，env 可調）
4. 串流寫入磁碟，timeout、redirect 上限（例如 5）
5. 可選：先讓 yt-dlp 試一次（支援不少通用站），失敗再 requests

### 4.3 Google Drive（`pipeline/google_drive.py`）

- Phase 1：解析 file id → `gdown.download(url, output=..., quiet=False)`
- 錯誤：`Permission Denied` → 提示改「知道連結者可檢視」
- Phase 2：OAuth + `google-api-python-client` `MediaIoBaseDownload`；scope 用最小必要（`drive.readonly` 若審核成本高可僅本機個人用）

### 4.4 擷取（`pipeline/extract.py`）

```text
ffmpeg -i media -ar 16000 -ac 1 -c:a pcm_s16le audio.wav
```

場景偵測：

- 預設用 **AdaptiveDetector**（PySceneDetect 0.7 預設方向；對鏡頭晃動較穩）
- 提供 config：`scene_detector=adaptive|content`，`content_threshold=27`（簡報可 15–20）
- 每場景 1 張代表幀（場景中點）
- 場景長度 > 60s → 每 30s 補 1 張（規格要求）
- 輸出 `frames/{ms}.jpg` + `frames_index.json`（start/end/path）

### 4.5 ASR（`pipeline/asr.py`）

**不要 import whisperx 進 FastAPI venv**（CUDA/依賴地獄）。改 subprocess：

```bash
/home/kino/asr/.venv-whisperx/bin/python3 \
  /home/kino/git/asr-kino/.claude/skills/asr-local/whisperx/scripts/transcribe_whisperx.py \
  "{audio_wav}" --lang zh --format srt \
  --topic "{topic}" \
  --hotwords-file "{hotwords_path}"
```

然後 parse 輸出 srt/json → 統一 transcript JSON，複製到 `data/media/{id}/`。

GPU 鎖：ASR 開始前 `acquire_gpu("whisperx")`；結束後 `del` 不適用（子行程結束即釋放），但必須確保 **llama-server 未佔滿 VRAM**。

**與 llama-server 共存策略（選一，建議 A）**：

- **A（推薦）**：pipeline 需要 VL 前才 `systemctl`/script 啟動 llama-server；ASR 前若 server 在跑則停止或 `--no-mmproj-offload` 並卸載。最簡單：Phase 1 **ASR 期間不啟動 llama-server**；ASR 完再 start。
- **B**：llama-server 常駐但 `--n-gpu-layers` 調低 / 部分 CPU，留 VRAM 給 WhisperX——較難調，不建議 Phase 1。

### 4.6 Qwen3-VL（`pipeline/frames_vl.py`）

啟動範例（`scripts/start_llama_server.sh`）：

```bash
llama-server \
  -m "$MODELS_DIR/Qwen3VL-8B-Instruct-Q4_K_M.gguf" \
  --mmproj "$MODELS_DIR/mmproj-Qwen3VL-8B-Instruct-F16.gguf" \
  --host 127.0.0.1 --port 8080 \
  -c 8192 -ngl 99 --flash-attn on
```

呼叫契約（必須 nested url，否則 `Invalid url value`）：

```python
{
  "model": "qwen3-vl",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": prompt},
      {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
    ]
  }],
  "temperature": 0.2,
  "max_tokens": 256
}
```

- 逐幀帶入 `prev_summary`（規格 prompt）
- 並發：對單機 llama-server 建議 `concurrency=1`（或 2 視 VRAM）
- 結果寫 `frame_descriptions.json`

### 4.7 雲端 LLM adapter（`pipeline/cloud_llm.py`）

```python
class CloudLLMClient:
    async def complete(self, *, system: str, user: str, meta: dict) -> CloudLLMResult:
        ...
```

- 統一記錄：`input_tokens`, `output_tokens`, `model`, `purpose`（c1/c2/c3/summary/cross）→ SQLite `llm_usage`
- C1：依 segment 批次，保留 start/end；prompt 強調「不可增刪實質內容」
- `caption_source=manual`：跳過 C1 或只做輕量標點
- `caption_source=auto`：完整 C1，並在 UI 提示可重跑本地 WhisperX
- C3：輸出嚴格 JSON timeline（speech/frame），後端 validate 後入庫

### 4.8 LanceDB + SQLite

**SQLite 表**：`accounts`, `jobs`, `llm_usage`, `app_meta`

**LanceDB**：

- `embedding` 欄位 nullable；Phase 1 一律 null
- timeline 編輯用 `table.update(where=f"id = '{segment_id}'", values={"text": ..., "edited": True})` 或 `merge_insert`
- `videos.status` 高頻更新：可用 SQLite `jobs` 當即時狀態、LanceDB `videos` 定期 sync；或 Phase 1 直接 LanceDB `update`（本機量小可接受）

建議 Phase 1 簡化：

- **真相來源**：SQLite `videos` + `timeline_segments`（便於 PATCH）
- **LanceDB**：只在 summary ready 後同步一份（為 Phase 4 向量搜尋鋪路）

若嚴格遵守規格「Phase 1 先用 LanceDB 單一儲存」，也可以；但 PATCH 編輯請用官方 `update`/`merge_insert`，不要整表 overwrite。

---

## 5. API 契約（Phase 1 必做）

```
POST /api/videos/upload
POST /api/videos/from-youtube
POST /api/videos/from-url
POST /api/videos/from-google-drive   # Phase 1: public only
GET  /api/videos
GET  /api/videos/{id}
GET  /api/videos/{id}/status
GET  /api/videos/{id}/timeline
POST /api/videos/{id}/summarize
GET  /api/videos/{id}/summaries
GET  /api/prompts/summary
GET  /api/youtube/probe             # 輕量：字幕清單預覽（上傳頁用）
```

Phase 1 可 stub：accounts CRUD、timeline PATCH、cross-analysis、WebSocket。

**Status payload**：

```json
{
  "id": "...",
  "status": "transcribing",
  "stage_label": "語音轉文字",
  "progress": 45,
  "error_code": null,
  "error_message": null,
  "caption_source": "none",
  "source_type": "youtube"
}
```

---

## 6. 前端 Phase 1 範圍（刻意陽春）

1. **Upload**：五種入口（Drive/cookies 可顯示「Phase 2」disabled 或僅公開 Drive）
2. **YouTube probe**：貼上 URL 後呼叫 `/api/youtube/probe` 顯示字幕路徑
3. **Progress**：輪詢 status（2s）
4. **Library**：列表 + 狀態篩選
5. **Timeline**：唯讀時間軸 + 畫格縮圖
6. **Summary**：選預設模板 → 產生 → 顯示

不做：精美設計系統、WebSocket、播放器同步（Phase 2）。

---

## 7. 建置順序（Agent 按此 commit）

### Sprint 0 — Scaffold（0.5 day）

- [ ] `pyproject.toml`、`.env.example`、`README.md`、gitignore（`data/`）
- [ ] FastAPI hello + CORS + health
- [ ] SQLite schema + LanceDB init
- [ ] `job_worker` 空轉（enqueue → sleep → update status）

### Sprint 1 — Source normalize（1–2 days）

- [ ] upload 存檔
- [ ] YouTube probe + caption-or-download
- [ ] direct URL 安全下載
- [ ] gdown 公開 Drive
- [ ] 產出統一 `meta.json` + `local_media_path`
- [ ] 單元測試：fake yt-dlp info dict 優先序

### Sprint 2 — Extract + ASR（1–2 days）

- [ ] ffmpeg wav
- [ ] PySceneDetect + 長場景補幀
- [ ] subprocess 呼叫 asr-kino
- [ ] GPU 互斥：ASR 前確保 VL server 停
- [ ] SRT → 統一 transcript JSON

### Sprint 3 — VL frames（1–2 days）

- [ ] 下載 Qwen3-VL-8B GGUF + mmproj
- [ ] `start_llama_server.sh` + health check
- [ ] frames_vl 批次描述 + prev_summary
- [ ] 落地 `frame_descriptions.json`

### Sprint 4 — Cloud merge + summary（1–2 days）

- [ ] `cloud_llm` adapter + usage log
- [ ] C1/C2/C3 prompts 檔案化
- [ ] timeline 入庫
- [ ] summarize endpoint + 預設 bullet 模板

### Sprint 5 — Frontend MVP（1–2 days）

- [ ] Upload / Progress / Library / Timeline / Summary
- [ ] `scripts/smoke_e2e.sh`：10 分鐘樣本跑通

### Sprint 6 — Harden（1 day）

- [ ] 錯誤碼對齊前端
- [ ] yt-dlp 版本檢查
- [ ] MAX_DOWNLOAD_BYTES、timeout
- [ ] CLAUDE.md 給後續 Agent

**Phase 1 驗收**：一支約 10 分鐘影片（本地上傳或 YouTube），從建立任務到看到完整 timeline + 一份預設摘要，不報錯；中繼檔皆落地。

---

## 8. Phase 2+ 銜接（不要提前做）

| Phase | 內容 |
|---|---|
| 2 | Timeline PATCH、摘要模板編輯、播放器 seek、accounts（cookies Fernet + Drive OAuth）、auto caption「改用本地轉錄」按鈕 |
| 3 | 跨檔案彙整 API + UI |
| 4 | WebSocket、Celery/ARQ、diarization 預設策略、embedding 實際填入 |
| 5 | 批次上傳、匯出 md/docx/pdf |

---

## 9. 環境變數（`.env.example`）

```bash
DATA_DIR=./data
LANCEDB_URI=./data/lancedb
SQLITE_PATH=./data/sqlite/app.db

# ASR (asr-kino)
WHISPERX_PYTHON=/home/kino/asr/.venv-whisperx/bin/python3
WHISPERX_SCRIPT=/home/kino/git/asr-kino/.claude/skills/asr-local/whisperx/scripts/transcribe_whisperx.py
ASR_HOTWORDS_FILE=/home/kino/git/asr-kino/.claude/skills/asr-local/config/hotwords.txt
ASR_CORRECTIONS_FILE=/home/kino/git/asr-kino/.claude/skills/asr-local/config/corrections.json

# VL
LLAMA_SERVER_URL=http://127.0.0.1:8080/v1
LLAMA_SERVER_MODEL=qwen3-vl
QWEN_VL_GGUF=...
QWEN_VL_MMPROJ=...

# Cloud LLM (local proxy)
CLOUD_LLM_BASE_URL=
CLOUD_LLM_API_KEY=
CLOUD_LLM_MODEL=

# Downloads
MAX_DOWNLOAD_BYTES=4294967296
YOUTUBE_COOKIES_FILE=
YTDLP_SLEEP_SECONDS=5

# Security
CREDENTIALS_FERNET_KEY=   # Phase 2
```

---

## 10. 風險與緩解（研究結論）

| # | 風險 | 緩解 |
|---|---|---|
| 1 | 16GB VRAM 雙模衝突 | GPU 鎖 + ASR/VL 序列；ASR 時不跑 llama-server |
| 2 | YouTube PO Token / 反爬 | 鎖 yt-dlp 版本、文件化更新、錯誤碼 `YTDLP_EXTRACT_FAILED`、必要時 PO Token plugin |
| 3 | Cookie 旋轉失效 | robots.txt 匯出法；顯示 last_verified；失敗明確提示 |
| 4 | Auto caption 品質差 | UI 提供「改用本地 WhisperX」；C1 對 auto 加強 |
| 5 | BackgroundTasks 丟任務 | 用 DB job + 單 worker；重啟可 resume 未完成 job |
| 6 | 雲端 LLM 過度改寫 | Prompt 硬限制 + Phase 2 diff 檢視 |
| 7 | 直連 URL 濫用 | Content-Type/大小/超時/副檔名白名單 |
| 8 | WhisperX 部分 asr_options 無效 | 以 asr-kino 實測參數為準，不追新參數 |
| 9 | llama.cpp image_url 格式 | 必須 `image_url: {url: data:...}` nested |
| 10 | Drive 公開連接下載失敗 | 用 gdown 處理 confirm 頁；私有延 Phase 2 |

---

## 11. Agent 開工指令（複製即用）

```text
讀取 docs/IMPLEMENTATION_BLUEPRINT.md 與使用者執行計畫 v1.2。
從 Sprint 0 開始實作，嚴格遵守「鎖定決策」。
不要重寫 WhisperX；用 subprocess 呼叫 asr-kino。
Phase 1 不做 Celery、不做 Drive OAuth、不做 timeline 編輯。
每個 Sprint 結束後跑對應測試或 smoke。
回覆使用繁體中文，程式碼/路徑用 English。
```

---

## Key Takeaways

1. **重用 asr-kino，序列化 GPU，llama-server 與 WhisperX 互斥**——這是 16GB 卡上唯一穩的 Phase 1 路徑。
2. **YouTube 字幕用 extract_info 手動排優先序**；下載影片預留 PO Token / yt-dlp 更新流程。
3. **Job 狀態進 SQLite + 單 worker**，比純 BackgroundTasks 適合分鐘級 pipeline。
4. **Qwen3-VL-8B Q4 + mmproj** via llama-server；image 用 nested base64 data URL。
5. **Phase 1 砍掉帳號系統與跨檔彙整**，先打通「正規化 → 擷取 → ASR → VL → 雲端合併 → 摘要 → 唯讀 UI」。

---

## Sources

1. [yt-dlp Extract YouTube Subtitles Guide (2026)](https://skipthewatch.com/blog/yt-dlp-youtube-subtitles) — manual vs auto、silent failure、VTT/SRT
2. [yt-dlp FAQ — cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ) — cookies-from-browser / Netscape 格式
3. [yt-dlp Extractors wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors) — PO Token、cookie 匯出 robots.txt 法、OAuth 失效
4. [yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide) — mweb + plugin
5. [yt-dlp issue #15057](https://github.com/yt-dlp/yt-dlp/issues/15057) — `en.*` language wildcard
6. [WhisperX README](https://github.com/m-bain/whisperX) — VRAM、batch、int8
7. [WhisperX issue #894](https://github.com/m-bain/whisperX/issues/894) — large-v3-turbo 載入方式
8. [WhisperX issue #1170](https://github.com/m-bain/whisperX/issues/1170) — 部分 asr_options 可能無效
9. [Clore WhisperX guide](https://docs.clore.ai/guides/audio-and-voice/whisperx) — large-v3-turbo VRAM 約 10GB+
10. [llama.cpp multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md) — llama-server + mmproj
11. [Qwen3-VL-8B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) — 官方 GGUF 拆分
12. [Qwen3-VL 4B vs 8B VRAM guide (2026)](https://codersera.com/blog/qwen3-vl-4b-vs-qwen3-vl-8b-benchmarks-vram-guide/) — 8B Q4 ≈ 12GB 舒適
13. [llama.cpp discussion #21621](https://github.com/ggml-org/llama.cpp/discussions/21621) — nested `image_url.url` 必填
14. [LanceDB update docs](https://docs.lancedb.com/tables/update) — update / merge_insert
15. [LanceDB schema evolution](https://docs.lancedb.com/tables/schema) — nullable vector 欄位
16. [PySceneDetect detectors](https://www.scenedetect.com/docs/latest/api/detectors.html) — Content vs Adaptive
17. [PySceneDetect CLI](https://www.scenedetect.com/cli/) — threshold 調參、stats file
18. [PySceneDetect benchmarks](https://www.scenedetect.com/benchmarks/) — Adaptive 常優於 Content
19. [gdown](https://github.com/wkentaro/gdown) — Drive 公開檔下載
20. [Google Drive API downloads](https://developers.google.com/drive/api/guides/manage-downloads) — OAuth 私有檔
21. [FastAPI BackgroundTasks vs Celery (2026)](https://markaicode.com/vs/fastapi-background-tasks-vs-celery-ai-workloads/) — 長任務不宜純 BackgroundTasks
22. [FastAPI Patterns: BT vs Celery vs ARQ](https://fastapi-patterns.com/async-background-tasks-observability/background-task-processing/fastapi-backgroundtasks-vs-celery-vs-arq/) — 耐久性差異
23. [asr-kino CLAUDE.md](file:///home/kino/git/asr-kino/CLAUDE.md) — 本機已驗證 pipeline
24. [asr-kino transcribe_whisperx.py](file:///home/kino/git/asr-kino/.claude/skills/asr-local/whisperx/scripts/transcribe_whisperx.py) — 實際 asr_options

---

## Methodology

- 子問題：① YouTube 字幕/cookies/PO Token ② WhisperX+VRAM ③ Qwen3-VL/llama.cpp ④ LanceDB 更新模型 ⑤ 任務佇列 ⑥ Drive/直連 ⑦ PySceneDetect ⑧ 與 asr-kino 整合
- 工具：Exa `web_search_exa` + `web_fetch_exa`；本機讀取 asr-kino
- 查詢約 15 組、精讀官方 wiki/docs 與 2025–2026 實務文
- 缺口：使用者本機「雲端 LLM proxy」實際 endpoint 未驗證——實作時以 `.env` 對接；Qwen3-VL-8B 在 5070 Ti 上的實測速度待 Sprint 3 smoke
