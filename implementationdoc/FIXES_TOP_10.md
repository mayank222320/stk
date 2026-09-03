# Fixing the Ten Bugs — exact code

**Written:** 4 September 2026
**Purpose:** the smallest correct patch for each confirmed bug, in the order you should apply them.
**Companions:** `AGENT_GUIDE.md` (rules and work packages) · `PROGRESS.md` (tracker)

Each fix states: the bug, the evidence, the patch, and how to verify. These are **minimal** patches — they fix the bug without the full restructure in `ENGINEERING.md`. Where a fix is a stepping stone to a bigger work package, that's noted.

**Apply in this order.** Fixes 1–4 are about two hours total and clear the two most damaging problems.

---

# Fix 1 — Bot freezes during every scan 🔴

**Bug:** `yfinance` is synchronous. Calling it inside `async def` blocks the whole event loop, including Telegram polling.

**Evidence — three locations,** confirmed by grepping for sync calls not wrapped in `run_in_executor`:

| Location | Called from | Impact |
|---|---|---|
| [intraday/service.py:62-67](../features/intraday/service.py#L62-L67) | `run_intraday_scan` | inside a **loop over symbols** — the freeze multiplies |
| [market_data/router.py:44-45](../features/market_data/router.py#L44-L45) | `GET /market/news/stock` | **this is your UI news button.** `ticker.news` is a network call with no executor |
| [portfolio/service.py:70-71](../features/portfolio/service.py#L70-L71) | `get_positions` | one call per open position |

The rest of the codebase is fine — `market_data/service.py:116`, `technical_indicators.py:41-42/149/357` all run inside `run_in_executor` already. That's exactly why this needs to be an enforced rule rather than something you remember.

**Patch — create the adapter:**

```python
# adapters/market/yfinance_adapter.py   (new file)
"""All yfinance access goes through here. Never call yfinance directly from async code."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pandas as pd
import yfinance as yf

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="yf")


async def _run(fn, *args, **kwargs):
    """Run a blocking function off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_POOL, partial(fn, *args, **kwargs))


def _sync_last_prices(symbols: list[str]) -> dict[str, float]:
    """ONE batched request for all symbols instead of N sequential ones."""
    if not symbols:
        return {}
    tickers = " ".join(f"{s}.NS" for s in symbols)
    df = yf.download(tickers, period="1d", interval="1m", group_by="ticker",
                     threads=True, progress=False, auto_adjust=True)
    out: dict[str, float] = {}
    for s in symbols:
        try:
            col = df[f"{s}.NS"]["Close"] if len(symbols) > 1 else df["Close"]
            series = col.dropna()
            if not series.empty:
                out[s] = float(series.iloc[-1])
        except (KeyError, IndexError):
            continue
    return out


def _sync_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)


async def get_last_prices(symbols: list[str]) -> dict[str, float]:
    """Latest 1-minute close per symbol. Non-blocking."""
    return await _run(_sync_last_prices, sorted(set(symbols)))


async def get_history(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """OHLCV for one symbol. Tries .NS then .BO. Non-blocking."""
    for suffix in (".NS", ".BO"):
        df = await _run(_sync_history, symbol.upper() + suffix, period, interval)
        if df is not None and not df.empty:
            return df
    return pd.DataFrame()
```

**Patch — `features/portfolio/service.py`, replace lines 66-76:**

```python
    # BEFORE: a blocking yf.Ticker call per symbol inside async, wrapped in `except Exception: pass`
    open_symbols = sorted({d["symbol"] for d in docs if d["status"] == "open"})
    if open_symbols:
        from adapters.market.yfinance_adapter import get_last_prices
        try:
            prices = await get_last_prices(open_symbols)       # one request, off-loop
            for sym, price in prices.items():
                await update_position_price(sym, price)
        except Exception as exc:
            print(f"[Portfolio] price refresh failed: {exc}")   # logged, not swallowed
```

**Patch — `features/intraday/service.py`, replace lines 61-68:**

```python
        from adapters.market.yfinance_adapter import get_history
        try:
            info = await get_history(symbol, period="1d", interval="1m")   # handles .NS/.BO
            if info.empty:
                print(f"[Intraday] {symbol} -> no data")
                results.append({...})      # keep your existing DATA_UNAVAILABLE branch
                continue
            price = float(info["Close"].iloc[-1])
```

Everything after that line stays exactly as it is.

**Patch — `features/market_data/router.py`, replace lines 43-47** (your UI news path):

```python
    from adapters.market.yfinance_adapter import _run
    articles = []
    try:
        news = await _run(lambda: yf.Ticker(symbol).news) or []   # off the event loop
        for item in news[:10]:
            article = _parse_yfinance_news_item(item)
            if article:
                articles.append(article)
    except Exception as exc:
        print(f"[NewsRouter] yfinance error for {symbol}: {exc}")
    return articles
```

**Verify:** `grep -rn "yf\.Ticker\|yf\.download\|ticker\.news" features/ | grep -v run_in_executor` returns nothing. Then click the UI news button while messaging the bot — it must reply instantly.

---

# Fix 2 — API open to the internet 🔴

**Bug:** no authentication anywhere. `DELETE /performance/recommendations/all` will erase your track record for anyone who has the URL. CORS does not stop curl.

**Patch — new file:**

```python
# core/auth.py
import os
import secrets

from fastapi import Header, HTTPException, status

API_TOKEN = os.getenv("API_TOKEN", "")


async def require_token(x_api_token: str | None = Header(default=None)) -> None:
    if not API_TOKEN:                                    # fail closed, never open
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "API_TOKEN not configured on server")
    if not x_api_token or not secrets.compare_digest(x_api_token, API_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Invalid or missing X-API-Token")
```

**Patch — `main.py`, replace lines 98-107:**

```python
from fastapi import Depends
from core.auth import require_token

app.include_router(system_router)          # keep GET / open — Render's port scanner needs it

for r in (gemini_router, notifications_router, performance_router, chat_router,
          market_router, grok_router, portfolio_router, intraday_router,
          news_scanner_router):
    app.include_router(r, dependencies=[Depends(require_token)])
```

**Then:**
1. `API_TOKEN=<paste output of: openssl rand -hex 32>` into `.env`
2. Add the same value to your Vercel project env, and send it as the `X-API-Token` header on every request from the dashboard.

**Verify:** `curl https://<app>/performance/hit-rate` → 401. With `-H "X-API-Token: <token>"` → 200. Dashboard still works after adding the header.

---

# Fix 3 — Per-minute writes filling the cluster 🔴

**Bug:** `custom_stock_minute_scan` is registered with `minute='*'` for 8 hours a day, and every run inserts one document per tracked symbol unconditionally. ~960 docs/day ≈ 150 MB/year on a 512 MB cluster.

**Evidence:** [scheduler/service.py:78-83](../features/scheduler/service.py#L78-L83) (the cron) and [intraday/service.py:204](../features/intraday/service.py#L204) (the unconditional insert).

**Patch A — replace the cron registration** (`features/scheduler/service.py`, lines 78-83):

```python
    # BEFORE: minute='*'  → 480 runs/day
    # AFTER: three explicit in-session checks, IST-native (also fixes the 3-10 UTC straddle)
    for _h, _m, _tag in ((11, 30, "midday"), (14, 0, "afternoon"), (15, 10, "preclose")):
        scheduler.add_job(
            custom_stock_scan,
            CronTrigger(day_of_week="mon-fri", hour=_h, minute=_m,
                        timezone=SCHEDULER_TIMEZONE),
            id=f"custom_scan_{_tag}", replace_existing=True,
            coalesce=True, misfire_grace_time=1800,
        )
```

Then simplify the handler (replaces `custom_stock_minute_scan`, lines 585-601):

```python
async def custom_stock_scan() -> None:
    if not await is_trading_day():           # Fix 10
        return
    if mongo.db is None:
        return
    docs = await mongo.db.performance_log.find(
        {"is_custom": True, "result": None}).to_list(length=None)   # ← no date filter: multi-day
    if not docs:
        return
    from features.intraday.service import run_intraday_scan
    await run_intraday_scan(symbols_override=[d["symbol"] for d in docs])
```

**Patch B — write only on state change.** In `features/intraday/service.py`, immediately before the `insert_one` at line 204:

```python
        # Skip the write entirely if nothing changed since the last check.
        last = await mongo.db.intraday_scans.find_one(
            {"date": today, "symbol": symbol}, sort=[("scan_time", -1)])
        if last and last.get("status") == status and not alerted:
            results.append({**doc, "id": str(last["_id"]), "skipped_write": True})
            continue
        result = await mongo.db.intraday_scans.insert_one(doc)
```

**Patch C — reclaim the space already used.** Dropping beats deleting on M0: a drop returns the whole storage file, while `deleteMany` leaves it allocated, and shared Atlas tiers can't run `compact`.

```python
# scripts/drop_old_scans.py — run once, after confirming nothing reads historical scans
import asyncio
from core.database import mongo

async def main():
    await mongo.connect()
    print(await mongo.db.intraday_scans.count_documents({}), "docs to drop")
    await mongo.db.drop_collection("intraday_scans")
    print("dropped")
    await mongo.close()

asyncio.run(main())
```

**Verify:** after one trading day, `intraday_scans` has single-digit new rows, not hundreds.

---

# Fix 4 — `weekly_trend` has never worked 🔴

**Bug:** `pandas_ta` is imported but absent from `requirements.txt`, and the weekly block sits in a bare `except: pass` — so `weekly_trend` has been `"N/A"` since the file was written. Don't install `pandas_ta` either: it breaks on `numpy>=2` and you pin `numpy==2.5.1`.

**Evidence:** [technical_indicators.py:148-157](../features/market_data/technical_indicators.py#L148-L157)

**Patch A — replace lines 146-157 entirely:**

```python
    # ── Weekly trend context ─────────────────────────────────────────
    weekly_trend, weekly_ema20 = "N/A", None
    try:
        # 2y ≈ 104 weekly bars. The old "6mo" gave only ~26, too few for a reliable EMA20.
        df_w = ticker.history(period="2y", interval="1wk", auto_adjust=True)
        if df_w is not None and not df_w.empty and len(df_w) >= 30:
            w = float(df_w["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
            weekly_ema20 = round(w, 2)
            weekly_trend = "above_weekly_ema20" if cmp > w else "below_weekly_ema20"
        else:
            n = 0 if df_w is None else len(df_w)
            print(f"[TechnicalIndicators] {symbol}: {n} weekly bars, need 30 — weekly trend N/A")
    except Exception as exc:
        print(f"[TechnicalIndicators] {symbol}: weekly trend FAILED: {exc}")   # no longer silent
```

Add `"weekly_ema20": weekly_ema20,` to the returned dict next to `"weekly_trend"`.

**Patch B — fix the indicator maths while you're here.** The `ImportError` fallback uses simple moving averages where Wilder's smoothing is correct, so your RSI and ATR don't match any charting platform — and `atr_stop_loss_1_5x` feeds the model's stop-loss. Replace the manual RSI and ATR in that fallback block (lines 91-121):

```python
        # RSI — Wilder smoothing is EMA with alpha = 1/n
        delta = close.diff()
        up, dn = delta.clip(lower=0), (-delta).clip(lower=0)
        rs = up.ewm(alpha=1/14, adjust=False).mean() / dn.ewm(alpha=1/14, adjust=False).mean()
        rsi_s = 100 - 100 / (1 + rs)
        rsi = round(float(rsi_s.iloc[-1]), 2) if not rsi_s.empty else None

        # ATR — Wilder, not SMA
        pc = close.shift()
        tr = pd.concat([df["High"] - df["Low"],
                        (df["High"] - pc).abs(),
                        (df["Low"] - pc).abs()], axis=1).max(axis=1)
        atr = round(float(tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1]), 2)
```

Also delete the now-unused `import pandas_ta` at line 49 and its `try/except ImportError` wrapper, using the manual path unconditionally — it's correct and has no dependency risk.

**Verify:** print RSI/ATR for 2–3 symbols and compare against TradingView or your broker's chart — they should now match within rounding. Confirm `weekly_trend` returns a real value.

---

# Fix 5 + 6 — Swing trades graded same-day, entry zones ignored 🔴

**Bug:** a trade issued at 09:20 is graded at 15:35 the *same day*, so a 2–10 day trade almost always scores `FAIL`. And `entry_low, entry_high` are parsed then never used, so trades you'd never have been filled on get graded anyway.

**Evidence:** [performance/service.py:54-134](../features/performance/service.py#L54-L134); the dead variables are at [line 74](../features/performance/service.py#L74).

This is the minimal honest version. The full lifecycle is **WP11**.

**Patch — add these fields to `log_recommendation`'s document** (line 32-48):

```python
        "filled": False, "fill_price": None, "fill_date": None,
        "r_multiple": None, "close_reason": None,
```

**Patch — replace `evaluate_day` with a multi-day evaluator:**

```python
MAX_HOLD_DAYS = 10
ENTRY_VALID_DAYS = 2


async def evaluate_position(symbol: str, day_high: float, day_low: float,
                            day_close: float, today: str) -> dict:
    """Runs once per trading day per open recommendation. Grades only when a
    barrier is hit: target, stop, or the 10-day time limit."""
    if mongo.db is None:
        return {"error": "MongoDB not connected"}

    doc = await mongo.db[COLLECTION].find_one(
        {"symbol": symbol.upper(), "result": None})     # ← NO date filter: survives multiple days
    if not doc:
        return {"error": f"no open recommendation for {symbol}"}

    try:
        entry_low, entry_high = _parse_range(doc.get("entry_zone", ""))
        target = _parse_single(doc.get("target", ""))
        stop   = _parse_single(doc.get("stop_loss", ""))
    except Exception:
        await _set(doc, result="SKIPPED", evaluation_notes="unparseable levels")
        return {"error": f"{symbol}: unparseable levels"}

    days = _trading_days_between(doc["date"], today)

    # ── Stage 1: not filled yet. Did price actually trade into the entry zone? ──
    if not doc.get("filled"):
        if day_low <= entry_high:                       # zone was touched
            fill = min(entry_high, max(day_low, entry_low))
            await _set(doc, filled=True, fill_price=fill, fill_date=today)
            return {"symbol": symbol, "status": "FILLED", "fill_price": fill}
        if days >= ENTRY_VALID_DAYS:
            await _set(doc, result="CANCELLED",
                       evaluation_notes=f"entry zone {entry_low}-{entry_high} never touched")
            return {"symbol": symbol, "status": "CANCELLED"}
        return {"symbol": symbol, "status": "PENDING_ENTRY", "days": days}

    # ── Stage 2: filled. Grade only on a barrier hit. ──
    fill = float(doc["fill_price"])
    risk = fill - stop
    if risk <= 0:
        await _set(doc, result="SKIPPED", evaluation_notes="stop not below fill")
        return {"error": f"{symbol}: invalid stop"}

    if day_low <= stop:                                 # stop wins ties, always
        return await _close(doc, stop, "STOP", (stop - fill) / risk, day_close)
    if day_high >= target:
        return await _close(doc, target, "TARGET", (target - fill) / risk, day_close)
    if days >= MAX_HOLD_DAYS:
        return await _close(doc, day_close, "TIME_EXIT", (day_close - fill) / risk, day_close)

    return {"symbol": symbol, "status": "OPEN", "days": days,
            "r_multiple": round((day_close - fill) / risk, 2)}


async def _set(doc: dict, **fields) -> None:
    fields["evaluated_at"] = datetime.now(timezone.utc)
    await mongo.db[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": fields})


async def _close(doc: dict, price: float, reason: str, r: float, day_close: float) -> dict:
    r = round(r, 2)
    await _set(doc, result="WIN" if r > 0 else "LOSS", close_reason=reason,
               close_price=price, r_multiple=r, day_close=day_close,
               evaluation_notes=f"{reason} at {price:.2f}, {r:+.2f}R")
    return {"symbol": doc["symbol"], "status": reason, "r_multiple": r}
```

**Patch — `evening_routine`** (`features/scheduler/service.py`, around line 522): call `evaluate_position(...)` instead of `evaluate_day(...)`, and iterate **all open recommendations**, not just today's watchlist:

```python
    open_docs = await mongo.db.performance_log.find({"result": None}).to_list(length=None)
    symbols = sorted({d["symbol"] for d in open_docs})
```

**Patch — replace the hit-rate calculation** with R-based statistics:

```python
async def get_performance(last_n_days: int = 90) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=last_n_days))
    trades = await mongo.db[COLLECTION].find(
        {"result": {"$in": ["WIN", "LOSS"]}, "evaluated_at": {"$gte": cutoff}}
    ).to_list(length=None)
    if not trades:
        return {"trades": 0}

    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    wins = [r for r in rs if r > 0]
    losses = [abs(r) for r in rs if r <= 0]
    wr = len(wins) / len(rs)
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    return {
        "trades": len(rs),
        "win_rate_pct": round(wr * 100, 1),
        "avg_win_r": round(aw, 2),
        "avg_loss_r": round(al, 2),
        "expectancy_r": round(wr * aw - (1 - wr) * al, 3),   # ← the number that matters
        "total_r": round(sum(rs), 2),
    }
```

**Verify:** a recommendation whose entry zone is never touched ends `CANCELLED`, not `FAIL`. One that fills survives past day 1 and reports a running `r_multiple`. A position at day 10 closes as `TIME_EXIT`.

> ⚠️ **Note:** `expectancy_r` is only meaningful after ~30 closed trades, and genuinely trustworthy after ~100. See `RECOMMENDATION_ENGINE.md` §5.

---

# Fix 7 — `POST /news-scanner/trigger` returns nothing 🟡 *(downgraded — see correction)*

> ## ✅ Correction — your UI news IS working
>
> An earlier version of this document claimed your manual news fetch "can essentially never find anything." **That was wrong**, and the user was right to push back. Verified by tracing the endpoints:
>
> | Endpoint | What it does | Dedupe? | Status |
> |---|---|---|---|
> | `GET /market/news/live` | `fetch_all_news()` → returns RSS articles **directly** | **No** | ✅ works, returns latest every click |
> | `GET /market/news/stock?symbol=` | yfinance `ticker.news` | **No** | ✅ works — this is the yfinance path |
> | `GET /news-scanner/alerts` | stored `news_alerts` from scheduled runs | n/a | ✅ works — the "last scheduled fetches" history |
> | `POST /news-scanner/trigger` | re-runs the **alerting** pipeline | **Yes** | ⚠️ the only one affected |
>
> So the news display in your dashboard never touched the deduped path. Nothing about your news *viewing* is broken.

**The remaining, much smaller bug:** `POST /news-scanner/trigger` re-runs `run_news_scanner()`, which shares the `processed_news` dedupe with the 5-minute cron. Since the cron has already marked everything processed, a manual trigger usually finds zero candidates, returns `{"status": "scan complete"}`, and **generates no new AI analysis or alerts**. It's useful for testing but can't be used to force a re-analysis.

**Evidence:** [news_scanner/router.py:7-11](../features/news_scanner/router.py#L7-L11) and the `already_seen` filter in [news_scanner/service.py:128-140](../features/news_scanner/service.py#L128-L140).

**Worth fixing anyway** because it's a 10-minute change and gives you a real "re-analyse now" button — but it is **not** urgent, and it is not why news feels late. For actual latency, the fast lane in `NEWS_FAST_LANE.md` §2 (exchange filings, 5–30 min ahead of any article) is the real answer.

**Patch — add a `force` flag and return data** (`features/news_scanner/service.py`):

```python
async def run_news_scanner(force: bool = False) -> dict:
    """force=True: ignore the alert dedupe (for interactive use) and return the articles."""
    ...
    all_urls = [a.url for a in articles]
    already_seen = set() if force else await _get_processed_urls_today(all_urls)
    ...
    # at the end, instead of a bare return:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "forced": force,
        "fetched": len(articles),
        "new": len(candidates),
        "triggered": len(triggered),
        "analysed": ai_calls,
        "items": [
            {"title": a.title, "source": a.source, "url": a.url,
             "published": a.published, "age_min": int((time.time() - a.published_ts) / 60)}
            for a in (triggered or candidates)[:30]
        ],
    }
```

**Patch — `features/news_scanner/router.py`:**

```python
@router.post("/trigger")
async def trigger_scan(force: bool = False):
    """Cron calls this with force=False (unchanged). Manual/UI uses force=true."""
    return await run_news_scanner(force=force)
```

Keep the cron job calling `run_news_scanner()` with no argument — its behaviour is untouched.

**Verify:** run the cron scan, then immediately `POST /news-scanner/trigger?force=true` → **articles come back**, not an empty status.

---

# Fix 8 — Triggered articles beyond the first 3 vanish 🟠

**Bug:** every candidate is written to `processed_news`, but only the first 3 triggered articles are analysed. Articles 4+ are already marked processed, so the next run's dedupe skips them — never analysed, never alerted. On a busy morning you see 3 and silently lose the rest.

**Evidence:** [news_scanner/service.py:149-173](../features/news_scanner/service.py#L149-L173)

**Patch — queue instead of dropping** (replace the loop at lines 149-157):

```python
    triggered, bulk_inserts = [], []
    for article in candidates:
        is_triggered = bool(_TRIGGER_RE.search(article.title)
                            or _TRIGGER_RE.search(article.summary))
        bulk_inserts.append({
            "url": article.url, "title": article.title,
            "summary": article.summary, "source": article.source,
            "published_ts": article.published_ts,
            "processed_at": datetime.now(timezone.utc),
            "pending_ai": is_triggered,          # ← queue it instead of losing it
        })
        if is_triggered:
            triggered.append(article)
```

**Patch — drain the backlog first, newest-first** (before the AI loop at line 173):

```python
    # Pick up anything a previous run couldn't analyse
    backlog = await mongo.db.processed_news.find({"pending_ai": True}).to_list(length=50)
    queue = sorted(
        [{"url": a.url, "title": a.title, "summary": a.summary,
          "published_ts": a.published_ts} for a in triggered]
        + [{"url": d["url"], "title": d["title"], "summary": d.get("summary", ""),
            "published_ts": d.get("published_ts", 0)} for d in backlog],
        key=lambda x: -x["published_ts"],
    )
    seen_urls, deduped = set(), []
    for item in queue:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            deduped.append(item)

    ai_calls = 0
    for item in deduped[:_MAX_AI_CALLS_PER_RUN]:
        result = await analyze_news(item["title"], item["summary"], item["url"])
        await mongo.db.processed_news.update_one(
            {"url": item["url"]}, {"$set": {"pending_ai": False}})   # ← only now is it done
        if "error" in result:
            continue
        ai_calls += 1
        ...   # existing confidence/sentiment/alert logic unchanged
```

**Verify:** feed 10 triggered articles in one run — all 10 get analysed across successive runs, none dropped.

---

# Fix 9 — UTC date keys in an IST market 🟠

**Bug:** every date key is `datetime.now(timezone.utc).strftime("%Y-%m-%d")`. Between 00:00 and 05:30 IST the UTC date is still *yesterday*, so `daily_watchlist`, `performance_log` and `intraday_scans` keys can disagree between routines. Alerts also display UTC clock times to an IST trader.

**Patch — new file:**

```python
# core/timeutils.py
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo          # tzdata is already in requirements.txt

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> str:
    return now_ist().strftime("%Y-%m-%d")


def fmt_ist(dt: datetime | None = None) -> str:
    return (dt or now_ist()).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def is_market_hours(dt: datetime | None = None) -> bool:
    d = (dt or now_ist()).astimezone(IST)
    open_t  = d.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_t = d.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= d <= close_t


def session_state() -> str:
    d = now_ist()
    if d.weekday() >= 5:
        return "WEEKEND"
    hm = d.hour * 60 + d.minute
    if hm < 540:   return "CLOSED"      # before 09:00
    if hm < 555:   return "PRE_OPEN"    # 09:00-09:15
    if hm <= 930:  return "OPEN"        # 09:15-15:30
    if hm <= 960:  return "POST"        # 15:30-16:00
    return "CLOSED"
```

**Then, mechanically:**
- Replace every `datetime.now(timezone.utc).strftime("%Y-%m-%d")` with `today_ist()` in `features/scheduler/service.py`, `features/intraday/service.py`, `features/performance/service.py`, `features/portfolio/service.py`.
- Replace every displayed `strftime("%I:%M %p UTC")` with `fmt_ist()`.
- Replace `now.weekday() >= 5` with `now_ist().weekday() >= 5`.

Keep storing raw `datetime` objects in UTC — that part is already correct. Only **date keys** and **display strings** become IST.

**Migration note:** existing documents have UTC date keys. For a personal app, just accept a one-day discontinuity and note the changeover date in `PROGRESS.md`.

**Verify:** at 01:00 IST, `today_ist()` returns today's date, not yesterday's. Alerts show "IST", not "UTC".

---

# Fix 10 — No NSE holiday awareness 🟠

**Bug:** only weekends are skipped. On ~15 trading holidays a year the morning routine burns Gemini quota, generates a confident report from stale closing data, and writes rows you later have to clean up.

**Patch — new file:**

```python
# features/market_data/calendar_service.py
import asyncio
from datetime import date, datetime, timedelta

import requests

from core.database import mongo
from core.timeutils import now_ist

_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Accept": "application/json", "Referer": "https://www.nseindia.com"}

# Fallback so a blocked NSE call can never break the scheduler.
# VERIFY yearly at nseindia.com/resources/exchange-communication-holidays
_STATIC = {"2026-01-26", "2026-03-04", "2026-03-21", "2026-04-01", "2026-04-14",
           "2026-05-01", "2026-08-15", "2026-08-27", "2026-10-02", "2026-10-21",
           "2026-11-09", "2026-12-25"}


def _fetch() -> set[str]:
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=_H, timeout=10)     # cookie warm-up
    rows = s.get(_URL, headers=_H, timeout=10).json().get("CM", [])
    out = set()
    for r in rows:
        try:
            out.add(datetime.strptime(r["tradingDate"], "%d-%b-%Y").strftime("%Y-%m-%d"))
        except Exception:
            continue
    return out


async def get_holidays() -> set[str]:
    """One ~1 KB cached document, refreshed daily, with a static fallback."""
    if mongo.db is not None:
        doc = await mongo.db.market_calendar.find_one({"_id": "holidays"})
        if doc and doc.get("fetched_on") == now_ist().strftime("%Y-%m-%d"):
            return set(doc["dates"])
    try:
        dates = await asyncio.get_running_loop().run_in_executor(None, _fetch)
        if dates and mongo.db is not None:
            await mongo.db.market_calendar.replace_one(
                {"_id": "holidays"},
                {"_id": "holidays", "dates": sorted(dates),
                 "fetched_on": now_ist().strftime("%Y-%m-%d")},
                upsert=True)
        return dates or _STATIC
    except Exception as exc:
        print(f"[Calendar] NSE holiday fetch failed ({exc}) — using static list")
        return _STATIC


async def is_trading_day(d: date | None = None) -> bool:
    d = d or now_ist().date()
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in await get_holidays()


async def trading_days_between(start: str, end: str) -> int:
    holidays = await get_holidays()
    d, e, n = datetime.strptime(start, "%Y-%m-%d").date(), datetime.strptime(end, "%Y-%m-%d").date(), 0
    while d < e:
        d += timedelta(days=1)
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in holidays:
            n += 1
    return n
```

**Then** replace each weekend guard in `features/scheduler/service.py` (lines 106, 481, 572, 580):

```python
    if not await is_trading_day():
        msg = "🛑 <b>Market Closed</b>\n\nNSE is closed today (weekend or trading holiday)."
        if USER_ID:
            try:
                await bot.send_message(int(USER_ID), msg, parse_mode="HTML")
            except Exception as exc:
                print(f"[Scheduler] closed-market notice failed: {exc}")
        return
```

`trading_days_between()` is also what Fix 5 needs for its day counting — use it there rather than calendar days.

**Verify:** `await is_trading_day(date(2026, 10, 2))` → `False`. On the next holiday, you get one "market closed" note and no report.

---

# Two one-line fixes worth doing at the same time

**Stale model config** — `gemini-2.5-pro` in the root `models` file cannot work: Pro models left the Gemini free tier on 1 April 2026, so every call with it fails. Delete that entry (and the whole file — it duplicates `core/config.AVAILABLE_MODELS` with conflicting values). Set `DEFAULT_GEMINI_MODEL=gemini-3.6-flash`, the current stable Flash.

**Import-time crash** — [bot/handlers.py:63](../features/bot/handlers.py#L63) evaluates `int(USER_ID)` inside a decorator, so a missing `Userid` env var breaks the entire app at import. Compute it once at module load with a guard:

```python
OWNER_ID = int(USER_ID) if USER_ID and str(USER_ID).strip().isdigit() else 0
# then use  F.from_user.id == OWNER_ID  in the decorators
```

---

# Order and effort

| # | Fix | Effort | Why this position |
|---|---|---|---|
| 1 | Async I/O — bot freeze | 45 min | Affects every interaction you have with the system |
| 2 | API auth | 20 min | Open to the internet right now |
| 3 | Per-minute writes | 30 min | Actively consuming your 512 MB |
| 4 | Weekly trend + Wilder maths | 30 min | A filter that has never worked; wrong stops |
| 9 | IST dates | 30 min | Fix 5 and 10 depend on correct date keys |
| 10 | Trading calendar | 30 min | Fix 5 needs `trading_days_between()` |
| 5+6 | Multi-day grading + fills | 90 min | Makes every performance number meaningful |
| 7 | Manual news trigger | 20 min | Small change, immediately visible |
| 8 | News queue | 30 min | Stops silently losing signals |
| — | Model config + import crash | 10 min | Free wins |

**Total: about five hours** for all ten. Fixes 1–4 alone (~2 hours) clear everything that's actively harmful.

After each fix: confirm the app boots, the bot replies, and the dashboard still loads. Tick it in `PROGRESS.md` and commit separately — if something breaks, you'll know exactly which change did it.

---

# Everything else that's still broken

The ten above are the ones that cost you money or data. Here is the **complete remainder**, so nothing is lost. Each has a work package that owns it.

## Silent-failure class — fix these with any nearby edit

| Bug | Location | Fix |
|---|---|---|
| **6 swallowed exceptions** hide real failures | [technical_indicators.py:156](../features/market_data/technical_indicators.py#L156), [portfolio/service.py:75](../features/portfolio/service.py#L75) & [:118](../features/portfolio/service.py#L118) (bare `except:`), [knowledge_base/service.py:65](../features/knowledge_base/service.py#L65) | Replace every `except Exception: pass` with a logged message. Fix 4 already does one of them |
| **Parse failure silently drops a trade** — writes `SKIPPED`, vanishes from stats | [performance/service.py:80-84](../features/performance/service.py#L80-L84) | Alert on `SKIPPED` instead of only recording it. Fix 5 keeps this behaviour but you should be told |
| **Empty watchlist aborts with no notification** | [scheduler/service.py:120-122](../features/scheduler/service.py#L120-L122) | Send "no setup passed the gates today" — a valid result you must still hear about |
| **Bot polling dies silently** — broad `except` that only prints | [main.py:53-61](../main.py#L53-L61) | Needs an external watchdog (`ALERTS_AND_BOT.md`, `BOT_POLLING_DIED`) |

## Data-quality class

| Bug | Location | Fix |
|---|---|---|
| **T1/T2/T3 never stored for AI trades** — prompt generates them, regex has no pattern, `log_recommendation` doesn't persist them | [scheduler/service.py:605-620](../features/scheduler/service.py#L605-L620), [performance/service.py:32-48](../features/performance/service.py#L32-L48) | Structured JSON output (**WP10**) removes the regex entirely. Interim: add `t1`/`t2`/`t3` patterns and columns |
| **Fetched fundamentals thrown away** — `avg_volume_5d`, `market_cap`, `pe_ratio` are fetched, then the prompt block passes only CMP/High/Low/Volume/52W, while the valuation gate needs PE | fetched at [market_data/service.py:139-141](../features/market_data/service.py#L139-L141), dropped at [scheduler/service.py:322-333](../features/scheduler/service.py#L322-L333) | Add them to the yfinance block in the prompt (**WP14**) |
| **`ticker.info` in the hot path** — slow and heavily rate-limited | [market_data/service.py:114](../features/market_data/service.py#L114) | Fetch fundamentals once daily into a small collection (**WP14**) |
| **RAG probably contributes nothing** — query polluted with the ticker symbol against generic theory; returns `[]` on failure with no signal | [scheduler/service.py:426](../features/scheduler/service.py#L426), [knowledge_base/service.py:65-67](../features/knowledge_base/service.py#L65-L67) | Tag chunks, query by intent (**WP10**) |
| **Vector search is dead code** — nothing generates embeddings | [knowledge_base/service.py:7-44](../features/knowledge_base/service.py#L7-L44) | Delete it, or keep `$text` only. Don't leave an unreachable path |
| **`grok.get_sentiment` fabricates data** — asks a model with no X/Twitter access for "x_twitter_analysis", exposed at `POST /grok/sentiment` | [grok/service.py:115-118](../features/grok/service.py#L115-L118) | Delete the endpoint, or re-source it from real data |

## Structure class — fixed by WP1–WP3

| Bug | Location |
|---|---|
| **8 function-local imports** working around circular dependencies | `scheduler/service.py:299, 570, 579, 600`, `intraday/service.py:420, 452`, `chat/router.py:135, 144` |
| **Import-time singletons** read `.env` at import, so tests need a populated env | [gemini/service.py:98](../features/gemini/service.py#L98), [grok/service.py:98](../features/grok/service.py#L98) |
| **`sys.modules` replacement hack** to expose a property | [intraday/prompts.py:87-91](../features/intraday/prompts.py#L87-L91) — replace with a plain function |
| **215-line god function** mixing fetch, maths, formatting, alerting and persistence | [intraday/service.py:18-231](../features/intraday/service.py#L18-L231) |
| **`datetime.utcnow()`** (deprecated) | [market_data/service.py:142](../features/market_data/service.py#L142) |
| **Stray files** — root `models` duplicates `AVAILABLE_MODELS` with conflicting values; `bot_flow` is 0 bytes | repo root — delete both |
| **Index creation inline in `main.py`** | [main.py:32-36](../main.py#L32-L36) — move to a startup helper |

## Design-level, not a code bug

| Issue | Where it's addressed |
|---|---|
| Two contradictory prompts live — `prompt.txt` says "never refuse, always be confident" and it's the one your morning reports use | **WP10** / `PROMPTS.md` |
| LLM outranks computed numbers ("Gemini live_price is the authoritative CMP") | **WP8** / `IMPLEMENTATION.md` §3.1 |
| No timestamps or freshness discipline on any input | **WP8** / `ANALYTICS.md` §H |
| Analysis stops at retail level — no ADX, RS ranking, swing pivots, volume profile | **WP14** / `ANALYTICS.md` |
| No idempotency — a double cron fire duplicates rows | **WP6/WP13** — unique indexes |
| **Zero tests anywhere** | **WP1** / `ENGINEERING.md` §4 |
| Render free tier spins down after 15 min idle → the scheduler may not be firing at all | `IMPLEMENTATION.md` Appendix D — **check this before debugging anything else** |

**Grand total:** the ten above (~5 hours) plus roughly 4 more hours for the silent-failure and data-quality classes. The structure class comes free with WP1–WP3. The design-level items are the actual roadmap, not bug fixes.
