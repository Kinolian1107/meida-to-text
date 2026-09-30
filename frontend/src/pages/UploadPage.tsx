import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AccountItem,
  api,
  ExistingLookupItem,
  ExistingMatch,
  YoutubeProbe,
} from "../api";
import DuplicateDialog, { hrefForExisting } from "../components/DuplicateDialog";

type Mode = "file" | "youtube" | "url" | "drive";

function lookupItems(mode: Mode, url: string, files: File[]): ExistingLookupItem[] {
  if (mode === "file") {
    return files.map((f) => ({
      source_type: "upload",
      filename: f.name,
      size: f.size,
    }));
  }
  const source_type =
    mode === "youtube" ? "youtube" : mode === "url" ? "direct_url" : "google_drive";
  return [{ source_type, url }];
}

function queryKey(items: ExistingLookupItem[]): string {
  return JSON.stringify(items);
}

export default function UploadPage() {
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("file");
  const [topic, setTopic] = useState("");
  const [hotwords, setHotwords] = useState("");
  const [url, setUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [probe, setProbe] = useState<YoutubeProbe | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [accountId, setAccountId] = useState("");
  const [matches, setMatches] = useState<ExistingMatch[] | null>(null);
  const [dismissedKey, setDismissedKey] = useState("");
  const [pendingSubmit, setPendingSubmit] = useState(false);

  const items = useMemo(() => lookupItems(mode, url, files), [mode, url, files]);
  const currentKey = queryKey(items);
  const keyRef = useRef(currentKey);
  const dismissedRef = useRef(dismissedKey);
  const busyRef = useRef(false);
  keyRef.current = currentKey;
  dismissedRef.current = dismissedKey;

  useEffect(() => {
    api.listAccounts().then(setAccounts).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (dismissedKey === currentKey) return;
    if (!items.length || (mode !== "file" && url.trim().length < 8)) {
      setMatches(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void api
        .lookupExisting(items)
        .then((res) => {
          if (cancelled || keyRef.current !== currentKey) return;
          setMatches(res.matches.length ? res.matches : null);
        })
        .catch(() => {
          if (!cancelled) setMatches(null);
        });
    }, 450);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [currentKey, dismissedKey, items, mode, url]);

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

  async function createMedia(selectedFiles: File[], sourceUrl: string) {
    let id: string;
    if (mode === "file") {
      if (selectedFiles.length > 1) {
        const { ids } = await api.uploadBatch(selectedFiles, topic, hotwords);
        nav(`/progress/${ids[0]}`);
        return;
      }
      if (selectedFiles.length === 0) throw new Error("請選擇檔案");
      id = (await api.upload(selectedFiles[0], topic, hotwords)).id;
    } else if (mode === "youtube") {
      id = (await api.fromYoutube(sourceUrl, topic, hotwords, accountId || undefined)).id;
    } else if (mode === "url") {
      id = (await api.fromUrl(sourceUrl, topic, hotwords)).id;
    } else {
      id = (await api.fromDrive(sourceUrl, topic, hotwords, accountId || undefined)).id;
    }
    nav(`/progress/${id}`);
  }

  async function submit(opts: { force?: boolean; skipIndexes?: number[] } = {}) {
    if (busyRef.current) return;
    if (pendingSubmit && !opts.force) return;
    const startedKey = currentKey;
    const startedFiles = files;
    const startedUrl = url;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      if (!opts.force && dismissedRef.current !== startedKey) {
        const found = (await api.lookupExisting(items)).matches;
        if (keyRef.current !== startedKey) return;
        if (found.length) {
          setMatches(found);
          setPendingSubmit(true);
          return;
        }
      }
      if (keyRef.current !== startedKey) return;
      const skip = new Set(opts.skipIndexes ?? []);
      const selected = startedFiles.filter((_, i) => !skip.has(i));
      await createMedia(selected, startedUrl);
      setPendingSubmit(false);
    } catch (err) {
      setError(String(err));
      setPendingSubmit(false);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await submit();
  }

  function keepNewFilesOnly() {
    const skip = new Set((matches ?? []).map((m) => m.item_index));
    const next = files.filter((_, i) => !skip.has(i));
    setFiles(next);
    setDismissedKey(queryKey(lookupItems(mode, url, next)));
    setMatches(null);
    return { next, skip: [...skip] };
  }

  function rememberAddAnyway() {
    setDismissedKey(currentKey);
    setMatches(null);
  }

  const matchedIndexes = new Set((matches ?? []).map((m) => m.item_index));
  const newCount = files.filter((_, i) => !matchedIndexes.has(i)).length;
  const formLocked = busy || pendingSubmit;

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
            disabled={formLocked}
            onClick={() => {
              setMode(k);
              setProbe(null);
              setError(null);
              setAccountId("");
              setMatches(null);
              setDismissedKey("");
              setPendingSubmit(false);
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
              disabled={formLocked}
              onChange={(e) => {
                const list = Array.from(e.target.files || []);
                setFiles(list);
                setMatches(null);
                setDismissedKey("");
                setPendingSubmit(false);
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
              <input
                value={url}
                disabled={formLocked}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setMatches(null);
                  setDismissedKey("");
                  setPendingSubmit(false);
                }}
                required
              />
            </label>
            {mode === "youtube" && (
              <div style={{ alignSelf: "end" }}>
                <button type="button" className="secondary" disabled={formLocked} onClick={onProbe}>
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
            <select
              value={accountId}
              disabled={formLocked}
              onChange={(e) => setAccountId(e.target.value)}
            >
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
            <input value={topic} disabled={busy} onChange={(e) => setTopic(e.target.value)} />
          </label>
          <label>
            專有名詞（hotwords，逗號或換行）
            <input value={hotwords} disabled={busy} onChange={(e) => setHotwords(e.target.value)} />
          </label>
        </div>

        {error && <p className="error">{error}</p>}

        {matches && matches.length > 0 && !pendingSubmit && (
          <DuplicateDialog
            matches={matches}
            itemCount={items.length}
            newCount={newCount}
            busy={busy}
            onJump={(m) => nav(hrefForExisting(m))}
            onAddAgain={rememberAddAnyway}
            onAddNewOnly={() => {
              keepNewFilesOnly();
            }}
            onCancel={() => setMatches(null)}
          />
        )}

        <button type="submit" disabled={formLocked}>
          {busy ? "提交中…" : "開始處理"}
        </button>
      </form>

      {matches && matches.length > 0 && pendingSubmit && (
        <DuplicateDialog
          matches={matches}
          itemCount={items.length}
          newCount={newCount}
          blocking
          busy={busy}
          onJump={(m) => nav(hrefForExisting(m))}
          onAddAgain={() => {
            rememberAddAnyway();
            void submit({ force: true });
          }}
          onAddNewOnly={() => {
            const { skip } = keepNewFilesOnly();
            void submit({ force: true, skipIndexes: skip });
          }}
          onCancel={() => {
            setMatches(null);
            setPendingSubmit(false);
          }}
        />
      )}
    </div>
  );
}
