"""
article_fetcher.py
──────────────────
Fetches the full text of a news article from a URL.
Uses httpx for async fetching and BeautifulSoup for extraction.
Falls back gracefully — never crashes the parent request.
"""
import re
import asyncio
import httpx
from bs4 import BeautifulSoup

# Tags that typically carry the article body
_CONTENT_TAGS = ["article", "main", "section"]

# Selectors to remove (ads, nav, footer, scripts, etc.)
_NOISE_SELECTORS = [
    "script", "style", "noscript", "nav", "header", "footer",
    "aside", "form", "iframe", "figure", "figcaption",
    ".advertisement", ".ads", ".sidebar", ".related",
    ".social-share", ".cookie-banner", "#comments",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}


async def fetch_article_text(url: str, timeout: float = 8.0, max_chars: int = 8000) -> str:
    """
    Async — fetch the article at `url` and return its cleaned body text.
    Returns an empty string on any error so the caller can degrade gracefully.

    Args:
        url:       The article URL to fetch.
        timeout:   HTTP timeout in seconds.
        max_chars: Maximum characters to return (keeps prompts within Gemini limits).
    """
    if not url or not url.startswith("http"):
        return ""

    try:
        async with httpx.AsyncClient(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=timeout,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return ""

    try:
        soup = BeautifulSoup(html, "html.parser")

        # Strip noise
        for sel in _NOISE_SELECTORS:
            for tag in soup.select(sel):
                tag.decompose()

        # Try semantic containers first
        body_text = ""
        for tag_name in _CONTENT_TAGS:
            container = soup.find(tag_name)
            if container:
                body_text = container.get_text(separator="\n", strip=True)
                break

        # Fall back to full body
        if not body_text and soup.body:
            body_text = soup.body.get_text(separator="\n", strip=True)

        # Collapse excessive whitespace / blank lines
        lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
        cleaned = "\n".join(lines)

        return cleaned[:max_chars]

    except Exception:
        return ""
