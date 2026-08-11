import { useState } from "react";
import { TagItem, TagKind } from "../api";

const KIND_LABELS: Record<TagKind, string> = {
  speaker: "講者",
  show: "節目",
  channel: "頻道",
  topic: "主題",
};

export default function TagChips({
  tags,
  editable = false,
  onAdd,
  onRemove,
}: {
  tags: TagItem[];
  editable?: boolean;
  onAdd?: (name: string, kind: TagKind) => Promise<void> | void;
  onRemove?: (tagId: string) => Promise<void> | void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<TagKind>("topic");
  const [busy, setBusy] = useState(false);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed || !onAdd) return;
    setBusy(true);
    try {
      await onAdd(trimmed, kind);
      setName("");
      setAdding(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tag-chips">
      {tags.map((t) => (
        <span key={t.id} className={`tag-chip tag-chip-${t.kind}`} title={KIND_LABELS[t.kind]}>
          {t.name}
          {editable && onRemove && (
            <button
              type="button"
              className="tag-chip-remove"
              aria-label={`移除標籤 ${t.name}`}
              onClick={() => onRemove(t.id)}
            >
              ×
            </button>
          )}
        </span>
      ))}
      {editable && onAdd && !adding && (
        <button type="button" className="tag-chip-add" onClick={() => setAdding(true)}>
          + 標籤
        </button>
      )}
      {editable && onAdd && adding && (
        <span className="tag-chip-form">
          <select value={kind} onChange={(e) => setKind(e.target.value as TagKind)}>
            {Object.entries(KIND_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <input
            autoFocus
            value={name}
            placeholder="標籤名稱"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
              if (e.key === "Escape") setAdding(false);
            }}
          />
          <button type="button" disabled={busy || !name.trim()} onClick={submit}>
            加入
          </button>
          <button type="button" className="secondary" onClick={() => setAdding(false)}>
            取消
          </button>
        </span>
      )}
    </div>
  );
}
