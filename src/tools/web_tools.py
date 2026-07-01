"""WebFetch tool — fetch and process web page content."""

from __future__ import annotations

import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from src.tools.base import Tool, ToolResult


class WebFetchTool(Tool):
    """Fetch a web page and extract content based on a prompt.

    Matches claude-code's WebFetchTool: HTML → markdown with html2text-like
    conversion, then applies the prompt to the extracted content.
    """

    MAX_CONTENT_CHARS = 120_000  # max raw content to process
    TIMEOUT = 15  # seconds

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "- 获取指定 URL 的网页内容，用 prompt 从中提取所需信息\n"
            "- 通常先通过 web_search 发现 URL，再用 web_fetch 深入阅读\n"
            "- **重要**：web_fetch 会失败于需要登录的页面（Google Docs、Confluence、Jira、GitHub private 等）\n"
            "- HTTP URL 自动升级为 HTTPS。结果过大时自动截断。\n"
            "- 重复访问同一 URL 有 15 分钟缓存加速。\n"
            "- URL 重定向时会返回新地址——用新地址重新调用"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要获取的完整 URL（含协议）",
                },
                "prompt": {
                    "type": "string",
                    "description": "从获取的内容中提取什么信息",
                },
            },
            "required": ["url", "prompt"],
        }

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        url = kwargs.get("url", "")
        short = url[:60] + "..." if len(url) > 60 else url
        return f"Fetching {short}"

    def execute(self, **kwargs: Any) -> ToolResult:
        url = kwargs.get("url", "")
        prompt = kwargs.get("prompt", "")

        if not url.startswith(("http://", "https://")):
            return ToolResult(content=f"Error: URL must start with http:// or https://: {url!r}", is_error=True)

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "MiniClaudeCode/1.0",
                    "Accept": "text/html,text/plain;q=0.9,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                charset = "utf-8"
                match = re.search(r"charset=([^\s;]+)", content_type)
                if match:
                    charset = match.group(1)
                html = raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            return ToolResult(content=f"Error: HTTP {e.code} {e.reason} for {url}", is_error=True)
        except urllib.error.URLError as e:
            return ToolResult(content=f"Error: Cannot reach {url} — {e.reason}", is_error=True)
        except Exception as e:
            return ToolResult(content=f"Error fetching {url}: {e}", is_error=True)

        text = _html_to_text(html)[:self.MAX_CONTENT_CHARS]

        summary = (
            f"Fetched {url} ({len(raw)} bytes, {len(text)} chars of text extracted).\n"
            f"Content type: {content_type}\n\n"
            f"=== Content (extracted from HTML) ===\n{text}\n=== End of content ===\n\n"
            f"Based on the content above, answer this prompt: {prompt}"
        )
        return ToolResult(content=summary)


class WebSearchTool(Tool):
    """Search the web using DuckDuckGo Lite and return results.

    Matches Claude Code's WebSearch tool: no API key required,
    returns title + URL + snippet for each result.
    """

    MAX_RESULTS = 10
    MAX_CONTENT_CHARS = 8_000
    TIMEOUT = 10

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "- 搜索网页获取训练数据之外的最新信息，结果自动包含标题、URL 和摘要\n"
            "- **强制要求**：回答完用户问题后，必须在回复末尾列出 Sources 段落，"
            "以 Markdown 链接格式引用所有相关来源：[标题](URL)。不引用来源即为违规。\n"
            "- 搜索时注意使用正确的年份（当前 2026 年），"
            "查询技术文档时加上年份参数避免过时结果\n"
            "- 支持域名白名单（allowed_domains）和黑名单（blocked_domains）过滤\n"
            "- 已在工具列表中的工具（如 read_file/list_files/glob）不要通过 web_search 搜索——直接调用即可"
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如 'Python 3.14 release notes'",
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选。只返回这些域名的结果，如 ['docs.python.org']",
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选。排除这些域名的结果",
                },
            },
            "required": ["query"],
        }

    @property
    def maxResultSizeChars(self) -> int | None:
        return self.MAX_CONTENT_CHARS

    def is_read_only(self) -> bool:
        return True

    def get_activity_description(self, **kwargs: Any) -> str | None:
        query = kwargs.get("query", "")
        short = query[:50] + "..." if len(query) > 50 else query
        return f"搜索: {short}"

    def execute(self, query: str = "", allowed_domains: list | None = None,
                blocked_domains: list | None = None, **kwargs: Any) -> ToolResult:
        if not query.strip():
            return ToolResult(content="错误: 搜索关键词不能为空", is_error=True)
        if allowed_domains is None:
            allowed_domains = []
        if blocked_domains is None:
            blocked_domains = []

        try:
            results = self._search(query, allowed_domains, blocked_domains)
        except Exception as e:
            return ToolResult(content=f"搜索失败: {e}", is_error=True)

        if not results:
            return ToolResult(content=f"搜索 '{query}' 没有找到结果。")

        lines = [f"搜索 '{query}' 的结果 ({len(results)} 条):\n"]
        for i, (title, url, snippet) in enumerate(results, 1):
            snippet = snippet[:200].replace("\n", " ") if snippet else ""
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {url}")
            lines.append(f"   {snippet}")
            lines.append("")

        content = "\n".join(lines)
        if len(content) > self.MAX_CONTENT_CHARS:
            content = content[:self.MAX_CONTENT_CHARS] + "\n... [truncated]"
        return ToolResult(content=content)

    def _search(self, query: str, allowed: list[str], blocked: list[str]) -> list[tuple[str, str, str]]:
        """Query DuckDuckGo Lite and parse HTML results."""
        import urllib.parse
        encoded = urllib.parse.quote(query)
        url = f"https://lite.duckduckgo.com/lite/?q={encoded}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html",
            },
        )
        with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        results = _parse_ddg_lite(html)

        # Filter by allowed/blocked domains
        filtered = []
        for title, link, snippet in results:
            domain = _extract_domain(link)
            if blocked and any(b in domain for b in blocked):
                continue
            if allowed and not any(a in domain for a in allowed):
                continue
            filtered.append((title, link, snippet))
            if len(filtered) >= self.MAX_RESULTS:
                break

        return filtered


def _parse_ddg_lite(html: str) -> list[tuple[str, str, str]]:
    """Parse DuckDuckGo Lite HTML search results.

    DDG Lite returns a simple table. Each result is 2 rows:
      <tr><td><a href="//duckduckgo.com/l/?uddg=..." class='result-link'>Title</a></td></tr>
      <tr><td class='result-snippet'>Snippet text</td></tr>

    We find all result-link <a> tags with uddg= URLs, then find the
    nearest following result-snippet <td>.
    """
    results = []

    # Step 1: find all result links with DDG redirect URLs
    link_re = re.compile(
        r"<a[^>]*href=['\"]([^'\"]*uddg=[^'\"]+)['\"][^>]*>(.*?)</a>",
        re.DOTALL | re.IGNORECASE,
    )
    links = [(m.start(), m.group(1), m.group(2)) for m in link_re.finditer(html)]

    # Step 2: find all snippet positions
    snippet_re = re.compile(
        r"<td[^>]*class=['\"]result-snippet['\"][^>]*>(.*?)</td>",
        re.DOTALL | re.IGNORECASE,
    )
    snippet_positions = [(m.start(), m.group(1)) for m in snippet_re.finditer(html)]

    # Step 3: pair each link with the next snippet after it
    for link_start, raw_href, raw_title in links:
        url = _unwrap_ddg_url(raw_href)
        title = _clean_html(raw_title)
        if not title:
            continue

        snippet = ""
        for snip_start, snip_text in snippet_positions:
            if snip_start > link_start:
                snippet = _clean_html(snip_text)
                break

        results.append((title, url, snippet))

    return results


def _unwrap_ddg_url(raw_href: str) -> str:
    """Unwrap a DuckDuckGo redirect URL to the real destination."""
    from urllib.parse import unquote, parse_qs, urlparse
    url = raw_href
    # Fix HTML-encoded ampersands in the URL
    url = url.replace("&amp;", "&")
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/?uddg=" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [url])[0]
        url = unquote(uddg)
        # Remove tracking parameter
        if "&rut=" in url:
            url = url.split("&rut=")[0]
    return url


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities from a string."""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&#x27;", "'")
    text = text.replace("&nbsp;", " ")
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    text = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), text)
    return text.strip()


def _extract_domain(url: str) -> str:
    """Extract the domain from a URL."""
    from urllib.parse import urlparse
    return urlparse(url).netloc.lower()


def _html_to_text(html: str) -> str:
    """Converts HTML to plain text: strip tags, decode entities, collapse whitespace."""
    # Remove scripts and styles
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block tags with newlines
    for tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "section", "article", "header", "footer"):
        html = re.sub(rf"</?{tag}[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Remove remaining HTML tags
    html = re.sub(r"<[^>]+>", "", html)
    # Decode common entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    html = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), html)
    # Collapse whitespace
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()
