"""Terminal output styling — matches Claude Code's visual design.

Key patterns from claude-code:
  ↳ ToolName(args)                   ← tool call, dim
  ↳ ToolName(args) ... ⏳ running    ← tool executing
  ↳ ToolName(args) ... ✓ done        ← tool success
  ↳ ToolName(args) ... ✗ error       ← tool failure
  [auth] message                      ← permission prompt (yellow)
  BLOCKED: message                    ← self-destruct blocked (red)
"""

from __future__ import annotations

import sys

import colorama

colorama.init(autoreset=True)

# Raw ANSI
_RESET = colorama.Style.RESET_ALL
_DIM = colorama.Style.DIM
_BRIGHT = colorama.Style.BRIGHT
_BOLD = "\033[1m"

_CYAN = colorama.Fore.CYAN
_GREEN = colorama.Fore.GREEN
_YELLOW = colorama.Fore.YELLOW
_RED = colorama.Fore.RED
_LIGHT_BLACK = colorama.Fore.LIGHTBLACK_EX

_BOLD_GREEN = f"{_BOLD}{_GREEN}"


def set_no_color() -> None:
    global _RESET, _DIM, _BRIGHT, _BOLD
    global _CYAN, _GREEN, _YELLOW, _RED, _LIGHT_BLACK, _BOLD_GREEN
    _RESET = _DIM = _BRIGHT = _BOLD = ""
    _CYAN = _GREEN = _YELLOW = _RED = _LIGHT_BLACK = ""
    _BOLD_GREEN = ""


# ---------------------------------------------------------------------------
# Tool display — matches claude-code's ↳ prefix pattern
# ---------------------------------------------------------------------------

def tool_call(name: str, params: str) -> str:
    """Tool call header — matches claude-code '↳ ToolName(params)'."""
    return f"  {_DIM}↳ {_CYAN}{name}{_RESET}{_DIM}({params}){_RESET}"


def tool_running(name: str, params: str, activity: str = "") -> str:
    """Tool executing — '↳ ToolName(params) ... ⏳ activity'."""
    label = activity or "..."
    return f"  {_DIM}↳ {_CYAN}{name}{_RESET}{_DIM}({params}){_RESET} {_DIM}  ...  {label}{_RESET}"


def tool_done(result: str, max_len: int = 200) -> str:
    """Tool result — '  ↳ ✓ result_summary' (claude-code style)."""
    if len(result) > max_len:
        result = result[:max_len] + "..."
    first_line = result.split("\n")[0].strip()
    return f"  {_DIM}↳{_RESET} {_GREEN}✓{_RESET} {_DIM}{first_line}{_RESET}"


def tool_error(result: str, max_len: int = 200) -> str:
    """Tool error — '  ↳ ✗ error'."""
    if len(result) > max_len:
        result = result[:max_len] + "..."
    first_line = result.split("\n")[0].strip()
    return f"  {_DIM}↳{_RESET} {_RED}✗{_RESET} {_RED}{first_line}{_RESET}"


# -- Legacy compat aliases (kept for existing callers) -----------------------


def tool_header(name: str, params: str) -> str:
    """Legacy compat — use tool_call instead."""
    return tool_call(name, params)


# ---------------------------------------------------------------------------
# Permission display
# ---------------------------------------------------------------------------

def permission_prompt(text: str) -> str:
    """Permission prompt — yellow [auth] label (matches claude-code)."""
    return f"\n  {_YELLOW}[auth]{_RESET} {text}"


def permission_denied(text: str) -> str:
    """Permission denied — red."""
    return f"  {_RED}{text}{_RESET}"


def denied(text: str) -> str:
    """Denial / blocked message."""
    return f"  {_RED}{_BOLD}{text}{_RESET}"


def blocked(text: str) -> str:
    """Self-destruct blocked message."""
    return f"\n  {_RED}{_BOLD}BLOCKED:{_RESET} {_RED}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def assistant_text(text: str) -> str:
    """Assistant output — no extra styling, clean."""
    return text


def error(text: str) -> str:
    """Error message."""
    return f"  {_RED}{text}{_RESET}"


def info(text: str) -> str:
    """Informational — dimmed."""
    return f"  {_DIM}{text}{_RESET}"


def success(text: str) -> str:
    """Success — green."""
    return f"  {_GREEN}{text}{_RESET}"


def warning(text: str) -> str:
    """Warning — yellow."""
    return f"  {_YELLOW}{text}{_RESET}"


def compact(before: int, after: int) -> str:
    """Compaction notice."""
    return f"  {_DIM}[compacted {before} → {after} messages]{_RESET}"


def turn_warning(count: int) -> str:
    """Turn count warning — when tool rounds get high."""
    return f"\n  {_YELLOW}[{count} tool rounds — still working, Ctrl+C to stop]{_RESET}\n"


def turn_limit(count: int) -> str:
    """Turn limit reached — forcing wrap-up."""
    return f"\n  {_YELLOW}[{count} rounds, wrapping up]{_RESET}\n"


# ---------------------------------------------------------------------------
# Banner & layout
# ---------------------------------------------------------------------------

def banner_line(label: str, value: str) -> str:
    """Aligned key-value for startup banner."""
    return f"  {_DIM}{label:<12}{_RESET} {_BRIGHT}{value}{_RESET}"


def bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


def hr_fixed(width: int = 50) -> str:
    try:
        "─".encode(sys.stdout.encoding)
        ch = "─"
    except (UnicodeEncodeError, AttributeError):
        ch = "-"
    return _DIM + ch * width + _RESET


def hr() -> str:
    """Full-width horizontal rule."""
    try:
        import shutil
        w = shutil.get_terminal_size().columns
    except Exception:
        w = 80
    try:
        "─".encode(sys.stdout.encoding)
        ch = "─"
    except (UnicodeEncodeError, AttributeError):
        ch = "-"
    return _DIM + ch * min(w, 120) + _RESET


def prompt() -> str:
    """Prompt marker."""
    return f"{_BOLD_GREEN}>{_RESET}"


# ---------------------------------------------------------------------------
# Result formatting (legacy — used by old code paths)
# ---------------------------------------------------------------------------

def tool_result(text: str) -> str:
    """Legacy compat — indented dimmed result."""
    return f"  {_DIM}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Select menu — interactive keyboard navigation
# ---------------------------------------------------------------------------

def select_menu(options: list[str]) -> int:
    """Show interactive menu with ↑↓/jk/ws navigation, Enter to confirm.

    Returns index (0-based), -1 if cancelled (Esc/q).
    """
    n = len(options)
    if n == 0:
        return -1
    if n == 1:
        print(f"    {_BRIGHT}{options[0]}{_RESET}")
        return 0

    selected = 0

    def _render() -> None:
        for i, opt in enumerate(options):
            # Clear to end of line before writing (prevents stale text from
            # longer previous options from showing after re-render)
            if i == selected:
                sys.stdout.write(
                    f"\033[K    {_BRIGHT}{_GREEN}▸ {opt}{_RESET}   \n"
                )
            else:
                sys.stdout.write(
                    f"\033[K    {_DIM}  {opt}{_RESET}   \n"
                )
        sys.stdout.flush()

    # Drain leftover stdin + reset UTF-8 buffer
    global _utf8_buf
    _utf8_buf = b""
    _drain_console_buffer()

    # First render (print n lines + cursor ends below them)
    _render()

    sys.stdout.write("\033[?25l")  # hide cursor
    sys.stdout.flush()

    try:
        while True:
            ch = _getch()
            if ch in ("\r", "\n", " "):
                break
            if ch == "\x1b":
                seq = _getch()
                if seq == "[":
                    arrow = _getch()
                    if arrow == "A":
                        selected = (selected - 1) % n
                    elif arrow == "B":
                        selected = (selected + 1) % n
                else:
                    return -1
            elif ch in ("\x00", "\xe0"):
                k = _getch()
                if k == "H":
                    selected = (selected - 1) % n
                elif k == "P":
                    selected = (selected + 1) % n
            elif ch in ("w", "k"):
                selected = (selected - 1) % n
            elif ch in ("s", "j"):
                selected = (selected + 1) % n
            elif ch == "q":
                return -1

            # Move up n lines, clear from there to end, re-render
            sys.stdout.write(f"\033[{n}A")    # up to menu start
            sys.stdout.write("\033[J")         # clear to end of screen
            sys.stdout.flush()
            _render()
    finally:
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.flush()

    return selected


# ---------------------------------------------------------------------------
# Input reader
# ---------------------------------------------------------------------------

def readline(prompt_text: str = "> ") -> str:
    """Read a line of input. Multi-line pastes are joined."""
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    first = input()
    remaining = _drain_stdin()
    if not remaining:
        return first
    return first + " " + " ".join(remaining)


# ---------------------------------------------------------------------------
# Internal: stdin drain + single-char input
# ---------------------------------------------------------------------------

def _drain_console_buffer() -> None:
    """Drain pending stdin bytes before entering raw-input mode."""
    if sys.platform == "win32":
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    else:
        import select
        while select.select([sys.stdin], [], [], 0.0)[0]:
            sys.stdin.read(1)


def _drain_stdin() -> list[str]:
    lines: list[str] = []
    if sys.platform == "win32":
        import msvcrt
        import time
        time.sleep(0.02)
        while msvcrt.kbhit():
            try:
                lines.append(input())
            except (EOFError, KeyboardInterrupt):
                break
    else:
        import select
        while select.select([sys.stdin], [], [], 0.0)[0]:
            try:
                lines.append(input())
            except (EOFError, KeyboardInterrupt):
                break
    return lines


_utf8_buf: bytes = b""


def _getch() -> str:
    if sys.platform == "win32":
        import msvcrt
        return _getch_win(msvcrt)
    else:
        import termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _getch_win(msvcrt) -> str:
    global _utf8_buf
    while _utf8_buf:
        first = _utf8_buf[0]
        if first <= 0x7F or not _can_be_utf8(_utf8_buf[:1]):
            _utf8_buf = _utf8_buf[1:]
            return chr(first)
        break

    while True:
        b = msvcrt.getch()
        _utf8_buf += b
        try:
            s = _utf8_buf.decode("utf-8")
            _utf8_buf = b""
            if len(s) > 1:
                return s
            return s
        except UnicodeDecodeError:
            if len(_utf8_buf) >= 4:
                _utf8_buf = _utf8_buf[1:]
                return chr(_utf8_buf[0])
            continue


def _can_be_utf8(byte_seq: bytes) -> bool:
    try:
        byte_seq.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
