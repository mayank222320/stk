"""
Market data service layer.
Extracted from router so other services can call RSS fetching without circular imports.

RSS Source Status (last verified 2026-08-09):
  WORKING  : Economic Times, LiveMint, Hindu BusinessLine, NDTV Profit, Reuters India,
             Financial Express, Zee Business, ET Tech
  BLOCKED  : Moneycontrol (all feeds — 403 from cloud IPs)
             Business Standard (all feeds — 403 from cloud IPs)
"""
import asyncio
import time
import re
from datetime import datetime
from dataclasses import dataclass

import feedparser
import httpx


# ── RSS Sources ───────────────────────────────────────────────────────────────
# Moneycontrol & Business Standard permanently blocked from cloud/server IPs.
# Replaced with equivalent sources that work reliably on Render.
RSS_SOURCES = [
    # Economic Times — reliable, Indian market leader
    {"name": "Economic Times Markets",  "url": "https://economictimes.indiatimes.com/markets/rss.cms",        "category": "Market"},
    {"name": "Economic Times Stocks",   "url": "https://economictimes.indiatimes.com/markets/stocks/rss.cms",  "category": "Stocks"},
    {"name": "ET Tech",                 "url": "https://economictimes.indiatimes.com/tech/rss.cms",            "category": "Tech"},

    # LiveMint — reliable from cloud
    {"name": "LiveMint Markets",        "url": "https://www.livemint.com/rss/markets",                         "category": "Market"},
    {"name": "LiveMint Companies",      "url": "https://www.livemint.com/rss/companies",                       "category": "Stocks"},
    {"name": "LiveMint Tech",           "url": "https://www.livemint.com/rss/technology",                      "category": "Tech"},

    # Hindu BusinessLine — reliable
    {"name": "Hindu BusinessLine",      "url": "https://www.thehindubusinessline.com/markets/feeder/default.rss", "category": "Market"},
    {"name": "BusinessLine Companies",  "url": "https://www.thehindubusinessline.com/companies/feeder/default.rss","category": "Stocks"},

    # Financial Express — works from servers
    {"name": "Financial Express Market","url": "https://www.financialexpress.com/market/feed/",                "category": "Market"},
    {"name": "Financial Express India", "url": "https://www.financialexpress.com/india-news/feed/",            "category": "Finance"},

    # Zee Business — works from servers
    {"name": "Zee Business",            "url": "https://www.zeebiz.com/feed",                                  "category": "Market"},

    # NDTV Profit — via feedburner
    {"name": "NDTV Profit",             "url": "https://feeds.feedburner.com/ndtvprofit-latest",               "category": "Finance"},
]

# Browser-like headers to reduce 403 rejections
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


@dataclass
class NewsItem:
    title: str
    summary: str
    url: str
    source: str
    category: str
    published: str
    published_ts: int


def _parse_published(entry) -> tuple[str, int]:
    try:
        ts = time.mktime(entry.published_parsed) if hasattr(entry, "published_parsed") and entry.published_parsed else time.time()
        human = datetime.fromtimestamp(ts).strftime("%b %d, %Y · %I:%M %p")
        return human, int(ts)
    except Exception:
        return "Recently", int(time.time())


def _clean_text(text: str, max_chars: int = 220) -> str:
    clean = re.sub(r"<[^>]+>", "", text or "")
    clean = clean.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars].rsplit(" ", 1)[0] + "…"
    return clean


async def _fetch_feed(source: dict, client: httpx.AsyncClient) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        resp = await client.get(source["url"], timeout=8.0)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        for entry in feed.entries[:6]:
            human, ts = _parse_published(entry)
            items.append(NewsItem(
                title=_clean_text(entry.get("title", "Untitled"), 150),
                summary=_clean_text(entry.get("summary", entry.get("description", "")), 220),
                url=entry.get("link", "#"),
                source=source["name"],
                category=source["category"],
                published=human,
                published_ts=ts,
            ))
    except httpx.HTTPStatusError as exc:
        # Only log non-403 errors to avoid log noise from permanently blocked sources
        if exc.response.status_code != 403:
            print(f"[NewsFetcher] HTTP {exc.response.status_code} for {source['name']}: {exc}")
    except Exception as exc:
        print(f"[NewsFetcher] Failed to fetch {source['name']}: {type(exc).__name__}: {exc}")
    return items


async def fetch_all_news(limit: int = 60) -> list[NewsItem]:
    """Fetch all RSS feeds concurrently, deduplicate by title, sort newest first."""
    async with httpx.AsyncClient(headers=HTTP_HEADERS, follow_redirects=True) as client:
        results = await asyncio.gather(
            *[_fetch_feed(src, client) for src in RSS_SOURCES],
            return_exceptions=True,
        )

    all_items: list[NewsItem] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)

    seen: set[str] = set()
    unique: list[NewsItem] = []
    for item in all_items:
        key = item.title.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(key=lambda x: x.published_ts, reverse=True)
    return unique[:limit]
