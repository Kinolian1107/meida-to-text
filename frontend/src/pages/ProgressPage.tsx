import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, VideoStatus } from "../api";

export default function ProgressPage() {
  const { id = "" } = useParams();
  const [st, setSt] = useState<VideoStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [via, setVia] = useState<"ws" | "poll">("poll");

  useEffect(() => {
    let alive = true;
    let ws: WebSocket | null = null;
    let pollTimer: number | undefined;

    const apply = (s: VideoStatus) => {
      if (alive) setSt(s);
    };

    const startPoll = () => {
      setVia("poll");
      const tick = async () => {
        try {
          apply(await api.status(id));
        } catch (e) {
          if (alive) setError(String(e));
        }
      };
      void tick();
      pollTimer = window.setInterval(tick, 2000);
    };

    try {
      ws = new WebSocket(api.progressWsUrl(id));
      ws.onopen = () => setVia("ws");
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          if (data.type === "ping") return;
          apply({
            id,
            status: data.status,
            stage_label: data.stage_label,
            progress: data.progress ?? 0,
            error_code: data.error_code ?? null,
            error_message: data.error_message ?? null,
            caption_source: st?.caption_source || "none",
            source_type: st?.source_type || "",
            filename: st?.filename || "",
            can_retranscribe_locally: false,
          });
          // Refresh full status once for filename etc.
          void api.status(id).then(apply).catch(() => undefined);
        } catch {
          /* ignore */
        }
      };
      ws.onerror = () => {
        ws?.close();
        startPoll();
      };
      ws.onclose = () => {
        if (alive && (!st || (st.status !== "ready" && st.status !== "failed"))) {
          // fallback if closed early
        }
      };
      // Also seed once via HTTP
      void api.status(id).then(apply).catch((e) => setError(String(e)));
    } catch {
      startPoll();
    }

    return () => {
      alive = false;
      ws?.close();
      if (pollTimer) clearInterval(pollTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function onResume() {
    setError(null);
    try {
      await api.resume(id);
      const s = await api.status(id);
      setSt(s);
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div>
      <h1>處理進度</h1>
      <p className="muted">更新方式：{via === "ws" ? "WebSocket" : "輪詢"}</p>
      {error && <p className="error">{error}</p>}
      {st && (
        <div className="panel">
          <p>
            <strong>{st.filename || st.id}</strong>
          </p>
          <p className="muted">
            {st.source_type} · caption: {st.caption_source}
          </p>
          <p>
            {st.stage_label}（{st.status}）
          </p>
          <div className="progress-bar">
            <span style={{ width: `${st.progress}%` }} />
          </div>
          <p>{st.progress}%</p>
          {st.error_code && (
            <p className="error">
              {st.error_code}: {st.error_message}
            </p>
          )}
          {st.status === "failed" && (
            <button type="button" onClick={onResume}>
              從中繼檔續跑
            </button>
          )}
          {st.status === "ready" && (
            <>
              {st.correction?.applied && st.correction.model ? (
                <p className="correction-badge applied">
                  雲端校稿：{st.correction.model}
                </p>
              ) : st.correction && !st.correction.applied ? (
                <p className="correction-badge skipped">雲端校稿：未套用</p>
              ) : null}
              <p>
                <Link to={`/timeline/${id}`}>查看時間軸</Link>
                {" · "}
                <Link to={`/summary/${id}`}>查看摘要</Link>
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
