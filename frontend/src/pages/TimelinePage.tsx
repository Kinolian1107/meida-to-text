import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, CorrectionInfo, DiffItem, TimelineSegment } from "../api";

function fmt(t: number) {
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1);
  return `${m}:${s.padStart(4, "0")}`;
}

function correctionLabel(c: CorrectionInfo | null | undefined): string | null {
  if (!c) return null;
  if (c.applied && c.model) return `雲端校稿：${c.model}`;
  if (c.applied) return "雲端校稿：已套用（模型未記錄）";
  return "雲端校稿：未套用";
}

export default function TimelinePage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const [segments, setSegments] = useState<TimelineSegment[]>([]);
  const [caption, setCaption] = useState("none");
  const [canRetranscribe, setCanRetranscribe] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [mediaType, setMediaType] = useState<"video" | "audio">("video");
  const [diffs, setDiffs] = useState<DiffItem[]>([]);
  const [showDiff, setShowDiff] = useState(false);
  const [correction, setCorrection] = useState<CorrectionInfo | null>(null);

  async function load() {
    const r = await api.timeline(id);
    setSegments(r.segments);
    setCaption(r.caption_source);
    setCanRetranscribe(r.can_retranscribe_locally || r.caption_source === "auto");
    setCorrection(r.correction ?? null);
    const st = await api.status(id);
    setMediaType(st.source_type.includes("audio") ? "audio" : "video");
    if (!r.correction && st.correction) setCorrection(st.correction);
    try {
      setDiffs((await api.diff(id)).items);
    } catch {
      setDiffs([]);
    }
  }

  useEffect(() => {
    load().catch((e) => setError(String(e)));
  }, [id]);

  function seekTo(t: number) {
    const el = mediaRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, t);
    void el.play();
  }

  async function saveSegment(seg: TimelineSegment) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.patchSegment(id, seg.id, draft);
      setSegments((prev) => prev.map((s) => (s.id === seg.id ? updated : s)));
      setEditing(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onRebuild() {
    setBusy(true);
    try {
      await api.rebuildTimeline(id);
      nav(`/progress/${id}`);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  async function onRetranscribe() {
    if (!confirm("將丟棄 YouTube 字幕，改以本地 WhisperX 重新轉錄？")) return;
    setBusy(true);
    try {
      await api.retranscribe(id);
      nav(`/progress/${id}`);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  async function onDelete() {
    if (
      !confirm(
        "確定要永久刪除此項目？\n\n會硬刪除媒體檔、逐字稿、摘要與相關跨檔彙整，無法復原。",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.deleteVideo(id);
      nav("/library");
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>時間軸</h1>
      <p className="muted">
        caption_source={caption} · <Link to={`/summary/${id}`}>摘要</Link> ·{" "}
        <Link to="/library">項目庫</Link>
      </p>
      {correctionLabel(correction) && (
        <p className={`correction-badge ${correction?.applied ? "applied" : "skipped"}`}>
          {correctionLabel(correction)}
        </p>
      )}

      <div className="panel">
        {mediaType === "audio" ? (
          <audio
            className="media-player audio"
            ref={(el) => {
              mediaRef.current = el;
            }}
            controls
            src={api.mediaUrl(id)}
          />
        ) : (
          <video
            className="media-player"
            ref={(el) => {
              mediaRef.current = el;
            }}
            controls
            src={api.mediaUrl(id)}
          />
        )}
        <div className="row" style={{ marginTop: "0.75rem" }}>
          <button type="button" className="secondary" disabled={busy} onClick={onRebuild}>
            重新雲端校稿合併
          </button>
          {canRetranscribe && (
            <button type="button" className="secondary" disabled={busy} onClick={onRetranscribe}>
              改用本地 WhisperX 轉錄
            </button>
          )}
          <button
            type="button"
            className="secondary"
            onClick={() => setShowDiff((v) => !v)}
            disabled={diffs.length === 0}
          >
            {showDiff ? "隱藏校正 Diff" : `校正 Diff（${diffs.length}）`}
          </button>
          <a className="secondary" href={api.exportUrl(id, "md")}>
            匯出 MD
          </a>
          <a className="secondary" href={api.exportUrl(id, "docx")}>
            匯出 DOCX
          </a>
          <a className="secondary" href={api.exportUrl(id, "pdf")}>
            匯出 PDF
          </a>
          <button type="button" className="danger" disabled={busy} onClick={onDelete}>
            刪除項目
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {showDiff && (
        <div className="panel">
          <h2>校正前後 Diff</h2>
          {diffs.map((d, i) => (
            <div key={i} className="segment">
              <div className="seg-meta">
                {fmt(d.start)} · {d.type}
              </div>
              <div className="muted">前：{d.before || "（空）"}</div>
              <div>後：{d.after || "（空）"}</div>
            </div>
          ))}
        </div>
      )}

      <div className="panel">
        {segments.map((seg) => (
          <div key={seg.id} className={`segment ${seg.type}`}>
            <div className="seg-meta">
              <button type="button" className="secondary" onClick={() => seekTo(seg.start)}>
                {seg.type === "speech"
                  ? `${fmt(seg.start)} – ${fmt(seg.end)}`
                  : fmt(seg.start)}
              </button>{" "}
              · {seg.type}
              {seg.speaker ? ` · ${seg.speaker}` : ""}
              {seg.edited ? " · edited" : ""}
            </div>
            {editing === seg.id ? (
              <div className="segment-edit">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  rows={Math.max(5, Math.ceil(draft.length / 48))}
                />
                <div className="row">
                  <button type="button" disabled={busy} onClick={() => saveSegment(seg)}>
                    儲存
                  </button>
                  <button type="button" className="secondary" onClick={() => setEditing(null)}>
                    取消
                  </button>
                </div>
              </div>
            ) : (
              <div
                className="segment-text"
                onDoubleClick={() => {
                  setEditing(seg.id);
                  setDraft(seg.text);
                }}
                title="雙擊編輯"
              >
                {seg.text}
              </div>
            )}
            {seg.type === "frame" && seg.frame_path && (
              <img
                className="frame-thumb"
                src={api.frameUrl(id, seg.frame_path)}
                alt={seg.text.slice(0, 40)}
                onClick={() => seekTo(seg.start)}
              />
            )}
          </div>
        ))}
        {segments.length === 0 && !error && <p className="muted">尚無段落</p>}
      </div>
    </div>
  );
}
