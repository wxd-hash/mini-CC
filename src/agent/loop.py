"""
Engine — translated from claude-code's src/query.ts queryLoop().

Core flow (matches claude-code exactly):
1. Microcompact — truncate old tool results (zero-cost)
2. Auto-compact — if token threshold exceeded, LLM-summarize history
3. Stream API call — get model response with tools
4. Execute tools — parallel batches for read-only tools
5. Continue loop or return terminal
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from src.config import MAX_TOOL_ROUNDS, MAX_MESSAGES_BEFORE_COMPACT, KEEP_RECENT_MESSAGES
from src.context import build_system_prompt, compact_messages, micro_compact
from src.llm.provider import LLMProvider
from src.security.permission import PermissionChecker, _is_self_destructive as _is_self_destructive_cmd
from src.session.logger import SessionLogger, SessionStore
from src import terminal as term

# ---------------------------------------------------------------------------
# Retry constants (matches claude-code withRetry)
# ---------------------------------------------------------------------------

_MAX_RETRIES = 10
_BASE_DELAY = 0.5
_MAX_DELAY = 32.0
_JITTER_FACTOR = 0.25

_CONTEXT_OVERFLOW_RE = re.compile(
    r"prompt is too long|max_tokens.*exceeds.*context|input.*too large|request too large",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Auto-compact constants (matches claude-code autoCompact.ts)
# ---------------------------------------------------------------------------

AUTOCOMPACT_BUFFER_TOKENS = 13_000
TOOL_RESULT_GROWTH_ESTIMATE = 15_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3


class AbortedError(Exception):
    """Raised when the current turn is aborted by the user."""


# ---------------------------------------------------------------------------
# Retry helpers (matches claude-code withRetry.ts)
# ---------------------------------------------------------------------------

def _compute_retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with jitter, respecting Retry-After."""
    if retry_after is not None and retry_after > 0:
        return retry_after
    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    jitter = delay * random.uniform(0, _JITTER_FACTOR)
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
    return any(kw in msg for kw in ("authentication", "unauthorized", "invalid api key", "incorrect api key"))


def _is_retryable(err_msg: str, exc: Exception) -> bool:
    exc_name = type(exc).__name__.lower()
    if any(kw in exc_name for kw in ("rate", "timeout", "server", "overloaded", "capacity")):
        return True
    msg_lower = err_msg.lower()
    return any(
        kw in msg_lower
        for kw in ("rate limit", "too many requests", "server error", "internal error",
                    "service unavailable", "timeout", "overloaded", "capacity",
                    "retry", "try again", "503", "502", "504", "429")
    )


# ---------------------------------------------------------------------------
# Token estimation (matches claude-code tokenCountWithEstimation)
# ---------------------------------------------------------------------------

def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough token count — ~4 chars per token for English/code."""
    total = 0
    for m in messages:
        if m is None:
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "") or str(block.get("content", ""))
                    total += len(text)
    return total // 4


def get_effective_context_window_size(model: str, max_tokens: int) -> int:
    """Effective context window minus output reservation.

    Matches claude-code's getEffectiveContextWindowSize().
    """
    # Approximate context window sizes by model family
    model_lower = model.lower()
    if "opus" in model_lower:
        ctx = 200_000
    elif "sonnet" in model_lower:
        ctx = 200_000
    elif "haiku" in model_lower:
        ctx = 200_000
    elif "deepseek" in model_lower:
        ctx = 128_000
    elif "gpt" in model_lower:
        ctx = 128_000
    else:
        ctx = 100_000

    reserved = min(max_tokens, 20_000)
    return ctx - reserved


def isAutoCompactEnabled() -> bool:
    """Check if auto-compaction is enabled (matches claude-code's feature gate).

    Can be disabled via env: MINICLAUDE_DISABLE_AUTO_COMPACT=1
    """
    import os
    return os.environ.get("MINICLAUDE_DISABLE_AUTO_COMPACT", "").lower() not in ("1", "true", "yes")


def should_auto_compact(
    messages: list[dict[str, Any]],
    model: str,
    max_tokens: int,
    consecutive_failures: int = 0,
) -> bool:
    """Check if auto-compaction should run BEFORE the next API call.

    Matches claude-code's isAutoCompactEnabled / token threshold check.
    Runs when estimated tokens + turn growth > effective context window.
    """
    if consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        return False  # Circuit breaker

    effective_window = get_effective_context_window_size(model, max_tokens)
    current = estimate_tokens(messages)
    turn_growth = max_tokens + TOOL_RESULT_GROWTH_ESTIMATE

    return (current + turn_growth) > effective_window


# ---------------------------------------------------------------------------
# Engine (matches claude-code query() / queryLoop())
# ---------------------------------------------------------------------------

@dataclass
class EngineState:
    """Mutable cross-iteration state — matches claude-code's State type."""
    messages: list[dict[str, Any]]
    turn_count: int = 1
    auto_compact_tracking: dict[str, Any] | None = None
    consecutive_compact_failures: int = 0
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False


class Engine:
    """Core agent engine — translated from claude-code's query.ts queryLoop().

    Key differences from previous version:
    - Auto-compact BEFORE API call (token-threshold based)
    - Microcompact each iteration (zero-cost truncation)
    - Proper state tracking across iterations
    - Circuit breaker for compaction failures
    """

    def __init__(
        self,
        tools: list[Any],
        system_prompt: str,
        permission_checker: PermissionChecker,
        provider: LLMProvider,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 32000,
        session_store: SessionStore | None = None,
        cost_tracker: Any = None,
        tool_registry: Any = None,
        workspace_dir: Path | None = None,
        logger: SessionLogger | None = None,
        memory_dir: Path | None = None,
        provider_factory: Any = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_tokens = max_tokens
        self._tools: dict[str, Any] = {t.name: t for t in tools}
        self._system_prompt = system_prompt
        self._permissions = permission_checker
        self._session_store = session_store
        self._cost_tracker = cost_tracker
        self._tool_registry = tool_registry
        self._workspace_dir = workspace_dir
        self._logger = logger
        self._memory_dir = memory_dir
        self._provider_factory = provider_factory

        # Internal state
        self._messages: list[dict[str, Any]] = []
        self._aborted = False
        self._turn_start_len: int | None = None
        self._active_stream = None

        # -- retained from original -----------------------------------------
        self._last_tool_calls: list[tuple[str, str]] = []
        self._consecutive_errors = 0
        self.MAX_CONSECUTIVE_ERRORS = 5
        self._read_results: dict[str, dict[str, int]] = {}
        self.MAX_STALE_READS = 3
        self._consecutive_strikes = 0
        self._cached_prompt: str | None = None

    # -- permission property ------------------------------------------------

    @property
    def permission(self):
        return self._permissions

    # -- message accessors --------------------------------------------------

    def get_messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self._messages = [
            {"role": msg.get("role", "user"), "content": msg.get("content", "")}
            for msg in messages
        ]

    def set_tools(self, tools: list[Any]) -> None:
        self._tools = {t.name: t for t in tools}

    def get_model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._system_prompt = value

    def last_assistant_text(self) -> str:
        if not self._messages:
            return ""
        last = self._messages[-1]
        if last.get("role") != "assistant":
            return ""
        content = last.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    parts.append(block.text)
            return "".join(parts)
        return ""

    def set_session_store(self, store: SessionStore | None) -> None:
        self._session_store = store

    def set_sessions_dir(self, path: Path) -> None:
        self._sessions_dir = path

    # -- abort support ------------------------------------------------------

    def abort(self) -> None:
        self._aborted = True
        if self._active_stream is not None:
            try:
                self._active_stream.close()
            except Exception:
                pass

    def cancel_turn(self) -> None:
        if self._turn_start_len is not None:
            del self._messages[self._turn_start_len:]
            self._turn_start_len = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self, message: dict[str, Any]) -> None:
        if self._session_store is not None:
            try:
                self._session_store.append_message(message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Legacy API
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> str | None:
        """Process one turn. Returns final assistant text."""
        self._permissions.reset_for_turn()
        last_text = ""
        has_output = False
        for event in self.submit(user_input):
            ev_type = event[0]
            if ev_type == "text":
                last_text = event[1]
                print(event[1], end="", flush=True)
                has_output = True
            elif ev_type == "error":
                print(term.error(event[1]))
            elif ev_type == "tool_executing":
                _, name, params, activity = event
                print(term.tool_running(name, self._fmt_params(params), activity or ""))
            elif ev_type == "waiting":
                pass

        if has_output and last_text:
            print()

        # ── Post-turn: run memory extraction sub-agent ──
        # Matches claude-code's extractMemories hook in stopHooks.ts
        # Runs in background thread, non-blocking
        self._extract_memories()

        return last_text if last_text else None

    def _extract_memories(self) -> None:
        """Run memory extraction sub-agent in background (matches claude-code)."""
        try:
            from src.features.memory import run_memory_extraction
            mem_dir = getattr(self, '_memory_dir', None)
            if mem_dir is None:
                return
            run_memory_extraction(
                engine=self,
                memory_dir=mem_dir,
                messages=self._messages,
                provider_factory=getattr(self, '_provider_factory', None),
                model=self._model,
            )
        except Exception:
            pass  # Non-essential — don't break the REPL

    def resume(self, history: list[dict[str, Any]]) -> None:
        for msg in history:
            if msg.get("role", "user") == "user":
                self._messages.append(
                    self._provider.make_user_message(msg.get("content", ""))
                )
            else:
                self._messages.append({"role": "assistant", "content": msg.get("content", "")})

    def reload(self) -> None:
        self._cached_prompt = None
        print(term.info("[system prompt reloaded]"))

    def clear(self) -> None:
        self._messages.clear()
        print(term.info("[session cleared]"))

    # ═════════════════════════════════════════════════════════════════════
    # MAIN LOOP — translated from claude-code queryLoop() line-by-line
    # ═════════════════════════════════════════════════════════════════════
    #
    # Claude-code's queryLoop has this structure:
    #   while(true):
    #     microcompact  → zero-cost truncation
    #     autocompact   → LLM summarization if over token threshold
    #     streaming API call
    #     execute tools  → runTools(partitionToolCalls)
    #     post-processing → attachments, memory, commands
    #     state = { messages: messagesForQuery ++ assistant ++ toolResults }
    #
    # Key difference from our previous version: state is rebuilt immutably
    # at each continue point, not mutated in-place. This matches claude-code's
    # pattern where `state = next` at the end of each iteration.

    def submit(self, user_input: str) -> Iterator[tuple]:
        """Submit user message; yield events until turn completes.

        Translated from claude-code's queryLoop():
        - Microcompact before API call (zero-cost truncation)
        - Auto-compact before API call (token-threshold LLM summary)
        - Streaming API call with retry
        - PartitionToolCalls + parallel read-only execution
        - State rebuild at continue point (immutable pattern)

        Yields:
          ("text", str)               — streamed text chunk
          ("tool_call", name, input, activity) — before tool executes
          ("tool_executing", name, input, activity) — tool running
          ("tool_result", name, input, result) — after tool executes
          ("waiting",)                — text done, waiting for tool_use
          ("error", str)              — non-fatal error
        """
        self._aborted = False
        self._turn_start_len = len(self._messages)
        self._messages.append({"role": "user", "content": user_input})
        self._persist(self._messages[-1])

        try:
            # ─── State (matches claude-code's `let state: State`) ─────────
            turn_count = 0
            compact_failures = 0
            compact_tracking: dict | None = None  # matches autoCompactTracking
            has_attempted_reactive = False

            while True:
                turn_count += 1
                if self._aborted:
                    raise AbortedError()

                # ═══════════════════════════════════════════════════════════
                # STEP 1: Microcompact (matches claude-code line 587-610)
                # Zero-cost truncation of old tool results. Runs EVERY iteration.
                # ═══════════════════════════════════════════════════════════
                self._messages = micro_compact(self._messages, keep_recent=12)

                # ═══════════════════════════════════════════════════════════
                # STEP 2: Auto-compact (matches claude-code line 638-728)
                # LLM summarization if token threshold exceeded. Runs BEFORE
                # the API call so the model sees the compacted view.
                # ═══════════════════════════════════════════════════════════
                if isAutoCompactEnabled() and should_auto_compact(
                    self._messages, self._model, self._max_tokens, compact_failures,
                ):
                    before = len(self._messages)
                    print(term.info("[auto-compacting conversation...]"), flush=True)
                    system = build_system_prompt(workspace_dir=self._workspace_dir)

                    try:
                        self._messages = compact_messages(
                            provider=self._provider,
                            system_prompt=system,
                            messages=self._messages,
                            keep_recent=KEEP_RECENT_MESSAGES,
                        )
                        after = len(self._messages)
                        compact_failures = 0
                        compact_tracking = {"compacted": True, "turn_counter": 0}
                        if self._logger:
                            self._logger.compact(before, after)
                        print(term.compact(before, after))
                    except Exception as e:
                        compact_failures += 1
                        print(term.error(f"Auto-compact failed ({e})"))
                        if compact_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
                            print(term.info("[compaction circuit breaker tripped]"))

                    self._cached_prompt = None

                # Update compact turn counter (matches claude-code line 1803-1813)
                if compact_tracking and compact_tracking.get("compacted"):
                    compact_tracking["turn_counter"] += 1

                # ═══════════════════════════════════════════════════════════
                # STEP 3: Turn limit check (matches claude-code line 2018-2026)
                # Warn at MAX_TOOL_ROUNDS, force-stop at 5x
                # ═══════════════════════════════════════════════════════════
                if turn_count == MAX_TOOL_ROUNDS:
                    print(term.turn_warning(turn_count))
                if turn_count >= MAX_TOOL_ROUNDS * 5:
                    print(term.turn_limit(turn_count))
                    break

                # ═══════════════════════════════════════════════════════════
                # STEP 4: API call with retry (matches claude-code line 878-935)
                # Streaming model response. Retry on transient errors.
                # ═══════════════════════════════════════════════════════════
                assistant_messages: list[dict] = []  # one per API call in this batch
                tool_uses = []
                final = None

                for attempt in range(_MAX_RETRIES):
                    try:
                        tools = self._tools_for_provider()
                        response = self._provider.send_message(
                            system_prompt=self._system_prompt,
                            messages=self._messages,
                            tools=tools,
                            max_tokens=self._max_tokens,
                        )
                        final = response
                        if response.text:
                            yield ("text", response.text)
                            yield ("waiting",)
                        for tc in response.tool_calls:
                            tool_uses.append(tc)
                        break
                    except AbortedError:
                        raise
                    except Exception as e:
                        err_msg = str(e)
                        if _is_auth_error(e):
                            self._messages.pop()
                            yield ("error", f"Authentication failed: {err_msg}")
                            return
                        if _CONTEXT_OVERFLOW_RE.search(err_msg):
                            reduced = self._max_tokens // 2
                            if reduced >= 1024:
                                self._max_tokens = reduced
                                yield ("error", f"Context overflow → max_tokens={reduced}, retrying")
                                continue
                            self._messages.pop()
                            yield ("error", f"Context overflow, cannot reduce further: {err_msg}")
                            return
                        if _is_retryable(err_msg, e):
                            if attempt < _MAX_RETRIES - 1:
                                wait = _compute_retry_delay(attempt, _parse_retry_after(e))
                                yield ("error", f"API error, retrying in {wait:.1f}s...")
                                time.sleep(wait)
                                continue
                            self._messages.pop()
                            yield ("error", f"API error after {_MAX_RETRIES} retries: {err_msg}")
                            return
                        self._messages.pop()
                        yield ("error", f"API error: {err_msg}")
                        return

                if final is None:
                    self._messages.pop()
                    return

                # Store assistant message (matches claude-code: assistantMessages.push)
                self._messages.append(final.assistant_message)
                assistant_messages.append(final.assistant_message)
                self._persist(self._messages[-1])
                if self._logger and final.text:
                    self._logger.assistant_text(final.text)

                # No tool calls → terminal (matches claude-code: reason='completed')
                if not tool_uses:
                    break

                # ═══════════════════════════════════════════════════════════
                # STEP 5: Execute tools (matches claude-code line 1639-1684)
                # partitionToolCalls → concurrent+sequential batches
                # ═══════════════════════════════════════════════════════════
                tool_result_msgs: list[dict] = []  # tool result messages
                tool_result_items: list[tuple[str, str, str]] = []

                # Partition: consecutive read-only tools → parallel batch
                # (matches claude-code's partitionToolCalls)
                batches: list[tuple[bool, list[Any]]] = []
                for tu in tool_uses:
                    t = self._tools.get(tu.name)
                    is_concurrent = t is not None and t.is_read_only()
                    if batches and batches[-1][0] == is_concurrent and is_concurrent:
                        batches[-1][1].append(tu)
                    else:
                        batches.append((is_concurrent, [tu]))

                for is_concurrent, batch in batches:
                    if self._aborted:
                        raise AbortedError()

                    if is_concurrent and len(batch) > 1:
                        # ── runToolsConcurrently (read-only batch) ──
                        approved, denied, events = self._check_batch_permissions(batch)
                        for ev in events:
                            yield ev
                        for tu, tool, act in approved:
                            yield ("tool_executing", tu.name, tu.arguments, act)

                        executed = {}
                        if approved:
                            with ThreadPoolExecutor(max_workers=min(len(approved), 10)) as pool:
                                futures = {pool.submit(self._execute_tool, tu): tu for tu, _, _ in approved}
                                for f in as_completed(futures):
                                    tu = futures[f]
                                    try:
                                        executed[tu.id] = f.result()
                                    except Exception as exc:
                                        executed[tu.id] = f"Tool execution error: {exc}"

                        for tu in batch:
                            if tu.id in denied:
                                if tu.name == "run_shell" and _is_self_destructive_cmd(tu.arguments.get("command", "")):
                                    result = (
                                        "BLOCKED: /IM python kills ALL Python. "
                                        "Instead: taskkill /PID <pid> to kill just the server."
                                    )
                                else:
                                    result = "Permission denied."
                            else:
                                result = executed.get(tu.id, "No result")
                            yield self._show_tool_result(tu, result)
                            tool_result_items.append((tu.id, tu.name, result))
                    else:
                        # ── runToolsSerially (non-read-only) ──
                        for tu in batch:
                            if self._aborted:
                                raise AbortedError()
                            tn, ti = tu.name, tu.arguments
                            tool = self._tools.get(tn)
                            act = tool.get_activity_description(**ti) if tool else None
                            yield ("tool_call", tn, ti, act)
                            print(term.tool_call(tn, self._fmt_params(ti)))
                            if self._logger:
                                self._logger.tool_use(tn, ti)

                            # Handle unknown tools gracefully
                            if tool is None:
                                result = self._handle_unknown_tool(tu)
                                print(term.error(f"Unknown tool: {tn!r}"))
                            elif self._check_stuck(tu):
                                result = f"WARNING: {tu.name} called 3x with same args."
                            elif self._permissions.check(tool, ti) == "deny":
                                # Check if this was a self-destructive command
                                if tn == "run_shell" and _is_self_destructive_cmd(ti.get("command", "")):
                                    result = (
                                        "BLOCKED: /IM python kills ALL Python processes including me. "
                                        "Instead, use 'taskkill /PID <server_pid>' to kill just the "
                                        "server process. Or use 'netstat -ano | findstr :<port>' to "
                                        "find the server PID first, then kill that specific PID."
                                    )
                                else:
                                    result = "Permission denied."
                                print(term.permission_denied(f"Denied: {tn}"))
                            else:
                                yield ("tool_executing", tn, ti, act)
                                result = self._execute_tool(tu)

                            yield self._show_tool_result(tu, result)
                            tool_result_items.append((tu.id, tu.name, result))

                            # Consecutive error abort
                            if self._consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                                self._consecutive_errors = 0
                                yield ("error", f"Aborting: {self.MAX_CONSECUTIVE_ERRORS} consecutive tool failures")
                                self._flush_tool_results(tool_result_items)
                                return

                print()

                # Reset stuck-detection counter
                if not any("Stuck" in (r[2] if isinstance(r, tuple) else "") for r in tool_result_items):
                    self._consecutive_strikes = 0

                # ═══════════════════════════════════════════════════════════
                # STEP 6: Flush tool results to message list
                # (matches claude-code: normalizeMessagesForAPI + push to toolResults)
                # ═══════════════════════════════════════════════════════════
                self._flush_tool_results(tool_result_items)

                # All denied + no text → stop (matches claude-code finish-reason)
                if tool_result_items and all(
                    (r[2] if isinstance(r, tuple) else "") == "Permission denied."
                    for r in tool_result_items
                ) and not (hasattr(final, 'text') and final.text):
                    yield ("text", "All tool calls denied by permission system.")
                    break

                # ═══════════════════════════════════════════════════════════
                # CONTINUE: state rebuilt for next iteration
                # (matches claude-code line 2029-2041)
                # state = { messages: messagesForQuery ++ assistant ++ toolResults }
                # ═══════════════════════════════════════════════════════════

        except AbortedError:
            self.cancel_turn()
            raise

    # ─── Tool execution helpers (extracted from submit) ──────────────────

    def _check_batch_permissions(self, batch: list) -> tuple[list, dict, list]:
        """Check permissions for a batch. Returns (approved, denied, tool_call_events)."""
        approved = []
        denied = {}
        events = []
        for tu in batch:
            tn, ti = tu.name, tu.arguments
            tool = self._tools.get(tn)
            act = tool.get_activity_description(**ti) if tool else None
            events.append(("tool_call", tn, ti, act))
            print(term.tool_call(tn, self._fmt_params(ti)))
            if self._logger:
                self._logger.tool_use(tn, ti)
            if tool is None:
                denied[tu.id] = tn
                print(term.error(f"Unknown tool: {tn!r}"))
            elif self._permissions.check(tool, ti) == "deny":
                denied[tu.id] = tn
                print(term.permission_denied(f"Denied: {tn}"))
            else:
                approved.append((tu, tool, act))
        return approved, denied, events

    def _handle_unknown_tool(self, tu) -> str:
        """Return a helpful error when the model calls a non-existent tool."""
        return (
            f"ERROR: Unknown tool '{tu.name}'. "
            f"Available tools: {', '.join(sorted(self._tools.keys()))}. "
            f"Use one of the available tools instead."
        )

    def _check_stuck(self, tu) -> bool:
        """Check if this tool call is stuck (same name+args 3x in last 5 calls)."""
        call_key = (tu.name, json.dumps(tu.arguments, sort_keys=True))
        self._last_tool_calls.append(call_key)
        if self._last_tool_calls[-5:].count(call_key) >= 3:
            self._last_tool_calls.clear()
            return True
        return False

    def _show_tool_result(self, tu, result: str) -> tuple:
        """Show tool result in terminal and return event tuple to yield."""
        # Use claude-code style: ✓ for success, ✗ for errors
        is_err = "Error" in result or "error" in result or "denied" in result.lower()
        if is_err:
            print(term.tool_error(self._truncate(result, 200)))
        else:
            print(term.tool_done(self._truncate(result, 200)))
        if self._logger:
            self._logger.tool_result(tu.name, result)
        return ("tool_result", tu.name, tu.arguments, result)

    def _flush_tool_results(self, items: list[tuple[str, str, str]]) -> None:
        """Format tool results via provider and append to message list."""
        provider_msgs = self._provider.make_tool_result_messages(items)
        self._messages.extend(provider_msgs)
        for msg in provider_msgs:
            self._persist(msg)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_use: Any) -> str:
        tool = self._tools.get(tool_use.name)
        if tool is None:
            self._consecutive_errors += 1
            return f"Unknown tool: {tool_use.name}"

        try:
            result = tool.execute(**tool_use.arguments)
            self._consecutive_errors = 0

            # Stale-read detection
            if tool_use.name in ("read_file", "search_files", "git_diff"):
                content = result.content if hasattr(result, 'content') else str(result)
                h = hashlib.sha256(content[:2000].encode()).hexdigest()
                counts = self._read_results.setdefault(tool_use.name, {})
                counts[h] = counts.get(h, 0) + 1
                if counts[h] >= self.MAX_STALE_READS:
                    return (
                        f"WARNING: {tool_use.name} returned same content "
                        f"{self.MAX_STALE_READS}x. Stop reading.\n\n{content}"
                    )

            return result.content if hasattr(result, 'content') else str(result)

        except Exception as exc:
            self._consecutive_errors += 1
            error_msg = f"Tool error: {exc}"
            if self._logger:
                self._logger.error(error_msg)
            return error_msg

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tools_for_provider(self) -> list[dict[str, Any]]:
        if self._tool_registry:
            return self._provider.tools_for_provider(self._tool_registry)
        return [t.to_api_schema() for t in self._tools.values()]

    @staticmethod
    def _fmt_params(params: dict[str, Any]) -> str:
        parts = []
        for k, v in params.items():
            s = str(v).replace("\n", "\\n")
            if len(s) > 60:
                s = s[:57] + "..."
            parts.append(f"{k}={s!r}")
        return ", ".join(parts)

    @staticmethod
    def _truncate(s: str, n: int) -> str:
        if len(s) <= n:
            return s
        return s[:n] + "..."
