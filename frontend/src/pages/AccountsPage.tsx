import { FormEvent, useEffect, useState } from "react";
import { api, AccountItem } from "../api";

export default function AccountsPage() {
  const [items, setItems] = useState<AccountItem[]>([]);
  const [label, setLabel] = useState("個人 YouTube");
  const [cookies, setCookies] = useState("");
  const [driveLabel, setDriveLabel] = useState("工作用 Drive");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function refresh() {
    setItems(await api.listAccounts());
  }

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
    const q = new URLSearchParams(window.location.search);
    if (q.get("google") === "ok") setMsg("Google Drive 授權成功");
  }, []);

  async function onAddYoutube(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createYoutubeAccount(label, cookies);
      setCookies("");
      setMsg("已新增 YouTube cookies（加密存放於本機）");
      await refresh();
    } catch (err) {
      setError(String(err));
    }
  }

  async function onGoogle() {
    setError(null);
    try {
      const { url } = await api.googleAuthUrl(driveLabel);
      window.location.href = url;
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <div>
      <h1>帳號管理</h1>
      <p className="muted">
        憑證僅加密存放於本機 SQLite。離開本機的只有校稿／摘要文字（不含 cookies / token）。
      </p>
      {msg && <p className="muted">{msg}</p>}
      {error && <p className="error">{error}</p>}

      <ul className="list panel">
        {items.map((a) => (
          <li key={a.id}>
            <div>
              <strong>{a.account_label}</strong>
              <div className="muted">
                {a.account_type} · {a.status}
                {a.last_verified_at ? ` · 驗證於 ${a.last_verified_at}` : ""}
              </div>
            </div>
            <div className="row">
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  try {
                    await api.verifyAccount(a.id);
                    setMsg("驗證成功");
                    await refresh();
                  } catch (e) {
                    setError(String(e));
                    await refresh();
                  }
                }}
              >
                驗證
              </button>
              <button
                type="button"
                className="secondary"
                onClick={async () => {
                  if (!confirm("確定刪除？")) return;
                  await api.deleteAccount(a.id);
                  await refresh();
                }}
              >
                刪除
              </button>
            </div>
          </li>
        ))}
        {items.length === 0 && <li className="muted">尚無帳號</li>}
      </ul>

      <form className="panel" onSubmit={onAddYoutube}>
        <h2>新增 YouTube cookies</h2>
        <p className="muted">
          建議：無痕登入 → 開 youtube.com/robots.txt → 匯出 cookies.txt → 立刻關閉視窗。
        </p>
        <label>
          代稱
          <input value={label} onChange={(e) => setLabel(e.target.value)} required />
        </label>
        <label>
          cookies.txt 內容
          <textarea value={cookies} onChange={(e) => setCookies(e.target.value)} required />
        </label>
        <button type="submit">匯入並加密儲存</button>
      </form>

      <div className="panel">
        <h2>Google Drive OAuth</h2>
        <label>
          代稱
          <input value={driveLabel} onChange={(e) => setDriveLabel(e.target.value)} />
        </label>
        <button type="button" onClick={onGoogle}>
          授權 Google Drive（唯讀）
        </button>
        <p className="muted">需在 .env 設定 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET。</p>
      </div>
    </div>
  );
}
