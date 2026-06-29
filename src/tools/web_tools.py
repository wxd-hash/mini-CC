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
            "获取指定 URL 的网页内容，并用 prompt 从中提取相关信息。"
            "用于查阅在线文档、API 参考、博客文章等。"
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
