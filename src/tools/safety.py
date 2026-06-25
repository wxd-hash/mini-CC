"""Loop-safety detectors — stuck-call and stale-read detection.

Extracted from Engine so they can be tested independently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class StuckDetector:
    """Detects when the model makes the same tool call 3x in its last 5 calls.

    Matches claude-code's stuck-detection pattern.
    """

    def __init__(self, window_size: int = 5, threshold: int = 3) -> None:
        self._window: list[tuple[str, str]] = []
        self._window_size = window_size
        self._threshold = threshold

    def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return True if this call is stuck — same name+args >= threshold times."""
        key = (tool_name, json.dumps(arguments, sort_keys=True))
        self._window.append(key)
        if len(self._window) > self._window_size * 2:
            self._window = self._window[-self._window_size:]
        recent = self._window[-self._window_size:]
        if recent.count(key) >= self._threshold:
            self._window.clear()
            return True
        return False

    def reset(self) -> None:
        self._window.clear()


class StaleReadDetector:
    """Detects when the model reads the same file content repeatedly.

    After MAX_STALE_READS identical reads, warns and shows the content.
    """

    def __init__(self, max_stale: int = 3) -> None:
        self._max_stale = max_stale
        self._counts: dict[str, dict[str, int]] = {}

    def check(self, tool_name: str, content: str) -> str | None:
        """Return a warning string if stale, or None."""
        if tool_name not in ("read_file", "search_files", "git_diff"):
            return None
        h = hashlib.sha256(content[:2000].encode()).hexdigest()
        counts = self._counts.setdefault(tool_name, {})
        counts[h] = counts.get(h, 0) + 1
        if counts[h] >= self._max_stale:
            return (
                f"WARNING: {tool_name} returned same content "
                f"{self._max_stale}x. Stop reading.\n\n{content}"
            )
        return None
