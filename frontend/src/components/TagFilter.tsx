import { useEffect, useMemo, useState } from "react";
import { api, TagItem, TagKind } from "../api";

const KIND_LABELS: Record<TagKind, string> = {
  show: "節目",
  speaker: "講者",
  channel: "頻道",
  topic: "主題",
};
const KIND_ORDER: TagKind[] = ["show", "speaker", "channel", "topic"];
// 預設只列出「至少這麼多支影片共用」的標籤。只出現一兩次的一次性主題標籤
// （目前 topic 就有 180+ 個）會把整個篩選區塞爆，要用時再靠搜尋或展開撈出來。
const MIN_VISIBLE_COUNT = 3;

type TagGroup = {
  kind: TagKind;
  visible: TagItem[];
  hiddenCount: number;
};

export default function TagFilter({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (tagIds: string[]) => void;
}) {
  const [tags, setTags] = useState<TagItem[]>([]);
  const [query, setQuery] = useState("");
  const [expandedKinds, setExpandedKinds] = useState<TagKind[]>([]);

  useEffect(() => {
    api.listTags().then(setTags).catch(() => setTags([]));
  }, []);

  const normalizedQuery = query.trim().toLowerCase();

  const groups = useMemo<TagGroup[]>(() => {
    const built = KIND_ORDER.map((kind) => {
      const inKind = tags.filter((t) => t.kind === kind);
      if (normalizedQuery) {
        return {
          kind,
          visible: inKind.filter((t) => t.name.toLowerCase().includes(normalizedQuery)),
          hiddenCount: 0,
        };
      }
      if (expandedKinds.includes(kind)) {
        return { kind, visible: inKind, hiddenCount: 0 };
      }
      // 已選取的標籤一定要留著，否則篩選中的冷門標籤會憑空消失、無法取消。
      const visible = inKind.filter(
        (t) => (t.count ?? 0) >= MIN_VISIBLE_COUNT || selected.includes(t.id),
      );
      return { kind, visible, hiddenCount: inKind.length - visible.length };
    });
    return built.filter((g) => g.visible.length > 0 || g.hiddenCount > 0);
  }, [tags, normalizedQuery, expandedKinds, selected]);

  if (tags.length === 0) return null;

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  function toggleKind(kind: TagKind) {
    setExpandedKinds((prev) =>
      prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind],
    );
  }

  const hasResult = groups.some((g) => g.visible.length > 0);

  return (
    <div className="tag-filter">
      <div className="tag-filter-bar">
        <input
          type="search"
          className="tag-filter-search"
          placeholder="搜尋標籤…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="muted tag-filter-hint">
          預設只列出 {MIN_VISIBLE_COUNT} 支以上共用的標籤
        </span>
        {selected.length > 0 && (
          <button type="button" className="plain-link" onClick={() => onChange([])}>
            清除篩選（{selected.length}）
          </button>
        )}
      </div>
      {!hasResult && <span className="muted">找不到符合的標籤</span>}
      {groups.map(({ kind, visible, hiddenCount }) => (
        <div key={kind} className="tag-filter-group">
          <span className="tag-filter-label">{KIND_LABELS[kind]}</span>
          {visible.map((t) => (
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
          {hiddenCount > 0 && (
            <button type="button" className="tag-filter-more" onClick={() => toggleKind(kind)}>
              +{hiddenCount} 更多
            </button>
          )}
          {!normalizedQuery && expandedKinds.includes(kind) && (
            <button type="button" className="tag-filter-more" onClick={() => toggleKind(kind)}>
              收合
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
