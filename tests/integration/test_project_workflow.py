"""
Project-level integration test for mini-cc streaming tool execution.

Simulates a real multi-turn coding session: the agent reads an existing
project structure, modifies files, runs tests, and commits changes.
Every turn is driven by MockProvider with realistic tool-call sequences.

Run:
    python tests/integration/test_project_workflow.py
"""

import io
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.llm.provider import (
    LLMProvider, LLMResponse, ToolCall,
    TextDelta, ToolUseBlock, StreamEnd,
)
from src.tools.registry import ToolRegistry
from src.tools.file_tools import ReadFile, WriteFile, FileEditTool, ListFiles, SearchFiles
from src.tools.shell_tool import RunShell
from src.tools.git_tools import GitDiff
from src.security.permission import PermissionChecker, Mode
from src.session.logger import SessionLogger
from src.agent.loop import Engine
from src.context import build_system_prompt


# ═══════════════════════════════════════════════════════════════════════════
# Test project scaffolding
# ═══════════════════════════════════════════════════════════════════════════

PROJECT_FILES = {
    "src/__init__.py": "",
    "src/models.py": '''\
"""Data models for the todo app."""

from dataclasses import dataclass


@dataclass
class TodoItem:
    id: int
    title: str
    completed: bool = False
    priority: str = "medium"


def create_item(title: str, priority: str = "medium") -> TodoItem:
    return TodoItem(id=0, title=title, priority=priority)
''',
    "src/storage.py": '''\
"""JSON file storage for todos."""

import json
from pathlib import Path


class TodoStorage:
    def __init__(self, path: Path):
        self._path = path
        self._items: list[dict] = []
        self._load()

    def _load(self):
        if self._path.exists():
            self._items = json.loads(self._path.read_text())

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._items, indent=2))

    def add(self, title: str) -> int:
        new_id = max((i["id"] for i in self._items), default=0) + 1
        item = {"id": new_id, "title": title, "completed": False}
        self._items.append(item)
        self._save()
        return new_id

    def list_all(self) -> list[dict]:
        return list(self._items)

    def toggle(self, item_id: int) -> bool:
        for item in self._items:
            if item["id"] == item_id:
                item["completed"] = not item["completed"]
                self._save()
                return True
        return False
''',
    "src/cli.py": '''\
"""CLI for the todo app."""

import sys
from pathlib import Path
from .storage import TodoStorage

DATA_FILE = Path.home() / ".todos.json"


def main():
    store = TodoStorage(DATA_FILE)
    args = sys.argv[1:]

    if not args:
        _show_help()
        return

    cmd = args[0]
    if cmd == "add":
        title = " ".join(args[1:])
        tid = store.add(title)
        print(f"Added todo #{tid}: {title}")
    elif cmd == "list":
        for item in store.list_all():
            mark = "x" if item["completed"] else " "
            print(f"  [{mark}] #{item['id']} {item['title']}")
    elif cmd == "done":
        store.toggle(int(args[1]))
        print(f"Toggled #{args[1]}")
    else:
        _show_help()


def _show_help():
    print("Usage: todo <add|list|done> [...]")
''',
    "tests/__init__.py": "",
    "tests/test_models.py": """\
from src.models import TodoItem, create_item


def test_create_item():
    item = create_item("buy milk", "high")
    assert item.title == "buy milk"
    assert item.priority == "high"
    assert item.completed is False
""",
    "tests/test_storage.py": """\
import tempfile
from pathlib import Path
from src.storage import TodoStorage


def test_add_and_list():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        store = TodoStorage(path)
        tid = store.add("test item")
        items = store.list_all()
        assert len(items) == 1
        assert items[0]["title"] == "test item"


def test_toggle():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        store = TodoStorage(path)
        tid = store.add("test")
        assert store.toggle(tid) is True
        assert store.list_all()[0]["completed"] is True
""",
    "pyproject.toml": """\
[project]
name = "todo-app"
version = "0.1.0"
requires-python = ">=3.10"

[project.scripts]
todo = "src.cli:main"
""",
    "README.md": "# Todo App\n\nA simple CLI todo app.\n",
}


def scaffold_project(root: Path) -> None:
    for relpath, content in PROJECT_FILES.items():
        full = root / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# Mock provider with real streaming event interleaving
# ═══════════════════════════════════════════════════════════════════════════

class RealisticMockProvider(LLMProvider):
    """Mock that yields stream events mimicking real Anthropic streaming."""

    def __init__(self, turns: list[dict[str, Any]]):
        self._turns = turns
        self._turn_idx = 0
        self._call_count = 0
        self.last_messages: list[dict] = []
        self.last_tools: list[dict] = []

    @property
    def provider_name(self) -> str:
        return "realistic_mock"

    def send_message(self, system_prompt, messages, tools, max_tokens=4096):
        events = list(self.send_message_stream(system_prompt, messages, tools, max_tokens))
        text = ""
        tool_calls = []
        assistant_msg = {}
        for ev in events:
            if isinstance(ev, TextDelta):
                text += ev.text
            elif isinstance(ev, ToolUseBlock):
                tool_calls.append(ToolCall(id=ev.id, name=ev.name, arguments=ev.arguments))
            elif isinstance(ev, StreamEnd):
                assistant_msg = ev.assistant_message
        return LLMResponse(text=text, tool_calls=tool_calls, assistant_message=assistant_msg)

    def send_message_stream(self, system_prompt, messages, tools, max_tokens=4096):
        self.last_messages = list(messages)
        self.last_tools = list(tools)
        self._call_count += 1

        if self._turn_idx >= len(self._turns):
            yield TextDelta(text="done.")
            yield StreamEnd(
                assistant_message={"role": "assistant", "content": "done."},
                text="done.",
            )
            return

        turn = self._turns[self._turn_idx]
        self._turn_idx += 1
        text = turn.get("text", "")
        tool_calls = turn.get("tool_calls", [])

        # Stream text in chunks (simulates network)
        chunk = max(1, len(text) // 3) if text else 1
        for i in range(0, len(text), chunk):
            yield TextDelta(text=text[i:i + chunk])

        # Stream tool_use blocks one at a time
        for tool_id, tool_name, tool_args in tool_calls:
            yield ToolUseBlock(id=tool_id, name=tool_name, arguments=tool_args)

        # Build assistant_message
        assistant_message = {"role": "assistant", "content": text or None}
        yield StreamEnd(assistant_message=assistant_message, text=text)

    def make_user_message(self, content: str) -> dict[str, Any]:
        return {"role": "user", "content": content}

    def make_tool_result_messages(self, items):
        return [
            {"role": "tool", "tool_call_id": tid, "content": content}
            for tid, _name, content in items
        ]

    def make_compaction_summary_message(self, summary):
        return {"role": "user", "content": f"<summary>\n{summary}\n</summary>"}

    def compact(self, system_prompt, messages):
        return "summary"

    def tools_for_provider(self, registry):
        return [{"type": "function", "function": {"name": n, "description": d, "parameters": {}}}
                for n, d in registry.list_tools()]


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _make_agent(ws: Path, turns: list[dict], mode: str = "auto") -> tuple[Engine, RealisticMockProvider]:
    reg = ToolRegistry()
    for cls in (ReadFile, WriteFile, FileEditTool, ListFiles, SearchFiles, RunShell, GitDiff):
        reg.register(cls(ws) if cls is not GitDiff else cls(ws))

    tools = [t for t in reg.values()]
    provider = RealisticMockProvider(turns=turns)
    perm = PermissionChecker()
    perm.mode = Mode(mode)
    log = SessionLogger(Path(tempfile.gettempdir()) / f"itest_{os.urandom(4).hex()}.jsonl")
    prompt = build_system_prompt(workspace_dir=ws)

    agent = Engine(
        tools=tools,
        system_prompt=prompt,
        permission_checker=perm,
        provider=provider,
        model="claude-sonnet-4-6",
        tool_registry=reg,
        workspace_dir=ws,
        logger=log,
    )
    return agent, provider


def _suppress_stdout():
    """Redirect stdout to avoid Windows GBK encoding issues with Unicode chars."""
    return io.StringIO()


PASS = 0
FAIL = 0


def ok(label: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {label}")


def fail(label: str, msg: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {label}  --  {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: 3 parallel read-only tools
# ═══════════════════════════════════════════════════════════════════════════

def test_parallel_reads():
    """3 reads in one response — all execute in parallel during stream."""
    print("\n[1] 3 parallel reads in one turn")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        agent, provider = _make_agent(ws, turns=[
            {
                "text": "Let me understand the project structure.",
                "tool_calls": [
                    ("t1", "read_file", {"path": "src/models.py"}),
                    ("t2", "read_file", {"path": "src/storage.py"}),
                    ("t3", "read_file", {"path": "src/cli.py"}),
                ],
            },
            {
                "text": "I see models, storage, and CLI modules.",
            },
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            events = list(agent.submit("explore this project"))
        finally:
            sys.stdout = old_stdout

        # 3 tool_call + 3 tool_result events
        tc = sum(1 for e in events if e[0] == "tool_call")
        tr = sum(1 for e in events if e[0] == "tool_result")
        assert tc == 3, f"expected 3 tool calls, got {tc}"
        assert tr == 3, f"expected 3 tool results, got {tr}"
        ok("3 tool calls dispatched, 3 results returned")

        assert provider._call_count == 2
        ok("2 API calls (tools + final)")

        msgs = agent.get_messages()
        # user + assistant + 3 tool results (OpenAI format) + assistant final = 6
        assert len(msgs) == 6, f"expected 6 messages, got {len(msgs)}"
        ok("6 messages in history (user + asst + 3 tools + final)")


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: Mixed read + write (reads parallel, write serial)
# ═══════════════════════════════════════════════════════════════════════════

def test_mixed_read_write():
    """Read + write in one response — reads run during stream, write after."""
    print("\n[2] Mixed read + write — reads parallel, write serial")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        agent, provider = _make_agent(ws, turns=[
            {
                "text": "Reading models then updating CLI.",
                "tool_calls": [
                    ("t1", "read_file", {"path": "src/models.py"}),
                    ("t2", "read_file", {"path": "src/cli.py"}),
                    ("t3", "write_file", {"path": "src/cli.py", "content": "# updated\ndef main():\n    pass\n"}),
                ],
            },
            {
                "text": "CLI updated with priority support.",
            },
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            events = list(agent.submit("add priority to CLI"))
        finally:
            sys.stdout = old_stdout

        reads = [e for e in events if e[0] == "tool_result" and e[1] == "read_file"]
        writes = [e for e in events if e[0] == "tool_result" and e[1] == "write_file"]
        assert len(reads) == 2, f"expected 2 reads, got {len(reads)}"
        assert len(writes) == 1, f"expected 1 write, got {len(writes)}"
        ok("2 reads + 1 write all executed")

        updated = (ws / "src" / "cli.py").read_text(encoding="utf-8")
        assert "updated" in updated
        ok("write_file persisted to disk")

        assert provider._call_count == 2
        ok("2 API calls")


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Full multi-turn workflow (search -> read -> edit -> test -> diff)
# ═══════════════════════════════════════════════════════════════════════════

def test_full_workflow():
    """6-turn workflow: search, read, edit, test, git status, summary."""
    print("\n[3] Full 6-turn workflow -- search -> read -> edit -> test -> git")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        agent, provider = _make_agent(ws, turns=[
            # Turn 1: search
            {"text": "Searching for priority usage.",
             "tool_calls": [("t1", "search_files", {"query": "priority", "path": "."})]},
            # Turn 2: read
            {"text": "Found it. Reading models.",
             "tool_calls": [("t2", "read_file", {"path": "src/models.py"})]},
            # Turn 3: write updated models.py with new due_date field
            {"text": "Adding due_date field.",
             "tool_calls": [("t3", "write_file", {
                 "path": "src/models.py",
                 "content": (
                     '"""Data models for the todo app."""\n\n'
                     'from dataclasses import dataclass\n\n\n'
                     '@dataclass\n'
                     'class TodoItem:\n'
                     '    id: int\n'
                     '    title: str\n'
                     '    completed: bool = False\n'
                     '    priority: str = "medium"\n'
                     '    due_date: str | None = None\n\n\n'
                     'def create_item(title: str, priority: str = "medium") -> TodoItem:\n'
                     '    return TodoItem(id=0, title=title, priority=priority)\n'
                 ),
             })]},
            # Turn 4: run tests
            {"text": "Running tests.",
             "tool_calls": [("t4", "run_shell", {"command": f"cd {ws} && python -m pytest tests/ -v 2>&1 || echo 'tests done'"})]},
            # Turn 5: git status
            {"text": "Checking git status.",
             "tool_calls": [("t5", "run_shell", {"command": f"cd {ws} && git init && git add -A && git status"})]},
            # Turn 6: final summary
            {"text": "Added due_date field to TodoItem. All tests pass. Changes staged."},
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            result = agent.run("add due_date field, run tests, review changes")
        finally:
            sys.stdout = old_stdout

        assert result is not None
        assert "due_date" in result.lower()
        ok("final response mentions due_date")

        assert provider._call_count == 6
        ok("6 API calls across full workflow")

        models = (ws / "src" / "models.py").read_text(encoding="utf-8")
        assert "due_date" in models
        ok("edit_file actually modified models.py")

        msgs = agent.get_messages()
        assert len(msgs) >= 8
        ok(f"{len(msgs)} messages accumulated")


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Streaming event order
# ═══════════════════════════════════════════════════════════════════════════

def test_streaming_event_order():
    """Verify text deltas arrive before tool_results (streaming behavior)."""
    print("\n[4] Streaming event order -- text before tool_results")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        agent, provider = _make_agent(ws, turns=[
            {
                "text": "Reading project files...",
                "tool_calls": [
                    ("t1", "read_file", {"path": "README.md"}),
                    ("t2", "read_file", {"path": "pyproject.toml"}),
                ],
            },
            {"text": "This is a todo app."},
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            events = list(agent.submit("what is this project?"))
        finally:
            sys.stdout = old_stdout

        seq = [e[0] for e in events]
        print(f"  Sequence: {' -> '.join(seq)}")

        assert seq[0] == "text", f"first event should be text, got {seq[0]}"
        ok("first event is text delta (streaming)")

        tc = seq.count("tool_call")
        tr = seq.count("tool_result")
        assert tc == tr == 2, f"expected 2 each, got {tc} calls, {tr} results"
        ok("2 tool calls matched with 2 results")

        assert "waiting" in seq
        ok("'waiting' event present")


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: Permission boundary
# ═══════════════════════════════════════════════════════════════════════════

def test_permission_boundary():
    """Plan mode: reads allowed, writes denied."""
    print("\n[5] Permission boundary -- plan mode blocks writes")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        agent, provider = _make_agent(ws, mode="plan", turns=[
            {
                "text": "",
                "tool_calls": [
                    ("t1", "write_file", {"path": "README.md", "content": "hacked"}),
                    ("t2", "write_file", {"path": "src/cli.py", "content": "hacked"}),
                ],
            },
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            result = agent.run("update the readme")
        finally:
            sys.stdout = old_stdout

        assert result is not None
        assert "denied" in result.lower(), f"expected 'denied' in {result!r}"
        ok("write denied in plan mode")

        assert provider._call_count == 1
        ok("1 API call -- stops after all-denied")

        readme = (ws / "README.md").read_text(encoding="utf-8")
        assert "hacked" not in readme
        ok("file not modified (permission enforced)")


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: Many parallel reads
# ═══════════════════════════════════════════════════════════════════════════

def test_many_parallel_reads():
    """6 read_file calls in one response — all execute concurrently."""
    print("\n[6] 6 parallel reads -- all execute concurrently")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        files = list(PROJECT_FILES.keys())[:6]
        tool_calls = [(f"t{i}", "read_file", {"path": files[i]}) for i in range(len(files))]

        agent, provider = _make_agent(ws, turns=[
            {"text": "Reading all files...", "tool_calls": tool_calls},
            {"text": "All files read successfully."},
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            start = time.time()
            result = agent.run("read all files")
            elapsed = time.time() - start
        finally:
            sys.stdout = old_stdout

        assert result == "All files read successfully."
        assert provider._call_count == 2
        ok(f"6 reads, 2 calls, {elapsed:.3f}s")


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Tool execution error doesn't crash the loop
# ═══════════════════════════════════════════════════════════════════════════

def test_tool_error_recovery():
    """A tool error is returned as content and the loop continues."""
    print("\n[7] Tool error recovery -- error returned, loop continues")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)

        agent, provider = _make_agent(ws, turns=[
            {
                "text": "Let me read a missing file.",
                "tool_calls": [
                    ("t1", "read_file", {"path": "nonexistent.txt"}),
                ],
            },
            {"text": "That file doesn't exist. Let me try something else."},
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            result = agent.run("read nonexistent file")
        finally:
            sys.stdout = old_stdout

        assert result == "That file doesn't exist. Let me try something else."
        assert provider._call_count == 2
        ok("tool error returned as content, loop continued")


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Verify assistant_message stored correctly in history
# ═══════════════════════════════════════════════════════════════════════════

def test_message_history_correct():
    """After multi-turn workflow, message history is valid."""
    print("\n[8] Message history correctness after multi-turn workflow")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        scaffold_project(ws)

        agent, provider = _make_agent(ws, turns=[
            {"text": "Reading storage...",
             "tool_calls": [("t1", "read_file", {"path": "src/storage.py"})]},
            {"text": "The storage module uses JSON. Let me read models too.",
             "tool_calls": [("t2", "read_file", {"path": "src/models.py"})]},
            {"text": "Both modules look good. The project is well-structured."},
        ])

        old_stdout = sys.stdout
        sys.stdout = _suppress_stdout()
        try:
            result = agent.run("review the project")
        finally:
            sys.stdout = old_stdout

        msgs = agent.get_messages()
        # Expected: user, assistant(tc1), tool_results, assistant(tc2), tool_results, assistant(final)
        # = 6 messages
        assert len(msgs) == 6, f"expected 6 messages, got {len(msgs)}"
        ok(f"6 messages: user + 2x(assistant+tool_results) + final")

        # Verify roles
        roles = [m.get("role") for m in msgs]
        assert roles[0] == "user"
        assert roles[1] == "assistant"  # turn 1 assistant
        assert roles[5] == "assistant"  # final assistant
        ok("correct role sequence: user -> assistant -> ... -> assistant")


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Force UTF-8 for Windows terminal
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=" * 55)
    print("Mini-CC Streaming Tool Execution -- Integration Tests")
    print("=" * 55)

    test_parallel_reads()
    test_mixed_read_write()
    test_full_workflow()
    test_streaming_event_order()
    test_permission_boundary()
    test_many_parallel_reads()
    test_tool_error_recovery()
    test_message_history_correct()

    print(f"\n{'=' * 55}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 55}")

    if FAIL:
        sys.exit(1)
