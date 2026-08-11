import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, VideoListItem } from "../api";

export default function LibraryPage() {
  const [items, setItems] = useState<VideoListItem[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function refresh() {
    setItems(await api.listVideos(filter || undefined));
  }

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, [filter]);

  async function onDelete(v: VideoListItem) {
    const ok = confirm(
      `確定要永久刪除「${v.filename}」？\n\n會硬刪除媒體檔、逐字稿、摘要與相關跨檔彙整，無法復原。`,
    );
    if (!ok) return;
    setBusyId(v.id);
    setError(null);
    try {
      await api.deleteVideo(v.id);
      setItems((prev) => prev.filter((x) => x.id !== v.id));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      <h1>項目庫</h1>
      <p className="muted">
        已完成項目可到 <Link to="/cross">跨檔彙整</Link> 多選比較。
      </p>
      <div className="row">
        <label>
          狀態篩選
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">全部</option>
            <option value="pending">排隊中</option>
            <option value="ready">完成</option>
            <option value="failed">失敗</option>
          </select>
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="list panel">
        {items.map((v) => (
          <li key={v.id}>
            <div className="item-info">
              <div className="item-title" title={v.filename}>
                {v.filename}
              </div>
              <div className="muted">
                {v.source_type} · {v.status} · {v.progress}%
                {v.duration_sec != null ? ` · ${Math.round(v.duration_sec)}s` : ""}
              </div>
              {v.error_code && (
                <div className="error">
                  {v.error_code}
                </div>
              )}
            </div>
            <div className="list-actions">
              {v.status === "ready" ? (
                <>
                  <Link to={`/timeline/${v.id}`}>時間軸</Link>
                  {" · "}
                  <Link to={`/summary/${v.id}`}>摘要</Link>
                </>
              ) : (
                <Link to={`/progress/${v.id}`}>進度</Link>
              )}
              {" · "}
              <button
                type="button"
                className="danger-link"
                disabled={busyId === v.id}
                onClick={() => onDelete(v)}
              >
                {busyId === v.id ? "刪除中…" : "刪除"}
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 && <li className="muted">尚無項目</li>}
      </ul>
    </div>
  );
}
