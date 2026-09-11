import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  CorrectionInfo,
  DiffItem,
  TimelineSegment,
  TranslationState,
} from "../api";
import VideoPlayer, { SubtitleMode } from "../components/VideoPlayer";

const SUBTITLE_MODES: { value: SubtitleMode; label: string }[] = [
  { value: "zh", label: "繁體中文" },
  { value: "both", label: "中英對照" },
  { value: "en", label: "原文" },
  { value: "off", label: "關閉" },
];

const SUBTITLE_SIZES: { value: string; label: string; scale: number }[] = [
  { value: "xs", label: "極小", scale: 0.7 },
  { value: "s", label: "小", scale: 0.85 },
  { value: "m", label: "中", scale: 1 },
  { value: "l", label: "大", scale: 1.2 },
  { value: "xl", label: "特大", scale: 1.45 },
];

const SUBTITLE_MODE_KEY = "media2text.subtitleMode";
const SUBTITLE_SIZE_KEY = "media2text.subtitleSize";
const TRANSLATION_POLL_MS = 2500;

function fmt(t: number) {
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1);
  return `${m}:${s.padStart(4, "0")}`;
}

function readStoredMode(): SubtitleMode {
  const stored = localStorage.getItem(SUBTITLE_MODE_KEY);
  return SUBTITLE_MODES.some((m) => m.value === stored)
    ? (stored as SubtitleMode)
    : "zh";
}

function readStoredSize(): string {
  const stored = localStorage.getItem(SUBTITLE_SIZE_KEY);
  return SUBTITLE_SIZES.some((s) => s.value === stored) ? (stored as string) : "m";
}

function subtitleScaleOf(size: string): number {
  return SUBTITLE_SIZES.find((s) => s.value === size)?.scale ?? 1;
}

function translationLabel(t: TranslationState | null): string {
  if (!t || t.total === 0) return "";
  if (t.status === "running") return `翻譯中… ${t.translated}/${t.total}`;
  if (t.status === "failed") return `翻譯失敗：${t.error ?? "未知錯誤"}`;
  if (t.status === "done") {
    const partial = t.error ? ` · ${t.error}` : "";
    return `已翻譯 ${t.translated}/${t.total}${t.model ? ` · ${t.model}` : ""}${partial}`;
  }
  return `尚未翻譯（${t.total} 句）`;
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
  const [translation, setTranslation] = useState<TranslationState | null>(null);
  const [subtitleMode, setSubtitleMode] = useState<SubtitleMode>(readStoredMode);
  const [subtitleSize, setSubtitleSize] = useState<string>(readStoredSize);

  async function load() {
    const r = await api.timeline(id);
    setSegments(r.segments);
    setCaption(r.caption_source);
    setCanRetranscribe(r.can_retranscribe_locally || r.caption_source === "auto");
    setCorrection(r.correction ?? null);
    setTranslation(r.translation ?? null);
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

  useEffect(() => {
    localStorage.setItem(SUBTITLE_MODE_KEY, subtitleMode);
  }, [subtitleMode]);

  useEffect(() => {
    localStorage.setItem(SUBTITLE_SIZE_KEY, subtitleSize);
  }, [subtitleSize]);

  useEffect(() => {
    if (translation?.status !== "running") return;
    const timer = window.setInterval(async () => {
      try {
        const st = await api.translationStatus(id);
        setTranslation(st);
        if (st.status !== "running") {
          window.clearInterval(timer);
          await load();
        }
      } catch {
        // transient poll failure: keep the interval and try again
      }
    }, TRANSLATION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [translation?.status, id]);

  async function onTranslate() {
    setError(null);
    try {
      setTranslation(await api.translateSubtitles(id));
    } catch (e) {
      setError(String(e));
    }
  }

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
          <VideoPlayer
            src={api.mediaUrl(id)}
            segments={segments}
            subtitleMode={subtitleMode}
            subtitleScale={subtitleScaleOf(subtitleSize)}
            onMediaElement={(el) => {
              mediaRef.current = el;
            }}
          />
        )}
        {mediaType === "video" && (
          <div className="row subtitle-bar">
            <label htmlFor="subtitle-mode">字幕</label>
            <select
              id="subtitle-mode"
              value={subtitleMode}
              onChange={(e) => setSubtitleMode(e.target.value as SubtitleMode)}
            >
              {SUBTITLE_MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
            <label htmlFor="subtitle-size">字級</label>
            <select
              id="subtitle-size"
              value={subtitleSize}
              onChange={(e) => setSubtitleSize(e.target.value)}
              disabled={subtitleMode === "off"}
            >
              {SUBTITLE_SIZES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="secondary"
              disabled={
                busy ||
                translation?.status === "running" ||
                (translation?.total ?? 0) === 0
              }
              onClick={onTranslate}
            >
              {translation && translation.translated > 0
                ? "重新翻譯字幕"
                : "翻譯字幕（繁中）"}
            </button>
            {subtitleMode !== "off" && (
              <a
                className="secondary"
                href={api.subtitlesUrl(id, subtitleMode)}
                download={`${id}.${subtitleMode}.vtt`}
              >
                下載 VTT
              </a>
            )}
            <span className="muted">{translationLabel(translation)}</span>
          </div>
        )}
        {mediaType === "video" && (
          <p className="muted vp-hints">
            快捷鍵：← / → 前後 1 秒 · Shift + ← / → 5 秒 · Ctrl + ← / → 30 秒 ·
            Space 播放暫停 · F 全螢幕 · M 靜音（先點一下播放器）
          </p>
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
          <a
            className="secondary"
            href={api.subtitlesSrtUrl(
              id,
              mediaType === "audio" || subtitleMode === "off" ? "zh" : subtitleMode,
            )}
            download={`${id}.srt`}
          >
            匯出 SRT
          </a>
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
              <>
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
                {seg.text_zh && <div className="segment-text zh">{seg.text_zh}</div>}
              </>
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
