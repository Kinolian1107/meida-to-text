from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_url TEXT,
                    caption_source TEXT DEFAULT 'none',
                    used_account TEXT,
                    upload_time TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_sec REAL,
                    topic TEXT DEFAULT '',
                    hotwords TEXT DEFAULT '',
                    local_media_path TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    meta_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS timeline_segments (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    start REAL NOT NULL,
                    end REAL NOT NULL,
                    type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    frame_path TEXT,
                    edited INTEGER DEFAULT 0,
                    FOREIGN KEY(video_id) REFERENCES videos(id)
                );

                CREATE TABLE IF NOT EXISTS summaries (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    prompt_template TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(video_id) REFERENCES videos(id)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage_label TEXT NOT NULL,
                    progress INTEGER DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    payload_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(video_id) REFERENCES videos(id)
                );

                CREATE TABLE IF NOT EXISTS llm_usage (
                    id TEXT PRIMARY KEY,
                    video_id TEXT,
                    purpose TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    account_type TEXT NOT NULL,
                    account_label TEXT NOT NULL,
                    credential_encrypted BLOB,
                    last_verified_at TEXT,
                    status TEXT DEFAULT 'active'
                );

                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS prompt_templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cross_analyses (
                    id TEXT PRIMARY KEY,
                    source_summary_ids TEXT NOT NULL,
                    user_prompt TEXT DEFAULT '',
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Best-effort migrations for older DBs
            cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(timeline_segments)").fetchall()
            }
            if "speaker" not in cols:
                conn.execute(
                    "ALTER TABLE timeline_segments ADD COLUMN speaker TEXT"
                )

    def create_video(
        self,
        *,
        filename: str,
        media_type: str,
        source_type: str,
        source_url: str | None = None,
        topic: str = "",
        hotwords: str = "",
        caption_source: str = "none",
        meta: dict[str, Any] | None = None,
    ) -> str:
        video_id = str(uuid.uuid4())
        now = _utc_now()
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    id, filename, media_type, source_type, source_url,
                    caption_source, upload_time, updated_at, status,
                    topic, hotwords, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    video_id,
                    filename,
                    media_type,
                    source_type,
                    source_url,
                    caption_source,
                    now,
                    now,
                    topic,
                    hotwords,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO jobs (
                    id, video_id, status, stage_label, progress,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, 'pending', '排隊中', 0, '{}', ?, ?)
                """,
                (str(uuid.uuid4()), video_id, now, now),
            )
        return video_id

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_videos(self, status: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM videos WHERE status = ? ORDER BY upload_time DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM videos ORDER BY upload_time DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def update_video(self, video_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _utc_now()
        if "meta" in fields:
            fields["meta_json"] = json.dumps(fields.pop("meta"), ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [video_id]
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE videos SET {cols} WHERE id = ?", values)

    def update_job(
        self,
        video_id: str,
        *,
        status: str,
        stage_label: str,
        progress: int,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = ?, stage_label = ?, progress = ?,
                    error_code = ?, error_message = ?, updated_at = ?
                WHERE video_id = ?
                """,
                (
                    status,
                    stage_label,
                    progress,
                    error_code,
                    error_message,
                    now,
                    video_id,
                ),
            )
            conn.execute(
                """
                UPDATE videos SET status = ?, error_code = ?, error_message = ?,
                    updated_at = ? WHERE id = ?
                """,
                (status, error_code, error_message, now, video_id),
            )

    def get_job(self, video_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE video_id = ? ORDER BY created_at DESC LIMIT 1",
                (video_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_pending_jobs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status IN ('pending', 'fetching_source', 'extracting',
                                 'transcribing', 'describing', 'merging')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def replace_timeline(
        self, video_id: str, segments: list[dict[str, Any]]
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "DELETE FROM timeline_segments WHERE video_id = ?", (video_id,)
            )
            for seg in segments:
                conn.execute(
                    """
                    INSERT INTO timeline_segments (
                        id, video_id, start, end, type, text, frame_path, edited, speaker
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        seg.get("id") or str(uuid.uuid4()),
                        video_id,
                        seg["start"],
                        seg["end"],
                        seg["type"],
                        seg["text"],
                        seg.get("frame_path"),
                        1 if seg.get("edited") else 0,
                        seg.get("speaker"),
                    ),
                )

    def get_timeline(self, video_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM timeline_segments
                WHERE video_id = ?
                ORDER BY start ASC, type DESC
                """,
                (video_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_summary(
        self, *, video_id: str, prompt_template: str, content: str
    ) -> str:
        summary_id = str(uuid.uuid4())
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO summaries (id, video_id, prompt_template, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (summary_id, video_id, prompt_template, content, _utc_now()),
            )
        return summary_id

    def list_summaries(self, video_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM summaries WHERE video_id = ?
                ORDER BY created_at DESC
                """,
                (video_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_llm_usage(
        self,
        *,
        video_id: str | None,
        purpose: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_usage (
                    id, video_id, purpose, model, input_tokens, output_tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    video_id,
                    purpose,
                    model,
                    input_tokens,
                    output_tokens,
                    _utc_now(),
                ),
            )

    def get_latest_correction_models(
        self, video_id: str
    ) -> dict[str, str | None]:
        """Latest model used per merge purpose (c1/c2/c3) for a video."""
        out: dict[str, str | None] = {"c1": None, "c2": None, "c3": None}
        with self.connect() as conn:
            for purpose in ("c1", "c2", "c3"):
                row = conn.execute(
                    """
                    SELECT model FROM llm_usage
                    WHERE video_id = ? AND purpose = ? AND model IS NOT NULL AND model != ''
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (video_id, purpose),
                ).fetchone()
                if row and row["model"]:
                    out[purpose] = str(row["model"])
        return out

    def get_timeline_segment(
        self, video_id: str, segment_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM timeline_segments
                WHERE video_id = ? AND id = ?
                """,
                (video_id, segment_id),
            ).fetchone()
        return dict(row) if row else None

    def update_timeline_segment(
        self, video_id: str, segment_id: str, text: str
    ) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                UPDATE timeline_segments
                SET text = ?, edited = 1
                WHERE video_id = ? AND id = ?
                """,
                (text, video_id, segment_id),
            )
        return self.get_timeline_segment(video_id, segment_id)

    def create_account(
        self,
        *,
        account_type: str,
        account_label: str,
        credential_encrypted: bytes,
        status: str = "active",
    ) -> str:
        account_id = str(uuid.uuid4())
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, account_type, account_label, credential_encrypted,
                    last_verified_at, status
                ) VALUES (?, ?, ?, ?, NULL, ?)
                """,
                (
                    account_id,
                    account_type,
                    account_label,
                    credential_encrypted,
                    status,
                ),
            )
        return account_id

    def list_accounts(self, account_type: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if account_type:
                rows = conn.execute(
                    """
                    SELECT id, account_type, account_label, last_verified_at, status
                    FROM accounts WHERE account_type = ?
                    ORDER BY account_label
                    """,
                    (account_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, account_type, account_label, last_verified_at, status
                    FROM accounts ORDER BY account_label
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def get_account(self, account_id: str, *, with_secret: bool = False) -> dict[str, Any] | None:
        with self.connect() as conn:
            if with_secret:
                row = conn.execute(
                    "SELECT * FROM accounts WHERE id = ?", (account_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id, account_type, account_label, last_verified_at, status
                    FROM accounts WHERE id = ?
                    """,
                    (account_id,),
                ).fetchone()
        return dict(row) if row else None

    def update_account(self, account_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [account_id]
        with self._lock, self.connect() as conn:
            conn.execute(f"UPDATE accounts SET {cols} WHERE id = ?", values)

    def delete_account(self, account_id: str) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            return cur.rowcount > 0

    def upsert_prompt_template(self, *, tid: str, name: str, content: str) -> dict[str, Any]:
        now = _utc_now()
        with self._lock, self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM prompt_templates WHERE id = ?", (tid,)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE prompt_templates
                    SET name = ?, content = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, content, now, tid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO prompt_templates (id, name, content, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (tid, name, content, now, now),
                )
        return {
            "id": tid,
            "name": name,
            "content": content,
            "updated_at": now,
        }

    def list_prompt_templates(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, content, created_at, updated_at
                FROM prompt_templates ORDER BY name
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def ensure_job_row(self, video_id: str) -> None:
        """Reset/create a pending job for re-queue."""
        now = _utc_now()
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM jobs WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE jobs SET status = 'pending', stage_label = '排隊中',
                        progress = 0, error_code = NULL, error_message = NULL,
                        updated_at = ?
                    WHERE video_id = ?
                    """,
                    (now, video_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, video_id, status, stage_label, progress,
                        payload_json, created_at, updated_at
                    ) VALUES (?, ?, 'pending', '排隊中', 0, '{}', ?, ?)
                    """,
                    (str(uuid.uuid4()), video_id, now, now),
                )
            conn.execute(
                """
                UPDATE videos SET status = 'pending', error_code = NULL,
                    error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, video_id),
            )

    def get_summary(self, summary_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM summaries WHERE id = ?", (summary_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_all_summaries(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM summaries ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_cross_analysis(
        self,
        *,
        source_summary_ids: list[str],
        user_prompt: str,
        result: dict[str, Any],
    ) -> str:
        analysis_id = str(uuid.uuid4())
        with self._lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO cross_analyses (
                    id, source_summary_ids, user_prompt, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    json.dumps(source_summary_ids, ensure_ascii=False),
                    user_prompt,
                    json.dumps(result, ensure_ascii=False),
                    _utc_now(),
                ),
            )
        return analysis_id

    def get_cross_analysis(self, analysis_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cross_analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_cross(dict(row))

    def list_cross_analyses(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cross_analyses ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_cross(dict(r)) for r in rows]

    def delete_prompt_template(self, tid: str) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute("DELETE FROM prompt_templates WHERE id = ?", (tid,))
            return cur.rowcount > 0

    def delete_video(self, video_id: str) -> bool:
        """Hard-delete video and all related SQLite rows. Returns False if missing."""
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM videos WHERE id = ?", (video_id,)
            ).fetchone()
            if not row:
                return False

            summary_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM summaries WHERE video_id = ?", (video_id,)
                ).fetchall()
            ]
            summary_set = set(summary_ids)

            # Drop cross-analyses that reference any of this video's summaries
            if summary_set:
                for cross in conn.execute("SELECT id, source_summary_ids FROM cross_analyses").fetchall():
                    try:
                        ids = set(json.loads(cross["source_summary_ids"] or "[]"))
                    except Exception:
                        ids = set()
                    if ids & summary_set:
                        conn.execute(
                            "DELETE FROM cross_analyses WHERE id = ?", (cross["id"],)
                        )

            conn.execute(
                "DELETE FROM timeline_segments WHERE video_id = ?", (video_id,)
            )
            conn.execute("DELETE FROM summaries WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM jobs WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM llm_usage WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
            return True

    @staticmethod
    def _row_to_cross(row: dict[str, Any]) -> dict[str, Any]:
        try:
            ids = json.loads(row.get("source_summary_ids") or "[]")
        except Exception:
            ids = []
        try:
            result = json.loads(row.get("result_json") or "{}")
        except Exception:
            result = {}
        return {
            "id": row["id"],
            "source_summary_ids": ids,
            "user_prompt": row.get("user_prompt") or "",
            "result": result,
            "created_at": row.get("created_at") or "",
        }
