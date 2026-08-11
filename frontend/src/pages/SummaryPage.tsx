import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, PromptTemplate, RelatedItem, SummaryItem } from "../api";

export default function SummaryPage() {
  const { id = "" } = useParams();
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [template, setTemplate] = useState("bullet_points");
  const [custom, setCustom] = useState("");
  const [saveName, setSaveName] = useState("");
  const [saveId, setSaveId] = useState("");
  const [items, setItems] = useState<SummaryItem[]>([]);
  const [related, setRelated] = useState<RelatedItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const list = await api.summaries(id);
    setItems(list);
    if (list[0]) {
      try {
        setRelated(await api.related(list[0].id));
      } catch {
        setRelated([]);
      }
    }
  }

  async function loadPrompts() {
    const p = await api.prompts();
    setPrompts(p);
    const cur = p.find((x) => x.id === template) || p.find((x) => x.id === "bullet_points") || p[0];
    if (cur) {
      setTemplate(cur.id);
      setCustom(cur.content);
      setSaveId(cur.id);
      setSaveName(cur.name);
    }
  }

  useEffect(() => {
    loadPrompts().catch((e) => setError(String(e)));
    refresh().catch((e) => setError(String(e)));
  }, [id]);

  async function onGenerate() {
    setBusy(true);
    setError(null);
    try {
      await api.summarize(id, template, custom);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveTemplate() {
    setBusy(true);
    setError(null);
    try {
      const tid = (saveId || template).trim();
      await api.savePrompt(tid, saveName || tid, custom);
      await loadPrompts();
      setTemplate(tid);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>摘要</h1>
      <p className="muted">
        <Link to={`/timeline/${id}`}>時間軸</Link> · <Link to="/library">項目庫</Link> ·{" "}
        <Link to="/cross">跨檔彙整</Link>
        {" · "}
        <a href={api.exportUrl(id, "md")}>匯出 MD</a>
        {" · "}
        <a href={api.exportUrl(id, "docx")}>DOCX</a>
        {" · "}
        <a href={api.exportUrl(id, "pdf")}>PDF</a>
      </p>

      <div className="panel">
        <label>
          Prompt 模板
          <select
            value={template}
            onChange={(e) => {
              const tid = e.target.value;
              setTemplate(tid);
              const p = prompts.find((x) => x.id === tid);
              if (p) {
                setCustom(p.content);
                setSaveId(p.id);
                setSaveName(p.name);
              }
            }}
          >
            {prompts.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.builtin === false ? "（自訂）" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Prompt（可編輯）
          <textarea value={custom} onChange={(e) => setCustom(e.target.value)} />
        </label>
        <div className="row">
          <label>
            儲存為模板 id
            <input value={saveId} onChange={(e) => setSaveId(e.target.value)} />
          </label>
          <label>
            顯示名稱
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)} />
          </label>
        </div>
        <div className="row">
          <button type="button" onClick={onGenerate} disabled={busy}>
            {busy ? "產生中…" : "產生摘要"}
          </button>
          <button type="button" className="secondary" onClick={onSaveTemplate} disabled={busy}>
            儲存 Prompt 模板
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </div>

      {related.length > 0 && (
        <div className="panel">
          <h2>相關項目建議</h2>
          <ul className="list">
            {related.map((r) => (
              <li key={r.summary_id}>
                <div>
                  <Link to={`/summary/${r.video_id}`}>{r.filename}</Link>
                  <div className="muted">
                    score={r.score.toFixed(3)} · {r.snippet}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <h2>歷史版本</h2>
      {items.map((s) => (
        <div className="panel" key={s.id}>
          <div className="muted">
            {s.prompt_template} · {s.created_at}
          </div>
          <div className="summary-body">{s.content}</div>
        </div>
      ))}
      {items.length === 0 && <p className="muted">尚無摘要</p>}
    </div>
  );
}
