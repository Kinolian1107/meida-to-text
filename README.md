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

- UI: `http://127.0.0.1:5173`（若 WSL 端口轉發正常）
- 或先查 WSL IP：`hostname -I | awk '{print $1}'`，再開 `http://<WSL_IP>:5173`
- API health: `http://127.0.0.1:8000/health`

> 若 HOST 連不到，在 Windows PowerShell（系統管理員）執行：  
> `netsh interface portproxy add v4tov4 listenport=5173 listenaddress=0.0.0.0 connectport=5173 connectaddress=<WSL_IP>`  
> 並對 8000 做同樣設定。

## 功能一覽

| 階段 | 內容 |
|---|---|
| Phase 1–2 | 五種來源、ASR/VL、校稿合併、timeline 編輯、帳號、retranscribe |
| Phase 3 | 跨檔彙整（共同重點／衝突）、歷史紀錄 |
| Phase 4 | WebSocket 進度、可選 ARQ/Redis、diarization 開關、embedding |
| Phase 5 | 批次上傳、匯出 md/docx/pdf、Prompt 模板庫管理 |
| 摘要頁優化 | markdown 渲染、關鍵影格圖片自動嵌入摘要、歷史版本彈窗檢視 |
| 佇列可靠性 | 阻塞 I/O（ffmpeg／下載／上傳寫檔）移出 event loop、雲端 LLM 5xx 重試＋退避、長逐字稿依 5000 字元分段校稿 |
| 缺口補齊 | 失敗續跑 `/resume`（目前中斷 job 為整段重跑） |

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
