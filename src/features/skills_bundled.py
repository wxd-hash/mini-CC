"""Built-in skills — matches claude-code's bundled skills pattern.

Each skill has a body (the SKILL.md content) and get_prompt() builds the
final prompt by merging body + user args.
"""

from __future__ import annotations

from src.features.skills import Skill, register_skill


_REVIEW_BODY = """\
审查当前变更中的 bug、安全问题、不清晰代码和缺失的测试。

步骤：
1. 运行 git_diff 查看所有变更（未暂存和已暂存）
2. 检查每个变更文件的：
   - 逻辑 bug 或边界情况
   - 安全漏洞（注入、XSS、路径遍历）
   - 不清晰的变量名或复杂逻辑缺少注释
   - 缺失的错误处理
   - 缺失或不充分的测试
3. 按结构化格式报告：
   - 严重问题（必须修复）
   - 警告（应该修复）
   - 建议（锦上添花）
4. 如果没有变更，直接说明。"""

_COMMIT_BODY = """\
为当前变更创建 git 提交。

步骤：
1. 运行 git_diff 查看所有变更
2. 运行 git_diff --staged 检查已暂存的变更
3. 生成简洁、描述性的提交信息：
   - 格式：<类型>: <描述>（如 feat:、fix:、docs:、refactor:）
   - 关注 WHY（为什么改），而不是 WHAT（改了什么）
   - 标题行控制在 72 字符以内
4. 暂存所有变更并提交
5. 确认提交已成功创建

重要：除非明确要求，否则绝不跳过 hooks（--no-verify、--no-gpg-sign 等）。
绝不 force push 到 main/master。绝不提交 secrets 或 .env 文件。

如果没有已暂存的内容，先询问用户是否要暂存全部变更。"""

_TEST_BODY = """\
运行项目测试。

步骤：
1. 查找测试文件（用 search_files 搜索 'def test_' 或查找 test_*.py）
2. 运行测试：python -m pytest -v
3. 如果测试失败，阅读失败的测试和源码来诊断原因
4. 报告：哪些测试通过、哪些失败、失败原因是什么
5. 如果项目没有测试，建议创建一些"""

_SIMPLIFY_BODY = """\
审查当前变更的代码质量并简化。

步骤：
1. 运行 git_diff 查看所有变更
2. 查找以下问题：
   - 重复的代码模式
   - 过于复杂的逻辑（深层嵌套、过长函数）
   - 未使用的变量或导入
   - 不必要的抽象（3 行相似代码好过过早抽象）
3. 应用简化
4. 验证测试仍然通过
5. 报告简化了什么以及为什么"""


def register_bundled_skills() -> None:
    register_skill(Skill(
        name="review",
        description="审查当前代码变更",
        body=_REVIEW_BODY,
    ))
    register_skill(Skill(
        name="commit",
        description="生成提交信息并创建 git 提交",
        body=_COMMIT_BODY,
    ))
    register_skill(Skill(
        name="test",
        description="运行项目测试",
        body=_TEST_BODY,
    ))
    register_skill(Skill(
        name="simplify",
        description="审查代码质量并简化",
        body=_SIMPLIFY_BODY,
    ))
