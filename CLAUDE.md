# media2text — Agent 備忘

## 鎖定決策（勿辯論）

- GPU：全域鎖，先 ASR 再 VL；`LLAMA_AUTO_MANAGE` 可自動啟停 llama-server
- 佇列：預設 `asyncio.Queue`；可選 `JOB_QUEUE_BACKEND=arq` + Redis
- 儲存：SQLite 為真相來源；LanceDB 同步摘要／timeline／embeddings
- 綁定：`API_HOST=0.0.0.0`、Vite `host: 0.0.0.0`（WSL2 → HOST）

## 已完成範圍

- Phase 1–2：來源正規化、擷取、WhisperX、VL、merge/summary、timeline 編輯、帳號、retranscribe
- Phase 3：`/api/cross-analysis`、跨檔彙整 UI（多選比較）
- Phase 4：WebSocket `/ws/videos/{id}/progress`、diarization flag、hashing embeddings、ARQ 可選
- Phase 5：batch upload、export md/docx/pdf、Prompt 管理頁
- 摘要頁（`SummaryPage.tsx`）：只顯示最新版摘要，歷史版本收合成清單（最近 2 筆）＋點擊彈窗檢視；Prompt 設定區預設收合；內容用 `react-markdown`＋`remark-gfm` 渲染；`/api/cross-analysis/related/{summaryId}`（相關摘要建議）API 還在但已從此頁 UI 移除，未被任何頁面呼叫
- 摘要生成（`pipeline/summarize.py`）：若時間軸含帶 `frame_path` 的畫格片段，會在 system prompt 附加標註規則，要求 LLM 對「主要依據畫面」的重點加 `{{frame:TIMESTAMP}}`；`inject_frame_images()` 事後把標記換成 `![](/api/videos/{id}/frames/{name})`，讓關鍵影格圖片直接嵌進摘要內容——依賴 LLM 照格式輸出，非強制結構化，效果需實測驗證
- 佇列可靠性：`orchestrator.py`／`api/videos.py` 內原本同步阻塞的 I/O（ffmpeg 抽取、yt-dlp／direct-url／Google Drive 下載、上傳寫檔、YouTube probe）全部包進 `asyncio.to_thread()`，避免單一 job 處理期間整個 event loop 被卡住、拖累其他 API（含項目庫列表）回應；`JobWorker` 重啟後仍會把中斷中的 job 打回 `pending` 從 `fetching_source` 整段重跑，非真正斷點續傳，屬於下方 `/resume` 缺口的一部分（ASR/VL 產物有落地快取，重跑不會真的重打模型，但雲端校稿會重來）
- Cloud LLM 可靠性（`pipeline/cloud_llm.py` + `pipeline/merge.py`）：`CloudLLMClient.complete()` 對 5xx／連線錯誤加了最多 3 次重試＋指數退避（2s/4s）；C1 逐字稿校稿改成依 `C1_CHUNK_CHAR_LIMIT=5000` 字元分段送出（永遠在 segment 邊界切斷，不腰斬單一 segment 的文字），單一 chunk 重試用盡仍失敗只影響該段落（保留原文），不會讓長影片整支判 `CLOUD_LLM_FAILED`；分段期間進度會在 82–89% 間依 chunk 內插，避免 UI 卡在固定百分比看似卡死
- 缺口：`/resume`（中斷 job 目前是整段重跑而非續傳，見上）；correction diff（`build_correction_diff`）與 llama auto manage（`llama_manager.py`/`gpu_lock.py`）皆已實作，非缺口

## 建置順序提醒

改依賴後：`pip install -e ".[dev]"`；前端：`cd frontend && npm install`
