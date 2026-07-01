"""Skills system — matches claude-code's skill architecture.

Skills are discovered from:
1. Built-in (skills_bundled.py) — registered at startup.
2. Project-level: {cwd}/.claude/skills/<name>/SKILL.md
3. User-level: ~/.claude/skills/<name>/SKILL.md

Each skill is a SKILL.md file with YAML frontmatter:
  ---
  name: my-skill
  description: What this skill does
  ---
  The actual skill prompt content...
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class Skill:
    """A named, invocable skill matching claude-code's Skill type.

    Control flags (matching Claude Code):
      - user_invocable: user can trigger via /name slash command
      - disable_model_invocation: model CANNOT auto-invoke via Skill tool
    """

    def __init__(
        self,
        name: str,
        description: str,
        body: str = "",
        files: list[str] | None = None,
        user_invocable: bool = True,
        disable_model_invocation: bool = False,
        source: str = "",
    ) -> None:
        self.name = name
        self.description = description
        self.body = body  # the SKILL.md content (after frontmatter)
        self.files = files or []
        self.user_invocable = user_invocable
        self.disable_model_invocation = disable_model_invocation
        self.source = source  # where it was loaded from
        self._frontmatter: dict[str, Any] = {}

    def get_prompt(self, user_args: str = "") -> str:
        """Build prompt by merging body + user args (matching claude-code)."""
        parts = [self.body]
        if user_args:
            parts.append(f"\n## User Request\n\n{user_args}")
        # Append referenced file contents
        if self.files:
            parts.append("\n## Reference Files\n")
            for fp in self.files:
                p = Path(fp) if Path(fp).is_absolute() else Path(self.source).parent / fp
                try:
                    content = p.read_text(encoding="utf-8")
                    parts.append(f"\n### {p.name}\n{content}")
                except Exception:
                    parts.append(f"\n### {p.name}\n(file not found)")
        return "\n\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

_registry: dict[str, Skill] = {}


def register_skill(skill: Skill) -> None:
    _registry[skill.name] = skill


def get_skill(name: str) -> Skill | None:
    return _registry.get(name)


def list_skills() -> list[Skill]:
    return list(_registry.values())


# Skill listing budget (matches Claude Code: ~1% of context, ~1500 chars)
SKILL_LISTING_BUDGET = 1500
SKILL_LISTING_MIN_DESC = 20  # fall back to name-only below this


def build_skills_prompt_section() -> str:
    """Build the skills section for the system prompt.

    Uses a character budget with 2-level degradation
    (matching Claude Code's formatCommandsWithinBudget):
      1. Full descriptions (if within budget)
      2. Name-only (if budget exhausted)
    """
    skills = list_skills()
    if not skills:
        return ""
    header = "\n## Available Skills"
    lines = [f"- /{s.name}: {s.description}" for s in skills]
    full_text = header + "\n" + "\n".join(lines)

    if len(full_text) <= SKILL_LISTING_BUDGET:
        return full_text

    # Budget exceeded — fall back to name-only
    name_lines = [f"- /{s.name}" for s in skills]
    return header + "\n" + "\n".join(name_lines)


# ---------------------------------------------------------------------------
# SKILL.md loader — matches claude-code's loadSkillsDir
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter (between --- markers) from SKILL.md."""
    fm: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            # Simple key: value parsing (avoids yaml dependency)
            for line in parts[1].strip().split("\n"):
                line = line.strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    fm[k] = v
                elif line:
                    # Could be a list item or continuation
                    pass
            body = parts[2].strip()
    return fm, body


def discover_skills(project_dir: str) -> None:
    """Scan for SKILL.md files in project and user skill directories.

    Only searches .mini-claude/skills/ — keeps Mini-CC skills separate
    from Claude Code skills to avoid name/description conflicts.
    """
    from pathlib import Path

    search_dirs = [
        Path(project_dir) / ".mini-claude" / "skills",
        Path.home() / ".mini-claude" / "skills",
    ]

    seen = set()
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for skill_dir in sorted(search_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file() or skill_dir.name in seen:
                continue
            seen.add(skill_dir.name)

            try:
                text = skill_md.read_text(encoding="utf-8")
                fm, body = _parse_frontmatter(text)
            except Exception:
                continue

            name = fm.get("name", skill_dir.name)
            desc = fm.get("description", body[:80].split("\n")[0] if body else name)

            # Discover referenced files
            files: list[str] = []
            for entry in sorted(skill_dir.iterdir()):
                if entry.is_file() and entry.name != "SKILL.md":
                    files.append(str(entry))

            # Parse control flags from frontmatter (matches Claude Code)
            user_invocable = fm.get("user-invocable", fm.get("userInvocable", True))
            if isinstance(user_invocable, str):
                user_invocable = user_invocable.lower() != "false"
            disable_model = fm.get("disable-model-invocation", fm.get("disableModelInvocation", False))
            if isinstance(disable_model, str):
                disable_model = disable_model.lower() == "true"

            skill = Skill(
                name=name,
                description=desc,
                body=body,
                files=files,
                user_invocable=user_invocable,
                disable_model_invocation=disable_model,
                source=str(skill_dir),
            )
            skill._frontmatter = fm
            register_skill(skill)


# ---------------------------------------------------------------------------
# File watcher — live reload skills without restart
# (matches Claude Code's skillChangeDetector with chokidar)
# ---------------------------------------------------------------------------

_watcher_thread: Any = None
_watcher_stop: bool = False
_watched_dirs: dict[str, float] = {}  # path → last mtime
_WATCH_INTERVAL = 3.0  # seconds between polls


def start_skill_watcher(project_dir: str) -> None:
    """Start a background thread that watches skill directories for changes.

    When changes are detected, skills are re-discovered and re-registered.
    Uses polling (no external dependencies) with a 3-second interval.
    """
    global _watcher_thread, _watcher_stop, _watched_dirs
    from pathlib import Path
    import os as _os

    # Build watch list
    search_dirs = [
        Path(project_dir) / ".mini-claude" / "skills",
        Path.home() / ".mini-claude" / "skills",
    ]
    for d in search_dirs:
        if d.is_dir():
            try:
                _watched_dirs[str(d)] = d.stat().st_mtime
            except OSError:
                pass

    if not _watched_dirs:
        return

    if _watcher_thread is not None and _watcher_thread.is_alive():
        return  # already running

    _watcher_stop = False

    def _poll():
        import time
        while not _watcher_stop:
            time.sleep(_WATCH_INTERVAL)
            changed = False
            for dir_path, last_mtime in list(_watched_dirs.items()):
                try:
                    cur_mtime = _os.stat(dir_path).st_mtime
                except OSError:
                    continue
                if cur_mtime > last_mtime:
                    # Also check individual files inside
                    try:
                        for entry in _os.scandir(dir_path):
                            if entry.is_dir():
                                skill_md = Path(entry.path) / "SKILL.md"
                                if skill_md.is_file():
                                    try:
                                        fmtime = skill_md.stat().st_mtime
                                    except OSError:
                                        continue
                                    if fmtime > last_mtime:
                                        changed = True
                                        break
                    except OSError:
                        pass
                    _watched_dirs[dir_path] = cur_mtime
                if changed:
                    break

            if changed:
                _reload_skills(project_dir, dir_path)

    _watcher_thread = threading.Thread(target=_poll, daemon=True)
    _watcher_thread.start()


def stop_skill_watcher() -> None:
    global _watcher_stop
    _watcher_stop = True


def _reload_skills(project_dir: str, changed_dir: str) -> None:
    """Clear and re-discover skills from the changed directory."""
    global _registry
    # Only remove discovered (non-bundled) skills — keep built-ins
    to_remove = [
        name for name, skill in _registry.items()
        if getattr(skill, 'source', '') and changed_dir in str(skill.source)
    ]
    for name in to_remove:
        del _registry[name]
    discover_skills(project_dir)


# Need threading at module level for the watcher
import threading
