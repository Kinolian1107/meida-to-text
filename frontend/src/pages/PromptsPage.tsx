import { FormEvent, useEffect, useState } from "react";
import { api, PromptTemplate } from "../api";

export default function PromptsPage() {
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function load() {
    setItems(await api.prompts());
  }

  useEffect(() => {
    load().catch((e) => setError(String(e)));
  }, []);

  function edit(t: PromptTemplate) {
    setId(t.id);
    setName(t.name);
    setContent(t.content);
    setMsg(null);
  }

  async function onSave(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.savePrompt(id.trim(), name.trim() || id.trim(), content);
      setMsg("已儲存");
      await load();
    } catch (err) {
      setError(String(err));
    }
  }

  async function onDelete(tid: string) {
    if (!confirm(`刪除自訂模板 ${tid}？`)) return;
    try {
      await api.deletePrompt(tid);
      await load();
      if (id === tid) {
        setId("");
        setName("");
        setContent("");
      }
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <div>
      <h1>Prompt 模板庫</h1>
      <p className="muted">管理摘要 Prompt；內建模板可覆蓋，自訂模板可刪除。</p>
      {error && <p className="error">{error}</p>}
      {msg && <p className="muted">{msg}</p>}

      <div className="panel">
        <ul className="list">
          {items.map((t) => (
            <li key={t.id}>
              <div>
                <strong>{t.name}</strong>
                <div className="muted">
                  {t.id} {t.builtin ? "· builtin" : "· custom"}
                </div>
              </div>
              <div>
                <button type="button" className="secondary" onClick={() => edit(t)}>
                  編輯
                </button>{" "}
                {!t.builtin && (
                  <button type="button" className="secondary" onClick={() => onDelete(t.id)}>
                    刪除
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>

      <form className="panel" onSubmit={onSave}>
        <div className="row">
          <label>
            ID
            <input value={id} onChange={(e) => setId(e.target.value)} required />
          </label>
          <label>
            名稱
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        </div>
        <label>
          內容
          <textarea value={content} onChange={(e) => setContent(e.target.value)} required rows={12} />
        </label>
        <button type="submit">儲存模板</button>
      </form>
    </div>
  );
}
