#!/usr/bin/env python3
"""Rebuild the LanceDB `summaries` table at the current embed_dim and
recompute every summary's embedding via the local Ollama server.

Run after switching embedding models/dimensions (e.g. hashing -> bge-m3),
so existing summaries become searchable again without re-running any LLM.

Usage: .venv/bin/python scripts/backfill_embeddings.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db.lancedb_store import LanceDBStore  # noqa: E402
from app.db.sqlite_store import SQLiteStore  # noqa: E402
from app.pipeline.embeddings import embed_text_remote  # noqa: E402


async def main() -> None:
    settings = get_settings()
    store = SQLiteStore(settings.sqlite_path)

    lance = LanceDBStore(settings.lancedb_uri, embed_dim=settings.embed_dim)
    if "summaries" in lance.db.table_names():
        lance.db.drop_table("summaries")
    lance._ensure_tables()

    summaries = store.list_all_summaries()
    print(f"Backfilling {len(summaries)} summaries at dim={settings.embed_dim} "
          f"via {settings.ollama_base_url} ({settings.ollama_embedding_model})")

    ok = 0
    for i, row in enumerate(summaries, start=1):
        vector = await embed_text_remote(row.get("content") or "", settings)
        if any(vector):
            ok += 1
        lance.upsert_summary(row, vector=vector)
        print(f"[{i}/{len(summaries)}] {row['id']} {'ok' if any(vector) else 'ZERO-VECTOR'}")

    print(f"Done. {ok}/{len(summaries)} summaries embedded successfully.")
    if ok < len(summaries):
        print("Some summaries got a zero vector — check that Ollama is running "
              f"at {settings.ollama_base_url} with model {settings.ollama_embedding_model} pulled.")


if __name__ == "__main__":
    asyncio.run(main())
