"""Built-in skills — matches cc-mini's bundled skills.

Provides: /review, /commit, /test, /simplify
Each skill runs by submitting a prompt to the engine.
"""

from __future__ import annotations

from typing import Any

from src.features.skills import Skill, register_skill


def _review_handler(args: str) -> str | None:
    """Code review skill — returns a prompt for the engine to execute."""
    return (
        "Run a code review on the current changes. Steps:\n"
        "1. Run git_diff to see all changes\n"
        "2. Check for bugs, security issues, unclear code, missing tests\n"
        "3. Report findings in a structured format\n"
        "4. If no changes found, say so and suggest running git_diff first"
    )


def _commit_handler(args: str) -> str | None:
    """Git commit skill — generates commit message and commits."""
    message = args.strip() if args else ""
    if message:
        return (
            f"Create a git commit with the following message: {message}\n"
            "1. Run git_diff --staged to verify what will be committed\n"
            "2. If nothing is staged, run git_diff and ask if I want to stage everything\n"
            "3. Run: git add -A\n"
            "4. Run: git commit -m \"{message}\"\n"
            "5. Confirm the commit was created"
        )
    return (
        "Create a git commit for the current changes:\n"
        "1. Run git_diff to see all changes\n"
        "2. Run git_diff --staged to check staged changes\n"
        "3. Generate a concise commit message describing the changes\n"
        "4. Stage and commit with that message\n"
        "5. Confirm the commit was created"
    )


def _test_handler(args: str) -> str | None:
    """Run tests skill."""
    return (
        "Run the project tests:\n"
        "1. First list_files to find test files\n"
        "2. If tests exist, run them (e.g., python -m pytest or python test_all.py)\n"
        "3. If no tests exist, suggest creating some\n"
        "4. Report which tests passed/failed"
    )


def _simplify_handler(args: str) -> str | None:
    """Code simplification skill."""
    return (
        "Review the current changes for code quality and simplify where possible:\n"
        "1. Run git_diff to see changes\n"
        "2. Look for: duplicated code, over-complicated logic, unused variables,\n"
        "   overly long functions, unnecessary abstraction\n"
        "3. Apply simplifications and verify tests still pass"
    )


def register_bundled_skills() -> None:
    """Register all built-in skills."""
    register_skill(Skill(
        name="review",
        description="Code review the current changes",
        handler=_review_handler,
    ))
    register_skill(Skill(
        name="commit",
        description="Create a git commit with a generated message",
        handler=_commit_handler,
    ))
    register_skill(Skill(
        name="test",
        description="Run project tests",
        handler=_test_handler,
    ))
    register_skill(Skill(
        name="simplify",
        description="Review code for quality and simplify",
        handler=_simplify_handler,
    ))
