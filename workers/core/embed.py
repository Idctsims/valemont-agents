"""The ONLY caller of the embed Edge Function (supabase/functions/embed).

    embed(texts) -> one 384-dimension gte-small vector per text, normalized.

Batches of BATCH (8): the function's cap, set from measurement on the Free
plan (docs/feed_sources.md, "Embed function"): 8 never failed in 50 calls;
12 and 16 failed half the time with 546 WORKER_RESOURCE_LIMIT.

Failure handling, loud by design (a feed that silently stops embedding stops
clustering, and nothing else would notice):

    546 / 503   compute limit: the batch is halved and each half retried,
                down to single texts, with backoff between tries. A single
                text that still fails after RETRIES raises EmbedError.
    429 / 5xx / network / timeout
                retried with backoff, RETRIES times, then EmbedError.
    any other status (400, 401, 413...)
                a bug or a config error (wrong EMBED_TOKEN): EmbedError at once.
    a 200 with the wrong shape (count or dimensions)
                EmbedError at once.

EmbedError propagates to the caller's job, so run_tracked records it in
job_health and the health monitor pushes after two failures in a row.

Config: EMBED_URL and EMBED_TOKEN (root .env locally, Railway variables in
production). Missing either raises EmbedError naming it; the value is never
logged or put in an error message.

FALLBACK (not built): if the Edge Function's Free-plan compute limit ever
becomes the bottleneck, embed in the worker instead: Railway Hobby (more RAM)
plus `fastembed` with the same model (BAAI/bge-small or thenlper/gte-small,
384 dims, ONNX, no torch). Vectors from a different runtime are close but not
identical, so switching means re-embedding the 48-hour candidate window, and
the thresholds should be re-checked.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Sequence

log = logging.getLogger("valemont.embed")

DIMS = 384
BATCH = 8
MAX_CHARS = 2000
RETRIES = 4
BACKOFF_S = (1.0, 2.0, 4.0, 8.0)
TIMEOUT_S = 30.0
#: The compute limit (546 is Supabase's WORKER_RESOURCE_LIMIT): halve and retry.
SHRINK_ON = frozenset({546, 503})
RETRY_ON = frozenset({429, 500, 502, 504})


class EmbedError(RuntimeError):
    """Embeddings could not be produced. Never contains the token."""


#: (url, payload, headers, timeout) -> (status, parsed JSON or None). Injected
#: by tests; production uses httpx.
Post = Callable[[str, dict, dict, float], tuple[int, Any]]


def _httpx_post(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, Any]:
    import httpx  # deferred: tests inject `post` and never import it

    r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body


def _config() -> tuple[str, str]:
    url = os.getenv("EMBED_URL", "").strip()
    token = os.getenv("EMBED_TOKEN", "").strip()
    missing = [n for n, v in (("EMBED_URL", url), ("EMBED_TOKEN", token)) if not v]
    if missing:
        raise EmbedError(f"{' and '.join(missing)} not set: cannot embed. Add to the root .env "
                         "locally, or to the Railway service variables.")
    return url, token


def _clean(text: str) -> str:
    t = " ".join(str(text).split())[:MAX_CHARS]
    if not t:
        raise EmbedError("an empty text cannot be embedded")
    return t


class _Client:
    def __init__(self, post: Post, sleep: Callable[[float], None]) -> None:
        self.url, token = _config()
        self.headers = {"Authorization": f"Bearer {token}"}
        self.post = post
        self.sleep = sleep
        self.calls = 0

    def _backoff(self, attempt: int) -> None:
        self.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])

    def run(self, chunk: list[str]) -> list[list[float]]:
        last = "no attempt made"
        for attempt in range(RETRIES):
            if attempt:
                self._backoff(attempt - 1)
            self.calls += 1
            try:
                status, body = self.post(self.url, {"texts": chunk}, self.headers, TIMEOUT_S)
            except Exception as exc:  # network, timeout: retry
                last = type(exc).__name__
                log.warning("embed: %s on a batch of %d (try %d)", last, len(chunk), attempt + 1)
                continue
            if status == 200:
                return self._checked(body, len(chunk))
            if status in SHRINK_ON:
                last = f"HTTP {status}"
                if len(chunk) > 1:
                    half = len(chunk) // 2
                    log.warning("embed: HTTP %d on a batch of %d; halving", status, len(chunk))
                    self._backoff(attempt)
                    return self.run(chunk[:half]) + self.run(chunk[half:])
                log.warning("embed: HTTP %d on a single text (try %d)", status, attempt + 1)
                continue
            if status in RETRY_ON:
                last = f"HTTP {status}"
                log.warning("embed: HTTP %d on a batch of %d (try %d)", status, len(chunk), attempt + 1)
                continue
            error = body.get("error") if isinstance(body, dict) else None
            raise EmbedError(f"embed function answered HTTP {status}"
                             + (f": {str(error)[:120]}" if error else "")
                             + (" (check EMBED_TOKEN)" if status == 401 else ""))
        raise EmbedError(f"embed failed after {RETRIES} tries on a batch of {len(chunk)}: {last}")

    @staticmethod
    def _checked(body: Any, n: int) -> list[list[float]]:
        vectors = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(vectors, list) or len(vectors) != n:
            raise EmbedError(f"embed returned {len(vectors) if isinstance(vectors, list) else 'no'} "
                             f"vectors for {n} texts")
        for v in vectors:
            if not isinstance(v, list) or len(v) != DIMS:
                raise EmbedError(f"embed returned a vector of {len(v) if isinstance(v, list) else '?'} "
                                 f"dimensions; expected {DIMS}")
        return [[float(x) for x in v] for v in vectors]


def embed(texts: Sequence[str], *, post: Post | None = None,
          sleep: Callable[[float], None] = time.sleep) -> list[list[float]]:
    """One vector per text, in order. Raises EmbedError (see the module doc)."""
    cleaned = [_clean(t) for t in texts]
    if not cleaned:
        return []
    client = _Client(post or _httpx_post, sleep)
    out: list[list[float]] = []
    for i in range(0, len(cleaned), BATCH):
        out.extend(client.run(cleaned[i:i + BATCH]))
    log.info("embed: %d text(s) in %d call(s)", len(cleaned), client.calls)
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. The function's vectors are unit length, but a story
    centroid (a running mean) is not, so this always normalizes."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0
