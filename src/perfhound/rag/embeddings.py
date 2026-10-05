"""Dense retrieval: embed commit documents and the symptom query, rank by cosine.

Backend: fastembed (ONNX runtime - no PyTorch, small install, works on Windows).
    pip install -e ".[embed]"
Models compared in the evaluation:
    BAAI/bge-small-en-v1.5                 general text, 384-d, ~67 MB
    jinaai/jina-embeddings-v2-base-code    trained on code, 768-d, ~640 MB
The model is downloaded from Hugging Face on first use into ~/.perfhound/models
(or $PERFHOUND_CACHE_DIR/models). fastembed's own default is the OS temp dir, which
Windows may clean - forcing a 640 MB re-download.

Vectors are cached in SQLite keyed by (model, sha256(text)): a commit's text
never changes, so a commit is embedded once per model, ever.
"""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
from array import array
from pathlib import Path
from typing import Protocol, Sequence

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
CODE_MODEL = "jinaai/jina-embeddings-v2-base-code"


class Embedder(Protocol):
    name: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    def __init__(self, model: str = DEFAULT_MODEL, *, cache_dir: str | Path | None = None, max_chars: int = 6000) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise RuntimeError('dense retrieval needs fastembed:  pip install -e ".[embed]"') from None
        self.name = model
        self.max_chars = max_chars   # keep inputs inside the model's context window
        model_dir = Path(cache_dir) if cache_dir else default_model_dir()
        model_dir.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name=model, cache_dir=str(model_dir))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.passage_embed([t[: self.max_chars] for t in texts])]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._model.query_embed(text[: self.max_chars])))]


def _cache_base() -> Path:
    base = os.environ.get("PERFHOUND_CACHE_DIR")
    return Path(base) if base else Path.home() / ".perfhound"


def default_model_dir() -> Path:
    return _cache_base() / "models"


def default_embedding_db() -> Path:
    base = os.environ.get("PERFHOUND_CACHE_DIR")
    return (Path(base) if base else Path.home() / ".perfhound") / "embeddings.db"


class CachedEmbedder:
    """Wraps any Embedder with a persistent SQLite vector cache (documents and queries)."""

    def __init__(self, inner: Embedder, db_path: str | Path | None = None) -> None:
        self.inner = inner
        self.name = inner.name
        self.path = Path(db_path) if db_path else default_embedding_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=10)
        self._conn.execute("CREATE TABLE IF NOT EXISTS vectors (model TEXT, kind TEXT, hash TEXT, vec BLOB, "
                           "PRIMARY KEY (model, kind, hash))")
        self._conn.commit()
        self.hits = self.misses = 0

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get(self, kind: str, hashes: list[str]) -> dict[str, list[float]]:
        out = {}
        for i in range(0, len(hashes), 500):
            chunk = hashes[i:i + 500]
            q = ",".join("?" * len(chunk))
            for h, blob in self._conn.execute(
                f"SELECT hash, vec FROM vectors WHERE model=? AND kind=? AND hash IN ({q})", (self.name, kind, *chunk)
            ):
                out[h] = list(array("f", blob))
        return out

    def _put(self, kind: str, items: dict[str, list[float]]) -> None:
        with self._conn:
            self._conn.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                   [(self.name, kind, h, array("f", v).tobytes()) for h, v in items.items()])

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        hashes = [self._hash(t) for t in texts]
        found = self._get("doc", list(dict.fromkeys(hashes)))
        todo = [(h, t) for h, t in dict(zip(hashes, texts)).items() if h not in found]
        self.hits += len(texts) - len(todo)
        self.misses += len(todo)
        if todo:
            vectors = self.inner.embed_documents([t for _, t in todo])
            new = {h: v for (h, _), v in zip(todo, vectors)}
            self._put("doc", new)
            found.update(new)
        return [found[h] for h in hashes]

    def embed_query(self, text: str) -> list[float]:
        h = self._hash(text)
        found = self._get("query", [h])
        if h in found:
            self.hits += 1
            return found[h]
        self.misses += 1
        v = self.inner.embed_query(text)
        self._put("query", {h: v})
        return v

    def close(self) -> None:
        self._conn.close()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
