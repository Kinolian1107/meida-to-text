# media2text

本地影片／音訊多模態分析系統：來源正規化 → FFmpeg/PySceneDetect → WhisperX（asr-kino）→ Qwen3-VL（llama-server）→ 雲端校稿合併 → 摘要 → 跨檔彙整。

實作依據：[`docs/IMPLEMENTATION_BLUEPRINT.md`](docs/IMPLEMENTATION_BLUEPRINT.md)

## 快速開始（WSL2 → Windows HOST 可連）

```bash
# 後端（預設綁 0.0.0.0:8000）
cp .env.example .env   # 填 CLOUD_LLM_*、QWEN_VL_*（可選）
./scripts/start_backend.sh

# 前端（另開終端，綁 0.0.0.0:5173）
./scripts/start_frontend.sh
```

在 **Windows HOST** 瀏覽器開啟：

- UI: `http://localhost:5173`（WSL2 預設開 localhost forwarding，實測可直接連）
- API health: `http://localhost:8000/health`
- 若 `localhost` 連不到，先查 WSL IP：`hostname -I | awk '{print $1}'`，改開 `http://<WSL_IP>:5173`

> 若 `localhost`／WSL IP 都連不到，在 Windows PowerShell（系統管理員）執行：  
> `netsh interface portproxy add v4tov4 listenport=5173 listenaddress=0.0.0.0 connectport=5173 connectaddress=<WSL_IP>`  
> 並對 8000 做同樣設定。

## 開機自動啟動（systemd）

WSL 的 `/etc/wsl.conf` 已開 `[boot] systemd=true`，可以把 backend／frontend／PO Token provider 註冊成系統層級 systemd service，WSL 一啟動就把該跑的東西全部帶起（不需要手動跑 `start_backend.sh`／`start_frontend.sh`，也不需要先登入該使用者）：

```bash
sudo ./scripts/systemd/install.sh
```

會把 `scripts/systemd/` 下三個單元檔複製到 `/etc/systemd/system/`，`daemon-reload` 後 `enable --now`：

| Service | 用途 |
|---|---|
| `media2text-potprovider` | YouTube PO Token provider（Docker 容器），backend 排在它後面啟動 |
| `media2text-backend` | FastAPI／uvicorn |
| `media2text-frontend` | Vite dev server |

之後管理：

```bash
systemctl status media2text-potprovider.service media2text-backend.service media2text-frontend.service
journalctl -u media2text-backend.service -f   # 看 log
sudo systemctl restart media2text-backend.service
curl http://127.0.0.1:4416/ping               # PO Token provider 健康檢查
```

> 三個 service 都用 `User=kino` 執行（非 root），backend 會在啟動時 `source .env`；改了 `.env` 記得 `sudo systemctl restart` 才會生效。frontend 用 Vite dev server（跟手動啟動方式一致，非 production build）。
>
> **PO Token provider 是 YouTube 下載的必要條件**——沒跑的話 yt-dlp 一律吃 `HTTP Error 403`，UI 會顯示 `POT_PROVIDER_UNAVAILABLE`。容器由 systemd 獨佔管理（前景 `docker run --rm`，非 Docker restart policy），backend 用 `Wants=` 而非 `Requires=` 依賴它，所以 provider 掛掉時 API 仍照常服務，只有 YouTube 下載降級。

## 功能一覽

| 階段 | 內容 |
|---|---|
| Phase 1–2 | 五種來源、ASR/VL、校稿合併、timeline 編輯、帳號、retranscribe |
| Phase 3 | 跨檔彙整（共同重點／衝突）、歷史紀錄 |
| Phase 4 | WebSocket 進度、可選 ARQ/Redis、diarization 開關、embedding |
| Phase 5 | 批次上傳、匯出 md/docx/pdf、Prompt 模板庫管理 |
| 摘要頁優化 | markdown 渲染、關鍵影格圖片自動嵌入摘要、歷史版本彈窗檢視 |
| 佇列可靠性 | 阻塞 I/O（ffmpeg／下載／上傳寫檔）移出 event loop、雲端 LLM 5xx 重試＋退避、長逐字稿依 5000 字元分段校稿 |
| 標籤與搜尋 | AI 自動標籤（講者／節目／主題）＋ YouTube 頻道自動標籤＋手動增刪、項目庫標籤篩選／分頁／關鍵字搜尋、摘要語意模糊搜尋（Ollama bge-m3 embedding） |
| 部署 | 系統層級 systemd service（`scripts/systemd/`），WSL 開機自動帶起 PO Token provider／backend／frontend，Windows HOST 透過 localhost forwarding 直連 |
| YouTube 下載韌性 | PO Token provider＋`curl_cffi` impersonation 繞過 403，間歇性 403 自動重試 4 次（換新 session）；直播轉檔中（post_live）提前失敗不空跑 |
| 缺口補齊 | 失敗續跑 `/resume`（目前中斷 job 為整段重跑）；既有影片無 AI 標籤 backfill |

## GPU（RTX 5070 Ti 16GB）

WhisperX 與 Qwen3-VL **不可同時佔滿 VRAM**。預設 `LLAMA_AUTO_MANAGE=true`：ASR 前自動停 llama-server，畫格描述前自動啟動。

手動：`./scripts/start_llama_server.sh`（需設定 `QWEN_VL_GGUF` / `QWEN_VL_MMPROJ`）

會議類 speaker 分離：`.env` 設 `ENABLE_DIARIZATION=true`（需 asr-kino / pyannote 模型）

## 可選 ARQ 佇列

預設用進程內 `asyncio.Queue`（單 worker，適合本機 GPU）。若要 Redis/ARQ：

```bash
# .env
REDIS_URL=redis://127.0.0.1:6379
JOB_QUEUE_BACKEND=arq

./scripts/start_arq_worker.sh
```

## Smoke / 測試

```bash
./scripts/smoke_e2e.sh /path/to/sample.mp4
source .venv/bin/activate && pytest -q
```
