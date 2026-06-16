"""Terminal output styling — ANSI escape codes via colorama.

Inspired by Claude Code's terminal UI.  Every function returns a string
that can be printed directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import colorama

colorama.init(autoreset=True)

# ---------------------------------------------------------------------------
# Raw ANSI codes
# ---------------------------------------------------------------------------

_RESET = colorama.Style.RESET_ALL
_DIM = colorama.Style.DIM
_BRIGHT = colorama.Style.BRIGHT
_BOLD = "\033[1m"

_CYAN = colorama.Fore.CYAN
_GREEN = colorama.Fore.GREEN
_YELLOW = colorama.Fore.YELLOW
_RED = colorama.Fore.RED
_MAGENTA = colorama.Fore.MAGENTA
_WHITE = colorama.Fore.WHITE
_LIGHT_BLACK = colorama.Fore.LIGHTBLACK_EX


def set_no_color() -> None:
    """Disable all ANSI styling — useful for CI logs or piping."""
    global _RESET, _DIM, _BRIGHT, _BOLD
    global _CYAN, _GREEN, _YELLOW, _RED, _MAGENTA, _WHITE, _LIGHT_BLACK
    global _BOLD_GREEN
    _RESET = _DIM = _BRIGHT = _BOLD = ""
    _CYAN = _GREEN = _YELLOW = _RED = _MAGENTA = _WHITE = _LIGHT_BLACK = ""
    _BOLD_GREEN = ""


_BOLD_GREEN = f"{_BOLD}{_GREEN}"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def tool_header(name: str, params: str) -> str:
    """``[tool] name(params)`` — cyan label, bright name, dim args."""
    return f"  {_DIM}[{_RESET}{_CYAN}tool{_RESET}{_DIM}]{_RESET} {_BRIGHT}{name}{_RESET}{_DIM}({params}){_RESET}"


def tool_result(text: str) -> str:
    """Indented, dimmed result line."""
    return f"  {_DIM}{text}{_RESET}"


def assistant_text(text: str) -> str:
    """Clean assistant output — no extra styling."""
    return text


def permission_prompt(text: str) -> str:
    """``[auth] message`` — yellow label, bright text."""
    return f"  {_YELLOW}[auth]{_RESET} {_BRIGHT}{text}{_RESET}"


def denied(text: str) -> str:
    """Denial message — red."""
    return f"  {_RED}{text}{_RESET}"


def error(text: str) -> str:
    """Error — red bold."""
    return f"  {_RED}{_BOLD}{text}{_RESET}"


def info(text: str) -> str:
    """Informational — dimmed."""
    return f"  {_DIM}{text}{_RESET}"


def success(text: str) -> str:
    """Success — green."""
    return f"  {_GREEN}{text}{_RESET}"


def compact(before: int, after: int) -> str:
    """Compaction notice — dimmed."""
    return f"  {_DIM}[compacted {before} → {after} messages]{_RESET}"


def banner_line(label: str, value: str) -> str:
    """Aligned key-value for startup banner."""
    return f"  {_DIM}{label:<12}{_RESET} {_BRIGHT}{value}{_RESET}"


def bold(text: str) -> str:
    """Bold text — no leading newline."""
    return f"{_BOLD}{text}{_RESET}"


def section(title: str) -> str:
    """Bold section header."""
    return f"\n{_BOLD}{title}{_RESET}"


def hr_fixed(width: int = 50) -> str:
    """Dimmed horizontal rule of fixed width."""
    try:
        "─".encode(sys.stdout.encoding)
        ch = "─"
    except (UnicodeEncodeError, AttributeError):
        ch = "-"
    return _DIM + ch * width + _RESET


def hr() -> str:
    """Dimmed horizontal rule (full terminal width)."""
    try:
        import shutil
        w = shutil.get_terminal_size().columns
    except Exception:
        w = 80
    # Try box-drawing, fall back to ASCII dash
    try:
        "─".encode(sys.stdout.encoding)
        ch = "─"
    except (UnicodeEncodeError, AttributeError):
        ch = "-"
    return _DIM + ch * w + _RESET


def path_str(p: str | Path) -> str:
    """Dimmed path."""
    return f"{_DIM}{p}{_RESET}"


def prompt() -> str:
    """User-input prompt — bold green chevron."""
    return f"{_GREEN}{_BRIGHT}>{_RESET} "


# ---------------------------------------------------------------------------
# Interactive menu — keyboard navigation, Enter to confirm
# ---------------------------------------------------------------------------

def select_menu(options: list[str]) -> int:
    """Show an interactive menu with keyboard navigation.

    Navigation:  ↑ / ↓  or  w / s  or  j / k
    Confirm:     Enter / Space
    Cancel:      Esc / q

    Returns the index of the selected option (0-based).
    Returns -1 if cancelled.
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
            if i == selected:
                print(f"    {_BRIGHT}{_GREEN}▸ {opt}{_RESET}   ")
            else:
                print(f"    {_DIM}  {opt}{_RESET}   ")

    # Hide cursor during menu
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    try:
        _render()
        while True:
            ch = _getch()
            if ch in ("\r", "\n", " "):
                break
            if ch == "\x1b":
                # ESC sequence
                seq = _getch()
                if seq == "[":
                    arrow = _getch()
                    if arrow == "A":
                        selected = (selected - 1) % n
                    elif arrow == "B":
                        selected = (selected + 1) % n
                else:
                    return -1  # plain ESC = cancel
            elif ch in ("\x00", "\xe0"):
                # Windows extended key prefix
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

            # Move cursor up and re-render
            sys.stdout.write(f"\033[{n}A")
            sys.stdout.flush()
            _render()
    finally:
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.flush()

    return selected


# ---------------------------------------------------------------------------
# Cross-platform single-char input
# ---------------------------------------------------------------------------

def readline(prompt_text: str = "> ") -> str:
    """Read a line of input. Multi-line pastes are joined with spaces."""
    sys.stdout.write(prompt_text)
    sys.stdout.flush()
    first = input()
    remaining = _drain_stdin()
    if not remaining:
        return first
    # Join all pasted lines into one, replacing newlines with spaces
    return first + " " + " ".join(remaining)


def _drain_stdin() -> list[str]:
    """Read any immediately-available lines from stdin.  Empty = no paste."""
    lines: list[str] = []
    if sys.platform == "win32":
        import msvcrt
        # Give a tiny window for the paste buffer to fill
        import time
        time.sleep(0.02)
        while msvcrt.kbhit():
            try:
                line = input()
                lines.append(line)
            except (EOFError, KeyboardInterrupt):
                break
    else:
        import select
        # Non-blocking check: is more data already in the buffer?
        while select.select([sys.stdin], [], [], 0.0)[0]:
            try:
                line = input()
                lines.append(line)
            except (EOFError, KeyboardInterrupt):
                break
    return lines


# ---------------------------------------------------------------------------
# Cross-platform single-char input (for interactive menus)
# ---------------------------------------------------------------------------

def _getch() -> str:
    """Read a single character from stdin without waiting for Enter.

    On Windows, accumulates raw bytes from ``msvcrt.getch`` and decodes
    them as UTF-8 so that multi-byte characters (Chinese, emoji, etc.)
    are returned correctly as a single Python string.
    """
    if sys.platform == "win32":
        import msvcrt
        return _getch_win(msvcrt)
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# Windows: stateful UTF-8 decoder for raw console bytes
# ---------------------------------------------------------------------------

_utf8_buf: bytes = b""


def _getch_win(msvcrt) -> str:
    """Accumulate console bytes until a complete UTF-8 character emerges."""
    global _utf8_buf

    # Drain leftover raw bytes from a previous failed decode first
    # (e.g. the scan-code byte after a Windows extended-key prefix).
    while _utf8_buf:
        first = _utf8_buf[0]
        if first <= 0x7F or not _can_be_utf8(_utf8_buf[:1]):
            _utf8_buf = _utf8_buf[1:]
            return chr(first)
        break  # partial UTF-8 — need more bytes from console

    while True:
        b = msvcrt.getch()
        _utf8_buf += b

        try:
            s = _utf8_buf.decode("utf-8")
            _utf8_buf = b""
            if len(s) > 1:
                _utf8_buf = s[1:].encode("utf-8")
            return s[0]
        except UnicodeDecodeError:
            if not _can_be_utf8(_utf8_buf):
                raw = chr(_utf8_buf[0])
                _utf8_buf = _utf8_buf[1:]
                return raw
            continue


def _can_be_utf8(data: bytes) -> bool:
    """Return True if *data* could be the prefix of a valid UTF-8 sequence."""
    if not data:
        return True
    first = data[0]
    if first <= 0x7F:          # ASCII
        return True
    if 0xC0 <= first <= 0xDF:  # 2-byte lead
        if len(data) == 1:
            return True
        return 0x80 <= data[1] <= 0xBF
    if 0xE0 <= first <= 0xEF:  # 3-byte lead
        if len(data) == 1:
            return True
        if not (0x80 <= data[1] <= 0xBF):
            return False
        if len(data) == 2:
            return True
        return 0x80 <= data[2] <= 0xBF
    if 0xF0 <= first <= 0xF7:  # 4-byte lead
        for i in range(1, min(len(data), 4)):
            if not (0x80 <= data[i] <= 0xBF):
                return False
        return True
    return False  # invalid lead byte
