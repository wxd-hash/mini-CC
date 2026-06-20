"""MiniClaudeAgent — processes one user turn via LLM ↔ tool loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.config import MAX_MESSAGES_BEFORE_COMPACT, KEEP_RECENT_MESSAGES, MAX_TOOL_ROUNDS
from src.context import build_system_prompt, compact_messages, micro_compact
from src.llm.provider import LLMProvider
from src.security.permission import PermissionManager
from src.session.logger import SessionLogger
from src import terminal as term


class MiniClaudeAgent:
    """Agent that processes one user message through the LLM-tool loop."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission: PermissionManager,
        logger: SessionLogger,
        workspace_dir: Path,
        provider: LLMProvider,
        max_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self.tool_registry = tool_registry
        self.permission = permission
        self.logger = logger
        self._workspace_dir = workspace_dir.resolve()
        self._provider = provider
        self.max_rounds = max_rounds
        self._sessions_dir: Path | None = None
        self._messages: list[dict[str, Any]] = []
        self._cached_prompt: str | None = None
        self._last_tool_calls: list[tuple[str, str]] = []  # (name, json_args)
        self._consecutive_errors = 0
        self.MAX_CONSECUTIVE_ERRORS = 5
        self._read_results: dict[str, dict[str, int]] = {}  # tool_name → hash → count
        self.MAX_STALE_READS = 3
        self._consecutive_strikes = 0  # progressive intervention counter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> str | None:
        """Process one user turn.  Returns the final assistant text or None."""
        self.permission.reset_for_turn()
        self._messages.append(self._provider.make_user_message(user_input))
        self.logger.user_input(user_input)

        tools = self._provider.tools_for_provider(self.tool_registry)

        for _ in range(self.max_rounds):
            self._maybe_compact()
            system = self._system_prompt

            try:
                response = self._provider.send_message(
                    system_prompt=system,
                    messages=self._messages,
                    tools=tools,
                    max_tokens=4096,
                )
            except Exception as exc:
                print(term.error(f"API error: {exc}"))
                self.logger.error(str(exc))
                return None

            # Log any interleaved text
            if response.text:
                self.logger.assistant_text(response.text)

            # No tool calls — final answer (text already streamed by provider)
            if not response.tool_calls:
                if response.text:
                    print()
                self._messages.append(response.assistant_message)
                return response.text

            # Separate streamed text from tool output
            if response.text:
                print()

            # Execute tool calls
            tool_msgs, final_text = self._execute_tools(response)

            self._messages.append(response.assistant_message)
            self._messages.extend(tool_msgs)

            if final_text is not None:
                if final_text:
                    print(term.denied(final_text))
                return final_text

        # Max rounds — finalize without tools
        print()
        print(term.info(f"[max {self.max_rounds} tool calls reached, finishing]"))

        self._maybe_compact()
        system = build_system_prompt(self._workspace_dir, self._sessions_dir)
        try:
            response = self._provider.send_message(
                system_prompt=system,
                messages=self._messages,
                tools=[],
                max_tokens=4096,
            )
        except Exception as exc:
            print(term.error(f"API error: {exc}"))
            self.logger.error(str(exc))
            return None

        if response.text:
            print()
        self._messages.append(response.assistant_message)
        self.logger.assistant_text(response.text)
        return response.text

    def resume(self, history: list[dict[str, Any]]) -> None:
        """Load previous session messages into the agent's history."""
        for msg in history:
            if msg["role"] == "user":
                self._messages.append(self._provider.make_user_message(msg["content"]))
            else:
                self._messages.append({"role": "assistant", "content": msg["content"]})

    @property
    def _system_prompt(self) -> str:
        """Lazily-built, cached system prompt.  Call ``reload()`` to refresh."""
        if self._cached_prompt is None:
            self._cached_prompt = build_system_prompt(
                self._workspace_dir, self._sessions_dir
            )
        return self._cached_prompt

    def reload(self) -> None:
        """Force-rebuild the system prompt on the next API call
        (e.g. after editing CLAUDE.md or after compaction restructures messages)."""
        self._cached_prompt = None
        print(term.info("[system prompt reloaded]"))

    def set_sessions_dir(self, path: Path) -> None:
        """Set the sessions directory for memory loading."""
        self._sessions_dir = path

    def clear(self) -> None:
        """Reset conversation history."""
        self._messages.clear()
        print(term.info("[session cleared]"))

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    def _maybe_compact(self) -> None:
        if len(self._messages) <= MAX_MESSAGES_BEFORE_COMPACT:
            return

        before = len(self._messages)

        # Step 1: free in-memory truncation of old tool results
        self._messages = micro_compact(self._messages, keep_recent=8)

        # Step 2: LLM summarization to reduce message count
        print(term.info("[compacting conversation...]"), flush=True)
        system = build_system_prompt(self._workspace_dir, self._sessions_dir)

        self._messages = compact_messages(
            provider=self._provider,
            system_prompt=system,
            messages=self._messages,
            keep_recent=KEEP_RECENT_MESSAGES,
        )

        after = len(self._messages)
        self.logger.compact(before, after)
        print(term.compact(before, after))
        self.reload()  # rebuild prompt so the LLM sees the summary

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tools(
        self,
        response: Any,
    ) -> tuple[list[dict[str, Any]], str | None]:
        items: list[tuple[str, str, str]] = []
        all_denied = True
        stuck_detected = False
        stuck_tool_name = ""

        for tc in response.tool_calls:
            import json as _json
            call_key = (tc.name, _json.dumps(tc.arguments, sort_keys=True))
            self._last_tool_calls.append(call_key)
            recent = self._last_tool_calls[-5:]
            if recent.count(call_key) >= 3:
                stuck_detected = True
                stuck_tool_name = tc.name
                self._last_tool_calls.clear()
                break  # stop processing tools, go to intervention

            self.logger.tool_use(tc.name, tc.arguments)
            print(term.tool_header(tc.name, self._fmt_params(tc.arguments)))

            if not self.permission.check(tc.name, tc.arguments):
                self.logger.permission_denied(tc.name)
                items.append((tc.id, tc.name, f"Permission denied: {tc.name}"))
                continue

            all_denied = False
            result = self._call_tool(tc.name, tc.arguments)
            print(term.tool_result(f"→ {self._truncate(result, 300)}"))
            items.append((tc.id, tc.name, result))

            err_count = self._consecutive_errors
            if err_count >= self.MAX_CONSECUTIVE_ERRORS:
                self._consecutive_errors = 0
                abort_msg = (
                    f"Aborting: {err_count} consecutive tool failures. "
                    "Check the error pattern and fix the root cause."
                )
                print(term.error(abort_msg))
                tool_msgs = self._provider.make_tool_result_messages(items)
                return tool_msgs, abort_msg

            if err_count == 3:
                warning = (
                    "WARNING: 3 consecutive tool calls have failed. "
                    "Stop retrying. Analyze what each error means and "
                    "choose a different approach."
                )
                items.append(("err_warn", tc.name, warning))
                print(term.info(warning))
                tool_msgs = self._provider.make_tool_result_messages(items)
                return tool_msgs, None  # continue, LLM sees warning

            if err_count == 4:
                print(term.info("[consecutive failures — forcing reflection]"))
                reflect_text = (
                    "4 tool calls in a row have failed. Answer these:\n"
                    "1. What exact errors are you getting?\n"
                    "2. What is the root cause of these errors?\n"
                    "3. What can you change to avoid all of them?\n"
                    "Explain your fix, then execute it."
                )
                items.append(("err_reflect", tc.name, reflect_text))
                tool_msgs = self._provider.make_tool_result_messages(items)
                return tool_msgs, None  # continue, LLM sees reflection prompt

        print()

        # --- Progressive intervention for stuck loops ---
        if stuck_detected:
            self._consecutive_strikes += 1

            if self._consecutive_strikes == 1:
                warning = (
                    f"WARNING: {stuck_tool_name} was called 3 times with "
                    "identical arguments. The results haven't changed. "
                    "Consider a different approach."
                )
                items.append(("strike1", stuck_tool_name, warning))
                print(term.info(warning))
                tool_msgs = self._provider.make_tool_result_messages(items)
                return tool_msgs, None  # continue loop, LLM sees warning

            if self._consecutive_strikes == 2:
                print(term.info("[stuck — forcing reflection]"))
                reflect_text = (
                    "You are stuck in a loop. Before continuing, answer these:\n"
                    "1. What approaches have you tried so far?\n"
                    "2. What did each attempt teach you?\n"
                    "3. What is a DIFFERENT approach you haven't tried yet?\n"
                    "4. Explain your new plan, then execute it.\n\n"
                    "Do NOT repeat any approach that has already failed."
                )
                items.append(("strike2", stuck_tool_name, reflect_text))
                tool_msgs = self._provider.make_tool_result_messages(items)
                return tool_msgs, None  # continue loop, LLM sees reflection prompt

            # Level 3: hard abort
            self._consecutive_strikes = 0
            abort_msg = (
                f"Task stopped after 3 repeated patterns. "
                "Too many approaches failed. Please re-state your goal."
            )
            items.append(("strike3", stuck_tool_name, abort_msg))
            print(term.error(abort_msg))
            tool_msgs = self._provider.make_tool_result_messages(items)
            return tool_msgs, abort_msg  # abort → end turn

        # No loop detected → reset counter
        self._consecutive_strikes = 0

        tool_msgs = self._provider.make_tool_result_messages(items)

        if all_denied and not response.text:
            return tool_msgs, "All tool calls denied by permission system."

        return tool_msgs, None

    def _call_tool(self, name: str, params: dict[str, Any]) -> str:
        try:
            tool = self.tool_registry.get_tool(name)
            result = tool.run(params)
            self._consecutive_errors = 0  # reset on success
            self.logger.tool_result(name, result)

            # Stale-read detection: warn if same content returned repeatedly
            if name in ("read_file", "search_files", "git_diff"):
                h = hashlib.sha256(result[:2000].encode()).hexdigest()
                counts = self._read_results.setdefault(name, {})
                counts[h] = counts.get(h, 0) + 1
                if counts[h] >= self.MAX_STALE_READS:
                    return (
                        f"WARNING: {name} has returned the same content "
                        f"{self.MAX_STALE_READS} times. You are re-reading "
                        f"unchanged data. Stop reading and act on what you "
                        f"already know.\n\n{result}"
                    )

            return result
        except Exception as exc:
            self._consecutive_errors += 1
            error_msg = f"Tool error: {exc}"
            self.logger.error(error_msg)
            return error_msg

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
