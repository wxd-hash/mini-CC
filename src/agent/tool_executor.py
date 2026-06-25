"""Streaming tool executor — starts running tools while the model is still streaming.

Matches claude-code's StreamingToolExecutor pattern:
- Concurrency-safe (read-only) tools can start immediately when their block arrives
- Non-safe tools are queued and run serially after the stream ends
- Results are collected in stream order for message history
"""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any

from src.llm.provider import ToolUseBlock
from src.tools.safety import StuckDetector, StaleReadDetector


class StreamingToolExecutor:
    """Executes tool calls as their blocks arrive in the model stream.

    Usage::

        executor = StreamingToolExecutor(tools, permissions)

        for event in stream:
            if isinstance(event, ToolUseBlock):
                executor.add_tool(event)
                for result in executor.get_completed_results():
                    show(result)
            elif isinstance(event, TextDelta):
                print(event.text)

        for result in executor.get_remaining_results():
            show(result)

        tool_results = executor.get_all_results()
    """

    def __init__(
        self,
        tools: dict[str, Any],
        permission_checker: Any,
        logger: Any = None,
        max_workers: int = 10,
    ) -> None:
        self._tools = tools
        self._permissions = permission_checker
        self._logger = logger

        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future] = {}       # tool_use_id → Future
        self._blocks: dict[str, ToolUseBlock] = {}   # tool_use_id → block
        self._results: dict[str, str] = {}           # tool_use_id → result content
        self._order: list[str] = []                  # tool_use_id in stream order
        self._denied: dict[str, str] = {}            # tool_use_id → tool_name

        self._running_concurrent: set[str] = set()   # ids of running concurrent tools
        self._pending_serial: list[ToolUseBlock] = []  # queued for after-stream
        self._yielded: set[str] = set()              # ids already returned to caller

        self._stuck = StuckDetector()
        self._stale = StaleReadDetector()
        self._consecutive_errors = 0
        self._max_consecutive_errors = 5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_tool(self, block: ToolUseBlock) -> None:
        """Enqueue a tool_use block. May start executing immediately."""
        self._order.append(block.id)
        self._blocks[block.id] = block

        tool = self._tools.get(block.name)

        # Permission check for denied tools
        if tool is None:
            self._denied[block.id] = block.name
            self._results[block.id] = (
                f"ERROR: Unknown tool '{block.name}'. "
                f"Available tools: {', '.join(sorted(self._tools.keys()))}."
            )
            return

        perm_result = self._permissions.check(tool, block.arguments)
        if perm_result == "deny":
            self._denied[block.id] = block.name
            if block.name == "run_shell":
                from src.security.permission import _is_self_destructive
                cmd = block.arguments.get("command", "")
                if _is_self_destructive(cmd):
                    import os
                    self._results[block.id] = (
                        f"BLOCKED: 会杀死 agent 自身（PID={os.getpid()}）。"
                        f"用 taskkill /PID <目标PID> 杀特定进程，不要用 /IM python 全部杀。"
                    )
                    return
            self._results[block.id] = "Permission denied."
            return

        # Check if stuck
        if self._stuck.check(block.name, block.arguments):
            self._results[block.id] = (
                f"WARNING: {block.name} called 3x with same args."
            )
            return

        # Decide: can start now, or must wait?
        if tool.is_read_only() and not self._pending_serial:
            # Can run concurrently with other read-only tools
            self._start_tool(block)
        else:
            # Must wait — queue for serial execution later
            self._pending_serial.append(block)

    def get_completed_results(self) -> list[tuple[str, str, dict[str, Any], str]]:
        """Non-blocking poll: return (tool_use_id, tool_name, arguments, result_content)
        for any tools that have finished. Marked as yielded so get_remaining_results
        won't return them again."""
        completed = []
        done_ids = [
            tid for tid, fut in self._futures.items()
            if fut.done() and tid not in self._results
        ]
        for tid in done_ids:
            block = self._blocks[tid]
            try:
                content = self._futures[tid].result()
            except Exception as exc:
                content = f"Tool execution error: {exc}"
            self._results[tid] = content
            self._running_concurrent.discard(tid)
            self._yielded.add(tid)

            completed.append((tid, block.name, block.arguments, content))
        return completed

    def get_remaining_results(self) -> list[tuple[str, str, dict[str, Any], str]]:
        """Block until all pending tools finish. Returns results in stream order.

        Only returns results that haven't been yielded by get_completed_results().
        """
        # First, wait for any running concurrent tools
        for tid in list(self._running_concurrent):
            if tid not in self._results:
                block = self._blocks[tid]
                try:
                    content = self._futures[tid].result()
                except Exception as exc:
                    content = f"Tool execution error: {exc}"
                self._results[tid] = content

        # Now run serial tools one at a time
        for block in self._pending_serial:
            content = self._execute_one(block)
            self._results[block.id] = content

        self._pending_serial.clear()
        self._running_concurrent.clear()

        # Return results in stream order, excluding already-yielded
        remaining = [
            (tid, self._blocks[tid].name, self._blocks[tid].arguments, self._results[tid])
            for tid in self._order
            if tid in self._results and tid not in self._yielded
        ]
        self._yielded.update(tid for tid, _, _, _ in remaining)
        return remaining

    def get_all_results(self) -> list[tuple[str, str, dict[str, Any], str]]:
        """Return all results collected so far (doesn't wait for pending)."""
        return [
            (tid, self._blocks[tid].name, self._blocks[tid].arguments, self._results[tid])
            for tid in self._order
            if tid in self._results
        ]

    def discard(self) -> None:
        """Cancel all pending/running work."""
        for fut in self._futures.values():
            fut.cancel()
        self._futures.clear()
        self._running_concurrent.clear()
        self._pending_serial.clear()
        self._yielded.clear()

    @property
    def all_denied(self) -> bool:
        """True if every tool in this batch was denied."""
        if not self._order:
            return False
        return all(
            tid in self._denied
            or "Permission denied." in self._results.get(tid, "")
            for tid in self._order
        )

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_tool(self, block: ToolUseBlock) -> None:
        """Submit a tool for immediate execution in the thread pool."""
        fut = self._executor.submit(self._execute_one, block)
        self._futures[block.id] = fut
        if block.id not in self._denied:
            self._running_concurrent.add(block.id)

    def _execute_one(self, block: ToolUseBlock) -> str:
        """Execute a single tool call and return the result content string."""
        tool = self._tools.get(block.name)
        if tool is None:
            self._consecutive_errors += 1
            return f"Unknown tool: {block.name}"

        try:
            result = tool.execute(**block.arguments)
            self._consecutive_errors = 0

            content = result.content if hasattr(result, 'content') else str(result)

            # Stale-read detection
            stale_warning = self._stale.check(block.name, content)
            if stale_warning:
                return stale_warning

            return content

        except Exception as exc:
            self._consecutive_errors += 1
            error_msg = f"Tool error: {exc}"
            if self._logger:
                self._logger.error(error_msg)
            return error_msg


# ---------------------------------------------------------------------------
# Legacy batch execution (for non-streaming use)
# ---------------------------------------------------------------------------

def partition_tool_calls(
    tool_uses: list[Any],
    tools: dict[str, Any],
) -> list[tuple[bool, list[Any]]]:
    """Partition tool calls into (is_concurrent, [calls]) batches.

    Consecutive read-only tools form parallel batches.
    Non-read-only tools each get their own serial batch.
    """
    batches: list[tuple[bool, list[Any]]] = []
    for tu in tool_uses:
        t = tools.get(tu.name)
        is_concurrent = t is not None and t.is_read_only()
        if batches and batches[-1][0] == is_concurrent and is_concurrent:
            batches[-1][1].append(tu)
        else:
            batches.append((is_concurrent, [tu]))
    return batches
