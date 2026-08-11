import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, CrossAnalysisItem, SummaryItem, VideoListItem } from "../api";

type ReadyItem = {
  video: VideoListItem;
  summary: SummaryItem;
};

export default function CrossPage() {
  const [ready, setReady] = useState<ReadyItem[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [prompt, setPrompt] = useState("找出共同重點與衝突");
  const [history, setHistory] = useState<CrossAnalysisItem[]>([]);
  const [current, setCurrent] = useState<CrossAnalysisItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    const { items: videos } = await api.listVideos({ status: "ready", page_size: 100 });
    const items: ReadyItem[] = [];
    for (const v of videos) {
      const summaries = await api.summaries(v.id);
      if (summaries[0]) items.push({ video: v, summary: summaries[0] });
    }
    setReady(items);
    setHistory(await api.listCross());
  }

  useEffect(() => {
    load().catch((e) => setError(String(e)));
  }, []);

  function toggle(summaryId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(summaryId)) next.delete(summaryId);
      else next.add(summaryId);
      return next;
    });
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (selected.size < 2) {
      setError("請至少勾選 2 個已完成摘要的項目");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const row = await api.createCross([...selected], prompt);
      setCurrent(row);
      setHistory(await api.listCross());
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>跨檔案彙整</h1>
      <p className="muted">勾選多個已產生摘要的項目，比較共同重點與衝突。</p>
      {error && <p className="error">{error}</p>}

      <form className="panel" onSubmit={onSubmit}>
        <ul className="list">
          {ready.map(({ video, summary }) => (
            <li key={summary.id}>
              <label style={{ display: "flex", gap: "0.75rem", alignItems: "flex-start" }}>
                <input
                  type="checkbox"
                  checked={selected.has(summary.id)}
                  onChange={() => toggle(summary.id)}
                />
                <span>
                  <strong>{video.filename}</strong>
                  <div className="muted">{summary.content.slice(0, 120)}…</div>
                  <Link to={`/summary/${video.id}`}>查看摘要</Link>
                </span>
              </label>
            </li>
          ))}
          {ready.length === 0 && <li className="muted">尚無已完成摘要的項目</li>}
        </ul>
        <label>
          補充指令
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "分析中…" : "開始彙整"}
        </button>
      </form>

      {current && (
        <div className="panel">
          <h2>結果</h2>
          <h3>共同重點</h3>
          <ul>
            {current.result.common_points.map((p, i) => (
              <li key={i}>
                {p.text}
                {p.sources?.length ? (
                  <span className="muted"> — {p.sources.join("、")}</span>
                ) : null}
              </li>
            ))}
          </ul>
          <h3>衝突與差異</h3>
          <ul>
            {current.result.conflicts.map((p, i) => (
              <li key={i}>
                {p.text}
                {p.sources?.length ? (
                  <span className="muted"> — {p.sources.join("、")}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="panel">
        <h2>歷史紀錄</h2>
        <ul className="list">
          {history.map((h) => (
            <li key={h.id}>
              <button type="button" className="secondary" onClick={() => setCurrent(h)}>
                {h.created_at} · {h.source_summary_ids.length} 份摘要
              </button>
            </li>
          ))}
          {history.length === 0 && <li className="muted">尚無紀錄</li>}
        </ul>
      </div>
    </div>
  );
}
