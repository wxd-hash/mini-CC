"""Skills system — matches cc-mini's plugin-based skill loader.

Skills are discovered from:
1. Built-in (skills_bundled.py) — registered at startup.
2. Project-level: {cwd}/.mini-claude/skills/
3. User-level: ~/.mini-claude/skills/

Each skill is a function with metadata: name, description, handler.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Skill type
# ---------------------------------------------------------------------------

class Skill:
    """A named, invocable skill (matches cc-mini's skill structure)."""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable[..., str | None],
        args_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.handler = handler
        self.args_schema = args_schema or {}

    def run(self, args: str = "") -> str | None:
        """Invoke the skill with optional arguments string."""
        return self.handler(args)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_skills: dict[str, Skill] = {}


def register_skill(skill: Skill) -> None:
    """Register a skill (overwrites existing with same name)."""
    _skills[skill.name] = skill


def get_skill(name: str) -> Skill | None:
    """Look up a skill by name."""
    return _skills.get(name)


def list_skills() -> list[Skill]:
    """Return all registered skills."""
    return list(_skills.values())


def build_skills_prompt_section() -> str:
    """Build a system prompt section listing available skills."""
    if not _skills:
        return ""
    lines = ["# Available Skills"]
    for s in _skills.values():
        lines.append(f"- /{s.name}: {s.description}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_skills(cwd: str) -> None:
    """Discover and load skills from project and user directories."""
    search_paths = [
        Path(cwd) / ".mini-claude" / "skills",
        Path.home() / ".mini-claude" / "skills",
    ]
    for dir_path in search_paths:
        if not dir_path.is_dir():
            continue
        for py_file in sorted(dir_path.glob("*.py")):
            _load_skill_file(py_file)


def _load_skill_file(path: Path) -> None:
    """Load a single skill .py file and register any Skill instances found."""
    try:
        spec = importlib.util.spec_from_file_location(
            f"_skill_{path.stem}", str(path)
        )
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"_skill_{path.stem}"] = mod
        spec.loader.exec_module(mod)
        # Look for Skill instances in the module
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, Skill):
                register_skill(attr)
    except Exception:
        pass  # Don't crash on broken skills
