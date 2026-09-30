import { useEffect } from "react";
import { ExistingMatch } from "../api";

const STATUS_LABELS: Record<string, string> = {
  pending: "排隊中",
  fetching_source: "抓取來源",
  extracting: "擷取中",
  transcribing: "轉錄中",
  describing: "畫格描述",
  merging: "合併中",
  ready: "已完成",
  failed: "失敗",
};

const REASON_LABELS: Record<ExistingMatch["match_reason"], string> = {
  youtube_id: "相同 YouTube 影片",
  drive_id: "相同 Google Drive 檔案",
  url: "相同網址",
  filename: "相同檔名",
};

export function formatAddedAt(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function hrefForExisting(match: ExistingMatch): string {
  return match.status === "ready" ? `/timeline/${match.id}` : `/progress/${match.id}`;
}

export function newestMatch(matches: ExistingMatch[]): ExistingMatch | undefined {
  return [...matches].sort((a, b) =>
    (b.upload_time || "").localeCompare(a.upload_time || ""),
  )[0];
}

function reasonText(match: ExistingMatch): string {
  if (match.match_reason === "filename" && match.size_matched) {
    return "相同檔名與大小";
  }
  return REASON_LABELS[match.match_reason];
}

type Props = {
  matches: ExistingMatch[];
  itemCount: number;
  newCount: number;
  blocking?: boolean;
  busy?: boolean;
  onJump: (match: ExistingMatch) => void;
  onAddAgain: () => void;
  onAddNewOnly: () => void;
  onCancel: () => void;
};

export default function DuplicateDialog({
  matches,
  itemCount,
  newCount,
  blocking = false,
  busy = false,
  onJump,
  onAddAgain,
  onAddNewOnly,
  onCancel,
}: Props) {
  const batch = itemCount > 1;
  const newest = newestMatch(matches);

  useEffect(() => {
    if (!blocking) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [blocking, onCancel]);

  const body = (
    <div
      className={blocking ? "modal panel dup-dialog" : "dup-dialog dup-inline"}
      role="dialog"
      aria-modal={blocking}
      aria-labelledby="dup-title"
      onClick={blocking ? (e) => e.stopPropagation() : undefined}
    >
      <h2 id="dup-title">這個媒體似乎已經加入過</h2>
      <p className="muted">
        {batch
          ? "部分檔案已在項目庫裡。要跳轉到既有項目，只加入還沒有的檔案，還是全部再加入一次？"
          : "項目庫已有相同來源。要跳轉過去，還是真的再加入一次？"}
      </p>
      <ul className="dup-list">
        {matches.map((m) => {
          const added = formatAddedAt(m.upload_time);
          return (
            <li key={`${m.item_index}-${m.id}`}>
              <div className="item-title">{m.filename || "未命名"}</div>
              <div className="muted">
                {reasonText(m)}
                {added ? ` · 加入於 ${added}` : ""}
                {` · ${STATUS_LABELS[m.status] || m.status}`}
                {m.status !== "ready" && m.status !== "failed" ? ` ${m.progress}%` : ""}
              </div>
              {(batch || matches.length > 1) && (
                <button
                  type="button"
                  className="plain-link"
                  disabled={busy}
                  onClick={() => onJump(m)}
                >
                  跳轉到這筆
                </button>
              )}
            </li>
          );
        })}
      </ul>
      <div className="dup-actions">
        {newest && (
          <button type="button" disabled={busy} onClick={() => onJump(newest)}>
            {matches.length > 1 ? "跳轉到最新一筆" : "跳轉過去"}
          </button>
        )}
        {batch && newCount > 0 && (
          <button type="button" className="secondary" disabled={busy} onClick={onAddNewOnly}>
            略過已有、只加入新檔（{newCount}）
          </button>
        )}
        <button type="button" className="secondary" disabled={busy} onClick={onAddAgain}>
          {batch ? "全部再加入一次" : "再加入一次"}
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
          {blocking ? "取消" : "稍後決定"}
        </button>
      </div>
    </div>
  );

  if (!blocking) return body;
  return (
    <div className="modal-overlay" onClick={onCancel} role="presentation">
      {body}
    </div>
  );
}
