# media2text — Agent 備忘

## 鎖定決策（勿辯論）

- GPU：全域鎖，先 ASR 再 VL；`LLAMA_AUTO_MANAGE` 可自動啟停 llama-server
- 佇列：預設 `asyncio.Queue`；可選 `JOB_QUEUE_BACKEND=arq` + Redis
- 儲存：SQLite 為真相來源；LanceDB 同步摘要／timeline／embeddings
- 綁定：`API_HOST=0.0.0.0`、Vite `host: 0.0.0.0`（WSL2 → HOST）
- Embedding：本機 Ollama（沿用 LazyBun 已在跑的 `http://localhost:11434`）+ `bge-m3`（1024 維），非自架模型或雲端 API；Ollama 不可用時 embedding 降級為零向量（語意搜尋搜不到，但不擋摘要／標籤流程）

## 已完成範圍

- Phase 1–2：來源正規化、擷取、WhisperX、VL、merge/summary、timeline 編輯、帳號、retranscribe
- Phase 3：`/api/cross-analysis`、跨檔彙整 UI（多選比較）
- Phase 4：WebSocket `/ws/videos/{id}/progress`、diarization flag、embeddings（見下方「標籤與語意搜尋」，已從 hashing 換成 Ollama bge-m3）、ARQ 可選
- Phase 5：batch upload、export md/docx/pdf、Prompt 管理頁
- 摘要頁（`SummaryPage.tsx`）：只顯示最新版摘要，歷史版本收合成清單（最近 2 筆）＋點擊彈窗檢視；Prompt 設定區預設收合；內容用 `react-markdown`＋`remark-gfm` 渲染；`/api/cross-analysis/related/{summaryId}`（相關摘要建議）API 還在但已從此頁 UI 移除，未被任何頁面呼叫
- 摘要生成（`pipeline/summarize.py`）：若時間軸含帶 `frame_path` 的畫格片段，會在 system prompt 附加標註規則，要求 LLM 對「主要依據畫面」的重點加 `{{frame:TIMESTAMP}}`；`inject_frame_images()` 事後把標記換成 `![](/api/videos/{id}/frames/{name})`，讓關鍵影格圖片直接嵌進摘要內容——依賴 LLM 照格式輸出，非強制結構化，效果需實測驗證
- 佇列可靠性：`orchestrator.py`／`api/videos.py` 內原本同步阻塞的 I/O（ffmpeg 抽取、yt-dlp／direct-url／Google Drive 下載、上傳寫檔、YouTube probe）全部包進 `asyncio.to_thread()`，避免單一 job 處理期間整個 event loop 被卡住、拖累其他 API（含項目庫列表）回應；`JobWorker` 重啟後仍會把中斷中的 job 打回 `pending` 從 `fetching_source` 整段重跑，非真正斷點續傳，屬於下方 `/resume` 缺口的一部分（ASR/VL 產物有落地快取，重跑不會真的重打模型，但雲端校稿會重來）
- Cloud LLM 可靠性（`pipeline/cloud_llm.py` + `pipeline/merge.py`）：`CloudLLMClient.complete()` 對 5xx／連線錯誤加了最多 3 次重試＋指數退避（2s/4s）；C1 逐字稿校稿改成依 `C1_CHUNK_CHAR_LIMIT=5000` 字元分段送出（永遠在 segment 邊界切斷，不腰斬單一 segment 的文字），單一 chunk 重試用盡仍失敗只影響該段落（保留原文），不會讓長影片整支判 `CLOUD_LLM_FAILED`；分段期間進度會在 82–89% 間依 chunk 內插，避免 UI 卡在固定百分比看似卡死
- 標籤與語意搜尋：`videos`/`video_tags` 兩表（`sqlite_store.py`），標籤有 `kind`（speaker／show／channel／topic）與 `source`（ai／manual／channel）；`pipeline/tagging.py:generate_tags()` 在摘要完成後同步呼叫雲端 LLM 產生標籤（prompt：`prompts/tag_generation.txt`），失敗只 log 不影響摘要本身；`finalize_summary_extras()` 是 `/summarize` 與 orchestrator 自動摘要共用的收尾（算 embedding＋產標籤），避免兩處各寫一份；YouTube 影片另外在建立當下直接用 `probe_youtube()` 的 channel/uploader 掛上 `source=channel` 標籤，不必等摘要、不吃 LLM 額度；`replace_ai_tags()` 只覆蓋 `source=ai` 的標籤，手動與頻道標籤不會被摘要重跑洗掉。Embedding 改用 `pipeline/embeddings.py:embed_text_remote()`（見上方 Ollama 決策），`LanceDBStore.upsert_summary()` 維持同步、不做網路呼叫——vector 一律由呼叫端先 `await` 算好再傳入，避免重蹈 event loop 卡住的覆轍；`search_summaries()` 是全量 cosine 掃描，量體大時才需要真正的向量索引。`GET /api/videos` 已改回應 envelope `{items,total,page,page_size}`，是 breaking change，前端 `api.listVideos()`／`CrossPage.tsx` 都已同步更新；既有摘要的 embedding 用 `scripts/backfill_embeddings.py` 補算，已對目前 17 筆摘要跑過一次。
- 缺口：`/resume`（中斷 job 目前是整段重跑而非續傳，見上）；correction diff（`build_correction_diff`）與 llama auto manage（`llama_manager.py`/`gpu_lock.py`）皆已實作，非缺口；既有影片沒有補跑 AI 標籤（僅 embedding 有 backfill，需要標籤要手動點「重新產生標籤」或等下次重跑摘要）
- 部署（systemd）：`scripts/systemd/media2text-backend.service`／`media2text-frontend.service`／`media2text-potprovider.service`（系統層級單元，`User=kino`，`WantedBy=multi-user.target`）＋ `scripts/systemd/install.sh`（需 `sudo` 執行一次，複製單元檔＋`daemon-reload`＋`enable --now`）。backend 單元用 `bash -c` 先 `source .env` 再啟動 uvicorn（不帶 `--reload`，避免 `data/` 目錄寫入觸發重載造成長跑 job crash-loop）；frontend 單元 source `nvm.sh` 後跑 `npm run dev`（維持 Vite dev server，非 production build，與手動啟動腳本一致）。WSL 這台機器 `/etc/wsl.conf` 已是 `boot.systemd=true`，兩個 service 都 `enable --now` 過，開機會自動帶起；Windows HOST 端測試過 `http://localhost:5173`／`http://localhost:8000/health` 靠 WSL2 預設 localhost forwarding 直連即可，不需要額外 `netsh portproxy`。踩雷記錄：這台機器現有的 `~/.config/systemd/user/*` (cursor-bridge 等) 用的是 **user-level** `systemd --user`，但新開的 shell 連不上該 session 的 D-Bus（`/run/user/1000/systemd` 不存在，即使 `loginctl show-user` 顯示 `Linger=yes`）——原因待查，暫時繞開，改用 system-level unit，所以本專案兩個 service 是 root 裝的、非 user-level，跟其他 bridge service 的管理方式不一致，未來要改也得走 `sudo systemctl`。PO Token provider 也收進 systemd（`media2text-potprovider.service`）：**由 systemd 獨佔管理容器生命週期**，用前景 `docker run --rm` 而非 Docker 自己的 restart policy（兩套機制會互搶，install.sh 會對既有容器下 `docker update --restart=no` 解除舊政策）；`ExecStartPost` 會 poll `/ping` 最多 30 秒當 readiness gate，backend 以 `Wants=`＋`After=` 排在它後面——刻意用 `Wants=` 不用 `Requires=`，provider 掛掉時 API 仍照常服務，只有 YouTube 下載降級並回報 `POT_PROVIDER_UNAVAILABLE`。port 綁 `127.0.0.1:4416`（不對外）。

- YouTube 下載阻擋（2026-08-14 排查）：yt-dlp 抓 YouTube 有兩種會被誤判成「程式壞了」的失敗，都不是本專案 code 的 bug，已各自加上明確 error code：
  - `LIVESTREAM_PROCESSING`：影片 `live_status == "post_live"`（直播剛結束、YouTube 還在轉完整 VOD）。這期間只給「最近 2 小時」的 rolling DASH manifest，下載會大量 `fragment not found` 最後以 `The downloaded file is empty` 收場。`fetch_youtube()` 現在在 probe 後就 fail fast，不再浪費約 2 分鐘跑註定失敗的下載；`probe_youtube()` 也會把警告字樣附在 message 尾端讓上傳頁預覽時就看得到。等數小時 YouTube 轉檔完成即可正常下載。
  - `POT_PROVIDER_UNAVAILABLE`：下載階段 `HTTP Error 403: Forbidden`。根因是 YouTube 的 PO Token（Proof of Origin）反爬機制，**跟 cookie 有沒有過期無關**——實測掛上 `帳號` 頁既有的 youtube_cookie 一樣 403，逐一換 `player_client`（web／web_safari／tv／tv_embedded／mweb／ios／android_vr）也全滅（tv 說 DRM、mweb／ios 直接警告缺 GVS PO Token、web 系列篩不出格式）。解法三層，缺一不可：
    1. **PO Token provider**：`bgutil-ytdlp-pot-provider`（pip，已在 `pyproject.toml`）＋ Docker 容器，yt-dlp 自動偵測 `127.0.0.1:4416`，不需改任何專案程式碼。健康檢查 `curl http://127.0.0.1:4416/ping`，回傳的 version 要跟 pip 端 plugin 版本一致。
    2. **`curl_cffi`（browser TLS impersonation）**：yt-dlp 的 YouTube extractor 會要求 impersonation，沒裝時 log 會一直出現 `no impersonate target is available` 且 403 機率大增。**版本必須 `>=0.10,<0.16`**——yt-dlp 2026.07.04 硬性拒絕 0.16+（`Only curl_cffi versions 0.5.10 and 0.10.x through 0.15.x are supported`），裝成 0.16 的話 `yt-dlp --list-impersonate-targets` 會全部顯示 unavailable，等同沒裝。
    3. **重試**：即使 1+2 都到位，403 仍是**間歇性**的（實測 3 次有 1 次失敗）。`_download_media_with_retry()` 對 403 最多重試 4 次、指數退避 3/6/12s，每次都建新的 `YoutubeDL` session（重用舊 session 只會重播已被拒絕的 URL）；非 403 的錯誤不重試，直接往上拋原本的 error code。
    4. **JS runtime + challenge solver + player client**（2026-08-19 補上，先前誤判為「可忽略」）：以上三層全部健康時仍會 403，根因是 yt-dlp 解不開 `n` challenge。`pyproject.toml` 原本只寫裸的 `yt-dlp`，沒帶 `[default]` extra，所以 `yt-dlp-ejs`（challenge solver script）沒裝，機器上也沒有 JS runtime；兩者一缺，所有需要簽章解密的 client（`web`／`tv_simply`／`web_embedded`／`mweb`）就只剩 images，yt-dlp 只好退回唯一不需 JS 的 `android_vr`，而 **`android_vr` 的 googlevideo URL 現在一律 403**（實測連 progressive format 18 也 403，`fetch_pot=always` 無效）；`web_safari` 雖能拿到 GVS PO Token 但被 YouTube 強制 SABR，https 格式全被 skip。解法：依賴改成 `yt-dlp[default,deno]`（帶入 `yt-dlp-ejs` 與 `.venv/bin/deno`），並用 `YTDLP_PLAYER_CLIENTS` 把 client 釘在 `tv_simply,web_embedded`（實測兩者都可下載；`tv` 回 `The page needs to be reloaded.`、`tv_embedded` 已不被支援）。`youtube.py:_ytdlp_base_opts()` 是 probe 與下載共用的 opts（兩邊必須同一組 client，否則 probe 拿到的 `live_status`／格式會跟實際下載脫節）；`_js_runtime_path()` 用**絕對路徑**指向 `.venv/bin/deno`，因為 systemd 直接 exec `.venv/bin/uvicorn`，`.venv/bin` 不在 `PATH` 上、`which` 找不到。成功時 log 會出現 `[jsc:deno] Solving JS challenges using deno`。
    附註：`POT_PROVIDER_UNAVAILABLE` 這個 error code 名字是歷史遺留，實際上 403 的成因不只 PO Token provider（訊息已改寫成三個可能性），code 本身為相容前端與測試保留不動。

## 建置順序提醒

改依賴後：`pip install -e ".[dev]"`；前端：`cd frontend && npm install`
語意搜尋需要本機 Ollama 跑著且已 `ollama pull bge-m3`（沿用 LazyBun 的 server，非本專案私有）
YouTube 下載需要 `media2text-potprovider.service` 跑著（見上方「YouTube 下載阻擋」），否則一律 403；改過 systemd 單元檔後要重跑 `sudo ./scripts/systemd/install.sh`
YouTube 下載另需 `.venv/bin/deno` 與 `yt-dlp-ejs`（由 `yt-dlp[default,deno]` 帶入，跑過 `pip install -e ".[dev]"` 即有），缺了會退回 `android_vr` 並全數 403
