import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, TagKind, VideoListItem } from "../api";
import Pagination from "../components/Pagination";
import TagChips from "../components/TagChips";
import TagFilter from "../components/TagFilter";

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

export default function LibraryPage() {
  const [items, setItems] = useState<VideoListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"keyword" | "semantic">("keyword");
  const [tagIds, setTagIds] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Guards against out-of-order responses: switching search mode/query fires
  // overlapping requests (e.g. a fast empty-query fetch racing a slower
  // semantic-search one), and whichever resolves last must win, not
  // whichever was requested last.
  const requestIdRef = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    setPage(1);
  }, [filter, debouncedQuery, searchMode, tagIds]);

  async function refresh() {
    const requestId = ++requestIdRef.current;
    const res = await api.listVideos({
      status: filter || undefined,
      q: debouncedQuery || undefined,
      search_mode: searchMode,
      tagIds,
      page,
      page_size: PAGE_SIZE,
    });
    if (requestId !== requestIdRef.current) return;
    setItems(res.items);
    setTotal(res.total);
  }

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, [filter, debouncedQuery, searchMode, tagIds, page]);

  async function onDelete(v: VideoListItem) {
    const ok = confirm(
      `確定要永久刪除「${v.filename}」？\n\n會硬刪除媒體檔、逐字稿、摘要與相關跨檔彙整，無法復原。`,
    );
    if (!ok) return;
    setBusyId(v.id);
    setError(null);
    try {
      await api.deleteVideo(v.id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function onRetry(v: VideoListItem) {
    setBusyId(v.id);
    setError(null);
    try {
      await api.resume(v.id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function onAddTag(videoId: string, name: string, kind: TagKind) {
    try {
      await api.addVideoTag(videoId, name, kind);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  async function onRemoveTag(videoId: string, tagId: string) {
    try {
      await api.removeVideoTag(videoId, tagId);
      await refresh();
    } catch (e) {
      setError(String(e));
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
        <label>
          搜尋
          <input
            type="text"
            placeholder={searchMode === "semantic" ? "模糊搜尋摘要內容…" : "搜尋標題或摘要…"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          搜尋模式
          <select
            value={searchMode}
            onChange={(e) => setSearchMode(e.target.value as "keyword" | "semantic")}
          >
            <option value="keyword">關鍵字</option>
            <option value="semantic">語意模糊</option>
          </select>
        </label>
      </div>
      <TagFilter selected={tagIds} onChange={setTagIds} />
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
              {v.error_code && <div className="error">{v.error_code}</div>}
              <TagChips
                tags={v.tags}
                editable
                onAdd={(name, kind) => onAddTag(v.id, name, kind)}
                onRemove={(tagId) => onRemoveTag(v.id, tagId)}
              />
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
              {v.status === "failed" && (
                <>
                  {" · "}
                  <button
                    type="button"
                    className="plain-link"
                    disabled={busyId === v.id}
                    onClick={() => onRetry(v)}
                  >
                    {busyId === v.id ? "重試中…" : "重試"}
                  </button>
                </>
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
      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
    </div>
  );
}
