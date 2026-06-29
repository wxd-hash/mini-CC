"""Built-in skills — matches claude-code's bundled skills pattern.

Each skill has a body (the SKILL.md content) and get_prompt() builds the
final prompt by merging body + user args.
"""

from __future__ import annotations

from src.features.skills import Skill, register_skill


_REVIEW_BODY = """\
Review the current changes for bugs, security issues, unclear code, and missing tests.

Steps:
1. Run git_diff to see all changes (both unstaged and staged)
2. Check each changed file for:
   - Logic bugs or edge cases
   - Security vulnerabilities (injection, XSS, path traversal)
   - Unclear variable names or missing comments on complex logic
   - Missing error handling
   - Missing or inadequate tests
3. Report findings in a structured format:
   - Critical issues (must fix)
   - Warnings (should fix)
   - Suggestions (nice to have)
4. If no changes found, say so."""

_COMMIT_BODY = """\
Create a git commit for the current changes.

Steps:
1. Run git_diff to see all changes
2. Run git_diff --staged to check staged changes
3. Generate a concise, descriptive commit message:
   - Format: <type>: <description> (e.g., feat:, fix:, docs:, refactor:)
   - Focus on WHY, not WHAT
   - Keep under 72 chars for the subject line
4. Stage all changes and commit
5. Confirm the commit was created successfully

IMPORTANT: Never skip hooks (--no-verify, --no-gpg-sign, etc) unless explicitly asked.
Never force push to main/master. Never commit secrets or .env files.

If nothing is staged, ask if the user wants to stage everything first."""

_TEST_BODY = """\
Run the project tests.

Steps:
1. Find test files (search_files for 'def test_' or look for test_*.py)
2. Run tests: python -m pytest -v
3. If tests fail, read the failing test and the source code to diagnose
4. Report: which tests passed, which failed, and what the failures mean
5. If no tests exist, suggest creating some"""

_SIMPLIFY_BODY = """\
Review the current changes for code quality and simplify.

Steps:
1. Run git_diff to see all changes
2. Look for:
   - Duplicated code patterns
   - Overly complex logic (deeply nested, long functions)
   - Unused variables or imports
   - Unnecessary abstraction (3 similar lines is better than premature abstraction)
3. Apply simplifications
4. Verify tests still pass
5. Report what was simplified and why"""


def register_bundled_skills() -> None:
    register_skill(Skill(
        name="review",
        description="Code review the current changes",
        body=_REVIEW_BODY,
    ))
    register_skill(Skill(
        name="commit",
        description="Create a git commit with a generated message",
        body=_COMMIT_BODY,
    ))
    register_skill(Skill(
        name="test",
        description="Run project tests",
        body=_TEST_BODY,
    ))
    register_skill(Skill(
        name="simplify",
        description="Review code for quality and simplify",
        body=_SIMPLIFY_BODY,
    ))
