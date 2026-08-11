from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from app.pipeline.embeddings import cosine

logger = logging.getLogger(__name__)


class LanceDBStore:
    """Vector-ready mirror; summary vectors come from a real embedding model."""

    def __init__(self, uri: Path, *, embed_dim: int = 1024) -> None:
        self.uri = uri
        self.embed_dim = embed_dim
        self.uri.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(uri))
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        if "videos" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("filename", pa.string()),
                    ("media_type", pa.string()),
                    ("source_type", pa.string()),
                    ("status", pa.string()),
                    ("caption_source", pa.string()),
                    ("upload_time", pa.string()),
                    ("duration_sec", pa.float64()),
                    ("topic", pa.string()),
                ]
            )
            self.db.create_table("videos", schema=schema)

        if "summaries" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("video_id", pa.string()),
                    ("prompt_template", pa.string()),
                    ("content", pa.string()),
                    ("created_at", pa.string()),
                    ("vector", pa.list_(pa.float32(), self.embed_dim)),
                ]
            )
            self.db.create_table("summaries", schema=schema)

        if "timelines" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("video_id", pa.string()),
                    ("start", pa.float64()),
                    ("end", pa.float64()),
                    ("type", pa.string()),
                    ("text", pa.string()),
                    ("frame_path", pa.string()),
                    ("edited", pa.bool_()),
                    ("speaker", pa.string()),
                ]
            )
            self.db.create_table("timelines", schema=schema)

        if "cross_analyses" not in self.db.table_names():
            schema = pa.schema(
                [
                    ("id", pa.string()),
                    ("source_summary_ids", pa.string()),
                    ("user_prompt", pa.string()),
                    ("result_json", pa.string()),
                    ("created_at", pa.string()),
                ]
            )
            self.db.create_table("cross_analyses", schema=schema)

    def upsert_video(self, video: dict[str, Any]) -> None:
        table = self.db.open_table("videos")
        row = {
            "id": video["id"],
            "filename": video.get("filename") or "",
            "media_type": video.get("media_type") or "",
            "source_type": video.get("source_type") or "",
            "status": video.get("status") or "",
            "caption_source": video.get("caption_source") or "none",
            "upload_time": video.get("upload_time") or "",
            "duration_sec": float(video["duration_sec"])
            if video.get("duration_sec") is not None
            else 0.0,
            "topic": video.get("topic") or "",
        }
        try:
            table.delete(f"id = '{video['id']}'")
        except Exception:
            pass
        table.add([row])

    def upsert_summary(
        self, summary: dict[str, Any], *, vector: list[float] | None = None
    ) -> None:
        """Store a summary row. `vector` must be precomputed by the caller —
        this method stays synchronous and must never make a network call
        (it runs on the event loop thread; see embed_text_remote())."""
        table = self.db.open_table("summaries")
        content = summary.get("content") or ""
        row = {
            "id": summary["id"],
            "video_id": summary["video_id"],
            "prompt_template": summary.get("prompt_template") or "",
            "content": content,
            "created_at": summary.get("created_at") or "",
            "vector": vector if vector is not None else [0.0] * self.embed_dim,
        }
        try:
            table.delete(f"id = '{summary['id']}'")
        except Exception:
            pass
        try:
            table.add([row])
        except Exception:
            # Schema mismatch (legacy table without vector): skip vector field
            row.pop("vector", None)
            table.add([row])

    def replace_timelines(
        self, video_id: str, segments: list[dict[str, Any]]
    ) -> None:
        table = self.db.open_table("timelines")
        try:
            table.delete(f"video_id = '{video_id}'")
        except Exception:
            pass
        if not segments:
            return
        rows = [
            {
                "id": seg["id"],
                "video_id": video_id,
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "type": seg["type"],
                "text": seg.get("text") or "",
                "frame_path": seg.get("frame_path") or "",
                "edited": bool(seg.get("edited")),
                "speaker": seg.get("speaker") or "",
            }
            for seg in segments
        ]
        try:
            table.add(rows)
        except Exception:
            for r in rows:
                r.pop("speaker", None)
            table.add(rows)

    def upsert_cross_analysis(self, row: dict[str, Any]) -> None:
        table = self.db.open_table("cross_analyses")
        import json

        payload = {
            "id": row["id"],
            "source_summary_ids": json.dumps(
                row.get("source_summary_ids") or [], ensure_ascii=False
            ),
            "user_prompt": row.get("user_prompt") or "",
            "result_json": json.dumps(row.get("result") or {}, ensure_ascii=False),
            "created_at": row.get("created_at") or "",
        }
        try:
            table.delete(f"id = '{row['id']}'")
        except Exception:
            pass
        table.add([payload])

    def delete_video(self, video_id: str, summary_ids: list[str] | None = None) -> None:
        """Hard-delete video rows from LanceDB tables (best-effort)."""
        sid = video_id.replace("'", "''")
        for name, predicate in (
            ("videos", f"id = '{sid}'"),
            ("timelines", f"video_id = '{sid}'"),
            ("summaries", f"video_id = '{sid}'"),
        ):
            if name not in self.db.table_names():
                continue
            try:
                self.db.open_table(name).delete(predicate)
            except Exception:
                logger.exception("LanceDB delete failed table=%s video=%s", name, video_id)

        # Remove cross_analyses that referenced deleted summaries
        if summary_ids and "cross_analyses" in self.db.table_names():
            import json

            try:
                table = self.db.open_table("cross_analyses")
                rows = table.to_pandas().to_dict(orient="records")
                drop = set(summary_ids)
                for row in rows:
                    try:
                        ids = set(json.loads(row.get("source_summary_ids") or "[]"))
                    except Exception:
                        ids = set()
                    if ids & drop:
                        rid = str(row["id"]).replace("'", "''")
                        try:
                            table.delete(f"id = '{rid}'")
                        except Exception:
                            pass
            except Exception:
                logger.exception(
                    "LanceDB cross_analyses cleanup failed video=%s", video_id
                )

    def find_related_summaries(
        self, summary_id: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Return related summaries by cosine similarity on stored vectors."""
        table = self.db.open_table("summaries")
        try:
            rows = table.to_pandas().to_dict(orient="records")
        except Exception:
            return []
        target = next((r for r in rows if r.get("id") == summary_id), None)
        if not target:
            return []
        q = target.get("vector")
        if q is None or not any(q):
            return []
        scored: list[dict[str, Any]] = []
        for r in rows:
            if r.get("id") == summary_id:
                continue
            vec = r.get("vector")
            if vec is None or not any(vec):
                continue
            score = cosine(list(q), list(vec))
            scored.append(
                {
                    "summary_id": r["id"],
                    "video_id": r["video_id"],
                    "score": score,
                    "snippet": (r.get("content") or "")[:160],
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def search_summaries(
        self, query_vector: list[float], *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Rank all summaries by cosine similarity to an external query vector."""
        table = self.db.open_table("summaries")
        try:
            rows = table.to_pandas().to_dict(orient="records")
        except Exception:
            return []
        if not any(query_vector):
            return []
        scored: list[dict[str, Any]] = []
        for r in rows:
            vec = r.get("vector")
            if vec is None or not any(vec):
                continue
            score = cosine(query_vector, list(vec))
            scored.append(
                {
                    "summary_id": r["id"],
                    "video_id": r["video_id"],
                    "score": score,
                    "snippet": (r.get("content") or "")[:160],
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]
