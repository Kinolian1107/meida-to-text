import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, PromptTemplate, SummaryItem } from "../api";

export default function SummaryPage() {
  const { id = "" } = useParams();
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [template, setTemplate] = useState("bullet_points");
  const [custom, setCustom] = useState("");
  const [saveName, setSaveName] = useState("");
  const [saveId, setSaveId] = useState("");
  const [items, setItems] = useState<SummaryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyItem, setHistoryItem] = useState<SummaryItem | null>(null);
  const [promptOpen, setPromptOpen] = useState(false);

  async function refresh() {
    const list = await api.summaries(id);
    setItems(list);
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

  useEffect(() => {
    if (!historyItem) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHistoryItem(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [historyItem]);

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
        <button
          type="button"
          className="disclosure"
          aria-expanded={promptOpen}
          onClick={() => setPromptOpen((v) => !v)}
        >
          <span className={`disclosure-arrow${promptOpen ? " open" : ""}`}>▶</span>
          Prompt 設定：{prompts.find((p) => p.id === template)?.name || template}
        </button>

        {promptOpen && (
          <>
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
          </>
        )}
        <div className="row">
          <button type="button" onClick={onGenerate} disabled={busy}>
            {busy ? "產生中…" : "產生摘要"}
          </button>
          {promptOpen && (
            <button type="button" className="secondary" onClick={onSaveTemplate} disabled={busy}>
              儲存 Prompt 模板
            </button>
          )}
        </div>
        {error && <p className="error">{error}</p>}
      </div>

      {items.length === 0 && <p className="muted">尚無摘要</p>}

      {items[0] && (
        <div className="panel">
          <div className="muted">
            {items[0].prompt_template} · {items[0].created_at}
          </div>
          <div className="summary-body markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{items[0].content}</ReactMarkdown>
          </div>
        </div>
      )}

      {items.length > 1 && (
        <>
          <h2>歷史版本</h2>
          <ul className="list">
            {items.slice(1, 3).map((s) => (
              <li key={s.id}>
                <button type="button" className="secondary" onClick={() => setHistoryItem(s)}>
                  {s.prompt_template} · {s.created_at}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {historyItem && (
        <div className="modal-overlay" onClick={() => setHistoryItem(null)}>
          <div className="modal panel" onClick={(e) => e.stopPropagation()}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
              <div className="muted">
                {historyItem.prompt_template} · {historyItem.created_at}
              </div>
              <button type="button" className="secondary" onClick={() => setHistoryItem(null)}>
                關閉
              </button>
            </div>
            <div className="summary-body markdown-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{historyItem.content}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
