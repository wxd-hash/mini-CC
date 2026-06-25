"""API-call retry logic — extracted from Engine.

Matches claude-code's withRetry.ts pattern:
- Exponential backoff with jitter
- Retry-After header support
- Auth errors → immediate abort
- Context overflow → reduce max_tokens
- Rate-limit / server errors → retry with backoff
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Iterator

from src.llm.provider import TextDelta, ToolUseBlock, StreamEnd

# ---------------------------------------------------------------------------
# Retry constants (matches claude-code withRetry)
# ---------------------------------------------------------------------------

MAX_RETRIES = 10
BASE_DELAY = 0.5
MAX_DELAY = 32.0
JITTER_FACTOR = 0.25

_CONTEXT_OVERFLOW_RE = re.compile(
    r"prompt is too long|max_tokens.*exceeds.*context|input.*too large|request too large",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Error classifiers
# ---------------------------------------------------------------------------

def _compute_retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with jitter, respecting Retry-After."""
    if retry_after is not None and retry_after > 0:
        return retry_after
    delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
    jitter = delay * random.uniform(0, JITTER_FACTOR)
    return delay + jitter


def _parse_retry_after(exc: Exception) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _is_auth_error(exc: Exception) -> bool:
    exc_name = type(exc).__name__.lower()
    if "auth" in exc_name or "unauthorized" in exc_name or "forbidden" in exc_name:
        return True
    msg = str(exc).lower()
    return any(
        kw in msg
        for kw in ("authentication", "unauthorized", "invalid api key", "incorrect api key")
    )


def _is_retryable(err_msg: str, exc: Exception) -> bool:
    exc_name = type(exc).__name__.lower()
    if any(kw in exc_name for kw in ("rate", "timeout", "server", "overloaded", "capacity")):
        return True
    msg_lower = err_msg.lower()
    return any(
        kw in msg_lower
        for kw in (
            "rate limit", "too many requests", "server error", "internal error",
            "service unavailable", "timeout", "overloaded", "capacity",
            "retry", "try again", "503", "502", "504", "429",
        )
    )


# ---------------------------------------------------------------------------
# Streaming model call with retry
# ---------------------------------------------------------------------------

class _CallAborted(Exception):
    """Internal signal to stop retries."""


def call_model_with_retry(
    provider: Any,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_tokens: int,
) -> Iterator[TextDelta | ToolUseBlock | StreamEnd]:
    """Call the model with retry logic. Yields stream events.

    On auth error or unrecoverable error, yields a single StreamEnd with
    empty assistant_message and returns.
    """
    current_max_tokens = max_tokens

    for attempt in range(MAX_RETRIES):
        try:
            stream = provider.send_message_stream(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=current_max_tokens,
            )
            # Re-yield all events from the provider stream
            for event in stream:
                yield event
            return  # success

        except _CallAborted:
            raise
        except Exception as exc:
            err_msg = str(exc)

            if _is_auth_error(exc):
                yield StreamEnd(
                    assistant_message={},
                    text=f"Authentication failed: {err_msg}",
                )
                return

            if _CONTEXT_OVERFLOW_RE.search(err_msg):
                reduced = current_max_tokens // 2
                if reduced >= 1024:
                    current_max_tokens = reduced
                    yield TextDelta(f"\n[Context overflow → max_tokens={reduced}, retrying...]\n")
                    continue
                yield StreamEnd(
                    assistant_message={},
                    text=f"Context overflow, cannot reduce further: {err_msg}",
                )
                return

            if _is_retryable(err_msg, exc):
                if attempt < MAX_RETRIES - 1:
                    wait = _compute_retry_delay(attempt, _parse_retry_after(exc))
                    yield TextDelta(f"\n[API error, retrying in {wait:.1f}s...]\n")
                    time.sleep(wait)
                    continue
                yield StreamEnd(
                    assistant_message={},
                    text=f"API error after {MAX_RETRIES} retries: {err_msg}",
                )
                return

            # Unknown error — don't retry
            yield StreamEnd(
                assistant_message={},
                text=f"API error: {err_msg}",
            )
            return
