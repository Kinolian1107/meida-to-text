# media2text — Agent 備忘

## 鎖定決策（勿辯論）

- GPU：全域鎖，先 ASR 再 VL；`LLAMA_AUTO_MANAGE` 可自動啟停 llama-server
- 佇列：預設 `asyncio.Queue`；可選 `JOB_QUEUE_BACKEND=arq` + Redis
- 儲存：SQLite 為真相來源；LanceDB 同步摘要／timeline／embeddings
- 綁定：`API_HOST=0.0.0.0`、Vite `host: 0.0.0.0`（WSL2 → HOST）

## 已完成範圍

- Phase 1–2：來源正規化、擷取、WhisperX、VL、merge/summary、timeline 編輯、帳號、retranscribe
- Phase 3：`/api/cross-analysis`、跨檔 UI、相關摘要
- Phase 4：WebSocket `/ws/videos/{id}/progress`、diarization flag、hashing embeddings、ARQ 可選
- Phase 5：batch upload、export md/docx/pdf、Prompt 管理頁
- 缺口：correction diff、`/resume`、llama auto manage

## 建置順序提醒

改依賴後：`pip install -e ".[dev]"`；前端：`cd frontend && npm install`
