"""Speech embeddings: chunked, mean-pooled, cached by text hash in a repo-local SQLite file."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

import numpy as np

from radar.corpus import Speech

MODEL = "intfloat/multilingual-e5-base"
MAX_TOKENS = 500  # model window is 512; leave room for the "passage: " prefix and special tokens
BATCH = 50  # speeches per commit, so an interrupted run resumes where it stopped

Encoder = Callable[[list[str]], np.ndarray]
TokenCounter = Callable[[str], int]


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embedding ("
        " key TEXT NOT NULL, text_sha TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (key, text_sha))"
    )
    return conn


def chunk_text(text: str, count_tokens: TokenCounter, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Paragraph-boundary windows of at most max_tokens; an oversized single paragraph stays whole."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for para in text.split("\n\n"):
        n = count_tokens(para)
        if current and current_tokens + n > max_tokens:
            chunks.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(para)
        current_tokens += n
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def load_model(name: str = MODEL) -> tuple[Encoder, TokenCounter]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(name, device="cpu")
    model.max_seq_length = 512

    def encode(texts: list[str]) -> np.ndarray:
        return model.encode(["passage: " + t for t in texts], batch_size=8, normalize_embeddings=True)

    def count_tokens(text: str) -> int:
        return len(model.tokenizer(text, truncation=False)["input_ids"])

    return encode, count_tokens


def embed_speeches(
    speeches: list[Speech],
    store: sqlite3.Connection,
    model: str = MODEL,
    loader: Callable[[str], tuple[Encoder, TokenCounter]] = load_model,
) -> np.ndarray:
    """One unit vector per speech: mean of its chunk vectors, cached by (model, chunking, text hash)."""
    key = f"{model}@{MAX_TOKENS}"
    shas = {s.id: hashlib.sha1(s.text.encode()).hexdigest() for s in speeches}
    vectors: dict[str, np.ndarray] = {}
    for s in speeches:
        row = store.execute("SELECT vector FROM embedding WHERE key = ? AND text_sha = ?", (key, shas[s.id])).fetchone()
        if row:
            vectors[s.id] = np.frombuffer(row[0], dtype=np.float32)
    missing = [s for s in speeches if s.id not in vectors]
    if missing:
        encode, count_tokens = loader(model)
    for i in range(0, len(missing), BATCH):
        batch = missing[i : i + BATCH]
        units, owner = [], []
        for s in batch:
            for c in chunk_text(s.text, count_tokens):
                units.append(c)
                owner.append(s.id)
        encoded = encode(units)
        with store:
            for s in batch:
                v = encoded[[j for j, o in enumerate(owner) if o == s.id]].mean(axis=0)
                v = (v / np.linalg.norm(v)).astype(np.float32)
                store.execute("INSERT OR REPLACE INTO embedding VALUES (?, ?, ?)", (key, shas[s.id], v.tobytes()))
                vectors[s.id] = v
        print(f"  embedded {min(i + BATCH, len(missing))}/{len(missing)}")
    return np.stack([vectors[s.id] for s in speeches])
