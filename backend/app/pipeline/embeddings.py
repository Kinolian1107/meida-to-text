from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

EMBED_DIM = 384


def embed_text(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Lightweight deterministic hashing embedder (no external model).

    Good enough for local related-item suggestions; swap later for
    sentence-transformers if needed.
    """
    vec = [0.0] * dim
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        # Use multiple hash buckets per token
        for i in range(0, 16, 4):
            idx = int.from_bytes(digest[i : i + 4], "big") % dim
            sign = 1.0 if digest[i] % 2 == 0 else -1.0
            vec[idx] += sign
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))
