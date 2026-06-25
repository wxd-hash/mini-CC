"""Terminal output styling — matches Claude Code's visual design.

Key patterns from claude-code:
  ↳ ToolName(args)                   ← tool call, dim
  ↳ ToolName(args) ... ⏳ running    ← tool executing
    ⎿ result line 1                   ← tool success (multi-line)
    ⎿ result line 2
    ⎿ ... +N lines                    ← overflow indicator
  [auth] message                      ← permission prompt (yellow)
  BLOCKED: message                    ← self-destruct blocked (red)
"""

from __future__ import annotations

import random
import sys
import threading
import time

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
# Spinner — animated progress indicator (matches claude-code)
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ["·", "✢", "✱", "✶", "✻", "✽"]
_SPINNER_FRAME_INTERVAL = 0.12  # ~8 fps, matches claude-code
_SHOW_TIMER_AFTER = 5  # show elapsed time after 5s

_SPINNER_VERBS = [
    "思考中", "分析中", "检索中", "计算中", "处理中",
    "推演中", "琢磨中", "构思中", "规划中", "执行中",
    "编译中", "调试中", "优化中", "评估中", "验证中",
    "学习中", "推理中", "归纳中", "演绎中", "综合中",
]

_MAX_RESULT_LINES = 3  # show at most this many lines


class Spinner:
    """Threaded animated spinner — runs while waiting for API response.

    Usage::

        spinner = Spinner()
        spinner.start()
        ...  # blocking API call
        spinner.stop()
    """

    def __init__(self) -> None:
        self._running = False
        self._thread: threading.Thread | None = None
        self._start_time = 0.0
        self._verb = ""
        self._frame_idx = 0

    @property
    def elapsed(self) -> float:
        if self._start_time == 0:
            return 0
        return time.monotonic() - self._start_time

    def start(self) -> None:
        self._running = True
        self._start_time = time.monotonic()
        self._verb = random.choice(_SPINNER_VERBS)
        self._frame_idx = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # Clear the spinner line
        sys.stdout.write(f"\r{_DIM}✓  已用时 {self.elapsed:.0f}s{_RESET}\n")
        sys.stdout.flush()

    def _run(self) -> None:
        while self._running:
            elapsed = time.monotonic() - self._start_time
            frame = _SPINNER_FRAMES[self._frame_idx % len(_SPINNER_FRAMES)]
            self._frame_idx += 1

            time_str = f"  ({elapsed:.0f}s)" if elapsed >= _SHOW_TIMER_AFTER else ""
            line = f"\r  {_DIM}  {frame}  {self._verb}{time_str}{_RESET}"
            sys.stdout.write(line)
            sys.stdout.flush()
            time.sleep(_SPINNER_FRAME_INTERVAL)


# ---------------------------------------------------------------------------
# Tool display — matches claude-code's ↳ prefix pattern
# ---------------------------------------------------------------------------

def tool_call(name: str, params: str) -> str:
    """Tool call header — matches claude-code '↳ ToolName(params)'."""
    return f"  {_DIM}↳ {_CYAN}{name}{_RESET}{_DIM}({params}){_RESET}"


def tool_running(name: str, params: str, activity: str = "") -> str:
    """Tool executing status — returns a line suitable for spinner overlay."""
    label = activity or "..."
    return f"    {_DIM}  ...  {label}{_RESET}"


def tool_done(content: str, max_len: int = 200) -> str:
    """Tool result — multi-line with ⎿ prefix, up to 3 lines.

    Matches claude-code's MessageResponse pattern::
          ⎿  first line of result
          ⎿  second line
          ⎿  ... +N lines
    """
    return _format_result(content, max_len, is_error=False)


def tool_error(content: str, max_len: int = 200) -> str:
    """Tool error — multi-line with ⎿ prefix, red color."""
    return _format_result(content, max_len, is_error=True)


def _format_result(content: str, max_len: int, is_error: bool) -> str:
    color = _RED if is_error else _DIM
    lines = content.split("\n")
    total = len(lines)

    # Trim each line
    trimmed: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if len(stripped) > max_len:
            stripped = stripped[:max_len] + "..."
        trimmed.append(stripped)

    if not trimmed:
        return f"    {color}⎿  (empty){_RESET}"

    # Show at most MAX_RESULT_LINES
    shown = trimmed[:_MAX_RESULT_LINES]
    parts = []
    for line in shown:
        parts.append(f"    {color}⎿  {line}{_RESET}")

    if total > _MAX_RESULT_LINES:
        remaining = total - _MAX_RESULT_LINES
        parts.append(f"    {_DIM}⎿  ... +{remaining} 行{_RESET}")

    return "\n".join(parts)


# -- Legacy compat aliases ---------------------------------------------------

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


def thinking() -> str:
    """DEPRECATED: use Spinner.start() / Spinner.stop() instead."""
    return f"  {_DIM}思考中...{_RESET}"


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
    """Compaction notice — matches claude-code style."""
    return f"\n  {_DIM}✻ 对话已压缩（{before} → {after} 条消息，腾出上下文空间）{_RESET}\n"


def turn_warning(count: int) -> str:
    """Turn count warning — when tool rounds get high."""
    return f"\n  {_YELLOW}[{count} 轮工具调用 — 仍在工作中，Ctrl+C 可停止]{_RESET}\n"


def turn_limit(count: int) -> str:
    """Turn limit reached — forcing wrap-up."""
    return f"\n  {_YELLOW}[{count} 轮，即将结束]{_RESET}\n"


# ---------------------------------------------------------------------------
# Banner & layout
# ---------------------------------------------------------------------------

def banner_line(label: str, value: str) -> str:
    """Aligned key-value for startup banner."""
    return f"  {_DIM}{label:<12}{_RESET} {_BRIGHT}{value}{_RESET}"


def bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


def hr_fixed(width: int = 50) -> str:
    return _DIM + _safe_hr_char() * width + _RESET


def hr() -> str:
    """Full-width horizontal rule."""
    try:
        import shutil
        w = shutil.get_terminal_size().columns
    except Exception:
        w = 80
    return _DIM + _safe_hr_char() * min(w, 120) + _RESET


def _safe_hr_char() -> str:
    try:
        "─".encode(sys.stdout.encoding)
        return "─"
    except (UnicodeEncodeError, AttributeError):
        return "-"


def prompt() -> str:
    """Prompt marker — ❯ on modern terminals, > on restricted ones."""
    try:
        "❯".encode(sys.stdout.encoding)
        ch = "❯"  # ❯
    except (UnicodeEncodeError, AttributeError):
        ch = ">"
    return f"{_BOLD_GREEN}{ch}{_RESET} "


# ---------------------------------------------------------------------------
# Result formatting (legacy — used by old code paths)
# ---------------------------------------------------------------------------

def tool_result(text: str) -> str:
    """Legacy compat — indented dimmed result."""
    return f"  {_DIM}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Select menu — interactive keyboard navigation
# ---------------------------------------------------------------------------

def select_menu(
    options: list[str],
    title: str = "",
    footer: str = "",
) -> int:
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
    hr_line = _DIM + _safe_hr_char() * 30 + _RESET

    def _render() -> None:
        print()
        if title:
            print(f"  {_BOLD}{title}{_RESET}")
            print(f"  {hr_line}")
        for i, opt in enumerate(options):
            if i == selected:
                print(f"  {_BOLD}{_GREEN}▸ {opt}{_RESET}")  # ▸
            else:
                print(f"  {_DIM}  {opt}{_RESET}")
        if footer:
            print(f"  {hr_line}")
            print(f"  {_DIM}{footer}{_RESET}")

    # Drain leftover stdin + reset UTF-8 buffer
    global _utf8_buf
    _utf8_buf = b""
    _drain_console_buffer()

    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    try:
        _render()
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

            # Move cursor up to re-render
            # Line count: 1 (empty print) + N options + optional title/footer
            rendered_lines = 1 + len(options)
            if title:
                rendered_lines += 2  # title + hr
            if footer:
                rendered_lines += 2  # hr + footer
            sys.stdout.write(f"\033[{rendered_lines}A")
            sys.stdout.flush()
            _render()
    finally:
        sys.stdout.write("\033[?25h")
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
        if first <= 0x7F:
            _utf8_buf = _utf8_buf[1:]
            return chr(first)
        if not _can_be_utf8(_utf8_buf[:1]):
            _utf8_buf = _utf8_buf[1:]
            return chr(first)
        break

    while True:
        b = msvcrt.getch()
        if b[0] <= 0x7F and not _utf8_buf:
            return chr(b[0])
        _utf8_buf += b
        try:
            s = _utf8_buf.decode("utf-8")
            _utf8_buf = b""
            return s
        except UnicodeDecodeError:
            if len(_utf8_buf) == 1 and _utf8_buf[0] >= 0x80:
                _utf8_buf = b""
                return chr(b[0])
            if len(_utf8_buf) >= 3:
                drained = _utf8_buf[0]
                _utf8_buf = _utf8_buf[1:]
                return chr(drained)
            continue


def _can_be_utf8(byte_seq: bytes) -> bool:
    try:
        byte_seq.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
