"""
News Scanner Service — runs every 5 minutes during market hours.
Architecture:
  1. Fetch all RSS articles (no AI cost)
  2. Deduplicate against DB cache (no AI cost)
  3. Keyword filter on headline/summary (no AI cost)
  4. Only then call Grok AI for high-signal articles
  5. Alert via Telegram if confidence > 85 and bullish/bearish
"""
import json
import re
from datetime import datetime, timezone, timedelta

from core.database import mongo
from core.config import USER_ID
from features.bot.setup import bot
from features.grok.service import analyze_news
from features.market_data.news_fetcher import fetch_all_news
from features.notifications.service import broadcast

# High-impact keywords that indicate a market-moving event
_TRIGGER_RE = re.compile(
    r"(surge|soars?|plummets?|crashes?|wins?|acquires?|acquisition|merger|"
    r"order win|earnings|dividend|fda approv|resigns?|scam|fraud|approves?|"
    r"invest|partners?|breakout|guidance|upgrades?|downgrades?|ipo|record)",
    re.IGNORECASE,
)

# Max articles to pass through to AI in a single run (token guard)
_MAX_AI_CALLS_PER_RUN = 3


async def _get_processed_urls_today(urls: list[str]) -> set[str]:
    """Return the subset of URLs that have already been processed today."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = mongo.db.processed_news.find(
        {"url": {"$in": urls}, "processed_at": {"$gte": today_start}},
        {"url": 1, "_id": 0},
    )
    return {doc["url"] async for doc in cursor}


async def _send_alert(article, result: dict) -> None:
    """Format and dispatch the Telegram + broadcast alert."""
    sentiment = result["sentiment"].lower()
    confidence = result["confidence"]
    symbols = result.get("impacted_symbols", [])
    sym_str = " · ".join(f"#{s}" for s in symbols[:5])

    # Sentiment icon + confidence bar
    if sentiment == "bullish":
        icon = "🚀"
        sentiment_label = "🟢 BULLISH"
    elif sentiment == "bearish":
        icon = "🔴"
        sentiment_label = "🔴 BEARISH"
    else:
        icon = "⚪"
        sentiment_label = "⚪ NEUTRAL"

    # Confidence bar (filled blocks out of 10)
    filled = round(confidence / 10)
    conf_bar = "█" * filled + "░" * (10 - filled)

    ist_time = datetime.now(timezone.utc).strftime("%d %b · %I:%M %p UTC")

    msg = (
        f"{icon} <b>Breaking News Alert</b>\n"
        f"🕒 {ist_time}\n"
        f"────────────────────\n"
        f"<b>{article.title}</b>\n"
        f"<i>Source: {article.source}</i>\n\n"
        f"<b>Stocks:</b> {sym_str or 'General Market'}\n"
        f"<b>Sentiment:</b> {sentiment_label}\n"
        f"<b>Confidence:</b> {conf_bar} {confidence}%\n\n"
        f"<b>📌 Trade Setup:</b>\n"
        f"{result.get('trade_setup', 'N/A')}\n\n"
        f"<b>🧠 Analysis:</b>\n"
        f"{result.get('summary', 'N/A')}\n\n"
        f"<a href='{article.url}'>📰 Read Full Article →</a>"
    )

    if USER_ID:
        try:
            await bot.send_message(chat_id=int(USER_ID), text=msg, parse_mode="HTML")
        except Exception as exc:
            print(f"[NewsScanner] Telegram send failed: {exc}")

    await broadcast(
        text=f"Breaking News: {article.title}\nSentiment: {sentiment.upper()} ({confidence}%)",
        title=f"{icon} {sym_str} Alert",
        ntfy_priority="max",
    )

    # Persist alert to DB for dashboard history
    await mongo.db.news_alerts.insert_one({
        "headline": article.title,
        "source": article.source,
        "url": article.url,
        "sentiment": sentiment,
        "confidence": confidence,
        "impacted_symbols": symbols,
        "trade_setup": result.get("trade_setup", ""),
        "summary": result.get("summary", ""),
        "alerted_at": datetime.now(timezone.utc),
    })


async def run_news_scanner() -> None:
    print("[NewsScanner] Starting 5-minute scan...")

    if mongo.db is None:
        print("[NewsScanner] MongoDB not connected — aborting.")
        return

    # Step 1: Fetch all articles (free, no AI)
    try:
        articles = await fetch_all_news(limit=80)
    except Exception as exc:
        print(f"[NewsScanner] RSS fetch failed: {exc}")
        return

    if not articles:
        return

    # Step 2: Deduplicate (free, DB lookup)
    all_urls = [a.url for a in articles]
    already_seen = await _get_processed_urls_today(all_urls)

    # Only consider articles published in the last 4 hours to avoid stale news
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(hours=4)).timestamp())

    candidates = [
        a for a in articles
        if a.url not in already_seen and a.published_ts >= cutoff_ts
    ]

    if not candidates:
        print("[NewsScanner] No new articles in last 4h.")
        return

    print(f"[NewsScanner] {len(candidates)} new articles to check.")

    # Step 3: Keyword filter (free, regex) — mark ALL new articles as processed
    # but only escalate ones that match the trigger pattern to the AI
    triggered = []
    bulk_inserts = []

    for article in candidates:
        bulk_inserts.append({
            "url": article.url,
            "title": article.title,
            "processed_at": datetime.now(timezone.utc),
        })
        if _TRIGGER_RE.search(article.title) or _TRIGGER_RE.search(article.summary):
            triggered.append(article)

    if bulk_inserts:
        try:
            await mongo.db.processed_news.insert_many(bulk_inserts, ordered=False)
        except Exception as exc:
            # ordered=False means partial inserts are fine; duplicate key errors are expected
            print(f"[NewsScanner] processed_news bulk insert note: {exc}")

    if not triggered:
        print("[NewsScanner] No high-signal articles found.")
        return

    print(f"[NewsScanner] {len(triggered)} articles passed keyword filter — sending to AI (max {_MAX_AI_CALLS_PER_RUN}).")

    # Step 4: AI analysis — capped to avoid burning tokens in one run
    ai_calls = 0
    for article in triggered[:_MAX_AI_CALLS_PER_RUN]:
        result = await analyze_news(article.title, article.summary, article.url)

        if "error" in result:
            print(f"[NewsScanner] AI error for '{article.title[:60]}': {result['error']}")
            continue

        ai_calls += 1
        confidence = result.get("confidence", 0)
        sentiment = result.get("sentiment", "neutral").lower()
        symbols = result.get("impacted_symbols", [])

        print(f"[NewsScanner] '{article.title[:60]}' → {sentiment} {confidence}%")

        # Step 5: Alert only on high-conviction signals with identifiable symbols
        if confidence >= 85 and sentiment in ("bullish", "bearish") and symbols:
            await _send_alert(article, result)

    print(f"[NewsScanner] Done. {ai_calls} AI calls made.")


async def get_news_alerts(limit: int = 20) -> list[dict]:
    """Return recent breaking news alerts for the dashboard."""
    if mongo.db is None:
        return []
    cursor = mongo.db.news_alerts.find({}).sort("alerted_at", -1).limit(limit)
    docs = await cursor.to_list(length=None)
    for d in docs:
        d["id"] = str(d.pop("_id"))
        if d.get("alerted_at"):
            d["alerted_at"] = d["alerted_at"].isoformat()
    return docs
