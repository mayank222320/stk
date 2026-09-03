# StockAI Backend — Implementation Guide

**Written:** 3 September 2026
**Companions:** `WEAKNESSES.md` (what's broken, with file:line refs) · `FEATURES.md` (what to build, and why)

**Scope confirmed:** swing only (2–10 days, 10 = hard cap) · **all existing features kept, converted to swing cadence** — manual tracking, in-session monitoring/alerts, and the virtual portfolio (now a swing paper portfolio) · SIPs + GOLDBEES/MON100 included · single user · **Atlas M0, 512 MB** · free resources only (paid options priced in Appendix C).

**Companion specs:** code structure and standards → **`ENGINEERING.md`** · professional calculations → `ANALYTICS.md` · prompts → `PROMPTS.md` · knowledge modules → `KNOWLEDGE_AND_PROMPTS.md` · LLM reliability → `LLM_ORCHESTRATION.md` · alerts and bot menu → `ALERTS_AND_BOT.md` · news latency → `NEWS_FAST_LANE.md` · edge validation → `RECOMMENDATION_ENGINE.md`.

---

## ⚠️ Before Phase 0 — read `ENGINEERING.md`

Two reasons it comes first:

**1. There's a live bug.** `yf.Ticker().history()` is called **directly inside `async def`** at [portfolio/service.py:70-71](../features/portfolio/service.py#L70-L71) and [intraday/service.py:62-67](../features/intraday/service.py#L62-L67) — inside a loop over symbols. `yfinance` is synchronous, so this blocks the **entire event loop**, including Telegram polling. Your bot goes unresponsive for seconds to tens of seconds on every scan. Fixed in **WP3**.

**2. You're about to roughly double the codebase.** Feature folders are the right instinct, but with no layering rules the structure is already producing symptoms: eight function-local imports working around circular dependencies, a 215-line function that mixes fetching, maths, formatting, alerting and persistence, `.env` read at import time, and six silently swallowed exceptions — one of which is why `weekly_trend` has been broken since it was written.

`ENGINEERING.md` gives you the target structure (`domain/` for pure logic, `adapters/` for all I/O, `features/` for orchestration only), a one-directional dependency rule enforced by a 20-line CI test, coding standards, the test table that covers the money-critical paths, a strangler migration plan (no rewrite — the system is live), and **22 PR-sized work packages** mapped to the phases below.

**Do WP1–WP4 first.** Half a day, fixes the live bug, and makes every phase below cheaper:

| WP | What | Why now |
|---|---|---|
| WP1 | `domain/ adapters/ tests/` skeleton + architecture test + free CI (ruff/mypy/pytest) | guardrails before growth |
| WP2 | `core/` — typed settings, IST helpers, structured logging, error hierarchy, no import-time side effects | everything depends on these |
| WP3 | market adapter — **all sync I/O behind executors with timeouts** | fixes the event-loop bug |
| WP4 | `domain/calc/indicators.py` — Wilder RSI/ATR/ADX, working weekly trend | fixes W6, becomes testable |

---

## Phase order

| Phase | Theme | Time | WPs | Why here |
|---|---|---|---|---|
| **E** | **Engineering foundations** → `ENGINEERING.md` | ~4 h | WP1–4 | **Do first.** Fixes the live event-loop bug; every later phase gets cheaper |
| 0 | Safety & hygiene | ~4 h | WP5 | Auth gap is live; small fixes unblock everything |
| 1 | **Convert intraday + virtual to swing cadence** | ~3 h | WP6 | Removes ~99% of storage growth, keeps every feature |
| 2 | **Storage & retention manager** | ~5 h | WP7 | The cleanup control you asked for |
| 3 | Data integrity **+ freshness/timestamps** | ~1.5 d | WP8 | Trustworthy, dated numbers |
| **C** | **LLM orchestration** → `LLM_ORCHESTRATION.md` | ~5 h | WP9 | Do with Phase 3 — makes every later LLM call robust |
| **B** | **Knowledge + the two prompts** → `PROMPTS.md`, `KNOWLEDGE_AND_PROMPTS.md` | ~5 h | WP10 | Do after Phase 3 — needs the new data contract |
| 4 | Swing lifecycle (real + manual + paper) | ~1.5 d | WP11–12 | Honest performance stats |
| **D** | **Alerts + Telegram menu** → `ALERTS_AND_BOT.md` | ~5 h | WP13 | Do after Phase 4 — needs position events to fire on |
| **A** | **Expert analytics** → `ANALYTICS.md` | 1 d + 2 d | WP14–15, 22 | Tier 1 after Phase 4; it feeds Phase 5 |
| 5 | Screener + regime | ~2 d | WP16 | Reproducible picks |
| 6 | Risk + journal | ~1 d | WP17–18 | Capital protection |
| 7 | SIP + ETF dip engine | ~1 d | WP19 | The other half of your portfolio |
| **N** | **News fast lane** → `NEWS_FAST_LANE.md` | ~5 h | WP20 | Anytime after Phase 0 — additive, existing scanner untouched |
| 8 | Backtest (local) | ~2 d | WP21 | Your real edge, at zero storage cost. **Rules only — see `RECOMMENDATION_ENGINE.md` §0** |
| 9 | Polish | ~1 d | — | Daily usability |

Phases A and B are lettered because they're cross-cutting specs with their own documents — insert them where the table says. Phases 1 and 2 together solve the storage problem permanently; do them right after Phase 0.

---

# PHASE 0 — Safety & hygiene

## 0.1 Lock the API (fixes W3)

**New file `core/auth.py`:**

```python
import os, secrets
from fastapi import Header, HTTPException, status

API_TOKEN = os.getenv("API_TOKEN", "")

async def require_token(x_api_token: str | None = Header(default=None)) -> None:
    if not API_TOKEN:                          # fail closed, never open
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API_TOKEN not configured")
    if not x_api_token or not secrets.compare_digest(x_api_token, API_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Token")
```

```python
# main.py — keep GET / open for Render's port scanner, protect the rest
from core.auth import require_token
from fastapi import Depends

app.include_router(system_router)
for r in (gemini_router, notifications_router, performance_router, chat_router,
          market_router, grok_router, swing_router, news_scanner_router, storage_router):
    app.include_router(r, dependencies=[Depends(require_token)])
```

`API_TOKEN=$(openssl rand -hex 32)` into `.env`, same value into your Vercel frontend env, sent as `X-API-Token` on every request.

**Verify:** `curl https://<app>/performance/hit-rate` → 401; with the header → 200.

## 0.2 One IST clock (fixes W8)

**New file `core/timeutils.py`:**

```python
from datetime import datetime
from zoneinfo import ZoneInfo               # tzdata already in requirements

IST = ZoneInfo("Asia/Kolkata")

def now_ist() -> datetime: return datetime.now(IST)
def today_ist() -> str:    return now_ist().strftime("%Y-%m-%d")
def fmt_ist(dt: datetime | None = None) -> str:
    return (dt or now_ist()).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")

def is_market_hours(dt: datetime | None = None) -> bool:
    d = (dt or now_ist()).astimezone(IST)
    return d.replace(hour=9, minute=15, second=0) <= d <= d.replace(hour=15, minute=30, second=0)
```

Replace every `datetime.now(timezone.utc).strftime("%Y-%m-%d")` with `today_ist()` and every `"%I:%M %p UTC"` display with `fmt_ist()`. Keep storing raw datetimes in UTC (correct); only **date keys** and **display strings** become IST.

## 0.3 Trading-day calendar (fixes W9)

**New file `features/market_data/calendar_service.py`:**

```python
import asyncio, requests
from datetime import date, datetime, timedelta
from core.timeutils import now_ist
from core.database import mongo

_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Accept": "application/json", "Referer": "https://www.nseindia.com"}

# Fallback so a blocked NSE call never breaks the scheduler.
# Refresh yearly from nseindia.com/resources/exchange-communication-holidays — VERIFY these.
_STATIC = {"2026-01-26","2026-03-04","2026-03-21","2026-04-01","2026-04-14",
           "2026-05-01","2026-08-15","2026-08-27","2026-10-02","2026-10-21",
           "2026-11-09","2026-12-25"}

def _fetch() -> set[str]:
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=_H, timeout=10)        # cookie warm-up
    rows = s.get(_URL, headers=_H, timeout=10).json().get("CM", [])  # cash market
    out = set()
    for r in rows:
        try: out.add(datetime.strptime(r["tradingDate"], "%d-%b-%Y").strftime("%Y-%m-%d"))
        except Exception: pass
    return out

async def get_holidays() -> set[str]:
    """One tiny cached document, refreshed daily. Costs ~1 KB of storage."""
    if mongo.db is not None:
        doc = await mongo.db.market_calendar.find_one({"_id": "holidays"})
        if doc and (now_ist().date() - doc["fetched_on"]).days < 1:
            return set(doc["dates"])
    try:
        dates = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        if dates and mongo.db is not None:
            await mongo.db.market_calendar.replace_one({"_id": "holidays"},
                {"_id": "holidays", "dates": sorted(dates), "fetched_on": now_ist().date()},
                upsert=True)
        return dates or _STATIC
    except Exception as e:
        print(f"[Calendar] NSE holiday fetch failed ({e}) — static list")
        return _STATIC

async def is_trading_day(d: date | None = None) -> bool:
    d = d or now_ist().date()
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in await get_holidays()
```

Replace each `if now.weekday() >= 5:` guard with `if not await is_trading_day():` and send one "market closed" note instead of generating a report.

## 0.4 Fix the indicators (fixes W6 — the weekly-trend bug)

Don't add `pandas_ta` (breaks on `numpy>=2`, and you pin `numpy==2.5.1`). Correct, dependency-free math:

**New file `features/market_data/indicators_math.py`:**

```python
import pandas as pd

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = d.clip(lower=0), (-d).clip(lower=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean() / dn.ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + rs)                      # Wilder smoothing = EMA(alpha=1/n)

def atr(h, l, c, n: int = 14) -> pd.Series:
    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()     # Wilder, not SMA

def macd(c, fast=12, slow=26, signal=9):
    line = c.ewm(span=fast, adjust=False).mean() - c.ewm(span=slow, adjust=False).mean()
    sig  = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig

def bbands(c, n=20, k=2):
    m, s = c.rolling(n).mean(), c.rolling(n).std()
    return m + k*s, m, m - k*s
```

In `technical_indicators.py`: delete **both** `import pandas_ta` blocks, import from `indicators_math`, and compute the weekly trend unconditionally:

```python
df_w = ticker.history(period="2y", interval="1wk", auto_adjust=True)
if df_w is not None and len(df_w) >= 20:
    w = float(df_w["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
    weekly_trend = "above_weekly_ema20" if cmp > w else "below_weekly_ema20"
    weekly_ema20 = round(w, 2)
```

**Verify:** RSI/ATR for 2–3 symbols now match your broker's chart within rounding, and `weekly_trend` is no longer `"N/A"` — it has never been anything else.

## 0.5 Validate models at startup (fixes W12)

```python
async def list_available_models(api_key: str) -> set[str]:
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers={"X-goog-api-key": api_key}) as r:
            return {m["name"].split("/")[-1] for m in (await r.json(content_type=None)).get("models", [])}

async def validate_model_config() -> dict:
    if not gemini_manager.keys: return {"ok": False, "reason": "no keys"}
    available = await list_available_models(gemini_manager.keys[0].value)
    valid   = [m for m in get_gemini_models() if m in available]
    invalid = [m for m in get_gemini_models() if m not in available]
    if invalid: print(f"[Gemini] Dropping unknown model IDs: {invalid}")
    gemini_manager.usable_models = valid or ["gemini-flash-latest"]
    return {"ok": bool(valid), "valid": valid, "invalid": invalid}
```

As of **September 2026**: set `DEFAULT_GEMINI_MODEL=gemini-3.6-flash` (stable Flash since 21 Jul 2026); remove `gemini-2.5-pro` from the root `models` file — **Pro left the free tier on 1 Apr 2026**. Make failures cheaper:

```python
except Exception as exc:
    msg = str(exc)
    if "404" in msg or "not found" in msg.lower():
        break                                            # bad model → skip remaining keys
    if "429" in msg or "quota" in msg.lower():
        gemini_manager.cooldown[key.name] = time.time() + 60
```

## 0.6 Never fail silently (fixes W15)

```python
# features/notifications/service.py
async def alert_ops(where: str, detail: str) -> None:
    from features.bot.setup import bot
    from core.config import USER_ID
    if USER_ID:
        try:
            await bot.send_message(int(USER_ID),
                f"⚠️ <b>Job failure — {where}</b>\n<code>{detail[:600]}</code>", parse_mode="HTML")
        except Exception: pass
    await broadcast(text=f"{where}: {detail[:300]}", title="⚠️ StockAI job failure")
```

Wrap every scheduled job, writing to a **capped** `job_runs` collection (Phase 2.2 — fixed size forever):

```python
async def _run_job(name: str, fn):
    started = now_ist()
    try:
        await fn(); status, detail = "ok", ""
    except Exception:
        import traceback; status, detail = "error", traceback.format_exc()
        await alert_ops(name, detail)
    if mongo.db is not None:
        await mongo.db.job_runs.insert_one({"job": name, "status": status,
            "started_at": started, "finished_at": now_ist(), "detail": detail[:1000]})
```

Also fix the silent abort at `scheduler/service.py:120-122` — if the watchlist is empty, **say so**.

## 0.7 Cleanup

Delete the root `models` file (conflicting duplicate of `core/config.AVAILABLE_MODELS`) and the 0-byte `bot_flow`. Drop the `connetion_string` typo fallback. Move `int(USER_ID)` out of import-time decorators in `bot/handlers.py`. Remove or re-source `grok.get_sentiment` (it asks a model with no X access for "x_twitter_analysis" — invented data).

---

# PHASE 1 — Convert intraday + virtual to swing cadence

**Nothing is deleted.** Monitoring, alerts, manual tracking and the virtual portfolio all stay — they're re-timed for a 2–10 day hold and made event-driven. That removes ~99% of storage growth (`WEAKNESSES.md` W1, W13) while every screen you use today keeps working.

## 1.1 Re-time the scheduler jobs

In `features/scheduler/service.py`, replace the two intraday registrations:

```python
# BEFORE — the storage bomb: one insert per symbol per MINUTE, 8h/day ≈ 960 rows/day
scheduler.add_job(custom_stock_minute_scan,
    CronTrigger(day_of_week='mon-fri', hour='3-10', minute='*', timezone='UTC'), ...)
scheduler.add_job(intraday_scan_routine,
    CronTrigger(day_of_week='mon-fri', hour='9-14', minute='20,50', timezone=SCHEDULER_TIMEZONE), ...)

# AFTER — 3 in-session checks + EOD, all IST, event-only writes ≈ 2-5 rows/day
for hh, mm, tag in ((11, 30, "midday"), (14, 0, "afternoon"), (15, 10, "preclose")):
    scheduler.add_job(lambda t=tag: _run_job(f"swing_check_{t}", lambda: check_positions(t)),
        CronTrigger(day_of_week='mon-fri', hour=hh, minute=mm, timezone=SCHEDULER_TIMEZONE),
        id=f"swing_check_{tag}", replace_existing=True, coalesce=True, misfire_grace_time=1800)

scheduler.add_job(lambda: _run_job("swing_tracker", track_positions),          # EOD, Phase 4.3
    CronTrigger(day_of_week='mon-fri', hour=15, minute=45, timezone=SCHEDULER_TIMEZONE),
    id="swing_tracker", replace_existing=True, coalesce=True, misfire_grace_time=3600)

scheduler.add_job(lambda: _run_job("gap_check", pre_open_gap_check),           # 09:10 IST
    CronTrigger(day_of_week='mon-fri', hour=9, minute=10, timezone=SCHEDULER_TIMEZONE),
    id="gap_check", replace_existing=True)
```

`3-10 UTC` also becomes IST-native, which fixes the 08:30–16:29 straddle (W8).

## 1.2 Event-only writes — the actual fix

The scan loop currently ends in an unconditional `insert_one` ([intraday/service.py:204](../features/intraday/service.py#L204)). Make persistence conditional on **state change**:

```python
async def check_positions(tag: str) -> list[dict]:
    """In-session check. Alerts immediately; writes ONLY when something changed."""
    events = []
    for p in await _open_positions():                 # all open positions, not "today's watchlist"
        q = await fetch_quote(p["symbol"])            # freshness-stamped (Phase 3.5)
        new_state = _classify(p, q["price"])          # SAFE | NEAR_TARGET | TARGET_HIT | SL_BREACHED
        if new_state == p.get("last_state", "SAFE"):
            continue                                  # ← nothing written on a quiet check
        await mongo.db.position_events.insert_one({
            "symbol": p["symbol"], "position_id": p["_id"], "date": today_ist(),
            "at": now_ist(), "check": tag, "state": new_state,
            "price": round(q["price"], 2), "price_age_s": q["age_seconds"]})
        await mongo.db.swing_positions.update_one(
            {"_id": p["_id"]}, {"$set": {"last_state": new_state, "last_price": q["price"]}})
        await _alert_state_change(p, new_state, q)    # Telegram + ntfy, same as today
        events.append({"symbol": p["symbol"], "state": new_state})
    return events
```

Quiet day with 3 open positions: **0 rows written**, 3 alerts-worth of monitoring still performed. Only real events (fill, target, stop, trailing move, invalidation) persist — and those are exactly the rows you'd ever want to read back.

## 1.3 Rename for clarity, keep the behaviour

Move into `features/swing/`, keeping every capability:

| Today | Becomes | Note |
|---|---|---|
| `add_custom_track()` | `create_manual_position()` | your manual entry path, now swing-graded |
| `update_custom_track()` | `update_position()` | unchanged behaviour |
| `close_position()` | `close_position()` | already correct |
| `get_all_custom_tracks()` | `get_positions()` | serves the same UI |
| `get_ai_update()` | `get_position_advice()` | swap prompt to swing (Phase B) |
| `untrack_stock()` | `cancel_position()` | unchanged |
| `run_intraday_scan()` | `check_positions()` | event-only writes (1.2) |
| `get_todays_scans()` / `get_stock_scans()` | `get_position_events()` | reads `position_events` |
| `features/portfolio/` | `features/swing/paper.py` | swing paper portfolio (F19) |
| `log_virtual_trade()` | `open_paper_position()` | entry-zone fill + ATR sizing, not instant fill at CMP |

Keep the existing route paths as thin aliases if the dashboard already calls them — no frontend rewrite needed on day one.

Fix the intraday-flavoured copy while you're here: `hold_duration` defaults to `"Swing"`, and the target-hit action text becomes trail/partial/hold instead of "square off before 3:10 PM" ([intraday/service.py:147-154](../features/intraday/service.py#L147-L154)).

## 1.4 Reclaim the space already consumed

The historical `intraday_scans` rows are the ~150 MB you've been fighting. Nothing reads them.

```python
# one-off. Dropping beats deleting on M0: a drop returns the whole storage file, while
# deleteMany leaves it allocated and shared tiers can't run `compact`.
await mongo.db.drop_collection("intraday_scans")        # historical per-minute rows

# migrate paper trades, then drop the old shape
await migrate_virtual_to_paper()                        # keep the history you care about
await mongo.db.drop_collection("virtual_portfolio")
```

**Verify:** `GET /storage/stats` (Phase 2) shows used-MB falling sharply, and after a full trading day `position_events` has single-digit rows rather than hundreds.

---

# PHASE 2 — Storage & retention manager

This is the "clear anything older than N days" control you asked for, built as a generalisation of the `/memory <days>` pattern your bot already has ([bot/handlers.py:80-132](../features/bot/handlers.py#L80-L132)).

## 2.1 Retention policy

**New file `core/retention.py`:**

```python
from core.database import mongo
from core.timeutils import now_ist
from datetime import timedelta

# Never swept by an age-based clear. Deleting these needs an explicit confirmation phrase.
SACRED = {"swing_positions", "paper_positions", "trade_journal", "monthly_rollups",
          "sip_contributions", "recommendations", "risk_config", "knowledge_chunks",
          "settings", "users", "market_calendar", "failure_library"}

# collection -> (date_field, default_days, field_is_string)
DEFAULTS = {
    "chat_history":     ("created_at",    25, False),  # already TTL'd — keep
    "morning_alerts":   ("logged_at",     45, False),  # prose archive; Telegram keeps it forever
    "processed_news":   ("processed_at",   3, False),   # dedupe only needs today
    "news_alerts":      ("alerted_at",    90, False),
    "position_events":  ("at",           120, False),  # event-only writes (Phase 1.2) — tiny
    "screener_scores":  ("date",          30, True),   # "YYYY-MM-DD" string
    "analytics_daily":  ("date",          30, True),   # per-symbol expert metrics (Phase A)
    "regime_daily":     ("date",         400, True),   # ~1.5y so backtests can replay regimes
    "etf_dip_status":   ("date",          90, True),
    "job_runs":         ("started_at",    14, False),
    "daily_watchlist":  ("date",          60, True),
}

async def get_policy() -> dict[str, int]:
    """Editable from UI/Telegram; falls back to DEFAULTS."""
    doc = await mongo.db.settings.find_one({"_id": "retention"}) or {}
    saved = doc.get("days", {})
    return {c: int(saved.get(c, d)) for c, (_, d, _) in DEFAULTS.items()}

async def set_policy(collection: str, days: int) -> None:
    if collection not in DEFAULTS:
        raise ValueError(f"unknown collection: {collection}")
    if not 1 <= days <= 3650:
        raise ValueError("days must be 1..3650")
    await mongo.db.settings.update_one({"_id": "retention"},
        {"$set": {f"days.{collection}": days}}, upsert=True)

def _cutoff(days: int, as_string: bool):
    d = now_ist() - timedelta(days=days)
    return d.strftime("%Y-%m-%d") if as_string else d
```

## 2.2 TTL and capped collections (the zero-maintenance layer)

TTL deletes continuously so freed space gets reused — much healthier on M0 than periodic mass deletes. Capped collections can never grow at all.

```python
async def ensure_storage_indexes() -> None:
    # TTL: MongoDB expires documents automatically
    for coll, (field, days, is_str) in DEFAULTS.items():
        if is_str:      # TTL needs a real date field; string dates are swept manually instead
            continue
        await mongo.db[coll].create_index(field, expireAfterSeconds=days*86400, background=True)

    # Capped: fixed ceiling, oldest overwritten, no maintenance ever
    for coll, size in (("job_runs", 2_000_000), ("news_alerts", 5_000_000),
                       ("source_health", 1_000_000)):
        try:
            await mongo.db.create_collection(coll, capped=True, size=size)
        except Exception:
            pass        # already exists — convert manually if you want it capped
```

> **Note on TTL vs policy changes:** a TTL index bakes in its duration. When you change retention days for a TTL'd collection, drop and recreate that index (`ensure_storage_indexes` should do this when the policy differs).

## 2.3 Storage stats

```python
# features/storage/service.py
M0_LIMIT = 512 * 1024 * 1024

async def storage_stats() -> dict:
    db = await mongo.db.command("dbStats")
    used = db["dataSize"] + db.get("indexSize", 0)
    policy = await get_policy()
    rows = []
    for name in await mongo.db.list_collection_names():
        try:
            st = (await mongo.db[name].aggregate(
                [{"$collStats": {"storageStats": {}}}]).to_list(1))[0]["storageStats"]
        except Exception:
            continue
        rows.append({"collection": name, "docs": st["count"],
                     "data_mb": round(st["size"]/1048576, 2),
                     "index_mb": round(st["totalIndexSize"]/1048576, 2),
                     "tier": "SACRED" if name in SACRED else
                             ("ROLLING" if name in DEFAULTS else "OTHER"),
                     "retention_days": policy.get(name)})
    rows.sort(key=lambda r: -(r["data_mb"] + r["index_mb"]))
    return {"used_mb": round(used/1048576, 2), "limit_mb": 512,
            "used_pct": round(used/M0_LIMIT*100, 1),
            "collections": rows}
```

## 2.4 Age-based cleanup, with dry-run and protection

```python
async def cleanup(older_than_days: int, collections: list[str] | None = None,
                  dry_run: bool = True, include_sacred: bool = False,
                  confirm: str = "") -> dict:
    """Delete documents older than N days. Dry-run by default. SACRED excluded unless
    include_sacred=True AND confirm == 'I_UNDERSTAND_TRACK_RECORD_LOSS'."""
    if not 1 <= older_than_days <= 3650:
        return {"error": "older_than_days must be 1..3650"}

    targets = collections or list(DEFAULTS.keys())
    if include_sacred:
        if confirm != "I_UNDERSTAND_TRACK_RECORD_LOSS":
            return {"error": "sacred collections require the confirmation phrase"}
        targets += [c for c in SACRED if c not in targets]

    results, total_docs, protected = [], 0, []
    for name in targets:
        if name in SACRED and not include_sacred:
            protected.append(name); continue
        meta = DEFAULTS.get(name)
        if not meta:
            results.append({"collection": name, "skipped": "no date field configured"}); continue
        field, _, is_str = meta
        q = {field: {"$lt": _cutoff(older_than_days, is_str)}}

        n = await mongo.db[name].count_documents(q)
        if n and not dry_run:
            await mongo.db[name].delete_many(q)
        results.append({"collection": name, "docs": n, "deleted": bool(n and not dry_run)})
        total_docs += n

    return {"dry_run": dry_run, "older_than_days": older_than_days,
            "total_docs": total_docs, "results": results,
            "protected_untouched": protected,
            "storage_after": None if dry_run else (await storage_stats())["used_mb"]}
```

**Always roll up before you sweep**, so clearing detail never costs you statistics:

```python
async def rollup_month(month: str) -> dict:      # "2026-08"
    """Aggregate closed trades into one ~2 KB document kept forever."""
    trades = await mongo.db.swing_positions.find(
        {"status": "CLOSED", "close_date": {"$regex": f"^{month}"}}).to_list(None)
    if not trades: return {"month": month, "trades": 0}
    wins = [t for t in trades if t["r_multiple"] > 0]
    wr = len(wins)/len(trades)
    aw = sum(t["r_multiple"] for t in wins)/len(wins) if wins else 0
    losses = [t for t in trades if t["r_multiple"] <= 0]
    al = abs(sum(t["r_multiple"] for t in losses)/len(losses)) if losses else 0
    doc = {"_id": month, "trades": len(trades), "win_rate": round(wr*100,1),
           "avg_win_r": round(aw,2), "avg_loss_r": round(al,2),
           "expectancy_r": round(wr*aw - (1-wr)*al, 3),
           "avg_hold_days": round(sum(t["days_held"] for t in trades)/len(trades),1),
           "by_setup": _group(trades, "setup_type"), "by_source": _group(trades, "source")}
    await mongo.db.monthly_rollups.replace_one({"_id": month}, doc, upsert=True)
    return doc
```

## 2.5 Endpoints

```python
# features/storage/router.py
router = APIRouter(prefix="/storage", tags=["Storage"])

@router.get("/stats")                    # usage, per collection, tiers, retention
@router.get("/retention")                # current policy
@router.put("/retention")                # {collection, days}
@router.post("/cleanup")                 # {older_than_days, collections?, dry_run, include_sacred?, confirm?}
@router.post("/rollup")                  # {month?} — force aggregation
@router.delete("/collection/{name}")     # drop a ROLLING collection outright (fastest reclaim)
```

Then **retire the blunt endpoints** — `DELETE /performance/recommendations/all`, `DELETE /performance/alerts/all`, `DELETE /intraday/scans/all` — or make them call `cleanup()` with a dry-run default. Those are what cost you your track record.

## 2.6 Telegram `/storage`

Mirrors your existing `/memory` handler, so the interaction feels familiar:

```python
@dp.message(Command("storage"))
async def storage_handler(message: types.Message) -> None:
    parts = (message.text or "").split()

    if len(parts) > 1:                                   # "/storage 60" → dry-run preview
        try: days = int(parts[1])
        except ValueError: return await message.answer("Usage: /storage <days>")
        prev = await cleanup(days, dry_run=True)
        lines = [f"🧹 <b>Would delete (older than {days} days)</b>\n"]
        for r in prev["results"]:
            if r.get("docs"): lines.append(f"  {r['collection']}: {r['docs']} docs")
        lines.append(f"\n<b>Total:</b> {prev['total_docs']} docs")
        lines.append(f"🔒 Protected: {', '.join(prev['protected_untouched'])}")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Confirm delete", callback_data=f"stor_go_{days}"),
            InlineKeyboardButton(text="Cancel",            callback_data="stor_cancel")]])
        return await message.answer("\n".join(lines), reply_markup=kb)

    s = await storage_stats()                            # "/storage" → dashboard
    top = "\n".join(f"  {r['collection']}: {r['data_mb']+r['index_mb']:.1f} MB"
                    f"{'  🔒' if r['tier']=='SACRED' else ''}" for r in s["collections"][:6])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Clear > 30d", callback_data="stor_pre_30"),
         InlineKeyboardButton(text="Clear > 60d", callback_data="stor_pre_60")],
        [InlineKeyboardButton(text="Clear > 90d", callback_data="stor_pre_90"),
         InlineKeyboardButton(text="Clean expired now", callback_data="stor_ttl")]])
    await message.answer(
        f"💾 <b>Storage</b> — {s['used_mb']} MB / 512 MB ({s['used_pct']}%)\n\n"
        f"<b>Top consumers</b>\n{top}\n\n"
        f"Type <code>/storage 60</code> for a custom age sweep.", reply_markup=kb)
```

Callbacks: `stor_pre_<days>` → preview, `stor_go_<days>` → execute + report freed space, `stor_ttl` → sweep string-date collections at their policy age. **Sacred collections never appear in these sweeps** — clearing them requires the API with the confirmation phrase.

## 2.7 Capacity monitor

```python
async def check_capacity() -> None:
    s = await storage_stats()
    pct = s["used_pct"]
    if pct >= 90:
        res = await cleanup(30, dry_run=False)           # safe tier only, never sacred
        await alert_ops("storage 90%", f"auto-swept {res['total_docs']} docs → {res['storage_after']} MB")
    elif pct >= 70:
        await broadcast(text=f"Storage at {pct}% of 512 MB. Run /storage to review.",
                        title="💾 Storage warning")
```

Schedule daily at ~16:00 IST. You should never again discover the limit by hitting it.

---

# PHASE 3 — Data integrity

## 3.1 Invert the authority order (fixes W4 — highest-value change)

**Rule:** deterministic sources own every number; the LLM owns judgement, narrative and catalysts, and never overrides a computed value.

**(a) Stop asking the LLM for numbers.** Replace the research schema ([scheduler/service.py:353-369](../features/scheduler/service.py#L353-L369)) with qualitative fields only:

```python
{"key_news": ["…"],                 # last 5 trading days, dated
 "catalysts_next_10d": ["…"],       # matches your swing horizon
 "earnings_date": "YYYY-MM-DD|unknown",
 "sector_context": "…", "promoter_or_insider_activity": "…",
 "regulatory_or_governance_flags": ["…"], "bear_case": "…",
 "narrative_bias": "bullish|bearish|neutral",
 "evidence_quality": "confirmed|reported|unverified"}
```

No `live_price`, no `rsi`, no `ohlcv`, no `macd_signal` — all already computed exactly.

**(b) Flip the instruction block** ([scheduler/service.py:456-464](../features/scheduler/service.py#L456-L464)):

```
DATA AUTHORITY (non-negotiable):
- CMP, OHLCV, RSI, MACD, EMA/SMA, ATR, Bollinger, volume, PCR, max pain and FII/DII
  above are computed from exchange data. They are AUTHORITATIVE. Never substitute a
  number from search or memory.
- If your research contradicts a number above, add a "Data Conflict" note and lower
  Data Confidence. Do not silently replace the number.
- Any figure not in the blocks above must be labelled [FROM SEARCH] with its source.
- Base the stop on atr_stop_loss_1_5x unless structure clearly justifies otherwise;
  if you deviate, say why and keep R:R to T1 at 1:2 or better.
- Horizon is SHORT SWING: 2-10 trading days. Never propose an intraday trade.
- WAIT / NO TRADE is a valid and often correct answer.
```

**(c) Make `cross_check` a gate**, not a comment:

```python
check = cross_check(symbol, yf_price, nse_price)     # two deterministic sources
if check["verdict"] == "significant_divergence":
    await alert_ops(f"price divergence {symbol}", check["note"])
    return                                           # skip: don't trade ambiguous prices
```

## 3.2 Structured JSON output (fixes W10 — and shrinks storage ~10×)

```python
RECO_SCHEMA = {
 "type": "object",
 "properties": {
   "symbol": {"type": "string"},
   "recommendation": {"type": "string",
     "enum": ["BUY","ACCUMULATE","HOLD","TRIM","SELL","AVOID","WAIT","AWAITING_USER_DATA"]},
   "entry_low": {"type":"number"}, "entry_high": {"type":"number"},
   "t1": {"type":"number"}, "t2": {"type":"number"}, "t3": {"type":"number"},
   "stop_loss": {"type":"number"}, "rr_to_t1": {"type":"number"},
   "max_hold_days": {"type":"integer"},
   "setup_type": {"type":"string",
     "enum": ["BREAKOUT","PULLBACK","REVERSAL","MOMENTUM_CONTINUATION","NONE"]},
   "invalidation_price": {"type":"number"}, "invalidation_event": {"type":"string"},
   "gates": {"type":"object","properties":{
     "valuation":  {"type":"string","enum":["PASS","FAIL","NA","UNVERIFIED"]},
     "structural": {"type":"string","enum":["PASS","FAIL","NA","UNVERIFIED"]},
     "liquidity":  {"type":"string","enum":["PASS","FAIL","NA","UNVERIFIED"]},
     "event_risk": {"type":"string","enum":["PASS","FAIL","NA","UNVERIFIED"]}}},
   "prob_bullish": {"type":"integer"}, "prob_base": {"type":"integer"},
   "prob_bearish": {"type":"integer"}, "data_confidence": {"type":"integer"},
   "thesis": {"type":"string"}, "bear_case": {"type":"string"},
   "data_conflicts": {"type":"array","items":{"type":"string"}}},
 "required": ["symbol","recommendation","stop_loss","thesis","data_confidence"]}
```

```python
payload["generationConfig"] = {"responseMimeType": "application/json",
                               "responseSchema": schema, "temperature": 0.2}
```

**Simplest reliable pattern — two calls:** (1) grounded research call with `google_search` → text + citations; (2) structuring call, no tools, with `responseSchema` → strict JSON built from the deterministic blocks + that research text.

**Validate in code before anything is sent or stored:**

```python
def validate_reco(r: dict, cmp: float) -> tuple[bool, list[str]]:
    e = []
    if r["recommendation"] in ("BUY","ACCUMULATE"):
        if not (r["stop_loss"] < r["entry_low"] <= r["entry_high"] < r["t1"]):
            e.append("level ordering invalid for a long")
        risk, rew = r["entry_high"] - r["stop_loss"], r["t1"] - r["entry_high"]
        if risk <= 0 or rew/risk < 2: e.append(f"R:R {rew/max(risk,1e-9):.2f} below 1:2")
        if any(v == "FAIL" for v in r.get("gates", {}).values()):
            e.append("a binding entry gate failed — BUY not permitted")
        if r.get("max_hold_days", 10) > 10: e.append("exceeds 10-day swing cap")
    if abs(sum(r.get(k,0) for k in ("prob_bullish","prob_base","prob_bearish")) - 100) > 1:
        e.append("probabilities do not sum to 100")
    if not (0.5*cmp < r["stop_loss"] < 1.5*cmp): e.append("stop implausible vs CMP")
    return (not e), e
```

Failed validation → downgrade to `WAIT`, log, notify. That turns today's silent `SKIPPED` rows into a visible signal.

**Storage:** store the **JSON** (~800 bytes) in `recommendations` permanently; keep the prose in `morning_alerts` for 45 days and let TTL take it — Telegram already keeps your readable archive forever.

## 3.3 Don't cache OHLCV on Atlas

Nifty 500 × 5 years ≈ 100–150 MB — a third of your quota for data yfinance re-serves free.

```python
# in-memory for the nightly screener, local parquet only for backtests
data = yf.download([f"{s}.NS" for s in symbols], period="1y",
                   group_by="ticker", auto_adjust=True, threads=True, progress=False)
```

Also stop calling `ticker.info` in the hot path ([market_data/service.py:114](../features/market_data/service.py#L114)) — slow and rate-limited. Fetch fundamentals once daily into a small `fundamentals` doc per symbol and **pass them into the prompt** (today `pe_ratio` and `market_cap` are fetched then dropped, while your valuation gate needs them). Add `trailingPE`, `priceToBook`, `debtToEquity`, `returnOnEquity`, `earningsGrowth` → derive a real PEG.

## 3.4 Source health (F14)

```python
# core/health.py — upsert one doc per source, never append
async def record(source: str, ok: bool, err: str = "") -> None:
    await mongo.db.source_health.update_one({"_id": source},
        {"$set": {("last_ok" if ok else "last_fail"): now_ist(),
                  "last_error": "" if ok else err[:300]},
         "$inc": {("ok_count" if ok else "fail_count"): 1}}, upsert=True)
```

Attach `data_confidence` (share of critical sources that succeeded) to each recommendation; below threshold → force `WAIT`.

## 3.5 Freshness & timestamps (fixes W19 · feature F18)

Nothing currently knows how old its inputs are. Make age a property of every value.

**New file `core/freshness.py`:**

```python
from dataclasses import dataclass, asdict
from datetime import datetime
from core.timeutils import now_ist, IST

# seconds allowed during a live session; outside session everything is LAST_CLOSE
BUDGET = {"quote": 900, "option_chain": 1800, "fii_dii": 129600,     # 15m / 30m / T-1
          "indicators": None, "fundamentals": 7776000, "news": 432000}  # close / 1q / 5d

@dataclass
class Stamped:
    value: object
    source: str
    captured_at: datetime
    kind: str
    @property
    def age_seconds(self) -> int:
        return int((now_ist() - self.captured_at.astimezone(IST)).total_seconds())
    @property
    def state(self) -> str:
        if self.value is None: return "UNAVAILABLE"
        if self.kind == "indicators": return "LAST_CLOSE"
        b = BUDGET.get(self.kind)
        if b is None: return "LAST_CLOSE"
        if not is_market_hours(): return "LAST_CLOSE"
        return "LIVE" if self.age_seconds <= b/3 else ("DELAYED" if self.age_seconds <= b else "STALE")
    def to_prompt(self) -> str:
        return f"{self.value}  [{self.state}, {self.source}, {self.age_seconds}s old]"

def session_state() -> str:
    """PRE_OPEN | OPEN | POST | CLOSED | HOLIDAY | WEEKEND"""
    d = now_ist()
    if d.weekday() >= 5: return "WEEKEND"
    hm = d.hour*60 + d.minute
    if hm < 540:  return "CLOSED"          # before 09:00
    if hm < 555:  return "PRE_OPEN"        # 09:00-09:15
    if hm <= 930: return "OPEN"            # 09:15-15:30
    if hm <= 960: return "POST"            # 15:30-16:00
    return "CLOSED"
```

Then:

1. **Every fetcher returns `Stamped`**, not a bare number — `fetch_quote`, option chain, FII/DII, fundamentals.
2. **The prompt receives ages.** Add a header block above the data:
   ```
   AS-OF: 2026-09-03 15:47 IST   SESSION: CLOSED   TRADING DAY: yes
   INPUT FRESHNESS: quote LAST_CLOSE (source yfinance) · indicators LAST_CLOSE (2026-09-03)
                    option_chain STALE (4h) · fii_dii T-1 · fundamentals 41d
   Never describe data as live unless its state says LIVE.
   ```
3. **Stale has a consequence, enforced in code** — not requested in prose:
   ```python
   def apply_freshness_gate(inputs: dict[str, Stamped], reco: dict) -> dict:
       stale = [k for k, s in inputs.items() if s.state in ("STALE", "UNAVAILABLE")]
       binding = {"quote", "indicators"}
       if binding & set(stale):
           reco["recommendation"] = "WAIT"
           reco["data_conflicts"] = reco.get("data_conflicts", []) + \
               [f"binding input stale/unavailable: {sorted(binding & set(stale))}"]
       reco["data_confidence"] = max(1, reco.get("data_confidence", 5) - 2*len(stale))
       return reco
   ```
4. **Persist `as_of` and `worst_input_age_s`** on every recommendation and position event, so a report opened two days later can't be mistaken for current.
5. **Session-aware copy** — the alert header says `Based on 3 Sep close (market CLOSED)` rather than implying a live quote, which also makes `prompt.txt`'s `Status: Live / Closed` field ([prompt.txt:219](../prompt.txt#L219)) something the system actually knows.

**Verify:** run the morning routine on a Sunday — every input reports `LAST_CLOSE`, the header says `WEEKEND`, and no `BUY` is emitted.

---

# PHASE A — Expert analytics

> **Full spec: `ANALYTICS.md`.** It carries the formulas, thresholds, interpretation bands and the exact veto rules, so it isn't duplicated here.

Sequence:
1. **Tier 1 (~1 day), right after Phase 4** — ADX/DI±, RS Rating percentile, algorithmic swing pivots, PE-vs-5-year-median + real PEG, base/consolidation detection, 52-week-high proximity. These plug straight into the Phase 5 screener score and the stop-placement logic.
2. **Tier 2 (~2 days), after Phase 5** — volume profile (POC/VAH/VAL), quantified VSA (effort vs result), OBV/CMF/U-D ratio, anchored VWAP, earnings surprise + PEAD, Piotroski F-score, Altman Z, IV rank, futures basis, OI build-up classification, pivot/Fibonacci levels.
3. **Tier 3 (with Phase 6)** — MAE/MFE per trade, expectancy and R-distribution, fractional Kelly, Monte Carlo drawdown.

Where it lands in code: `features/market_data/analytics/` (one module per family), surfaced through a single `compute_full_analytics(symbol) -> dict` used by the screener, the recommendation prompt and the position tracker. Store only shortlist results in `analytics_daily` (~1.5 KB/symbol, TTL 30 days).

**Non-negotiable:** every number the prompt names must now be *computed*, or explicitly marked `UNAVAILABLE`. No more narrating Wyckoff phases and IV ranks that were never calculated.

---

# PHASE C — LLM orchestration

> **Full spec: `LLM_ORCHESTRATION.md`.** Model routing, free-tier quota budget, error taxonomy, key cooldowns with local rate limiting, circuit breaker, the two-call pattern, schema repair, the Groq critic gate, caching and audit metadata.

Headlines, if you read nothing else:
- **Error classification.** Today every exception is handled identically ([gemini/service.py:232-236](../features/gemini/service.py#L232-L236)), so a bad model ID costs one failed call *per key* and a 429'd key gets retried first next time. Classify 400 / 401 / 404 / 429 / 5xx / safety and handle each differently.
- **RPM is the binding limit, not daily.** ~35 Gemini calls/day against 1,500+ available, but only ~10 per minute — so serialise, keep the `asyncio.sleep(5)` between symbols, and never fan out.
- **The news scanner is ~88% of all LLM usage** (~250 Groq calls/day vs ~10 Gemini for actual trading decisions). Escalate only articles matching held positions or today's shortlist: roughly −80% usage, and more relevant alerts.
- **The core loop must survive an LLM outage.** Screener, gates, sizing, tracking, alerts and dip detection are arithmetic. On a circuit-open condition, keep all of that running and return `WAIT` for new entries.

# PHASE B — Knowledge base + prompts

> **Prompt text: `PROMPTS.md`** (two versions — swing special and general, each with a Data Source Manifest).
> **Knowledge modules + retrieval fix: `KNOWLEDGE_AND_PROMPTS.md`.**

Sequence (do after Phase 3, so the prompts can reference the new data contract):
1. Add the 7 new YAML modules to `docs/`, then re-index: `python -m features.knowledge_base.indexer`.
2. Extend the indexer to write `tags` per chunk, and switch retrieval from symbol-polluted keywords to **intent tags** (fixes W14).
3. Create `prompts/swing.md` and `prompts/general.md`; add `pick_prompt(intent)`; route every call site per the `PROMPTS.md` wiring table. Delete `prompt.txt`; archive `qmaf_v2_personalized.md`.
4. Both prompts already embed the DATA AUTHORITY (3.1) and freshness (3.5) rules — make sure the runtime blocks they describe are actually supplied, and that each prints `UNAVAILABLE` on failure rather than being omitted.
5. Verify RAG actually returns relevant chunks — assert `knowledge_chunks` count > 0 at startup and log what was retrieved for one sample query.

# PHASE D — Alerts + Telegram menu

> **Full spec: `ALERTS_AND_BOT.md`.** The event catalogue (position, event-risk, portfolio, opportunity, system) with P0–P4 priorities and ntfy mapping, unique-index dedupe, quiet hours, rate caps, per-event config — plus `setMyCommands`, the `/menu` category tree, guided `/track` flow, and action buttons that write to the journal.

Do this after Phase 4, since position state changes are what most alerts fire on. Highest-value single alert in the catalogue: **EARNINGS_APPROACHING** on a held position — stops don't work across gaps.

---

# PHASE 4 — Swing lifecycle (F1)

**New folder `features/swing/`** — `service.py`, `router.py`, `models.py`.

## 4.1 Collection + indexes

```python
await mongo.db.swing_positions.create_index([("status", 1), ("symbol", 1)])
await mongo.db.swing_positions.create_index([("close_date", -1)])
# one live position per symbol
await mongo.db.swing_positions.create_index(
    [("symbol", 1)], unique=True,
    partialFilterExpression={"status": {"$in": ["PENDING_ENTRY", "OPEN"]}})
```

Document shape as in `FEATURES.md` F1 — note `daily[]` is **bounded to ≤10 entries**, so a position can never exceed ~1.5 KB.

## 4.2 Two creation paths

```python
async def create_from_reco(reco: dict, snapshot: dict) -> str | None:
    if reco["recommendation"] not in ("BUY", "ACCUMULATE"): return None
    return await _create(source="AI", **_map(reco), entry_snapshot=_slim(snapshot))

async def create_manual_position(symbol, entry_low, entry_high, t1, stop_loss,
                                 t2=0, t3=0, qty=0, notes="") -> dict:
    """Your kept manual-tracking feature — same lifecycle, same grading."""
    return await _create(source="MANUAL", symbol=symbol.strip().upper(),
                         entry_zone_low=entry_low, entry_zone_high=entry_high,
                         t1=t1, t2=t2, t3=t3, stop_loss=stop_loss, qty=qty,
                         thesis=notes or "manual entry", setup_type="MANUAL")

def _slim(snap: dict) -> dict:
    """Keep ~15 numbers that justify the trade — never the prose."""
    keys = ("cmp","rsi_14","macd_histogram","ema_20","ema_50","ema_200","atr_14",
            "weekly_trend","volume_ratio","pcr","max_pain","fii_net_cr","dii_net_cr")
    return {k: snap.get(k) for k in keys if snap.get(k) is not None}
```

## 4.3 Daily tracker (the core job, ~15:45 IST)

```python
async def track_positions() -> list[dict]:
    events = []
    for p in await _active():
        bar = await get_today_bar(p["symbol"])            # high/low/close from yfinance

        if p["status"] == "PENDING_ENTRY":
            if bar["low"] <= p["entry_zone_high"]:        # actually traded into the zone
                fill = min(p["entry_zone_high"], max(bar["low"], p["entry_zone_low"]))
                await _fill(p, fill, bar);  events.append(("FILLED", p["symbol"], fill))
            elif today_ist() > p["entry_valid_until"]:
                await _cancel(p, "setup expired unfilled")
                events.append(("EXPIRED", p["symbol"], None))
            continue

        r    = (bar["close"] - p["fill_price"]) / (p["fill_price"] - p["stop_loss"])
        days = await trading_days_between(p["fill_date"], today_ist())

        if   bar["low"]  <= p["trailing_stop"]:
            await _close(p, p["trailing_stop"], "STOP");    events.append(("STOP", p["symbol"], r))
        elif p["t3"] and bar["high"] >= p["t3"]:
            await _close(p, p["t3"], "TARGET");             events.append(("T3", p["symbol"], r))
        elif p["t2"] and bar["high"] >= p["t2"] and not _booked(p,"T2"):
            await _partial(p, p["t2"], 40, "T2");           events.append(("T2", p["symbol"], r))
        elif bar["high"] >= p["t1"] and not _booked(p,"T1"):
            await _partial(p, p["t1"], 40, "T1")
            await _raise_stop(p, p["fill_price"])           # breakeven after T1
            events.append(("T1", p["symbol"], r))
        elif days >= p["max_hold_days"]:
            events.append(("TIME_EXIT_DUE", p["symbol"], r))   # ask, never auto-sell
        else:
            await _update_trailing(p, bar)

        # bounded append — at most max_hold_days entries, ~40 bytes each
        await mongo.db.swing_positions.update_one({"_id": p["_id"]}, {"$push": {"daily":
            {"$each": [{"d": today_ist(), "c": round(bar["close"],2), "r": round(r,2)}],
             "$slice": -10}}})
    return events
```

```python
scheduler.add_job(lambda: _run_job("swing_tracker", track_positions),
    CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=SCHEDULER_TIMEZONE),
    id="swing_tracker", replace_existing=True, coalesce=True, misfire_grace_time=3600)
```

Optional safety net (Phase 4.6): two checks a day at 12:00 and 15:10 that alert on stop/target breach and **write only when status changes** — no rows on quiet days, unlike the old scanner.

## 4.4 Grade on close, not same day (fixes W2/W5)

Remove the `evaluate_day` call from `evening_routine`. Grade when a position closes, then rebuild stats over closed positions:

```python
async def performance(last_n_days: int = 90) -> dict:
    trades = await mongo.db.swing_positions.find(
        {"status": "CLOSED", "close_date": {"$gte": _days_ago(last_n_days)}}).to_list(None)
    if not trades: return {"trades": 0}
    wins   = [t for t in trades if t["r_multiple"] > 0]
    losses = [t for t in trades if t["r_multiple"] <= 0]
    wr = len(wins)/len(trades)
    aw = sum(t["r_multiple"] for t in wins)/len(wins) if wins else 0
    al = abs(sum(t["r_multiple"] for t in losses)/len(losses)) if losses else 0
    return {"trades": len(trades), "win_rate_pct": round(wr*100,1),
            "avg_win_r": round(aw,2), "avg_loss_r": round(al,2),
            "expectancy_r": round(wr*aw - (1-wr)*al, 3),          # ← the number that matters
            "avg_hold_days": round(sum(t["days_held"] for t in trades)/len(trades),1),
            "by_setup": _group(trades,"setup_type"),
            "by_source": _group(trades,"source")}                 # AI vs your own picks
```

Keep the evening routine, but change its job to a digest: fills, targets hit, stops, and anything due for time exit tomorrow.

## 4.5 Pre-open gap check (09:10 IST)

For each `OPEN` position, compare previous close against pre-open/first-tick data; if the likely open is beyond your stop, alert **before** 09:15 so you choose the exit.

---

# PHASE 5 — Screener + regime (F4, F5)

## 5.1 Universe

```
https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv
```
Same browser-ish headers as other NSE calls; keep a committed CSV fallback. Cache weekly (~50 KB). It also gives you the sector mapping for F3's concentration caps.

## 5.2 Score

```python
async def score_universe() -> list[dict]:
    symbols = await get_universe()
    frames  = await bulk_history(symbols, days=300)     # in memory — nothing written
    regime  = await get_regime()
    out = []
    for sym, df in frames.items():
        if len(df) < 200: continue
        c = df["Close"]
        e20, e50 = (c.ewm(span=n, adjust=False).mean() for n in (20, 50))
        r = float(rsi(c).iloc[-1]); _, _, hist = macd(c)
        a = float(atr(df["High"], df["Low"], c).iloc[-1]); atr_pct = a/float(c.iloc[-1])*100
        vol_ratio   = df["Volume"].tail(5).mean() / df["Volume"].tail(20).mean()
        turnover_cr = float(c.iloc[-1]) * df["Volume"].tail(5).mean() / 1e7
        rs20 = (c.iloc[-1]/c.iloc[-21]) / (regime["nifty"].iloc[-1]/regime["nifty"].iloc[-21])
        wk   = c.resample("W").last()
        weekly_ok = wk.iloc[-1] > wk.ewm(span=20, adjust=False).mean().iloc[-1]

        s, why = 0, []
        if c.iloc[-1] > e20.iloc[-1] > e50.iloc[-1]: s += 20; why.append("trend stack")
        if e50.iloc[-1] > e50.iloc[-6]:              s += 10; why.append("EMA50 rising")
        if weekly_ok:                                s += 15; why.append("weekly uptrend")
        if 50 <= r <= 68:                            s += 15; why.append(f"RSI {r:.0f}")
        elif r > 72:                                 s -= 15; why.append("overbought")
        if hist.iloc[-1] > 0 > hist.iloc[-3]:        s += 10; why.append("MACD turn")
        if rs20 > 1.02:                              s += 15; why.append("beating Nifty")
        if vol_ratio > 1.2:                          s += 10; why.append("volume expanding")
        if 1.5 <= atr_pct <= 6:                      s += 5
        else:                                        s -= 10; why.append("volatility unsuitable")
        if turnover_cr < 5:                          s -= 40; why.append("illiquid")

        dist = (c.tail(20).max() - c.iloc[-1]) / c.iloc[-1] * 100
        setup = ("BREAKOUT" if dist < 3 else
                 "PULLBACK" if abs(c.iloc[-1]-e20.iloc[-1])/c.iloc[-1] < 0.02
                 else "MOMENTUM_CONTINUATION")
        out.append({"symbol": sym, "score": s, "setup_type": setup, "reasons": why,
                    "cmp": round(float(c.iloc[-1]),2), "rsi": round(r,1),
                    "atr": round(a,2), "suggested_stop": round(float(c.iloc[-1])-1.5*a, 2),
                    "turnover_cr": round(turnover_cr,1)})
    return sorted(out, key=lambda x: -x["score"])
```

Apply exclusions (results within 5 days via F6, F&O ban, circuits), keep the **top 8–10**, store only those (`screener_scores`, ~400 bytes each, TTL 30 days), then send them to Gemini for the qualitative pass.

## 5.3 Regime

```python
async def get_regime() -> dict:
    nifty = await get_history("^NSEI", 400)
    above200 = nifty["Close"].iloc[-1] > nifty["Close"].rolling(200).mean().iloc[-1]
    breadth  = await pct_above_50dma()                  # free, from 5.2's in-memory frames
    vix      = await get_history("^INDIAVIX", 300)       # verify symbol; NSE API fallback
    vix_pct  = float((vix["Close"] < vix["Close"].iloc[-1]).mean() * 100)

    if   above200 and breadth > 55 and vix_pct < 70: st, mult, mx = "RISK_ON",  1.0, 5
    elif not above200 and breadth < 40:              st, mult, mx = "RISK_OFF", 0.0, 0
    else:                                            st, mult, mx = "NEUTRAL",  0.5, 3
    await mongo.db.regime_daily.replace_one({"date": today_ist()},   # ~150 bytes/day
        {"date": today_ist(), "state": st, "breadth_pct": round(breadth,1),
         "vix_pct": round(vix_pct,1), "nifty_above_200dma": bool(above200)}, upsert=True)
    return {"state": st, "size_multiplier": mult, "max_positions": mx, "nifty": nifty["Close"]}
```

`RISK_OFF` → no new equity longs, and that's exactly when to check GOLDBEES dips (Phase 7).

---

# PHASE 6 — Risk + journal (F3, F10)

```python
# features/risk/service.py
async def size_position(entry: float, stop: float) -> dict:
    cfg    = await get_risk_config()          # capital, risk_pct, caps
    regime = await get_regime()
    per_share = entry - stop
    if per_share <= 0: return {"error": "stop must be below entry for a long"}

    risk_amt = cfg["capital"] * cfg["risk_pct"]/100 * regime["size_multiplier"]
    qty      = int(risk_amt // per_share)
    heat     = await portfolio_heat()
    warn     = []
    if heat["open_risk_pct"] + cfg["risk_pct"] > cfg["max_heat_pct"]:
        warn.append(f"portfolio heat would hit {heat['open_risk_pct']+cfg['risk_pct']:.1f}%"
                    f" (cap {cfg['max_heat_pct']}%)")
    if heat["open_count"] >= regime["max_positions"]:
        warn.append(f"already at max positions for {regime['state']}")
    if qty*entry > cfg["capital"]*cfg["max_single_pct"]/100:
        qty = int(cfg["capital"]*cfg["max_single_pct"]/100 // entry)
        warn.append(f"trimmed to {cfg['max_single_pct']}% single-stock cap")

    return {"qty": qty, "capital_deployed": round(qty*entry,2),
            "risk_amount": round(qty*per_share,2), "regime": regime["state"],
            "warnings": warn, "blocked": bool(warn and cfg.get("hard_block", True))}
```

Add `portfolio_heat()`, sector caps (from the Nifty 500 CSV), and a 60-day correlation check against open positions. Wire it into every recommendation so alerts carry an exact quantity.

**Journal** — collection `trade_journal` (sacred tier), planned vs actual, plus an Indian cost model:

```python
def costs(buy_val: float, sell_val: float, delivery: bool = True) -> float:
    t = buy_val + sell_val
    stt   = 0.001*t if delivery else 0.00025*sell_val
    exch  = 0.0000297*t; sebi = 0.000001*t
    stamp = (0.00015 if delivery else 0.00003)*buy_val
    brok  = 0.0 if delivery else min(20, 0.0003*t)      # set to your broker's plan
    gst   = 0.18*(brok + exch + sebi)
    return round(stt+exch+sebi+stamp+brok+gst, 2)       # ← verify current rates
```

---

# PHASE 7 — SIP + Gold/Nasdaq ETF engine (F7, F8)

**New folder `features/investments/`**

## 7.1 Mutual funds via mfapi.in (free, no key)

```python
MF_API = "https://api.mfapi.in/mf"          # /{code}   ·   /search?q=name

async def fetch_nav_history(code: int) -> list[dict]:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{MF_API}/{code}", timeout=aiohttp.ClientTimeout(total=20)) as r:
            return (await r.json(content_type=None)).get("data", [])   # [{date:"dd-mm-yyyy", nav}]
```

Resolve scheme codes once via `/search` for Navi Nifty 50 Index, Parag Parikh Flexi Cap and MO Nifty Midcap 150 (Direct-Growth), then store them in config. **NAV history is fetched on demand and never stored** — only your contribution ledger persists (~11 KB/year).

XIRR with the `scipy` you already pin:

```python
from scipy.optimize import brentq

def xirr(flows: list[tuple[date, float]]) -> float:
    """flows: (date, amount) — contributions negative, current value positive."""
    t0 = min(d for d, _ in flows)
    npv = lambda r: sum(a / (1+r) ** ((d-t0).days/365) for d, a in flows)
    try:    return brentq(npv, -0.99, 10.0) * 100
    except Exception: return float("nan")
```

Then: invested / units / current value / absolute return / XIRR per fund and overall; monthly SIP-day confirmation; fund-health check that speaks only on material change.

## 7.2 GOLDBEES / MON100 dip engine

```python
TICKERS = {"GOLDBEES": "GOLDBEES.NS", "MON100": "MON100.NS",
           "NDX": "^NDX", "USDINR": "INR=X"}

async def dip_status(symbol: str) -> dict:
    c = (await get_history(TICKERS[symbol], 120))["Close"]
    cmp_, high20 = float(c.iloc[-1]), float(c.tail(20).max())
    dma20, dma50 = float(c.rolling(20).mean().iloc[-1]), float(c.rolling(50).mean().iloc[-1])
    r = float(rsi(c).iloc[-1]); below = (high20 - cmp_)/high20*100

    if   below >= 7 and cmp_ <= dma50*1.01 and r < 35: tier, dep = "STRONG_DIP", 100
    elif below >= 4 and cmp_ <= dma20      and r < 45: tier, dep = "GOOD_DIP",    50
    elif below >= 2                        and r < 55: tier, dep = "MILD_DIP",    33
    else:                                              tier, dep = "NO_DIP",       0

    b = await get_month_budget(symbol)                  # allocated/deployed/remaining/days_left
    if b["days_left"] <= 2 and b["remaining"] > 0:
        tier, dep = "MONTH_END_DEPLOY", 100             # dip-waiting must not become never-buying

    return {"symbol": symbol, "cmp": round(cmp_,2), "pct_below_20d_high": round(below,2),
            "vs_dma20": round((cmp_/dma20-1)*100,2), "rsi": round(r,1),
            "tier": tier, "deploy_pct": dep,
            "deploy_amount": round(b["remaining"]*dep/100), "budget": b}
```

**MON100 decomposition** — required by your prompt, and it protects you from buying a fat premium:

```python
async def mon100_breakdown(days: int = 5) -> dict:
    etf, ndx, fx = [(await get_history(TICKERS[k], 60))["Close"] for k in ("MON100","NDX","USDINR")]
    pct = lambda s: (float(s.iloc[-1]) / float(s.iloc[-1-days]) - 1) * 100
    e, n, f = pct(etf), pct(ndx), pct(fx)
    prem = e - (n + f)
    return {"etf_move_pct": round(e,2), "ndx_move_pct": round(n,2), "inr_move_pct": round(f,2),
            "premium_or_tracking_pct": round(prem,2),
            "note": "ETF ahead of index+currency — likely premium, consider waiting"
                    if prem > 1.5 else "tracking normally"}
```

For a true premium/discount use AMFI's daily ETF NAV (`NAVAll.txt`) or NSE's iNAV as the NAV leg; the decomposition above is the cheap proxy. Schedule one job ~15:00 IST on trading days; store one ~200-byte status doc per ETF per day (TTL 90 days) and ping only on `GOOD_DIP` or better.

---

# PHASE 8 — Backtest, run locally (F9)

> ## ⚠️ Read `RECOMMENDATION_ENGINE.md` §0 and §4 before writing any of this
>
> **The LLM layer cannot be backtested.** An LLM's training corpus contains information from after any historical date you test against, so a backtest of an LLM-driven decision is contaminated by construction — and the contamination flatters you ([research](https://arxiv.org/pdf/2605.24564)).
>
> **Backtest the deterministic rules only** (screener, gates, sizing). Validate the LLM layer **forward-only**, via the paper portfolio, from today onward.
>
> Also required from §4: the **shuffled-signal control run before trusting any result**; 8–10 OOS windows over 4–5 years; efficiency ratio (OOS ÷ IS) > 0.5; a **logged count of every trial** you evaluate; a deflated Sharpe alongside the raw one; and Monte Carlo permutation / noise / start-date-shift tests — because walk-forward tests only a single price path.

**Keep this off Atlas.** Local disk is free; your cluster has 512 MB and a 10 GB/week transfer cap.

```
scripts/backtest.py          # runs on your PC
data/ohlcv/*.parquet         # local cache, gitignored
→ pushes only a ~2 KB summary document to Mongo for the dashboard
```

```python
def run(start: str, end: str, cfg: dict) -> dict:
    trades = []
    for day in trading_days(start, end):
        if cfg["use_regime"] and regime_as_of(day)["state"] == "RISK_OFF":
            continue
        for cand in score_as_of(day)[: cfg["max_new"]]:      # point-in-time only
            trades.append(simulate(cand, day, cfg))          # zone fill, ATR stop, T1/T2/T3, 10-day cap
    return metrics(trades)
```

**Discipline that keeps it honest:**
- **No lookahead** — `score_as_of(day)` may only read data up to `day`. This is the bug that makes every backtest look brilliant.
- **Realistic fills** — require the day's range to touch the entry zone; add ~0.1% slippage plus the Phase 6 cost model.
- If stop and target are both touched the same day, assume the **stop** hit first.
- **Walk-forward**: tune on 2021–2024, verify untouched on 2025–2026.
- Know the bias: today's Nifty 500 isn't 2021's (survivorship). Acceptable for a personal tool if you're aware of it.
- **Control run**: shuffle the signal — expectancy should collapse to ~0. If it doesn't, you have a leak.

---

# PHASE 9 — Polish (F11, F13, F15, F16)

**Charts** (add `mplfinance` — you currently have no plotting library):

```python
import mplfinance as mpf, io
def chart_png(df, entry, stop, targets) -> bytes:
    buf = io.BytesIO()
    mpf.plot(df.tail(90), type="candle", volume=True, mav=(20,50), style="charles",
             hlines=dict(hlines=[entry, stop, *targets],
                         colors=["blue","red"]+["green"]*len(targets), linestyle="--"),
             savefig=buf, tight_layout=True)
    return buf.getvalue()
# await bot.send_photo(chat_id, BufferedInputFile(png, "chart.png"), caption=summary)
```

**Critic pass** (F11) — reuse your Groq keys, ask it to falsify:

```python
CRITIC = """You are a risk manager trying to REJECT this swing trade (2-10 day horizon).
Deterministic data: {data}
Proposed: {reco}
Reply JSON: {{"verdict":"APPROVE|REJECT|DOWNGRADE","failed_gates":[...],
"strongest_objection":"...","stop_realistic":true|false,"better_action":"..."}}"""
# emit BUY only when both models agree; otherwise WAIT + show the objection
```

**Inline buttons** (`Took it` / `Skipped`) → write straight into `trade_journal`.

**Broker feed** (F16) — same interface, yfinance stays as fallback:

```python
async def fetch_quote(symbol: str) -> dict:
    for src in (broker_quote, yfinance_quote, nse_quote):
        try:
            q = await src(symbol)
            if q: await record(src.__name__, True); return q
        except Exception as e:
            await record(src.__name__, False, str(e))
```

---

# Appendix A — Free resource reference

| What | Endpoint / package | Key? | Notes |
|---|---|---|---|
| Equity OHLCV, indices, FX | `yfinance` (`RELIANCE.NS`, `^NSEI`, `^NDX`, `INR=X`, `GOLDBEES.NS`, `MON100.NS`) | No | Batch with `yf.download`; delayed; avoid `.info` in hot paths |
| NSE quote / option chain / FII-DII | `nseindia.com/api/*` (via `nsepython`) | No | Cookie warm-up + browser headers; **often blocked from cloud IPs** |
| NSE holidays | `nseindia.com/api/holiday-master?type=trading` | No | Cache daily; keep static fallback |
| Nifty 500 list + sectors | `nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` | No | Cache weekly |
| NSE announcements / results / insider / bulk-block deals | `nseindia.com/api/corporate-announcements`, `.../corporates-financial-results`, `.../corporates-pit`, `.../historical/bulk-deals` | No | Tier-1 evidence when reachable |
| Alt NSE historical | [`openchart`](https://github.com/marketcalls/openchart), [`nselib`](https://github.com/RuchiTanmay/nselib) | No | Useful when yfinance intraday is patchy |
| Mutual fund NAV (all Indian MFs) | `https://api.mfapi.in/mf/{code}`, `/mf/search?q=` | **No** | Free, no registration, daily, full history (AMFI-sourced) |
| Official NAV dump (incl. ETFs) | `https://portal.amfiindia.com/spages/NAVAll.txt` | No | Use for true ETF NAV / premium calc |
| News RSS | 12 feeds already wired in `news_fetcher.py` | No | Moneycontrol/Business Standard are 403 from cloud — leave out |
| LLM — main | Gemini Flash (`gemini-3.6-flash`) | Yes (free) | ~10 RPM · 250k TPM · **1,500 req/day**; Pro **not** on free tier since 1 Apr 2026 |
| LLM — critic | Groq (Llama) | Yes (free) | ~30 RPM · **14,400 req/day** |
| Push | ntfy.sh + Telegram | No / bot token | Already wired; **Telegram = free unlimited cold archive** |
| External cron | cron-job.org (7 jobs, 1-min interval, free) | No | More punctual than GitHub Actions (Appendix C) |

**Quota budget:** 5 symbols × (research + structure + critic) ≈ 15 calls/day against 1,500. The binding limit is 10 RPM, so keep the `asyncio.sleep(5)` between symbols.

---

# Appendix B — Atlas M0 storage budget

**M0 free tier:** 512 MB storage · 500 connections · 500 collections / 100 databases · **10 GB in / 10 GB out per week** · one free cluster per project.

Target allocation after all phases:

| Tier | Contents | 5-year size |
|---|---|---|
| 🔒 Sacred | closed positions, journal, rollups, SIP ledger, recommendations JSON, knowledge chunks | ~10 MB |
| 🔄 Rolling | chat history (25 d), report prose (45 d), news dedupe (3 d), screener scores (30 d), capped logs | ~12 MB steady |
| 📉 Derived | OHLCV, backtest inputs — **not on Atlas** | 0 |
| **Total** | | **~25–35 MB (≈6%)** |

Compare with the current trajectory: `intraday_scans` alone was on track for ~150 MB/year.

**M0 gotchas worth knowing:**
- The quota counts **data + indexes**. An over-indexed small collection can surprise you — `GET /storage/stats` reports both.
- Deleting many documents at once does **not** return space to the OS, and shared tiers don't support `compact`. Prefer **TTL** (continuous, space gets reused) and **drop the collection** when you want space back immediately.
- Don't index what you never query. Each index on `swing_positions` costs real bytes.
- The 10 GB/week transfer cap is another reason backtests read local Parquet, not Atlas.

---

# Appendix C — Paid options, researched (September 2026)

Only two things here are worth money, and neither is urgent.

### Market data / broker APIs

| Provider | API cost | Market data included? | Verdict |
|---|---|---|---|
| **Angel One SmartAPI** | **Free** | Live + historical | ✅ best free upgrade over yfinance |
| **Fyers API** | **Free** | Quotes + historical (minute, ~1–2 yrs) | ✅ good alternative |
| **Dhan (DhanHQ)** | **Free** | Yes (some data packs may cost) | ✅ fine |
| **Upstox** | **Free** | Yes | ₹10/executed order via API until 31 Mar 2026 |
| **Zerodha Kite Connect** | Personal tier **free but excludes market data**; full Connect **₹500/mo per key** | Paid tier only (historical now bundled, not sold separately) | ⚠️ pay only for the ecosystem |

**Recommendation:** open a free **Angel One** or **Fyers** account purely as a data feed; keep executing wherever you like. Exchange-grade prices and reliable history at ₹0, and it removes the NSE-scraping fragility (W7).

### Hosting
- Render free web services **spin down after 15 min idle**, cold-start 30–60 s → your 09:20 cron is not reliably firing. Paid instance ≈ $7/mo is always-on; Render's **Cron Job** service type guarantees at-most-one run per schedule.
- Free workaround in Appendix D.

### Not worth paying for
- **Paid LLM tiers** — 1,500 free Gemini Flash calls/day against your ~15/day.
- **Paid data vendors** — a free Indian broker API beats them for NSE.
- **Atlas M2/M10** — after Phase 2 you'll be at ~6% of the free 512 MB.

---

# Appendix D — Making the scheduler actually fire

1. **APScheduler + external keep-alive (free).** Add `GET /health/ping`; cron-job.org hits it every 10 min, 08:45–16:00 IST weekdays.
2. **External cron drives the jobs (free, most reliable).** Expose token-protected `POST /jobs/{name}` and let cron-job.org call `morning`, `swing_tracker`, `dip_check`, `storage_check` at the right IST times. **Add idempotency** — a unique index on `(job, date)` in `job_runs` so a double fire can't double-post.
3. **Paid always-on (~$7/mo)** — keep APScheduler, add a persistent jobstore.

Avoid GitHub Actions `schedule` for market-hour jobs: 5-minute minimum, commonly **5–30 minutes late** on the hour, and schedules get skipped on inactive repos. Whichever you pick, set `coalesce=True` and `misfire_grace_time` so a cold start doesn't replay a burst of missed runs.

---

# Appendix E — New env vars

```bash
API_TOKEN=                      # required after Phase 0.1 (openssl rand -hex 32)
DEFAULT_GEMINI_MODEL=gemini-3.6-flash
TRADING_CAPITAL=200000
RISK_PCT_PER_TRADE=1.0          # 0.5 / 1 / 2  (docs/13_Risk_Management.yaml)
MAX_PORTFOLIO_HEAT_PCT=5.0
MAX_OPEN_POSITIONS=5
MAX_SINGLE_STOCK_PCT=15
MAX_HOLD_DAYS=10                # your hard swing cap
MIN_RR_TO_T1=2.0
GOLDBEES_MONTHLY_BUDGET=5000
MON100_MONTHLY_BUDGET=5000
SIP_SCHEME_CODES=               # comma-separated, resolved from mfapi.in/search
STORAGE_WARN_PCT=70
STORAGE_AUTOCLEAN_PCT=90
```

---

# Appendix F — Verification checklist

**Phase 0**
- [ ] `curl /performance/hit-rate` without header → 401; with header → 200
- [ ] `today_ist()` at 01:00 IST returns *today*
- [ ] `is_trading_day()` is False for the next NSE holiday
- [ ] RSI/ATR match your broker's chart within rounding
- [ ] `weekly_trend` is no longer `"N/A"` *(it never has been anything else)*
- [ ] Startup logs valid vs dropped Gemini model IDs
- [ ] Forced exception in the morning job → you get a failure alert

**Phase 1–2 (storage)**
- [ ] `intraday_scans` **collection** dropped (historical per-minute rows only — the monitoring *feature* stays, re-timed)
- [ ] `virtual_portfolio` **migrated** to `paper_positions`, then the old collection dropped — the paper-trading *feature* stays (F19)
- [ ] `/storage/stats` shows the size drop
- [ ] `/storage` in Telegram shows usage and top consumers
- [ ] `/storage 60` shows a **dry-run preview** and does not delete
- [ ] Confirm button deletes and reports freed space
- [ ] A sweep at any age leaves `swing_positions` / `trade_journal` untouched
- [ ] `cleanup(include_sacred=True)` without the confirm phrase is refused
- [ ] TTL indexes exist; `processed_news` self-expires at 3 days
- [ ] `monthly_rollups` has last month's stats **before** detail is purged
- [ ] Capacity check warns at 70%

**Phase 3**
- [ ] Recommendation JSON validates; a bad R:R is downgraded to `WAIT`
- [ ] Prompt no longer contains "Gemini live_price is the authoritative CMP"
- [ ] `data_conflicts` populates when you feed a deliberately wrong price
- [ ] Stored recommendation doc is < 1 KB (prose lives in `morning_alerts`)

**Phase 4**
- [ ] A position whose zone was never touched ends `CANCELLED`, not graded
- [ ] A position survives >1 day and is tracked on days 2, 3, …
- [ ] Day-10 position produces `TIME_EXIT_DUE`
- [ ] A manually tracked position goes through the identical lifecycle
- [ ] `expectancy_r` computes over closed positions only
- [ ] `daily[]` never exceeds 10 entries

**Phase 5–8**
- [ ] Screener excludes illiquid names and symbols with results in 3 days
- [ ] Regime flips to `RISK_OFF` on a Nifty-below-200DMA date and blocks new longs
- [ ] `size_position` risk ≈ capital × risk% (within one share); 6th position blocked
- [ ] XIRR matches a manual/online calculation
- [ ] Dip tiers fire correctly on a historical GOLDBEES drawdown
- [ ] MON100: `ndx + inr + premium ≈ etf move`
- [ ] Backtest control run (shuffled signal) gives ~0 expectancy

---

## Suggested build order

**Weekend 1** — **Phase E** (WP1–4) then Phases 0, 1, 2. You end with: the event-loop bug fixed and the bot responsive during scans, layer boundaries enforced in CI, correct Wilder indicators with a working weekly trend, a locked API, IST dates, holiday awareness, the storage bomb removed, and an age-based cleanup you control from Telegram that cannot touch your trade history.

**Weekend 2** — Phase 3 + **C** + **B**, then Phase 4. Prices the LLM can't overwrite, timestamps on every input, robust key rotation with a circuit breaker, the two prompts live, structured records ~10× smaller, and positions that live 2–10 days with real expectancy numbers — real, manual and paper on one lifecycle.

**Weekend 3** — Phase **D** + Phase 7. Alerts that fire on the right events at the right priority with a proper bot menu; SIPs and ETF dip-buying stop being manual.

**Weekend 4** — Phase **A** Tier 1, then Phase 5, then 6. ADX and RS Rating start vetoing bad trades, candidate generation becomes reproducible, and position sizing is enforced rather than suggested.

**Anytime after Phase 0** — Phase **N** (news fast lane). It's additive and independent; slot it in whenever the news latency annoys you.

**Later** — Phase 8 locally to measure the edge (rules only — the LLM layer cannot be backtested), Phase **A** Tier 2 for depth, Phase 9 for comfort, and the free broker feed (F16) when NSE blocking annoys you enough.

**Cadence discipline:** one work package per sitting, one commit, and every package ends green — app boots, tests pass, bot responds (`ENGINEERING.md` §7). Never leave the tree half-migrated across a session; you trade with this system.

---

### Sources

- Gemini: [models](https://ai.google.dev/gemini-api/docs/models) · [structured output](https://ai.google.dev/gemini-api/docs/structured-output) · [free-tier limits 2026](https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/)
- Groq: [free-tier limits 2026](https://tokenmix.ai/blog/groq-free-tier-limits-2026)
- Brokers: [Kite Connect pricing](https://zerodha.com/products/api/) · [Zerodha free personal APIs](https://zerodha.com/z-connect/updates/free-personal-apis-from-kite-connect) · [historical data bundled](https://kite.trade/forum/discussion/14806/historical-data-is-now-free-with-base-kite-connect-subscription) · [India trading API comparison 2026](https://indianbrokertest.com/best-trading-apis-in-india/)
- Mutual funds: [MFAPI.in](https://www.mfapi.in/) · [docs](https://www.mfapi.in/docs/)
- MongoDB: [Atlas free-cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/) · [Atlas service limits](https://www.mongodb.com/docs/atlas/reference/atlas-limits/)
- NSE libraries: [openchart](https://github.com/marketcalls/openchart) · [nselib](https://github.com/RuchiTanmay/nselib)
- Scheduling: [Render free-tier spin-down](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026) · [Render cron jobs](https://render.com/docs/cronjobs) · [GitHub Actions cron delays](https://runhooks.app/blog/github-actions-scheduled-workflows-unreliable/) · [cron-job.org](https://cron-job.org/en/)
