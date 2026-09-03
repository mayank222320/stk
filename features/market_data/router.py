"""
Market News Router
Fetches live financial news from free RSS feeds + yfinance.
No API keys required.
"""
import time
from datetime import datetime
from typing import Optional

import yfinance as yf  # type: ignore
from fastapi import APIRouter, Query
from pydantic import BaseModel

from features.market_data.news_fetcher import fetch_all_news, RSS_SOURCES

router = APIRouter(prefix="/market", tags=["Market News"])


class NewsArticle(BaseModel):
    title: str
    summary: str
    url: str
    source: str
    category: str
    published: str
    published_ts: int


@router.get("/news/live", response_model=list[NewsArticle])
async def get_live_news(limit: int = Query(30, ge=5, le=60)):
    """
    Fetch live Indian stock market news from multiple RSS feeds simultaneously.
    Returns articles sorted by newest first. No API key required.
    """
    items = await fetch_all_news(limit=limit)
    return [NewsArticle(**vars(item)) for item in items]


@router.get("/news/stock", response_model=list[NewsArticle])
async def get_stock_news(symbol: str = Query(..., description="e.g. RELIANCE.NS or INFY.NS")):
    """Fetch news for a specific stock using yfinance. Handles both v0.x and v1.x formats."""
    articles = []
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news or []
        for item in news[:10]:
            article = _parse_yfinance_news_item(item)
            if article:
                articles.append(article)
    except Exception as exc:
        print(f"[NewsRouter] yfinance error for {symbol}: {exc}")
    return articles


def _parse_yfinance_news_item(item: dict) -> "NewsArticle | None":
    """
    Parse a yfinance news item, handling both the old flat format (v0.x)
    and the new nested 'content' format (v1.x).
    Returns None if the item has no usable title or URL.
    """
    # ── yfinance v1.x: nested under 'content' key ──────────────────────
    if "content" in item:
        c = item["content"]
        title   = c.get("title", "").strip()
        summary = c.get("summary", "") or c.get("description", "") or ""
        # URL can live in canonicalUrl.url or clickThroughUrl.url
        url = (
            (c.get("canonicalUrl") or {}).get("url")
            or (c.get("clickThroughUrl") or {}).get("url")
            or "#"
        )
        source = (c.get("provider") or {}).get("displayName", "Yahoo Finance")
        # pubDate is an ISO string like "2026-08-02T10:30:00Z"
        pub_str = c.get("pubDate", "")
        try:
            from datetime import timezone
            dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            ts = int(dt.timestamp())
            published = dt.strftime("%b %d, %Y · %I:%M %p")
        except Exception:
            ts = int(time.time())
            published = datetime.fromtimestamp(ts).strftime("%b %d, %Y · %I:%M %p")

    # ── yfinance v0.x: flat format ──────────────────────────────────────
    else:
        title   = item.get("title", "").strip()
        summary = item.get("summary", "") or ""
        url     = item.get("link", "#") or "#"
        source  = item.get("publisher", "Yahoo Finance")
        ts      = item.get("providerPublishTime", int(time.time()))
        try:
            published = datetime.fromtimestamp(int(ts)).strftime("%b %d, %Y · %I:%M %p")
        except Exception:
            published = "Recently"

    # Skip placeholder / empty items
    if not title or title.lower() == "untitled" or url == "#":
        return None

    return NewsArticle(
        title=title,
        summary=summary.strip() or "No summary available.",
        url=url,
        source=source,
        category="Stock",
        published=published,
        published_ts=ts,
    )
