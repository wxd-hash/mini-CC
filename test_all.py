"""Quick self-test for Mini Claude Code — no API key required.

Run:   .venv/Scripts/python test_all.py
"""

import json
import sys
import tempfile
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

TESTS_PASSED = 0
TESTS_FAILED = 0


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
# 8. Agent instantiation
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
