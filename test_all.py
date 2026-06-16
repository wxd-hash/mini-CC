"""Quick self-test for Mini Claude Code — no API key required.

Run:   .venv/Scripts/python test_all.py
"""

import json
import sys
import tempfile
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from typing import Any

from src.llm.provider import LLMProvider, LLMResponse, ToolCall

TESTS_PASSED = 0
TESTS_FAILED = 0


# ======================================================================
# Mock LLM Provider — returns preset responses in sequence
# ======================================================================

class MockProvider(LLMProvider):
    """Configurable mock that returns responses from a queue.

    Each entry in *responses* is either:
    - ``LLMResponse`` — returned directly by ``send_message()``
    - ``Exception`` — raised by ``send_message()``
    - ``str`` — returned as ``LLMResponse(text=the_string)``

    *compact_text* is the text returned by the ``compact()`` method.
    """

    def __init__(
        self,
        responses: list[LLMResponse | Exception | str] | None = None,
        compact_text: str = "summary of old messages",
    ) -> None:
        self.responses: list[LLMResponse | Exception | str] = list(responses or [])
        self._call_count = 0
        self.compact_text = compact_text
        self.last_messages: list[dict[str, Any]] = []
        self.last_tools: list[dict[str, Any]] = []
        self.last_system: str = ""

    @property
    def provider_name(self) -> str:
        return "mock"

    def send_message(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.last_system = system_prompt
        self.last_messages = list(messages)
        self.last_tools = list(tools)
        self._call_count += 1

        if not self.responses:
            return LLMResponse(text="mock default response")

        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return LLMResponse(text=item)
        return item

    def make_user_message(self, content: str) -> dict[str, Any]:
        return {"role": "user", "content": content}

    def make_tool_result_messages(
        self, items: list[tuple[str, str, str]]
    ) -> list[dict[str, Any]]:
        return [
            {"role": "tool", "tool_call_id": tid, "content": content}
            for tid, _name, content in items
        ]

    def make_compaction_summary_message(self, summary: str) -> dict[str, Any]:
        return {"role": "user", "content": f"<summary>\n{summary}\n</summary>"}

    def compact(self, system_prompt: str, messages: list[dict[str, Any]]) -> str:
        return self.compact_text

    def tools_for_provider(self, registry: Any) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": n, "description": d, "parameters": {}}}
            for n, d in registry.list_tools()
        ]


def ok(label: str) -> None:
    global TESTS_PASSED
    TESTS_PASSED += 1
    print(f"  [PASS] {label}")


def fail(label: str, msg: str = "") -> None:
    global TESTS_FAILED
    TESTS_FAILED += 1
    print(f"  [FAIL] {label}  --  {msg}")


# ======================================================================
# 1. Sandbox
# ======================================================================
def test_sandbox():
    print("\n[1] Workspace sandbox")
    from src.tools.file_tools import safe_path

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp).resolve()

        # allowed
        assert safe_path("a.txt", ws) == ws / "a.txt"
        ok("relative path allowed")

        assert safe_path("sub/deep/a.txt", ws) == ws / "sub" / "deep" / "a.txt"
        ok("nested relative path allowed")

        # escape via ..
        try:
            safe_path("../secret.txt", ws)
            fail(".. escape", "should have raised ValueError")
        except ValueError:
            ok("../ escape rejected")

        # absolute path
        try:
            safe_path("/etc/passwd", ws)
            fail("absolute path", "should have raised ValueError")
        except ValueError:
            ok("absolute path rejected")


# ======================================================================
# 2. Tools (unit tests, no LLM)
# ======================================================================
def test_tools():
    print("\n[2] Tools")

    from src.tools.file_tools import ReadFile, WriteFile, ListFiles, SearchFiles
    from src.tools.shell_tool import RunShell
    from src.tools.git_tools import GitDiff
    from src.tools.base import Tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)

        # --- WriteFile ---
        w = WriteFile(ws)
        r = w.run({"path": "hello.txt", "content": "Hello World"})
        assert "Wrote" in r
        ok("write_file creates file")

        # --- ReadFile ---
        r = ReadFile(ws).run({"path": "hello.txt"})
        assert "Hello World" == r
        ok("read_file reads back")

        # --- ListFiles ---
        w.run({"path": "sub/nested.txt", "content": "deep"})
        r = ListFiles(ws).run({"path": "."})
        assert "hello.txt" in r and "sub/" in r
        ok("list_files shows files and dirs")

        # --- SearchFiles ---
        r = SearchFiles(ws).run({"query": "Hello"})
        assert "hello.txt:1: Hello World" in r
        ok("search_files finds match")

        # --- RunShell ---
        r = RunShell(ws).run({"command": "echo ok"})
        assert "exit_code: 0" in r and "ok" in r
        ok("run_shell executes command")

        # --- Tool ABC ---
        assert isinstance(ReadFile(ws), Tool)
        ok("tools are Tool subclasses")


# ======================================================================
# 3. Tool registry + schemas
# ======================================================================
def test_registry():
    print("\n[3] Tool registry")
    from src.tools.registry import ToolRegistry
    from src.tools.file_tools import ReadFile, WriteFile

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        reg = ToolRegistry()
        reg.register(ReadFile(ws))
        reg.register(WriteFile(ws))

        # list_tools
        names = [n for n, _ in reg.list_tools()]
        assert "read_file" in names and "write_file" in names
        ok("list_tools returns registered names")

        # get_tool
        t = reg.get_tool("read_file")
        assert t.name == "read_file"
        ok("get_tool lookup works")

        # anthropic schema
        schemas = reg.to_anthropic()
        assert schemas[0]["input_schema"]["type"] == "object"
        ok("to_anthropic has input_schema key")

        # openai schema
        schemas = reg.to_openai()
        assert schemas[0]["type"] == "function"
        assert "parameters" in schemas[0]["function"]
        ok("to_openai has function.parameters key")


# ======================================================================
# 4. Permission system
# ======================================================================
def test_permission():
    print("\n[4] Permission system")
    from src.security.permission import PermissionManager, Mode

    pm = PermissionManager()

    # plan
    pm.mode = Mode.PLAN
    assert pm.check("read_file", {"path": "x"})
    assert pm.check("search_files", {"query": "x"})
    assert pm.check("git_diff", {"path": "."})
    assert not pm.check("write_file", {"path": "x"})
    assert not pm.check("run_shell", {"command": "ls"})
    ok("plan mode: read/search/git OK, write/shell denied")

    # ask (read-only tests; prompt requires interactive)
    pm.mode = Mode.ASK
    assert pm.check("read_file", {"path": "x"})
    assert pm.check("list_files", {"path": "."})
    ok("ask mode: read tools auto-allowed")

    # auto
    pm.mode = Mode.AUTO
    assert pm.check("write_file", {"path": "x"})
    assert pm.check("run_shell", {"command": "ls"})
    ok("auto mode: write + low-risk shell auto-allowed")

    # high-risk detection
    assert PermissionManager._is_high_risk("rm -rf /")
    assert PermissionManager._is_high_risk("git push origin main")
    assert not PermissionManager._is_high_risk("git status")
    assert not PermissionManager._is_high_risk("echo hello")
    ok("high-risk command detection correct")


# ======================================================================
# 5. Session logger
# ======================================================================
def test_logger():
    print("\n[5] Session logger")
    from src.session.logger import SessionLogger

    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = Path(f.name)

    log = SessionLogger(path)
    log.user_input("hello")
    log.assistant_text("hi there")
    log.tool_use("read_file", {"path": "x"})
    log.tool_result("read_file", "content")
    log.permission_denied("write_file")
    log.error("something broke")
    log.compact(42, 11)
    log.close()

    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            events.append(json.loads(line.strip()))

    assert len(events) == 7
    assert events[0]["type"] == "user_input"
    assert events[2]["type"] == "tool_use"
    assert events[4]["type"] == "permission_denied"
    assert events[5]["type"] == "error"
    assert events[6]["type"] == "compact"
    assert all("ts" in e for e in events)
    ok("7 events logged with timestamps")

    path.unlink()


# ======================================================================
# 6. LLM providers (instantiation only, no API call)
# ======================================================================
def test_providers():
    print("\n[6] LLM providers (no API call)")
    from src.llm.anthropic_provider import AnthropicProvider
    from src.llm.openai_provider import OpenAIProvider

    ap = AnthropicProvider(model="claude-sonnet-4-5")
    assert ap.provider_name == "anthropic"
    assert ap.model == "claude-sonnet-4-5"
    ok("AnthropicProvider instantiated")

    op = OpenAIProvider(model="deepseek-chat", base_url="https://api.deepseek.com/v1")
    assert op.provider_name == "openai"
    assert op.model == "deepseek-chat"
    ok("OpenAIProvider instantiated")

    # message builders
    msg = ap.make_user_message("hello")
    assert msg["role"] == "user"
    ok("Anthropic make_user_message")

    msg = op.make_user_message("hello")
    assert isinstance(msg["content"], str)
    ok("OpenAI make_user_message")


# ======================================================================
# 7. Context (CLAUDE.md)
# ======================================================================
def test_context():
    print("\n[7] Context & CLAUDE.md")
    from src.context import load_project_instructions, build_system_prompt

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)

        # No CLAUDE.md
        assert load_project_instructions(ws) == ""
        ok("empty workspace returns ''")

        prompt = build_system_prompt(ws)
        assert "Mini Claude Code" in prompt
        ok("base system prompt always included")

        # With CLAUDE.md
        (ws / "CLAUDE.md").write_text("Test instructions", encoding="utf-8")
        instructions = load_project_instructions(ws)
        assert "Test instructions" in instructions
        ok("CLAUDE.md loaded")

        prompt = build_system_prompt(ws)
        assert "project_instructions" in prompt
        ok("system prompt includes project_instructions block")


# ======================================================================
# 8. Agent instantiation (smoke test, no API call)
# ======================================================================
def test_agent():
    print("\n[8] Agent instantiation (no API call)")
    from src.agent.loop import MiniClaudeAgent
    from src.llm.anthropic_provider import AnthropicProvider
    from src.tools.registry import ToolRegistry
    from src.tools.file_tools import ReadFile
    from src.security.permission import PermissionManager
    from src.session.logger import SessionLogger

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        reg = ToolRegistry()
        reg.register(ReadFile(ws))
        perm = PermissionManager()
        log = SessionLogger(Path(tmp) / "test.jsonl")
        provider = AnthropicProvider(model="claude-sonnet-4-5")

        agent = MiniClaudeAgent(reg, perm, log, ws, provider)
        assert agent is not None

        log.close()
    ok("MiniClaudeAgent instantiated with AnthropicProvider")


# ======================================================================
# 9. Agent loop — full LLM ↔ tool cycle via MockProvider
# ======================================================================

def _make_agent(
    ws: Path,
    responses: list[LLMResponse | Exception | str] | None = None,
    mode: str = "auto",
):
    """Create a MiniClaudeAgent wired to MockProvider + single tool."""
    from src.tools.registry import ToolRegistry
    from src.tools.file_tools import ReadFile, WriteFile
    from src.security.permission import PermissionManager, Mode
    from src.session.logger import SessionLogger
    from src.agent.loop import MiniClaudeAgent

    reg = ToolRegistry()
    reg.register(ReadFile(ws))
    reg.register(WriteFile(ws))

    provider = MockProvider(responses=responses)
    perm = PermissionManager(Mode(mode))
    log = SessionLogger(ws / "test.jsonl")
    agent = MiniClaudeAgent(reg, perm, log, ws, provider)
    return agent, provider, log


def test_agent_text_only():
    """Agent returns text immediately, no tool calls."""
    print("\n[9a] Agent loop: text-only response")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        agent, provider, log = _make_agent(ws, responses=["all done!"])

        result = agent.run("say hello")

        assert result == "all done!", f"expected 'all done!', got {result!r}"
        assert provider._call_count == 1

        log.close()
    ok("text-only response completes in one round")


def test_agent_single_tool_cycle():
    """Agent calls read_file → gets result → continues → finishes."""
    print("\n[9b] Agent loop: tool call → result → continue → finish")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "data.txt").write_text("hello world", encoding="utf-8")

        agent, provider, log = _make_agent(ws, responses=[
            LLMResponse(
                text="let me read the file",
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", arguments={"path": "data.txt"}),
                ],
                assistant_message={"role": "assistant", "content": "let me read the file"},
            ),
            "the file contains 'hello world'",
        ])

        result = agent.run("what's in data.txt?")

        assert result == "the file contains 'hello world'"
        assert provider._call_count == 2  # two API calls

        log.close()
    ok("tool call executed, result fed back, loop completed")


def test_agent_multiple_tools():
    """Agent calls two tools in one response, executes both, continues."""
    print("\n[9c] Agent loop: multiple tool calls in one response")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "a.txt").write_text("aaa", encoding="utf-8")
        (ws / "b.txt").write_text("bbb", encoding="utf-8")

        agent, provider, log = _make_agent(ws, responses=[
            LLMResponse(
                text="reading both files...",
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", arguments={"path": "a.txt"}),
                    ToolCall(id="tc2", name="read_file", arguments={"path": "b.txt"}),
                ],
                assistant_message={"role": "assistant", "content": "reading both files..."},
            ),
            "file a has aaa, file b has bbb",
        ])

        result = agent.run("show me both files")

        assert result == "file a has aaa, file b has bbb"
        assert provider._call_count == 2

        log.close()
    ok("two tool calls in one round, both executed")


def test_agent_api_error():
    """Agent handles API error gracefully, returns None."""
    print("\n[9d] Agent loop: API error handling")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        agent, provider, log = _make_agent(ws, responses=[
            RuntimeError("connection refused"),
        ])

        result = agent.run("do something")

        assert result is None
        assert provider._call_count == 1

        log.close()
    ok("API error returns None, logged as error")


def test_agent_all_tools_denied():
    """When all tool calls in a response are denied, agent returns denial."""
    print("\n[9e] Agent loop: all tools denied by permission")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        # plan mode denies write_file
        agent, provider, log = _make_agent(ws, mode="plan", responses=[
            LLMResponse(
                text="",
                tool_calls=[
                    ToolCall(id="tc1", name="write_file", arguments={"path": "x", "content": "y"}),
                ],
                assistant_message={"role": "assistant", "content": ""},
            ),
        ])

        result = agent.run("create a file")

        assert result is not None
        assert "denied" in result.lower()
        assert provider._call_count == 1  # stops after denial, no second API call

        log.close()
    ok("all-denied response ends loop without further API calls")


def test_agent_max_rounds():
    """Agent stops after MAX_TOOL_ROUNDS and asks for final summary."""
    print("\n[9f] Agent loop: max rounds exceeded")

    from src.config import MAX_TOOL_ROUNDS

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "loop.txt").write_text("data", encoding="utf-8")

        # Create responses that keep returning a tool call for every round
        tool_response = LLMResponse(
            text="still working...",
            tool_calls=[
                ToolCall(id="tc1", name="read_file", arguments={"path": "loop.txt"}),
            ],
            assistant_message={"role": "assistant", "content": "still working..."},
        )
        # Enough to exhaust all rounds + 1 for the final text response
        responses: list = [tool_response] * MAX_TOOL_ROUNDS
        responses.append("final answer after max rounds")

        agent, provider, log = _make_agent(ws, responses=responses)

        result = agent.run("keep reading")

        assert result == "final answer after max rounds"
        # MAX_TOOL_ROUNDS tool calls + 1 final summary call
        assert provider._call_count == MAX_TOOL_ROUNDS + 1
        # Final call should have tools=[] (no tools allowed)
        assert provider.last_tools == []

        log.close()
    ok(f"agent stops after {MAX_TOOL_ROUNDS} tool rounds, finalizes without tools")


def test_agent_interleaved_text():
    """Provider returns text + tool_call in same response — both handled."""
    print("\n[9g] Agent loop: interleaved text + tool calls")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "log.txt").write_text("error: out of memory", encoding="utf-8")

        agent, provider, log = _make_agent(ws, responses=[
            LLMResponse(
                text="I see the issue — let me check the log first",
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", arguments={"path": "log.txt"}),
                ],
                assistant_message={"role": "assistant", "content": "checking log..."},
            ),
            "found the problem: out of memory in log.txt",
        ])

        result = agent.run("diagnose the crash")

        assert result == "found the problem: out of memory in log.txt"
        assert provider._call_count == 2

        log.close()
    ok("interleaved text logged, tool executed, loop continued")


def test_agent_messages_accumulate():
    """Agent correctly accumulates messages across calls to run()."""
    print("\n[9h] Agent loop: message accumulation across turns")

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        agent, provider, log = _make_agent(ws, responses=[
            "response to first message",
        ])

        agent.run("first message")
        assert len(agent._messages) == 2  # user + assistant
        assert agent._messages[0]["content"] == "first message"

        # Queue another response for the next call
        provider.responses.append("response to second message")
        agent.run("second message")
        assert len(agent._messages) == 4  # accumulated

        log.close()
    ok("messages accumulate across turns")


def test_agent_mock_provider():
    """MockProvider itself works correctly."""
    print("\n[9i] MockProvider: behavior verification")

    mp = MockProvider(responses=["hello", RuntimeError("boom"), "world"])

    # First call returns string as LLMResponse
    r1 = mp.send_message("sys", [], [])
    assert r1.text == "hello"
    assert mp._call_count == 1

    # Second call raises
    try:
        mp.send_message("sys", [], [])
        raise AssertionError("should have raised")
    except RuntimeError:
        pass
    assert mp._call_count == 2

    # Third call returns next
    r3 = mp.send_message("sys", [], [])
    assert r3.text == "world"
    assert mp._call_count == 3

    # Empty queue returns default
    r4 = mp.send_message("sys", [], [])
    assert "mock default" in r4.text

    ok("MockProvider queue and error injection correct")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    tests = [
        test_sandbox,
        test_tools,
        test_registry,
        test_permission,
        test_logger,
        test_providers,
        test_context,
        test_agent,
        test_agent_mock_provider,
        test_agent_text_only,
        test_agent_single_tool_cycle,
        test_agent_multiple_tools,
        test_agent_api_error,
        test_agent_all_tools_denied,
        test_agent_max_rounds,
        test_agent_interleaved_text,
        test_agent_messages_accumulate,
    ]

    for t in tests:
        try:
            t()
        except Exception as exc:
            fail(t.__name__, str(exc))

    print(f"\n{'='*50}")
    print(f"Results: {TESTS_PASSED} passed, {TESTS_FAILED} failed")
    print(f"{'='*50}")

    if TESTS_FAILED:
        sys.exit(1)
