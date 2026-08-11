import { useEffect, useState } from "react";
import { api, TagItem, TagKind } from "../api";

const KIND_LABELS: Record<TagKind, string> = {
  show: "節目",
  speaker: "講者",
  channel: "頻道",
  topic: "主題",
};
const KIND_ORDER: TagKind[] = ["show", "speaker", "channel", "topic"];

export default function TagFilter({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (tagIds: string[]) => void;
}) {
  const [tags, setTags] = useState<TagItem[]>([]);

  useEffect(() => {
    api.listTags().then(setTags).catch(() => setTags([]));
  }, []);

  if (tags.length === 0) return null;

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  return (
    <div className="tag-filter">
      {KIND_ORDER.map((kind) => {
        const inKind = tags.filter((t) => t.kind === kind);
        if (inKind.length === 0) return null;
        return (
          <div key={kind} className="tag-filter-group">
            <span className="tag-filter-label">{KIND_LABELS[kind]}</span>
            {inKind.map((t) => (
              <button
                type="button"
                key={t.id}
                className={`tag-chip tag-chip-${t.kind}${selected.includes(t.id) ? " selected" : ""}`}
                onClick={() => toggle(t.id)}
              >
                {t.name}
                {t.count != null && <span className="muted"> {t.count}</span>}
              </button>
            ))}
          </div>
        );
      })}
      {selected.length > 0 && (
        <button type="button" className="secondary" onClick={() => onChange([])}>
          清除篩選
        </button>
      )}
    </div>
  );
}
