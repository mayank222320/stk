# StockAI Backend — Weakness Audit

**Audited:** 3 September 2026
**Scope:** full backend (`main.py`, `core/`, `features/`, `prompt.txt`, `docs/`)

**Your context (confirmed):**
- **Swing only — 2 to 10 days, 10 is a hard cap.** No intraday *trading*.
- **All existing features are kept — but converted to swing duration**, including manual position tracking, in-session monitoring/alerts, and the virtual (paper) portfolio.
- **What's dropped is the per-minute write cadence, not the features.** See W1 — the storage problem is caused by *how often* it writes, not by what it does.
- Also managing: 3 autopay SIPs, GOLDBEES + MON100 dip-buying.
- Single user (you). **MongoDB Atlas M0 free tier — 512 MB.** Storage is a real constraint.
- Analysis depth must rise to **professional standard** — see `ANALYTICS.md`.

**Document set** (hand all of these to any agent doing the work):
`PROJECT_BRIEF.md` → `WEAKNESSES.md` (this) → `FEATURES.md` → `IMPLEMENTATION.md` → `ANALYTICS.md` → `KNOWLEDGE_AND_PROMPTS.md`

Ranked by what each one costs you, not by difficulty.

---

## Verdict in one paragraph

The research plumbing is genuinely good — real indicator math from OHLCV, NSE option chain, FII/DII flows, RAG over a hand-built knowledge base, multi-key failover, performance logging. Four things undermine it: **(1)** a per-minute write loop is filling your 512 MB cluster, and the auto-delete schedules you added to cope are wiping the one thing you can't recompute — your track record; **(2)** the decision loop grades a swing trade the same afternoon it was issued, so your own hit-rate stat is meaningless; **(3)** the LLM has been given final authority over numbers the code already computes exactly; **(4)** the analysis stops at retail-level indicators — no trend-strength filter, no relative-strength ranking, no volume profile, no algorithmic swing pivots, no earnings-drift or quality screens, and no data-freshness discipline, so a confident-looking report can rest on a chop-market setup with stale inputs.

The good news is that the fix for (1) is a cadence change, not a feature deletion — every existing capability survives, converted to swing timing.

---

# CRITICAL

## W1. A per-minute scanner is eating your free cluster — and the fix you applied destroys your track record

This is the root cause of the storage problem you described.

`custom_stock_minute_scan` is registered with `minute='*'` — **every minute**, `hour='3-10'` UTC (8 hours) ([scheduler/service.py:78-83](../features/scheduler/service.py#L78-L83), [:585-601](../features/scheduler/service.py#L585-L601)). Each run calls `run_intraday_scan(symbols_override=…)`, which **unconditionally inserts one `intraday_scans` document per symbol** ([intraday/service.py:204](../features/intraday/service.py#L204)) — even when nothing changed.

| Tracked stocks | Docs/day | Approx/day | Approx/year |
|---|---|---|---|
| 1 | 480 | ~0.3 MB | ~75 MB |
| 2 | 960 | ~0.6 MB | ~150 MB |
| 3 | 1,440 | ~0.9 MB | ~220 MB |

Plus `intraday_scan_routine` adds 12 more runs/day × watchlist size. Each doc carries ~25 fields (`day_high`, `vwap`, `t1/t2/t3`, `progress_pct`, …) at roughly 500–700 bytes, before index overhead.

So on a 512 MB cluster, **one collection of data you will never read again** consumes a third of your quota per year.

**And the mitigation made it worse.** To free space you added scheduled deletions — but the blunt instruments available are `clear_all_scans()` ([intraday/service.py:558](../features/intraday/service.py#L558)), `DELETE /performance/recommendations/all`, `DELETE /performance/alerts/all`, `DELETE /portfolio/positions/all`. Those delete *everything* in the collection with no tiering, no dry-run, and no protection. `performance_log` **is** your track record — every recommendation, entry, target, stop and outcome. Once it's gone you cannot recompute it from anywhere, and every performance statistic resets to zero.

**The asymmetry that matters:** the data worth keeping forever is tiny. 200 closed swing trades a year with full context ≈ **300 KB/year**. The data crushing your cluster is per-minute snapshots you'd never open. You have been deleting the 300 KB to make room for the 150 MB.

**What fixes it — cadence, not features.** The monitoring itself is worth keeping; writing a row every minute is not. Three changes remove ~99% of the volume while keeping everything you can see today:

| Change | Effect |
|---|---|
| Per-minute scan → **3 in-session checks** (11:30, 14:00, 15:10) + EOD | 960 writes/day → 4 |
| Write **only on state change** (fill, target, stop, trailing-stop move, invalidation) | quiet days cost 0 rows |
| Store the daily series as a **bounded array inside the position** (≤10 entries) | a position can never exceed ~1.5 KB |

That's ~2–5 documents a day instead of ~960 — from ~150 MB/year down to well under 1 MB/year, with the alerts and history you actually use fully intact. Then replace the blunt delete-all endpoints with a tiered retention system plus the age-based "clear anything older than N days" control.

**Good news — you already have the right UX.** The `/memory <days>` command with its 7/14/30/365 buttons and `purge_old_turns()` ([bot/handlers.py:80-132](../features/bot/handlers.py#L80-L132), [chat_memory/service.py:198-215](../features/chat_memory/service.py#L198-L215)) is exactly the pattern needed — it just only governs `chat_history` today. Generalising it across every collection, with a dry-run preview and a protected tier for your trade record, is most of the work. Full design in `FEATURES.md` F2, build steps in `IMPLEMENTATION.md` Phases 1–2.

## W2. No swing lifecycle — a 2–10 day trade is graded the same afternoon

A recommendation issued 09:20 IST is graded at 15:35 the *same day* by `evaluate_day` ([performance/service.py:54-134](../features/performance/service.py#L54-L134), called at [scheduler/service.py:522-527](../features/scheduler/service.py#L522-L527)). A trade meant to run 2–10 days is marked `FAIL` — "Target ❌ Missed" — on day one.

Everything else follows from that:

| What happens | Where | Why it's wrong |
|---|---|---|
| Watchlist regenerated from scratch each morning | [scheduler/service.py:118-131](../features/scheduler/service.py#L118-L131) | Monday's picks vanish Tuesday; nothing re-analyses an open position |
| Tracking reads only *today's* watchlist | [intraday/service.py:29-35](../features/intraday/service.py#L29-L35) | A position opened Monday stops being followed Tuesday |
| `hold_duration` defaults to `'Intraday'` | [intraday/service.py:240](../features/intraday/service.py#L240) (function default), [:134](../features/intraday/service.py#L134) and [:178](../features/intraday/service.py#L178) (`.get(..., 'Intraday')`) | Alert copy says "Square off before 3:10 PM" for a 5-day hold |
| Manual tracks graded intraday-style | [intraday/service.py:102-116](../features/intraday/service.py#L102-L116) | Even your own tracked trades get judged on one session |

**Consequence:** `hit_rate_pct` ([performance/service.py:137-161](../features/performance/service.py#L137-L161)) is systematically pessimistic and tells you nothing. You cannot improve what you cannot measure. There is no concept anywhere of a position that is 4 days old with 6 days left.

## W3. No authentication, and the destructive endpoints are the dangerous ones

No `Depends`, no API key, no bearer check in any router (verified by grep across `features/*/router.py`). Anyone with your Render URL can call:

| Endpoint | Damage |
|---|---|
| `DELETE /performance/recommendations/all` | **wipes your track record** |
| `DELETE /performance/alerts/all` | wipes report history |
| `DELETE /intraday/scans/all`, `DELETE /portfolio/positions/all` | wipes position data |
| `POST /gemini/generate` | burns your free-tier quota (1,500 req/day) |
| `POST /notify` | spams your phone |
| `POST /intraday/track` | injects fake positions |
| `GET /portfolio/positions` | reads your holdings |

CORS ([main.py:83-96](../main.py#L83-L96)) does **not** protect you — CORS is a browser convention; curl and Postman ignore it. Combined with W1, the most damaging endpoints on this server are both unauthenticated *and* un-guarded: no dry-run, no confirmation, no distinction between "clear old scans" and "delete my trading history". One static token plus a tiered cleanup API closes both.

## W4. The LLM outranks your own math

The pipeline computes precise numbers, then instructs the model to override them:

- **"Gemini live_price is the authoritative CMP"** ([scheduler/service.py:463](../features/scheduler/service.py#L463)).
- The research step tells Gemini to declare *itself* more accurate on >2% divergence: "use YOUR researched live price as authoritative" ([scheduler/service.py:346-349](../features/scheduler/service.py#L346-L349)).
- `_gemini_research_stock` asks the model to **produce** RSI, MACD, EMA trend, OHLCV, delivery %, Wyckoff phase from web search ([scheduler/service.py:353-369](../features/scheduler/service.py#L353-L369)) — while [technical_indicators.py](../features/market_data/technical_indicators.py) already computes those exactly from OHLCV.
- Both conflicting sets get pasted into the same prompt ([scheduler/service.py:443-452](../features/scheduler/service.py#L443-L452)) and the model is asked to reconcile them.
- `cross_check` detects divergence but the verdict is cosmetic — nothing aborts, nothing is corrected ([market_data/service.py:73-93](../features/market_data/service.py#L73-L93)).

**Consequence:** your entry, target and stop can be anchored to a hallucinated price. For a swing trade sized off an ATR stop, one wrong CMP corrupts the stop level, the position size and the R:R simultaneously. Highest-value fix in the audit, and it costs nothing.

## W5. No entry-fill logic — grading assumes fills that never happened

- `evaluate_day` parses the entry zone into `entry_low, entry_high` and then **never uses them** ([performance/service.py:74](../features/performance/service.py#L74)). Pass = "target hit AND stop not hit" — even if price never traded into the recommended entry zone.
- `log_virtual_trade` hardcodes `trade_size: 20000` and uses morning CMP as the entry ([portfolio/service.py:7-26](../features/portfolio/service.py#L7-L26)).

So a "win" may be a trade you were never filled on, and a "loss" may be one you'd never have entered.

> **Scope note:** the virtual portfolio is **kept** and converted into a swing paper portfolio (W13, `FEATURES.md` F19) — it is *not* deleted. What must change is the instant-fill-at-CMP assumption and the hardcoded ₹20,000 size. The same flaw must not be carried into real swing tracking.

---

# HIGH

## W6. `pandas_ta` is imported but never installed → the weekly trend has never once worked

`technical_indicators.py:49` imports `pandas_ta`; it is **absent from `requirements.txt`** (verified).

1. **Weekly trend is permanently broken.** The weekly-EMA block does its own `import pandas_ta` inside a `try` with a bare `except: pass` ([technical_indicators.py:148-157](../features/market_data/technical_indicators.py#L148-L157)), so `weekly_trend` has returned `"N/A"` on every run since it was written. For 2–10 day trades the weekly trend is one of your highest-value filters, and it has never fired.
2. **RSI doesn't match your charts.** The fallback uses a simple rolling mean ([technical_indicators.py:95-99](../features/market_data/technical_indicators.py#L95-L99)) instead of Wilder's smoothing — so values differ from TradingView/Zerodha, exactly what you'd sanity-check against.
3. **Your ATR stop is slightly wrong.** Fallback ATR uses SMA, not Wilder's ([technical_indicators.py:121](../features/market_data/technical_indicators.py#L121)) — and `atr_stop_loss_1_5x` is handed to the model as the stop baseline ([scheduler/service.py:459](../features/scheduler/service.py#L459)).

Note: adding `pandas_ta` won't work either — the classic release breaks on `numpy>=2` (`from numpy import NaN`) and you pin `numpy==2.5.1`, `pandas==3.0.3`.

## W7. NSE-dependent features fail silently in the cloud

`_compute_option_chain` (nsepython) and `_compute_fii_dii` (direct `nseindia.com/api/fiidiiTradeReact`) are scrapers NSE routinely blocks from datacenter IPs. Your own code documents this for news: "Moneycontrol (all feeds — 403 from cloud IPs)" ([news_fetcher.py:6-9](../features/market_data/news_fetcher.py#L6-L9)).

On failure the prompt just receives `[Option Chain] Unavailable` / `[FII/DII Flows] Unavailable` ([technical_indicators.py:441](../features/market_data/technical_indicators.py#L441), [:458](../features/market_data/technical_indicators.py#L458)) and **the report is generated anyway** — with no signal that PCR, max pain, OI walls and institutional flows were all missing. Your own QMAF framework forbids exactly this ([qmaf_v2_personalized.md:791](../features/intraday/templates/qmaf_v2_personalized.md#L791)). No endpoint shows per-source success rates.

## W8. UTC dates in an IST market

Every date key is UTC-derived: `datetime.now(timezone.utc).strftime("%Y-%m-%d")` across scheduler, intraday, performance and portfolio services.

- Between **00:00–05:30 IST the UTC date is still yesterday**, so `daily_watchlist`, `performance_log` and scan keys can disagree between routines.
- Alerts show UTC clock time: `"%I:%M %p UTC"` ([intraday/service.py:133](../features/intraday/service.py#L133), [scheduler/service.py:546](../features/scheduler/service.py#L546)) — "10:05 AM UTC" during a 15:35 IST session.
- `now.weekday() >= 5` in UTC can misclassify early-Saturday IST.

## W9. No NSE trading-holiday awareness

Only weekends are skipped ([scheduler/service.py:106](../features/scheduler/service.py#L106), [:481](../features/scheduler/service.py#L481), [:572](../features/scheduler/service.py#L572), [:580](../features/scheduler/service.py#L580)). On ~15 annual NSE holidays the morning routine still burns Gemini quota, generates a confident report from stale closing data, and writes rows you'll later have to clean up. NSE publishes a free holiday endpoint; nothing consumes it.

## W10. Recommendation parsing is regex-over-markdown and silently loses trades

`_parse_recommendation_fields` ([scheduler/service.py:605-620](../features/scheduler/service.py#L605-L620)) regexes prose for Recommendation/Entry/Target/Stop-Loss, while `prompt.txt:243-249` asks for a **markdown table** with T1/T2/T3.

- **No pattern for T1/T2/T3** — `log_recommendation` never stores them ([performance/service.py:32-48](../features/performance/service.py#L32-L48)), so staged profit-booking is impossible for AI trades.
- `target` grabs the first number-ish match after "Target", which in table format may catch the wrong row.
- On parse failure, `evaluate_day` writes `result: "SKIPPED"` with "AI formatting error" ([performance/service.py:80-84](../features/performance/service.py#L80-L84)) — the trade silently vanishes from your stats.
- The broadcast path then does fragile table→text regex munging and HTML-entity juggling ([scheduler/service.py:240-265](../features/scheduler/service.py#L240-L265)), truncating at 3,800 chars.

Unnecessary: Gemini supports `responseMimeType: "application/json"` + `responseSchema`, and on Gemini 3 structured output combines with Search grounding. Structured storage is also **~10× smaller** than storing prose — a direct storage win (W1).

---

# MEDIUM

## W11. Two contradictory prompt frameworks, and the worse one runs your reports

| | `prompt.txt` (283 lines) | `qmaf_v2_personalized.md` (1,900+ lines) |
|---|---|---|
| Stance | "You **NEVER** refuse to analyze stocks", "MUST NOT use generic disclaimers", "Always be confident" ([prompt.txt:4-6](../prompt.txt#L4-L6)) | "Prefer WAIT/NO TRADE", "Never force a directional call", data integrity outranks all ([qmaf_v2_personalized.md:1029-1043](../features/intraday/templates/qmaf_v2_personalized.md#L1029-L1043)) |
| Used by | **the morning routine** ([scheduler/service.py:404](../features/scheduler/service.py#L404)) | only the manual-track AI update ([intraday/service.py:452-457](../features/intraday/service.py#L452-L457)) |

Your **better** framework — horizon discipline (it already defines "Short Swing: 2–10 trading days" as the default, [qmaf_v2_personalized.md:484-490](../features/intraday/templates/qmaf_v2_personalized.md#L484-L490)), entry gates, baseline-relative delivery %, the SIP/ETF engine, WAIT/NO TRADE — is **not** the one generating your daily recommendations. Meanwhile "always be confident, never refuse" is a direct instruction to produce a call when the honest answer is "nothing today". For swing trading, sitting out is most of the edge.

## W12. Model IDs are stale and unvalidated, so failures burn quota

- `DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"` ([core/config.py:15](../core/config.py#L15)) — real, but a generation behind: **`gemini-3.6-flash` is the stable Flash since 21 July 2026**.
- The root `models` file offers `gemini-2.5-pro` — **Pro models left the Gemini free tier on 1 April 2026**, so on a free key every call fails. `core/config.py:23-28` also offers `gemini-3.1-flash` / `-flash-lite`, unverified.
- `generate_with_gemini_fallback` loops models × keys treating all errors alike ([gemini/service.py:218-237](../features/gemini/service.py#L218-L237)). A 404 "unknown model" costs one failed call *per key*; a 429 doesn't cool the key down, so the next attempt retries the exhausted one first.
- Nothing validates IDs against `GET /v1beta/models` at startup.

## W13. Everything is kept, but three things are mis-tuned for swing

No feature gets deleted. Three are tuned for intraday and must be re-tuned:

| Component | Problem | Convert to |
|---|---|---|
| `custom_stock_minute_scan` ([scheduler/service.py:585-601](../features/scheduler/service.py#L585-L601)) | fires **every minute**, writes unconditionally → the storage bomb (W1) | 3 in-session checks + EOD, **event-only writes** |
| `intraday_scan_routine` ([:569-576](../features/scheduler/service.py#L569-L576)) | 12 runs/day tuned to intraday exits, reads only *today's* watchlist | daily swing tracker reading all **open positions** |
| `virtual_portfolio` ([portfolio/service.py](../features/portfolio/service.py)) | instant fill at CMP, hardcoded `trade_size: 20000`, no targets/stops/time-exit (W5) | swing **paper portfolio**: entry-zone fill simulation, real ATR sizing, T1/T2/T3 partials, 10-day cap, R-multiples |

The paper portfolio is worth keeping precisely *because* you trade swing — it's how you measure whether the AI's picks are worth following before committing money, and at one bounded document per paper trade it costs almost nothing to store.

Also mis-tuned, same root cause: `hold_duration` defaults to `'Intraday'` and alert copy says "square off before 3:10 PM" ([intraday/service.py:147-154](../features/intraday/service.py#L147-L154)) — for a swing hold the correct actions are trail/partial/hold-overnight.

Genuinely dead or duplicated (safe to delete):
- Root **`models`** file duplicates `AVAILABLE_MODELS` with *different* values than `core/config.py` — two sources of truth.
- **`bot_flow`** is a 0-byte file.
- `core/config.py:11` accepts the misspelling `connetion_string` — a typo promoted to an interface.
- `retrieve_rag_chunks` (vector search) unreachable — nothing generates embeddings ([knowledge_base/service.py:7-44](../features/knowledge_base/service.py#L7-L44)).
- `grok.get_sentiment` prompts for "x_twitter_analysis" ([grok/service.py:115-118](../features/grok/service.py#L115-L118)) from a model with **no X access** — it invents data, and it's exposed at `POST /grok/sentiment`.
- `log_recommendation` stores `market_data_snapshot` but not the technicals/option-chain/FII-DII that drove the call — so you can't audit *why* a past call was made (and it stores 4–8 KB of prose you don't need).

## W14. RAG retrieval is keyword-shaped and probably contributing nothing

- The scheduler queries `[symbol, wyckoff_phase, "volume spread", "support resistance"]` ([scheduler/service.py:426](../features/scheduler/service.py#L426)) against a `$text` index over **generic theory chunks**. "RELIANCE" appears nowhere in your Wyckoff notes — it just dilutes the query.
- `get_simple_rag_chunks` swallows every exception and returns `[]` ([knowledge_base/service.py:65-67](../features/knowledge_base/service.py#L65-L67)), so a missing index looks identical to "nothing relevant". Nothing verifies at startup that `knowledge_chunks` is populated.
- The indexer only globs `*.yaml` ([indexer.py:83](../features/knowledge_base/indexer.py#L83)) — your three PDFs in `docs/` (Wyckoff Analytics, Zerodha chart trends, mtm, ~12 MB) are **never indexed**. Good news for storage: keep them as local files, don't push them to Atlas.
- The 18 YAML modules are genuinely useful (`13_Risk_Management.yaml` encodes the 0.5%/1%/2% sizing rules the bot should enforce) — they're just not reaching the model reliably.

## W15. Reliability and observability gaps

- **Silent aborts.** Empty watchlist → bare `return` ([scheduler/service.py:120-122](../features/scheduler/service.py#L120-L122)) with no Telegram, no ntfy. You can't tell "no trades today" from "the job crashed".
- **Hosting risk.** APScheduler is in-process with no persistent jobstore. Render free web services **spin down after 15 min idle** and cold-start in 30–60s; outbound Telegram polling isn't inbound traffic, so it won't keep the app awake. On the free plan your 09:20 job is not reliably firing.
- **Fire-and-forget bot.** `dp.start_polling` launched after a 5s sleep with a broad `except` that only prints ([main.py:53-61](../main.py#L53-L61)) — if polling dies, the bot goes silent with no alert.
- **No idempotency.** A double cron fire or manual trigger inserts duplicate rows; alert dedupe relies on a query ([intraday/service.py:122-128](../features/intraday/service.py#L122-L128)) with no unique index behind it.
- **No storage monitoring.** Nothing reads `dbStats`, nothing warns you at 80% of 512 MB. You find out by hitting the wall — which is what led to the blind deletions.
- `print()` only, no persisted run log; `datetime.utcnow()` (deprecated) at [market_data/service.py:142](../features/market_data/service.py#L142); `int(USER_ID)` evaluated at **import time** in decorators ([bot/handlers.py:63](../features/bot/handlers.py#L63)) so a missing env var breaks app import.

## W16. No tests, no backtest — the edge has never been measured

No test file exists. The three things that decide whether you make money — indicator math, recommendation parsing, outcome grading — are unverified, and two are known-broken (W6, W10). And nothing tells you whether your entry rules have positive expectancy over a 2–10 day hold. Storage-friendly, too: a backtest run locally costs the cluster nothing but a few KB of results.

## W17. The SIP / Gold-ETF side exists only as prompt text

Section 21 of the v2 prompt describes your exact setup — three autopay SIPs (Navi Nifty 50, Parag Parikh Flexi Cap, MO Nifty Midcap 150) and dip-buy ETFs GOLDBEES + MON100 ([qmaf_v2_personalized.md:1234-1350](../features/intraday/templates/qmaf_v2_personalized.md#L1234-L1350)). But:

- **No code touches any of it.** No mutual-fund NAV source exists in the project. No job evaluates dip conditions. No monthly reminder. No allocation tracking.
- It only "works" if you ask in chat — and the model then has no NAV or price data, so it answers from memory.
- MON100 needs three things separated (Nasdaq-100 move, USD/INR move, ETF premium/discount) as the prompt itself demands — none of those inputs are fetched. MON100 has historically traded at large premiums; buying blind there costs real money.
- Free keyless sources exist for all of it (mfapi.in / AMFI for NAV; yfinance for `GOLDBEES.NS`, `MON100.NS`, `^NDX`, `INR=X`), and the storage footprint is trivial — a few KB per month.

---

## W18. The analysis stops at retail level

This is the gap between "an AI that talks like an analyst" and "an analyst". The prompt *lists* professional concepts — Wyckoff, VSA, Market Profile, RS line slope, IV rank, PEG vs 5-year median, ROC, Supertrend — but **the code computes none of them**, so the model narrates them from vibes. What's actually computed is RSI, MACD, EMA/SMA, Bollinger, ATR and volume ratio: a competent retail toolkit, and all of it direction-only.

The most damaging specific omissions for a 2–10 day hold:

| Missing | Why it matters for swing | Free? |
|---|---|---|
| **ADX / DI±** — trend *strength* | RSI and MACD are noise in a chop market. Without ADX there is no filter that says "this stock isn't trending, skip it". Probably the single highest-value addition. | ✅ from OHLCV |
| **RS Rating (percentile rank vs universe)** | The best-documented swing factor there is. You compute a raw ratio at best; professionals rank the whole universe and buy the top decile. | ✅ free from screener data |
| **Algorithmic swing pivots** | Your own rule is "stop below the last swing low" — nothing detects swing lows, so stops are ATR-only or invented. | ✅ from OHLCV |
| **Volume profile (POC / VAH / VAL)** | `prompt.txt:65` promises Market Profile. Real volume-at-price beats round-number support. | ✅ computable |
| **Effort vs result (quantified VSA)** | Wyckoff/VSA is claimed everywhere and computed nowhere. `range/ATR` vs `volume/avg` classifies absorption, no-demand, climax numerically. | ✅ |
| **PE vs 5-year median, real PEG** | Your valuation gate ([prompt.txt:103](../prompt.txt#L103)) *requires* this figure and nothing produces it — so the gate is decided by guesswork. | ✅ |
| **Earnings surprise + post-earnings drift** | PEAD is one of the few documented multi-week edges, and it fits a 2–10 day window exactly. | ✅ |
| **Base/consolidation detection, 52-week-high proximity** | Defines the entry zone and the breakout level; momentum near 52wk highs is well documented. | ✅ |
| **IV rank/percentile, futures basis, rollover** | Demanded by the prompt for derivative context; not computed. | ✅ w/ stored chain |
| **MAE/MFE, expectancy, fractional Kelly** | Tells you whether your stops are too tight and your targets too far — the feedback loop that improves a system. | ✅ from journal |

Full formulas, thresholds and interpretation bands: **`ANALYTICS.md`**.

## W19. No timestamp or data-freshness discipline

Nothing in the system knows how old its own inputs are, and the reports never say.

- `fetch_for_verification` stamps `timestamp` ([market_data/service.py:142](../features/market_data/service.py#L142)) but the deep-recommendation prompt never receives it, so the model can't distinguish a live price from a three-day-old close.
- yfinance NSE data is delayed; the model is nonetheless told the price is authoritative (W4) and the report header asks it to print `Status: Live / Closed` ([prompt.txt:219](../prompt.txt#L219)) — a field it has no way to determine.
- There is **no market-session state** anywhere (pre-open / open / closed / holiday), so a Sunday report reads exactly like a Tuesday 09:20 report.
- Indicators are computed from the last *daily* close but presented alongside "live" research with no distinction.
- On a holiday or after a failed fetch, stale values flow through silently (W7, W9) — there's no rule that says "inputs older than X ⇒ downgrade confidence" or "⇒ refuse to trade".
- Your own framework demands exactly this discipline — data-state classification, timestamp disclosure, never claiming live data that wasn't retrieved ([qmaf_v2_personalized.md:201-249](../features/intraday/templates/qmaf_v2_personalized.md#L201-L249)) — and the code gives it nothing to work with.

Fix: every input carries `{value, source, captured_at, age_seconds}`, a per-datatype freshness budget, and a session-state machine. Spec in `ANALYTICS.md` §G, prompt rules in `KNOWLEDGE_AND_PROMPTS.md`.

## W20. The YAML knowledge base isn't machine-usable

Your 18 modules in `docs/` are good study material, but as RAG for a decision engine they underperform for three separate reasons:

1. **They're theory without numbers.** `03_Wyckoff_Method.yaml` explains accumulation phases in prose; it doesn't say "classify absorption when range < 0.8×ATR and volume > 1.8×avg". A frontier model already knows the theory — retrieving prose adds tokens, not accuracy. The exception is `13_Risk_Management.yaml`, which *does* carry numbers (0.5/1/2%) and is exactly the right shape.
2. **Nothing covers this system.** No module encodes your swing rulebook, your entry gates, your freshness budget, your sizing caps, your SIP/ETF policy, or — most valuably — the mistakes this system has already made.
3. **Retrieval can't find them anyway** (W14): the query is polluted with the ticker symbol, chunks carry no tags, and failures return `[]` silently.

Fix: keep the theory as-is, **add 7 machine-usable modules** (thresholds, rules, this system's own policy and failure library), tag every chunk, and query by intent instead of by symbol. Drop-in YAML content is written out in **`KNOWLEDGE_AND_PROMPTS.md`**.

---

## Fix order (highest value per hour)

| # | Fix | Effort | Why in this position |
|---|---|---|---|
| 1 | W3 API auth + guarded cleanup | ~1 h | The delete-all endpoints are open to the internet right now |
| 2 | W13 re-tune cadence to event-only writes | ~3 h | Kills ~99% of storage growth while keeping every feature |
| 3 | W1 tiered retention + `/storage` control | ~4 h | Stops blind deletion of your track record permanently |
| 4 | W4 invert data authority | ~2 h | Stops hallucinated prices from setting your stops |
| 5 | W6 indicator math + weekly trend | ~2 h | Restores a filter that has never once worked |
| 6 | W2 + W5 swing lifecycle (real + paper) | ~1 day | Makes the system match how you actually trade |
| 7 | W19 timestamps + freshness budget | ~3 h | Every later improvement is worthless on stale inputs |
| 8 | W10 structured JSON output | ~3 h | Kills the SKIPPED failure class *and* shrinks docs ~10× |
| 9 | **W18 expert analytics tier 1** (ADX, RS rank, swing pivots, PE-vs-median) | ~1 day | The actual jump from retail to professional analysis |
| 10 | W8 + W9 IST dates + holidays | ~2 h | Removes date-key bugs and junk holiday rows |
| 11 | W11 + W20 one prompt + machine-usable knowledge | ~4 h | Removes the BUY bias; makes RAG contribute for the first time |
| 12 | W15 failure alerts + storage alerts | ~3 h | You learn about problems before they cost you |
| 13 | W17 SIP/ETF engine | ~1 day | Half your portfolio has zero automation |
| 14 | W18 expert analytics tier 2 (volume profile, VSA, PEAD, IV rank) | ~2 days | Depth, once the foundations hold |
| 15 | W16 backtest (run locally) | ~2 days | Tells you if the edge is real, at no storage cost |

Capabilities → `FEATURES.md`. Build steps → `IMPLEMENTATION.md`. Formulas → `ANALYTICS.md`. Knowledge/prompt content → `KNOWLEDGE_AND_PROMPTS.md`.
