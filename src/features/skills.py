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
    """A named, invocable skill matching claude-code's Skill type."""

    def __init__(
        self,
        name: str,
        description: str,
        body: str = "",
        files: list[str] | None = None,
        user_invocable: bool = True,
        source: str = "",
    ) -> None:
        self.name = name
        self.description = description
        self.body = body  # the SKILL.md content (after frontmatter)
        self.files = files or []
        self.user_invocable = user_invocable
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


def build_skills_prompt_section() -> str:
    """Build the skills section for the system prompt."""
    skills = list_skills()
    if not skills:
        return ""
    lines = ["\n## Available Skills"]
    for s in skills:
        lines.append(f"- /{s.name}: {s.description}")
    return "\n".join(lines)


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

    Matches claude-code's loadSkillsDir pattern:
    - {project}/.claude/skills/<name>/SKILL.md
    - ~/.claude/skills/<name>/SKILL.md
    """
    from pathlib import Path

    search_dirs = [
        Path(project_dir) / ".claude" / "skills",
        Path(project_dir) / ".mini-claude" / "skills",
        Path.home() / ".claude" / "skills",
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

            skill = Skill(
                name=name,
                description=desc,
                body=body,
                files=files,
                user_invocable=True,
                source=str(skill_dir),
            )
            skill._frontmatter = fm
            register_skill(skill)
