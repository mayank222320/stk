# StockAI — News Fast Lane (additive, free)

**Written:** 4 September 2026
**Constraint respected:** the existing news scanner is **not changed**. Its broad RSS sweep, dedupe, keyword filter and AI cap all stay exactly as they are — they work. This document adds a **parallel fast lane** and two zero-risk fixes inside the current path.
**Companions:** `ALERTS_AND_BOT.md` (how these alerts fire) · `LLM_ORCHESTRATION.md` (the AI budget they consume) · `IMPLEMENTATION.md` Phase D

---

## 1. Where the delay actually is

Before adding anything, it's worth seeing the real latency budget. Polling faster only fixes one small slice of it.

| Stage | Current delay | Can you fix it? |
|---|---|---|
| Event happens → company files with NSE/BSE | ~0 | — this is the primary source |
| **Filing → publisher writes and publishes the article** | **5–30 min** | ❌ not with RSS. **This is the biggest chunk.** |
| **Article → appears in the publisher's RSS feed** | **2–20 min** | ❌ feeds are cached and batched server-side |
| RSS → your next poll (`minute='*/5'`) | 0–5 min | ✅ but it's the smallest slice |
| Poll → dedupe + keyword filter | < 1 s | ✅ already fast |
| Trigger → Grok analysis (3 sequential, 30 s timeout) | 5–30 s | ✅ |
| Analysis → alert sent | < 1 s | — |
| **Total** | **~10–55 minutes** | |

**The conclusion that matters:** roughly 80% of your delay is publisher lag — the time between a company filing something and a journalist publishing an article about it. Polling every minute instead of every five wouldn't touch it.

**So the fast lane doesn't poll news faster. It stops waiting for journalists.**

---

## 2. Fast lane #1 — exchange filings (the biggest win, free)

Under SEBI disclosure rules, a company files a material event **with the exchange first**. Journalists then read that filing and write about it. So the exchange feed is structurally **5–30 minutes ahead** of every news source you currently poll — it's the same information, at the source.

### Endpoints (free, no key)

| Exchange | Access | Note |
|---|---|---|
| **BSE** | [`pip install bse`](https://pypi.org/project/bse/) — unofficial Python API ([BennyThadikaran/BseIndiaApi](https://github.com/BennyThadikaran/BseIndiaApi)), returns announcements as JSON with pagination | **Start here.** BSE is generally less aggressive than NSE at blocking datacenter IPs — and your NSE calls already get blocked (`WEAKNESSES.md` W7) |
| **NSE** | `https://www.nseindia.com/api/corporate-announcements?index=equities` | Same cookie warm-up pattern as your existing `_compute_fii_dii()`. Use as the secondary |

Announcements come back **exchange-wide and already structured** — company, category, subject, PDF link, timestamp — so you filter locally to your held positions and today's shortlist. Two HTTP requests cover every stock you care about.

### Why this is cheap enough to poll every 60–90 seconds

- 2 requests per cycle, regardless of how many symbols you track.
- **No AI call needed to alert.** The filing already carries a category (`Board Meeting`, `Award of Order`, `Financial Results`, `Credit Rating`) and a subject line. Keyword-match the category, alert immediately, and only spend a Groq call on interpretation *afterwards* if you want it.
- Dedupe on the filing ID, so a repeated poll costs nothing.

```python
# features/news_scanner/fast_lane.py  — NEW file, existing scanner untouched
HIGH_IMPACT = {"award of order", "financial results", "acquisition", "amalgamation",
               "credit rating", "board meeting", "dividend", "buy back",
               "resignation", "fund raising", "investor presentation", "open offer"}

async def poll_filings() -> list[dict]:
    """Every 60-90s during market hours. Only alerts on watched symbols."""
    watched = await watched_symbols()          # open positions + today's shortlist
    hits = []
    for filing in await fetch_bse_announcements():          # NSE as fallback
        if filing["symbol"] not in watched:
            continue
        if not any(k in filing["category"].lower() for k in HIGH_IMPACT):
            continue
        try:
            await mongo.db.filings_seen.insert_one({          # unique index on filing_id
                "filing_id": filing["id"], "at": now_ist()})
        except DuplicateKeyError:
            continue
        await send_alert("EXCHANGE_FILING", "P1", filing["symbol"],
                         _fmt(filing), f"📄 {filing['symbol']} — {filing['category']}")
        hits.append(filing)
    return hits
```

**This also upgrades your evidence quality, not just the speed.** A filing is Tier-1 primary evidence in your own source hierarchy ([qmaf_v2_personalized.md:313-326](../features/intraday/templates/qmaf_v2_personalized.md#L313-L326)) — better than a news summary you currently feed the model.

---

## 3. Fast lane #2 — Google News RSS, per symbol, one-hour window

Free, no API key, and it aggregates hundreds of publishers — so it typically surfaces a story sooner than any single publisher's own feed, and catches outlets you don't subscribe to (including the Moneycontrol and Business Standard content that's blocked from your server).

```
https://news.google.com/rss/search?q=%22Reliance+Industries%22+when:1h&hl=en-IN&gl=IN&ceid=IN:en
```

- `when:1h` restricts to the last hour, so every result is fresh by construction.
- Run it **only for held positions + today's shortlist** — 5–10 queries per cycle, not the whole market. That keeps it fast, polite and free.
- Merge and dedupe against your existing `processed_news` so the same story never alerts twice across lanes.

**One verified caveat:** Google News returns *redirect* URLs, and resolving them to the real article adds latency and occasionally breaks. For alerting you don't need the final URL — **alert on the headline and source name, resolve the link lazily** only if you open it.

---

## 4. Fast lane #3 — the price move itself (faster than any news source)

The genuinely fastest free signal isn't news at all. **Price moves before the headline exists.** A sudden volume spike with a price move on a watched stock tells you something happened *before* any journalist has written a word — often before the filing is even processed.

This inverts the pipeline: instead of *news → check the price*, it becomes **anomaly → go find out why**.

```python
async def detect_anomaly(symbol: str) -> dict | None:
    """1-minute bars, free via yfinance. Runs on watched symbols only."""
    df = await get_intraday(symbol, interval="1m", period="1d")
    if len(df) < 25:
        return None
    last5   = df.tail(5)
    vol_x   = last5["Volume"].sum() / (df["Volume"].tail(25).mean() * 5)
    move_pct = (last5["Close"].iloc[-1] / last5["Open"].iloc[0] - 1) * 100
    atr_frac = abs(move_pct) / (await atr_pct(symbol))          # move vs normal daily range

    if vol_x > 3 and atr_frac > 0.35:            # 3x volume AND >35% of a normal day's range
        return {"symbol": symbol, "vol_multiple": round(vol_x, 1),
                "move_pct": round(move_pct, 2),
                "direction": "UP" if move_pct > 0 else "DOWN"}
    return None
```

On a hit: alert immediately (`UNEXPLAINED_MOVE`, **P1**), then trigger a targeted Google News `when:1h` query and a filings check for that one symbol. You get *"PERSISTENT is up 3.2% on 4× volume — no filing or news found yet"* — which is genuinely actionable, and impossible to get from a news feed.

**Free upgrade path:** a broker WebSocket (Angel One SmartAPI / Fyers / Dhan — all free with an account, `FEATURES.md` F16) gives **true streaming ticks with no polling at all**. That takes this from ~60 seconds to sub-second, still at ₹0.

---

## 5. Two fixes inside the existing scanner (logic unchanged)

Neither of these alters how your scanner decides anything — they only stop it wasting time and losing articles.

### 5.1 Conditional GET — poll 3× more often for less bandwidth

Right now every poll downloads all 12 feeds in full. With `ETag` / `If-Modified-Since`, an unchanged feed returns **`304 Not Modified` with an empty body** — roughly 10–20× cheaper per poll. That means you can move from 5 minutes to 90 seconds and still use *less* bandwidth than today.

```python
async def _fetch_feed(source, client):
    meta = await mongo.db.feed_meta.find_one({"_id": source["url"]}) or {}
    headers = dict(HTTP_HEADERS)
    if meta.get("etag"):     headers["If-None-Match"]     = meta["etag"]
    if meta.get("modified"): headers["If-Modified-Since"]  = meta["modified"]

    resp = await client.get(source["url"], headers=headers, timeout=8.0)
    if resp.status_code == 304:
        return []                                          # nothing new — near-zero cost
    await mongo.db.feed_meta.replace_one({"_id": source["url"]},
        {"_id": source["url"], "etag": resp.headers.get("ETag"),
         "modified": resp.headers.get("Last-Modified")}, upsert=True)
    # ... existing parse logic, entirely unchanged
```

Also raise `feed.entries[:6]` → `[:15]` ([news_fetcher.py:97](../features/market_data/news_fetcher.py#L97)) so a burst of publishing doesn't push items off the list before you see them.

### 5.2 The silent-drop bug — this is probably why news *feels* late

Worth reading carefully, because it looks exactly like a latency problem and isn't:

Every new candidate is written to `processed_news` ([news_scanner/service.py:149-163](../features/news_scanner/service.py#L149-L163)), but only the **first 3** triggered articles are sent to the AI ([:173](../features/news_scanner/service.py#L173)). The 4th onward are already marked processed — so the next run's dedupe skips them, and **they are never analysed and never alerted.** On a busy morning with 10 market-moving headlines, you see 3 and silently lose 7.

Minimal fix, no logic change — add a queue flag instead of dropping:

```python
for article in candidates:
    triggered_flag = bool(_TRIGGER_RE.search(article.title) or _TRIGGER_RE.search(article.summary))
    bulk_inserts.append({"url": article.url, "title": article.title,
                         "processed_at": datetime.now(timezone.utc),
                         "pending_ai": triggered_flag})     # ← queue instead of drop

# then, at the top of each run, drain the backlog first — highest value first
pending = await mongo.db.processed_news.find({"pending_ai": True}).to_list(50)
queue = sorted(pending + triggered, key=lambda a: (
    a["symbol"] not in watched,          # held/shortlist symbols first
    -a.get("published_ts", 0)))          # then newest
for article in queue[:_MAX_AI_CALLS_PER_RUN]:
    ...                                  # existing analysis, unchanged
    await mongo.db.processed_news.update_one({"url": article["url"]},
                                            {"$set": {"pending_ai": False}})
```

Sorting the queue by *held-position-first* also means your 3 AI calls per run go to the stocks you actually own — which cuts LLM usage and raises alert relevance at the same time (`LLM_ORCHESTRATION.md` §2).

### 5.3 Alert first, analyse second

Currently the alert waits for the Groq call (up to 30 s). For a headline touching a **held position**, send the raw headline immediately, then `edit_message_text` with the AI analysis when it arrives. Saves 5–30 seconds on precisely the alerts that matter.

---

## 6. Manual trigger — a smaller issue than first stated

> ### ✅ Correction
> An earlier version of this section claimed the manual news fetch "can essentially never find anything." **That was wrong.** Your dashboard's news display uses paths with **no dedupe filter** and works correctly on every click:
>
> | Endpoint | Dedupe? | Status |
> |---|---|---|
> | `GET /market/news/live` → `fetch_all_news()`, returns articles directly | No | ✅ latest every click |
> | `GET /market/news/stock?symbol=` → yfinance `ticker.news` | No | ✅ works |
> | `GET /news-scanner/alerts` → stored alerts from scheduled runs | n/a | ✅ works |
> | `POST /news-scanner/trigger` → re-runs the **alerting** pipeline | **Yes** | ⚠️ affected |
>
> Only the last one is affected, and it exists for the cron job and testing — not for viewing news. **Nothing about your news viewing is broken, and this is not why news arrives late.** The real latency cause is publisher lag (§1), and the real fix is the filings lane (§2).

### The remaining issue

`POST /news-scanner/trigger` calls `run_news_scanner()` ([news_scanner/router.py:7-11](../features/news_scanner/router.py#L7-L11)) — **the same function as the 5-minute cron job, sharing the same dedupe.** Inside it:

```python
already_seen = await _get_processed_urls_today(all_urls)
candidates = [a for a in articles if a.url not in already_seen and a.published_ts >= cutoff_ts]
if not candidates:
    print("[NewsScanner] No new articles in last 4h.")
    return                          # ← returns nothing, says nothing
```

The automatic scan runs **every 5 minutes all day** and marks every article it sees as processed. So when `POST /news-scanner/trigger` runs, almost everything is already in `processed_news` — zero candidates, and it returns `{"status": "scan complete"}` having produced no new analysis or alerts.

**Consequence:** you have no way to force a re-analysis of today's news. Not a viewing problem — an *alerting* one. Worth a 10-minute fix, not urgent.

Four more issues on the same endpoint:

| Issue | Effect |
|---|---|
| Returns only `{"status": "scan complete"}` | the caller gets no articles at all — nothing to display |
| `await run_news_scanner()` blocks on up to 3 sequential Groq calls (30 s timeout each) | the HTTP request can hang ~90 s and time out in the dashboard |
| Subject to `_MAX_AI_CALLS_PER_RUN = 3` and the 4-hour cutoff | an interactive request is throttled like a background job |
| No auth, no rate limit ([W3](WEAKNESSES.md)) | repeated calls silently burn your Groq quota |

### The fix — separate "scan" from "fetch"

They're different operations and should stop sharing a function:

| Endpoint | Purpose | Behaviour |
|---|---|---|
| `POST /news-scanner/trigger` | the **alerting job** — cron and testing | unchanged: dedupe, keyword filter, capped AI, sends alerts |
| `GET /news/latest` | **your manual fetch** — user-facing read | ignores the alert dedupe, hits all lanes, returns data fast, no AI in the request path |

```python
@router.get("/news/latest")
async def latest_news(symbol: str | None = None, limit: int = 30, force: bool = True):
    """Interactive fetch. Returns articles; never blocks on an LLM."""
    if cached := await _recent_cache(symbol):            # 60s cache guards your quota
        return {**cached, "cached": True}

    watched = [symbol.upper()] if symbol else await watched_symbols()

    rss, filings, gnews = await asyncio.gather(          # all three lanes in parallel
        fetch_all_news(limit=60),                        # existing fetcher, unchanged
        fetch_filings(watched),                          # fast lane #1
        fetch_google_news(watched, window="1h"),         # fast lane #2
        return_exceptions=True,
    )

    items = _merge_dedupe(rss, filings, gnews)           # dedupe within THIS response only
    for it in items:
        it["age_minutes"]     = _age_min(it["published_ts"])
        it["watched"]         = it.get("symbol") in watched
        it["already_alerted"] = await _was_alerted(it["url"])   # informational, not a filter
    items.sort(key=lambda i: (not i["watched"], -i["published_ts"]))   # your stocks first

    payload = {"as_of": fmt_ist(), "session": session_state(),
               "count": len(items), "items": items[:limit],
               "sources": _source_status(rss, filings, gnews),   # what worked, what failed
               "cached": False}
    await _cache(symbol, payload, ttl_s=60)
    return payload
```

Four things this changes:

- **`force=true` is the default** — the alert dedupe no longer hides articles from *you*. Each item carries `already_alerted` so you can see what was pushed before, without it being filtered out.
- **All three lanes in one call** — filings and Google News `when:1h` are exactly what makes a manual fetch "latest", and filings need no AI to be useful.
- **No LLM in the request path**, so it returns in ~2–4 seconds instead of hanging for 90.
- **60-second cache + your stocks first** — repeat taps are free, and held positions sort to the top.

### AI analysis on demand, not up front

Move the Groq call out of the fetch and behind an explicit action:

```python
@router.post("/news/analyze")
async def analyze_one(url: str):
    """One Groq call, only when you actually ask for it."""
    if not await budget_allows("P2"):       # LLM_ORCHESTRATION.md §12
        return {"error": "LLM budget reserved for position alerts right now"}
    article = await _get_article(url)
    return await analyze_news(article.title, article.summary, url)
```

In Telegram this becomes a `🧠 Analyse` button under each headline; in the dashboard, a button on the card. You spend a call on the two or three stories you care about instead of an arbitrary first-three.

### Telegram side

```
/news              → latest across held positions + today's shortlist
/news RELIANCE     → that symbol only: filings + Google News when:1h + RSS matches
```

```
📰 Latest — 4 Sep, 11:42 AM IST (market OPEN)

🔴 CUMMINSIND · 3 min ago · BSE FILING
   Award of Order — order win, Rs 340 cr
   [ 🧠 Analyse ]  [ 📄 Filing ]

⚪ PERSISTENT · 12 min ago · Economic Times
   Q2 revenue guidance raised
   [ 🧠 Analyse ]  [ 🔗 Read ]  ✓ already alerted

Sources: RSS 11/12 · BSE ✓ · Google News ✓ · NSE ✗ blocked
[ 🔄 Refresh ]  [ 📊 My stocks only ]
```

The `Sources:` line matters — when something is genuinely quiet you can tell it apart from a source being down, which today is invisible.

---

## 7. Expected result

| Signal | Today | With the fast lane |
|---|---|---|
| Corporate action on a held stock (order win, results, rating) | 10–55 min | **1–3 min** (exchange filing) |
| Unexplained price move on a held stock | never detected | **< 1 min** (anomaly), sub-second with a broker WebSocket |
| Story from a blocked publisher (Moneycontrol, BS) | never seen | 2–15 min (Google News aggregation) |
| Broad market news | 10–55 min | 5–25 min — same logic, just polled at 90 s via conditional GET |
| Market-moving headlines actually analysed | first 3 per run only | **all of them**, held positions first |
| **Manual "find latest news"** | **usually returns nothing** (dedupe already consumed it) | **all three lanes, ~2–4 s, your stocks first** |

---

## 8. Two-speed architecture

```
FAST LANE  — every 60-90s, market hours, watched symbols only  [NEW]
  ├─ BSE/NSE filings        → P1 alert, no AI needed to fire
  ├─ Google News when:1h    → P1 alert on headline
  └─ price/volume anomaly   → P1 alert, then look for the cause

SLOW LANE  — every 5 min, whole market                          [UNCHANGED]
  └─ 12 RSS feeds → dedupe → keyword filter → capped AI → alert
     (+ conditional GET, + pending_ai queue — no logic change)

MANUAL LANE — on demand, GET /news/latest  and  /news [SYMBOL]  [FIXED]
  └─ all three lanes in parallel · ignores alert dedupe · no AI in the
     request path · 60s cache · held positions first · source status shown
     AI only when you tap "Analyse"
```

Three lanes, one shared dedupe *for alerts* — so a story never pushes twice — but the manual lane is never *hidden* by that dedupe, which is the bug it fixes.

The fast lane is narrow and targeted, so it's cheap. The slow lane keeps giving you broad market awareness exactly as it does now.

**Cost:** ₹0. All sources are free and keyless.
**Storage:** `filings_seen` (filing ID + timestamp, TTL 7 days) and `feed_meta` (12 documents, one per feed) — together well under 1 MB.
**LLM impact:** *negative* — filings and anomalies alert without an AI call, and the queue prioritises held symbols, so you spend fewer Groq calls on more relevant articles.

---

## 9. Build checklist

**Do first — the manual trigger (§6), because it's broken today**
1. [ ] `GET /news/latest` — all three lanes in parallel, ignores alert dedupe, returns articles + `sources` status + `as_of`
2. [ ] 60-second response cache keyed on `symbol`; `cached: true` on repeat taps
3. [ ] Move AI out of the fetch path → `POST /news/analyze {url}`, gated by the P2 LLM budget
4. [ ] `/news` and `/news <SYMBOL>` Telegram commands with `🧠 Analyse` buttons per headline
5. [ ] Leave `POST /news-scanner/trigger` exactly as it is for the cron job; add auth (Phase 0.1) and a rate limit

**Fast lanes**
6. [ ] `pip install bse`; verify announcements fetch from your host (BSE first, NSE fallback)
7. [ ] `features/news_scanner/fast_lane.py` — `poll_filings()` with a unique index on `filing_id`
8. [ ] Add `EXCHANGE_FILING` and `UNEXPLAINED_MOVE` to the alert catalogue (`ALERTS_AND_BOT.md`) at **P1**
9. [ ] Google News per-symbol `when:1h` for held positions + shortlist
10. [ ] `detect_anomaly()` on watched symbols; wire to the same job
11. [ ] Schedule one fast-lane job: `minute='*/2'` IST, market hours, trading days only

**Existing scanner — no logic change**
12. [ ] Conditional GET + `feed_meta` collection in the existing fetcher
13. [ ] `entries[:6]` → `[:15]`
14. [ ] `pending_ai` queue flag + held-symbol-first ordering (fixes the silent drop)
15. [ ] Alert-first-then-edit for held-position headlines
16. [ ] *(later)* broker WebSocket for true streaming ticks

**Verify**
- [ ] Run the cron scan, then immediately call `GET /news/latest` → **articles are returned**, not an empty result
- [ ] `/news RELIANCE` returns filings + Google News + RSS matches for that symbol only
- [ ] Two taps of Refresh within 60 s → second returns `cached: true`, zero network cost
- [ ] `/news/latest` responds in under ~5 s (no LLM in the path)
- [ ] Kill NSE access → `sources` reports `NSE ✗`, BSE and RSS still return
- [ ] A stock with a known recent filing → fast lane finds it before the RSS lane
- [ ] Simulate 10 triggered articles in one run → all 10 analysed across runs, none dropped
- [ ] Second poll of an unchanged feed returns `304` and costs no parse
- [ ] Anomaly on a watched symbol → alert fires with volume multiple and move %
- [ ] No story alerts twice across the lanes; manual lane still *shows* already-alerted items
