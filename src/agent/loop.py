"""
Engine — translated from claude-code's src/query.ts queryLoop().

Core flow (matches claude-code exactly):
1. Microcompact — truncate old tool results (zero-cost)
2. Auto-compact — if token threshold exceeded, LLM-summarize history
3. Stream API call — get model response with tools
4. Execute tools — StreamingToolExecutor runs tools as they arrive
5. Continue loop or return terminal
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from src.agent.retry import call_model_with_retry
from src.agent.tool_executor import StreamingToolExecutor
from src.config import MAX_TOOL_ROUNDS, KEEP_RECENT_MESSAGES
from src.context import build_system_prompt, compact_messages, micro_compact
from src.llm.provider import LLMProvider, TextDelta, ToolUseBlock, StreamEnd
from src.security.permission import PermissionChecker
from src.session.logger import SessionLogger, SessionStore
from src.utils.markdown import render as render_markdown
from src import terminal as term

# ---------------------------------------------------------------------------
# Auto-compact constants (matches claude-code autoCompact.ts)
# ---------------------------------------------------------------------------

AUTOCOMPACT_BUFFER_TOKENS = 13_000
TOOL_RESULT_GROWTH_ESTIMATE = 15_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3


class AbortedError(Exception):
    """Raised when the current turn is aborted by the user."""


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
    """Effective context window minus output reservation."""
    model_lower = model.lower()
    if "opus" in model_lower:
        ctx = 200_000
    elif "sonnet" in model_lower:
        ctx = 200_000
    elif "haiku" in model_lower:
        ctx = 200_000
    elif "deepseek-v4" in model_lower:
        ctx = 1_000_000  # DeepSeek V4: 1M
    elif "deepseek" in model_lower:
        ctx = 131_072  # DeepSeek V3/R1: 128K
    elif "gpt" in model_lower:
        ctx = 128_000
    else:
        ctx = 100_000

    reserved = min(max_tokens, 20_000)
    return ctx - reserved


def isAutoCompactEnabled() -> bool:
    import os
    return os.environ.get("MINICLAUDE_DISABLE_AUTO_COMPACT", "").lower() not in ("1", "true", "yes")


def should_auto_compact(
    messages: list[dict[str, Any]],
    model: str,
    max_tokens: int,
    consecutive_failures: int = 0,
) -> bool:
    if consecutive_failures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
        return False
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

    Uses streaming tool execution: tools start running as soon as their
    blocks arrive in the model stream, not after the full response.
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

        # Legacy compat
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
    # Legacy API — run() is used by sub-agents and REPL
    # ------------------------------------------------------------------

    def run(self, user_input: str, quiet: bool = False) -> str | None:
        """Process one turn. Returns final assistant text.

        If quiet=True, suppresses all terminal output (used by sub-agents).
        """
        self._permissions.reset_for_turn()
        last_text = ""
        has_output = False
        # Buffer text chunks per assistant response; render as markdown when
        # the text phase ends (tool_call or end of submit) because streaming
        # API splits markdown patterns across tiny chunks.
        _buf: list[str] = []
        _spinner: term.Spinner | None = None

        def _flush_buf() -> str:
            nonlocal _buf
            if not _buf:
                return ""
            full = "".join(_buf)
            _buf.clear()
            return full

        def _start_spinner() -> None:
            nonlocal _spinner
            if not quiet and _spinner is None:
                _spinner = term.Spinner()
                _spinner.start()

        def _stop_spinner() -> None:
            nonlocal _spinner
            if _spinner is not None:
                _spinner.stop()
                _spinner = None

        # Start spinner for initial API wait
        _start_spinner()

        for event in self.submit(user_input):
            ev_type = event[0]

            if ev_type == "waiting_api":
                _start_spinner()
                continue

            if ev_type == "text":
                chunk = event[1]
                last_text += chunk
                _buf.append(chunk)
                has_output = True
            elif ev_type == "tool_call":
                # Flush buffered text before showing tool header.
                # Tool executor handles spinner during execution.
                _stop_spinner()
                if _buf and not quiet:
                    print(render_markdown(_flush_buf()), end="", flush=True)
                last_text = ""
            elif ev_type == "tool_result":
                # Tool result is being printed — stop any remaining spinner
                _stop_spinner()
            elif ev_type == "error":
                _stop_spinner()
                if not quiet:
                    print(term.error(event[1]))
            elif ev_type == "tool_executing":
                if not quiet:
                    _, name, params, activity = event
                    print(term.tool_running(name, self._fmt_params(params), activity or ""))
            elif ev_type == "waiting":
                # Text stream done — stop spinner and flush now
                _stop_spinner()
                if _buf and not quiet:
                    print(render_markdown(_flush_buf()), end="", flush=True)

        # Flush remaining buffered text at end of response
        _stop_spinner()
        if _buf and not quiet:
            print(render_markdown(_flush_buf()), end="", flush=True)

        if has_output and last_text and not quiet:
            print()

        # Post-turn memory extraction (only for main agent, not sub-agents)
        if not quiet:
            self._extract_memories()

        return last_text if last_text else None

    def _extract_memories(self) -> None:
        """Run memory extraction sub-agent in background."""
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
            pass

    def resume(self, history: list[dict[str, Any]]) -> None:
        for msg in history:
            msg_type = msg.get("_type", "")
            role = msg.get("role", "")
            content = msg.get("content", "")

            if msg_type in ("user_input", "tool_result", "permission_denied", "error"):
                self._messages.append(
                    self._provider.make_user_message(content)
                )
            elif msg_type in ("assistant_text", "tool_call"):
                self._messages.append({"role": "assistant", "content": content})
            elif role == "user":
                self._messages.append(
                    self._provider.make_user_message(content)
                )
            else:
                self._messages.append({"role": "assistant", "content": content})

    def reload(self) -> None:
        self._cached_prompt = None
        print(term.info("[system prompt reloaded]"))

    def clear(self) -> None:
        self._messages.clear()
        print(term.info("[session cleared]"))

    # ═════════════════════════════════════════════════════════════════════
    # MAIN LOOP — streaming tool execution
    # ═════════════════════════════════════════════════════════════════════

    def submit(self, user_input: str) -> Iterator[tuple]:
        """Submit user message; yield events until turn completes.

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
            turn_count = 0
            compact_failures = 0
            compact_tracking: dict | None = None

            while True:
                turn_count += 1
                if self._aborted:
                    raise AbortedError()

                # ── STEP 1: Microcompact ──────────────────────────────
                self._messages = micro_compact(self._messages, keep_recent=12)

                # ── STEP 2: Auto-compact ──────────────────────────────
                if isAutoCompactEnabled() and should_auto_compact(
                    self._messages, self._model, self._max_tokens, compact_failures,
                ):
                    before = len(self._messages)
                    print(term.info("[auto-compacting conversation...]"), flush=True)
                    system = build_system_prompt(workspace_dir=self._workspace_dir, mode=self._permissions.mode.value)

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

                if compact_tracking and compact_tracking.get("compacted"):
                    compact_tracking["turn_counter"] += 1

                # ── STEP 3: Turn limit check ──────────────────────────
                if turn_count == MAX_TOOL_ROUNDS:
                    print(term.turn_warning(turn_count))
                if turn_count >= MAX_TOOL_ROUNDS * 5:
                    print(term.turn_limit(turn_count))
                    break

                # ── STEP 4: Streaming API call with retry ─────────────
                tools = self._tools_for_provider()
                executor = StreamingToolExecutor(
                    tools=self._tools,
                    permission_checker=self._permissions,
                    logger=self._logger,
                )
                assistant_message: dict[str, Any] = {}
                full_text = ""
                tool_uses_seen = False

                # Signal that we're about to wait for API
                yield ("waiting_api",)

                for stream_event in call_model_with_retry(
                    provider=self._provider,
                    system_prompt=self._system_prompt,
                    messages=self._messages,
                    tools=tools,
                    max_tokens=self._max_tokens,
                ):
                    if isinstance(stream_event, TextDelta):
                        full_text += stream_event.text
                        yield ("text", stream_event.text)

                    elif isinstance(stream_event, ToolUseBlock):
                        tool_uses_seen = True
                        tool = self._tools.get(stream_event.name)
                        activity = tool.get_activity_description(**stream_event.arguments) if tool else None
                        yield ("tool_call", stream_event.name, stream_event.arguments, activity)
                        print(term.tool_call(stream_event.name, self._fmt_params(stream_event.arguments)))
                        if self._logger:
                            self._logger.tool_use(stream_event.name, stream_event.arguments)

                        # Feed to streaming executor — may start immediately
                        executor.add_tool(stream_event)

                        # Poll for any completed results
                        tid = stream_event.id
                        for result_id, name, args, content, elapsed in executor.get_completed_results():
                            if self._logger:
                                self._logger.tool_result(name, content)
                            yield self._show_tool_result_tu(name, args, content, elapsed)

                    elif isinstance(stream_event, StreamEnd):
                        assistant_message = stream_event.assistant_message
                        if stream_event.text and not tool_uses_seen:
                            full_text = stream_event.text

                # Signal end of text stream
                if full_text:
                    yield ("waiting",)

                # ── Drain remaining tool results ──────────────────────
                for result_id, name, args, content, elapsed in executor.get_remaining_results():
                    if self._logger:
                        self._logger.tool_result(name, content)
                    yield self._show_tool_result_tu(name, args, content, elapsed)

                # Check for abort after tool execution
                if self._aborted:
                    raise AbortedError()

                # ── Build tool result items for message history ───────
                all_results = executor.get_all_results()

                # Abort on too many consecutive errors
                if executor.consecutive_errors >= 5:
                    yield ("error", f"Aborting: 5 consecutive tool failures")
                    self._flush_tool_results(all_results)
                    return

                # Store assistant message
                if assistant_message:
                    self._messages.append(assistant_message)
                    self._persist(self._messages[-1])
                    if self._logger and full_text:
                        self._logger.assistant_text(full_text)

                # No tool calls → terminal
                if not all_results:
                    break

                print()

                # ── STEP 5: Flush tool results to message list ────────
                self._flush_tool_results(all_results)

                # All denied + no text → stop
                if executor.all_denied and not full_text:
                    yield ("text", "All tool calls denied by permission system.")
                    break

        except AbortedError:
            self.cancel_turn()
            raise
        except Exception:
            import traceback
            traceback.print_exc()
            self.cancel_turn()
            yield ("error", "Internal error — turn cancelled. Please try again.")

    # ------------------------------------------------------------------
    # Tool result display
    # ------------------------------------------------------------------

    def _show_tool_result_tu(
        self, name: str, args: dict[str, Any], result: str, elapsed: float = 0,
    ) -> tuple:
        """Show tool result in terminal (multi-line ⎿ format) and return event tuple."""
        is_err = "Error" in result or "error" in result or "denied" in result.lower()
        if is_err:
            print(term.tool_error(result))
        else:
            print(term.tool_done(result))
        if elapsed >= 0.1:
            print(f"  {term._DIM}({elapsed:.1f}s){term._RESET}")
        return ("tool_result", name, args, result)

    def _flush_tool_results(self, items: list[tuple[str, str, dict[str, Any], str]]) -> None:
        """Format tool results via provider and append to message list."""
        # Convert to legacy (id, name, content) format for provider
        # items is now (tid, name, args, content, elapsed) — 5-tuple
        legacy = [(items[i][0], items[i][1], items[i][3]) for i in range(len(items))]
        provider_msgs = self._provider.make_tool_result_messages(legacy)
        self._messages.extend(provider_msgs)
        for msg in provider_msgs:
            self._persist(msg)

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
