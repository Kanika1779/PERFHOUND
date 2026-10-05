"""LLM clients. Standard library only (urllib) - no SDK dependency.

GeminiClient   Google AI Studio REST API. Key from $GEMINI_API_KEY (never from code or config files).
CachedLLM      SQLite cache keyed by sha256(model, prompt, schema): reruns are free and reproducible.

Any object with  .name  and  .complete_json(prompt, schema) -> dict  can be used as an LLM.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gemini-3.5-flash-lite"   # pinned version, not "-latest": results must be reproducible
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, model: str = DEFAULT_MODEL, *, api_key: str | None = None, timeout: float = 120,
                 max_retries: int = 6, min_interval: float = 0.0) -> None:
        self.model = model
        self.name = f"gemini:{model}"
        self._key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._key:
            raise LLMError("GEMINI_API_KEY is not set (setx GEMINI_API_KEY \"...\" and open a new terminal)")
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval      # seconds between calls (free-tier requests-per-minute)
        self._last = 0.0
        self.usage = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0}

    def complete_json(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        config: dict[str, Any] = {"temperature": 0.0, "responseMimeType": "application/json"}
        if schema is not None:
            config["responseSchema"] = schema
        body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                           "generationConfig": config}).encode("utf-8")
        data = self._post(body)
        try:
            cand = data["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"no answer from {self.model}: {json.dumps(data)[:500]}") from None
        meta = data.get("usageMetadata", {})
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += meta.get("promptTokenCount", 0)
        self.usage["output_tokens"] += meta.get("candidatesTokenCount", 0)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise LLMError(f"{self.model} returned invalid JSON (finishReason={cand.get('finishReason')}): "
                           f"{text[:300]}") from None

    def _post(self, body: bytes) -> dict[str, Any]:
        url = API.format(model=self.model)
        delay = 5.0
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json", "x-goog-api-key": self._key})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                if e.code in RETRY_STATUS and attempt < self.max_retries:
                    time.sleep(_retry_after(detail) or delay)
                    delay = min(delay * 2, 120)
                    continue
                if e.code == 404:
                    raise LLMError(f"model {self.model!r} unavailable (HTTP 404: {detail}) - "
                                   f"run scripts/check_llm.py to list models") from None
                raise LLMError(f"HTTP {e.code} from Gemini: {detail}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay = min(delay * 2, 120)
                    continue
                raise LLMError(f"cannot reach Gemini: {e}") from None
        raise LLMError("unreachable")


def _retry_after(detail: str) -> float | None:
    """Gemini 429 bodies carry e.g. "retryDelay": "17s"."""
    import re

    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', detail)
    return float(m.group(1)) + 1.0 if m else None


def default_llm_db() -> Path:
    base = os.environ.get("PERFHOUND_CACHE_DIR")
    return (Path(base) if base else Path.home() / ".perfhound") / "llm.db"


class CachedLLM:
    def __init__(self, inner, db_path: str | Path | None = None) -> None:
        self.inner = inner
        self.name = inner.name
        self.path = Path(db_path) if db_path else default_llm_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=10)
        self._conn.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, model TEXT, answer TEXT)")
        self._conn.commit()
        self.hits = self.misses = 0

    def complete_json(self, prompt: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
        key = hashlib.sha256(json.dumps([self.name, prompt, schema], sort_keys=True).encode("utf-8")).hexdigest()
        row = self._conn.execute("SELECT answer FROM answers WHERE key = ?", (key,)).fetchone()
        if row:
            self.hits += 1
            return json.loads(row[0])
        self.misses += 1
        answer = self.inner.complete_json(prompt, schema)
        self._conn.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?)", (key, self.name, json.dumps(answer)))
        self._conn.commit()
        return answer
