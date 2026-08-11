import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AccountItem, api, YoutubeProbe } from "../api";

type Mode = "file" | "youtube" | "url" | "drive";

export default function UploadPage() {
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("file");
  const [topic, setTopic] = useState("");
  const [hotwords, setHotwords] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [probe, setProbe] = useState<YoutubeProbe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [accountId, setAccountId] = useState("");

  useEffect(() => {
    api.listAccounts().then(setAccounts).catch(() => undefined);
  }, []);

  const filteredAccounts = accounts.filter((a) =>
    mode === "youtube"
      ? a.account_type === "youtube_cookie"
      : mode === "drive"
        ? a.account_type === "google_drive_oauth"
        : false,
  );

  async function onProbe() {
    setError(null);
    setProbe(null);
    try {
      setProbe(await api.probeYoutube(url));
    } catch (e) {
      setError(String(e));
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let id: string;
      if (mode === "file") {
        if (files.length > 1) {
          const { ids } = await api.uploadBatch(files, topic, hotwords);
          nav(`/progress/${ids[0]}`);
          return;
        }
        if (!file && files.length === 0) throw new Error("請選擇檔案");
        id = (await api.upload(file || files[0], topic, hotwords)).id;
      } else if (mode === "youtube") {
        id = (await api.fromYoutube(url, topic, hotwords, accountId || undefined)).id;
      } else if (mode === "url") {
        id = (await api.fromUrl(url, topic, hotwords)).id;
      } else {
        id = (await api.fromDrive(url, topic, hotwords, accountId || undefined)).id;
      }
      nav(`/progress/${id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1>新增媒體</h1>
      <p className="muted">上傳影片／音訊，或貼上 YouTube、直連 URL、Google Drive 連結。</p>

      <div className="tabs">
        {(
          [
            ["file", "上傳檔案"],
            ["youtube", "YouTube"],
            ["url", "直連 URL"],
            ["drive", "Google Drive"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            className={mode === k ? "active" : "secondary"}
            onClick={() => {
              setMode(k);
              setProbe(null);
              setError(null);
              setAccountId("");
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <form className="panel" onSubmit={onSubmit}>
        {mode === "file" ? (
          <label>
            檔案（可多選批次上傳）
            <input
              type="file"
              accept="video/*,audio/*"
              multiple
              onChange={(e) => {
                const list = Array.from(e.target.files || []);
                setFiles(list);
                setFile(list[0] ?? null);
              }}
            />
          </label>
        ) : (
          <div className="row">
            <label>
              {mode === "youtube"
                ? "YouTube URL"
                : mode === "url"
                  ? "媒體直連 URL"
                  : "Google Drive 連結"}
              <input value={url} onChange={(e) => setUrl(e.target.value)} required />
            </label>
            {mode === "youtube" && (
              <div style={{ alignSelf: "end" }}>
                <button type="button" className="secondary" onClick={onProbe}>
                  查詢字幕
                </button>
              </div>
            )}
          </div>
        )}

        {probe && (
          <p className="muted">
            {probe.title ? `《${probe.title}》` : ""} {probe.message}
          </p>
        )}

        {(mode === "youtube" || mode === "drive") && (
          <label>
            使用帳號（可選）
            <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              <option value="">不使用／公開存取</option>
              {filteredAccounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.account_label} ({a.status})
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="row">
          <label>
            主題（topic / initial_prompt）
            <input value={topic} onChange={(e) => setTopic(e.target.value)} />
          </label>
          <label>
            專有名詞（hotwords，逗號或換行）
            <input value={hotwords} onChange={(e) => setHotwords(e.target.value)} />
          </label>
        </div>

        {error && <p className="error">{error}</p>}

        <button type="submit" disabled={busy}>
          {busy ? "提交中…" : "開始處理"}
        </button>
      </form>
    </div>
  );
}
