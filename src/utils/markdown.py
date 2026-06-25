"""Lightweight markdown-to-ANSI converter for terminal output.

Converts common markdown patterns to ANSI escape sequences so that
assistant text renders with actual bold, italic, and code styling
in the terminal — matching Claude Code's visual behavior.
"""

from __future__ import annotations

import re

from src import terminal as term

# ---------------------------------------------------------------------------
# Patterns (ordered: match longer/fenced patterns before shorter inline ones)
# ---------------------------------------------------------------------------

# Fenced code blocks: ```lang\n...\n``` → dimmed, preserve content
_CODE_BLOCK_RE = re.compile(
    r"```(?:\w+)?\n(.*?)```", re.DOTALL
)

# Inline code: `text` → colored (no highlight, just theme color)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Bold: **text** or __text__
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")

# Italic: *text* or _text_ (but NOT inside words to avoid false matches)
_ITALIC_RE = re.compile(r"(?<!\w)\*([^*\n]+?)\*(?!\w)|(?<!\w)_([^_\n]+?)_(?!\w)")

# Headings: # to ###### at line start
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Horizontal rules: --- or *** alone on a line
_HR_RE = re.compile(r"^(?:---+|\*{3,})$", re.MULTILINE)

# Blockquotes: > at line start
_BLOCKQUOTE_RE = re.compile(r"^(>\s?)(.*)$", re.MULTILINE)

# Unordered lists: - or * at line start
_LIST_RE = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)


def _ansi_bold(text: str) -> str:
    return f"{term._BOLD}{text}{term._RESET}"


def _ansi_dim(text: str) -> str:
    return f"{term._DIM}{text}{term._RESET}"


def _ansi_code(text: str) -> str:
    """Inline code — colored similar to claude-code's 'permission' color."""
    return f"{term._CYAN}{text}{term._RESET}"


def render(text: str) -> str:
    """Convert markdown formatting to ANSI escape codes for terminal display.

    Handles: **bold**, *italic*, `code`, ```fenced blocks```,
    # headings, > blockquotes, - lists, and --- horizontal rules.
    """
    # Phase 1: Extract and protect fenced code blocks
    code_blocks: list[str] = []

    def _save_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CODEBLOCK{len(code_blocks) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_save_block, text)

    # Phase 2: Inline formatting
    text = _BOLD_RE.sub(lambda m: _ansi_bold(m.group(1) or m.group(2)), text)
    text = _ITALIC_RE.sub(lambda m: _ansi_dim(m.group(1) or m.group(2)), text)
    text = _INLINE_CODE_RE.sub(lambda m: _ansi_code(m.group(1)), text)

    # Phase 3: Block-level formatting
    text = _HEADING_RE.sub(
        lambda m: _ansi_bold(m.group(2)) + ("\n" + term._DIM + "─" * len(m.group(2)) + term._RESET),
        text,
    )
    text = _HR_RE.sub(lambda m: term.hr_fixed(40), text)
    text = _BLOCKQUOTE_RE.sub(
        lambda m: f"  {term._DIM}▎{term._RESET} {term._DIM}{m.group(2)}{term._RESET}",
        text,
    )
    text = _LIST_RE.sub(r"\1  • ", text)

    # Phase 4: Restore code blocks (dimmed)
    for i, block in enumerate(code_blocks):
        placeholder = f"\x00CODEBLOCK{i}\x00"
        # Indent each line of the code block
        indented = "\n".join(f"  {term._DIM}{line}{term._RESET}" for line in block.split("\n"))
        text = text.replace(placeholder, f"\n{indented}\n")

    return text
