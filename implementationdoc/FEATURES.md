# StockAI Backend — Feature Roadmap

**Written:** 3 September 2026
**For:** one user (you). **Swing only: 2–10 days, 10 = hard cap.**
**Also managing:** 3 autopay SIPs, GOLDBEES + MON100 dip-buying.
**Constraint:** free resources only, and **MongoDB Atlas M0 = 512 MB** — storage is a first-class design concern, not an afterthought.

---

## Scope decisions (confirmed)

**Nothing is deleted. Everything is converted to swing duration.**

| Existing feature | Converted to |
|---|---|
| Manual position tracking (`/intraday/track`) | ✅ **Kept** — first-class swing entry path, tracked daily for up to 10 days |
| In-session monitoring + target/SL alerts | ✅ **Kept** — 3 checks/day (11:30, 14:00, 15:10) + EOD, **writing only on state change** |
| Virtual portfolio (`virtual_portfolio`) | ✅ **Kept** — becomes a swing **paper portfolio** with entry-zone fills, ATR sizing, partials, 10-day cap (F19) |
| Same-day PASS/FAIL grading | ✅ Grading **on close**, by R-multiple |

The storage problem was the **write cadence**, not the features: ~960 rows/day becomes ~2–5, so ~150 MB/year becomes under 1 MB/year with every alert and history view intact (`WEAKNESSES.md` W1).

**Two new pillars** requested for this round, both critical: **F17 expert analytics** (professional-grade calculations — full spec in `ANALYTICS.md`) and **F18 data freshness & timestamp discipline**. Knowledge-base and prompt rewrites are in `KNOWLEDGE_AND_PROMPTS.md`.

---

## Priority map

Ordered by priority. F-numbers are stable identifiers (F17–F19 are new this round, not lower priority).

| # | Feature | Impact | Effort | Storage cost |
|---|---|---|---|---|
| F1 | Swing position lifecycle (+ manual tracking as entry path) | 🔴 Critical | 1 day | ~300 KB/yr |
| F2 | **Storage & retention manager** (clear >N days, from bot + UI) | 🔴 Critical | 5 h | negative — frees space |
| **F17** | **Expert analytics engine** — ADX, RS rank, swing pivots, volume profile, quantified VSA, PE-vs-median, PEAD | 🔴 Critical | 2 days | ~200 KB/yr |
| **F18** | **Data freshness & timestamp discipline** | 🔴 Critical | 4 h | ~0 |
| F3 | Position sizing + portfolio risk | 🔴 Critical | 4 h | ~0 |
| **F19** | **Swing paper portfolio** (converted `virtual_portfolio`) | 🟠 High | 4 h | ~150 KB/yr |
| F4 | Deterministic swing screener | 🟠 High | 1–2 days | ~600 KB/yr |
| F5 | Regime filter | 🟠 High | 4 h | ~50 KB/yr |
| F6 | Event & earnings guard | 🟠 High | 4 h | ~100 KB/yr |
| F7 | SIP / mutual fund tracker | 🟠 High | 6 h | ~15 KB/yr |
| F8 | GOLDBEES / MON100 dip engine | 🟠 High | 6 h | ~50 KB/yr |
| F9 | Backtest & expectancy (**runs locally**) | 🟠 High | 2 days | ~20 KB total |
| F10 | Trade journal (net of costs) | 🟡 Medium | 6 h | ~200 KB/yr |
| F11 | Two-model agreement gate | 🟡 Medium | 3 h | ~0 |
| F12 | Institutional footprint tracker | 🟡 Medium | 4 h | ~100 KB/yr |
| F13 | Alert quality: charts, digests, buttons | 🟡 Medium | 6 h | ~0 |
| F14 | Data-source health | 🟡 Medium | 3 h | capped 1 MB |
| F15 | Telegram command surface | 🟢 Nice | 4 h | ~0 |
| F16 | Broker API as data feed (free w/ account) | 🟠 High | 1 day | ~0 |

**Everything above, running for five years, fits in about 35 MB** — 7% of your free cluster. The current design fills it in roughly two years with per-minute snapshots you never read.

---

# F1 — Swing position lifecycle

**The flagship.** One collection, one daily job, positions that live 2–10 days. This absorbs your manual tracking feature rather than replacing it.

### Two ways a position is created

1. **From an AI recommendation** — validated JSON becomes a `PENDING_ENTRY` position.
2. **Manually (kept, per your decision)** — you send entry/target/SL from Telegram or the UI, exactly like today's `/intraday/track`, and it enters the same lifecycle. Same tracking, same grading, same stats — the only difference is who chose the trade.

### `swing_positions` document

```
symbol, direction, source ("AI" | "MANUAL"), status
  PENDING_ENTRY → OPEN → CLOSED   (or CANCELLED if never filled)

entry_zone_low / entry_zone_high, entry_valid_until
filled, fill_date, fill_price
t1, t2, t3, stop_loss, trailing_stop
qty, capital_deployed, risk_amount
opened_on, max_hold_days (default 10), days_held
thesis, invalidation_price, invalidation_event, setup_type
partial_exits[]          # {date, price, pct, reason}
daily[]                  # ← bounded to ≤10 entries: {d, close, r, stop}
entry_snapshot           # ~15 numbers that justified the trade (NOT the prose)
close_date, close_price, close_reason, r_multiple, pnl_pct, pnl_inr
```

**Storage note:** the daily history lives as a **bounded array inside the position** (max 10 entries, ~40 bytes each), not as a separate per-day collection. A position can never grow past ~1.5 KB by construction — the opposite of the current per-minute insert pattern.

### Sub-features

- **Fill simulation** — `PENDING_ENTRY` becomes `OPEN` only when the day's range actually touches your entry zone. Never touched within 2 days → auto-`CANCELLED` ("setup expired"). This alone makes your hit-rate honest.
- **Daily tracker** (one job, ~15:45 IST) — refresh close, days held, R-multiple, distance to target/stop, trailing stop, invalidation check.
- **Trailing stops** — breakeven at +1R; then trail below prior swing low or EMA20; ratchets up only.
- **Staged exits** — 40% at T1, 40% at T2, trail the last 20% (your prompt already specifies this; nothing implements it).
- **Hard time exit at day 10** — a `TIME_EXIT_DUE` alert asks you to decide; it never auto-sells. This is your QMAF horizon cap made real.
- **Pre-open gap check (09:10 IST)** — if the likely open is beyond your stop, you hear about it before 09:15 instead of after.
- **Optional intraday safety net** — *two* checks a day (12:00, 15:10) purely to alert on stop/target breach, and **writing only when status changes**. Not a scanner: no rows on quiet days. Leave it off if you'd rather have EOD-only.
- **Grading on close** — R-multiple and reason (`TARGET` / `STOP` / `TIME_EXIT` / `THESIS_BROKEN` / `MANUAL`), replacing same-day PASS/FAIL.

Then rebuild stats on closed positions: win rate, **average R**, **expectancy** = (win% × avgWin_R) − (loss% × avgLoss_R), profit factor, average holding days, split by `setup_type` and by `source` (AI vs your own picks — you'll want to know which is better).

---

# F2 — Storage & retention manager

This is the feature you asked for, generalized. The insight worth internalizing:

> **What's worth keeping is tiny. What fills your cluster is worthless.**
> 200 closed trades a year with full context ≈ 300 KB. Per-minute scan snapshots ≈ 150 MB/year. You've been deleting the 300 KB to make room for the 150 MB.

### Three tiers

| Tier | Contents | Policy | 5-yr size |
|---|---|---|---|
| 🔒 **SACRED** | `swing_positions` (closed), `trade_journal`, `monthly_rollups`, `sip_contributions`, `risk_config`, `knowledge_chunks`, recommendations (structured JSON only) | **Never auto-deleted.** Age-based clear skips these unless you type an explicit confirmation | ~10 MB |
| 🔄 **ROLLING** | `processed_news` (3 d), `chat_history` (25 d, already ✓), `morning_alerts` prose (45 d), `screener_scores` (30 d), `job_runs`, `news_alerts`, `source_health` | TTL indexes + capped collections — expires itself, no maintenance | ~12 MB steady |
| 📉 **DERIVED** | Anything recomputable: OHLCV history, backtest inputs, indicator series | **Not stored on Atlas at all** — recomputed, or cached locally on your PC | ~0 |

### Age-based cleanup — the control you asked for

Generalizes the `/memory <days>` pattern your bot already has ([bot/handlers.py:80-132](features/bot/handlers.py#L80-L132)) so it works across every collection:

**Telegram — `/storage`**

```
💾 Storage — 34.2 MB / 512 MB (6.7%)

Top consumers
  chat_history      12.1 MB   TTL 25d
  morning_alerts     8.4 MB   TTL 45d
  processed_news     4.2 MB   TTL 3d
  swing_positions    0.3 MB   🔒 sacred
  trade_journal      0.2 MB   🔒 sacred

[ Clear > 30 days ]  [ Clear > 60 days ]
[ Clear > 90 days ]  [ Custom days… ]
[ Per collection… ]  [ Clean expired now ]
```

**`/storage 60`** → dry-run preview first, always:

```
Would delete (older than 60 days):
  morning_alerts    142 docs   6.1 MB
  news_alerts        88 docs   0.4 MB
  screener_scores   900 docs   0.5 MB
  job_runs          410 docs   0.2 MB
  ─────────────────────────────────────
  total             1,540 docs  7.2 MB

🔒 Protected, untouched:
  swing_positions (203), trade_journal (188), monthly_rollups (14)

[ Confirm delete ]   [ Cancel ]
```

**Same from the UI**, via the API:

| Endpoint | Purpose |
|---|---|
| `GET /storage/stats` | per-collection size, docs, tier, retention, % of 512 MB, projected days to full |
| `POST /storage/cleanup` | `{older_than_days: 60, collections: [...] \| "all_non_sacred", dry_run: true}` |
| `GET / PUT /storage/retention` | view/edit retention days per collection |
| `POST /storage/rollup` | force monthly aggregation now |
| `DELETE /storage/collection/{name}` | drop a whole rolling collection (fastest space reclaim) |

### The rules that keep it safe

- **Dry-run is the default.** Nothing deletes without a preview and a confirmation.
- **Sacred collections are never included** in a "clear > N days" sweep. Deleting them requires naming the collection *and* typing a confirmation phrase. This is the guardrail that was missing when your track record got cleared.
- **Aggregate before delete.** A monthly rollup job writes `{month, trades, win_rate, avg_r, expectancy, profit_factor, best, worst, by_setup}` — one ~2 KB document — *before* any detail is purged. You keep the statistics forever and lose only the raw rows.
- **Prefer TTL over batch deletes.** TTL removes documents continuously so freed space gets reused. Big one-shot deletes don't return space to the OS, and shared Atlas tiers don't allow `compact`. Where you truly need space back fast, **drop the collection** rather than deleting its documents.
- **Capped collections** for logs (`job_runs`, `source_health`, `news_alerts`): fixed byte ceiling, oldest overwritten automatically, cannot ever grow. Zero maintenance.

### Additional storage wins

- **Store structured, not prose.** `performance_log.raw_ai_output` holds 4–8 KB of report text per recommendation. The structured JSON (F1/`IMPLEMENTATION.md` 2.2) carries everything you actually query in ~800 bytes. Keep the prose 45 days in `morning_alerts`, then let TTL take it. **~10× reduction on your second-biggest collection.**
- **Telegram is free cold storage.** Every full report is already sent to your chat and Telegram keeps it forever at no cost. Mongo doesn't need to be the archive — it needs to be the index.
- **Keep the PDFs local.** The three PDFs in `docs/` (~12 MB) must never go into Atlas; the indexer already only reads `*.yaml` — keep it that way.
- **Never cache OHLCV on Atlas.** Nifty 500 × 5 years ≈ 100–150 MB, a third of your quota, for data yfinance will hand you again for free. Compute nightly in memory; cache locally as Parquet if you want backtest speed.
- **Capacity monitor** — daily `dbStats` check: warn at 70%, urgent at 85%, and auto-run the *safe* sweep (rolling tier only) at 90%. You never discover the limit by hitting it again.
- **Bandwidth awareness** — M0 also caps network transfer at 10 GB in / 10 GB out per week. Another reason backtests read local files, not Atlas.

---

# F3 — Position sizing + portfolio risk

Pure arithmetic, no data cost, and probably the highest-₹ item here. Your own `docs/13_Risk_Management.yaml` already encodes the rules (0.5% / 1% / 2%, "survive first, profit second") — nothing enforces them.

- **Sizing calculator** — capital, risk %, entry, ATR stop → exact quantity, ₹ at risk, capital deployed, R:R to each target. Refuses if R:R to T1 < 1:2 (your QMAF mandate).
- **Portfolio heat** — total open risk across positions, capped (e.g. 5% of capital). A new entry that breaches the cap is flagged or blocked, not silently added.
- **Concentration caps** — max positions (5), max per sector (2), max single-stock exposure (15%).
- **Correlation check** — 60-day correlation > ~0.7 with an existing position gets a warning (two PSU banks is one bet).
- **Leverage clarity** — capital deployed vs notional exposure spelled out.
- **Drawdown circuit-breaker** — after N consecutive losses or an X% account drawdown, halve size or pause new entries for a week.

Storage: one config document plus a computed field per position. Effectively free.

---

# F4 — Deterministic swing screener

Today Gemini is asked to *name* the top 5 NSE stocks from web search ([scheduler/service.py:140-197](features/scheduler/service.py#L140-L197)) — unverifiable, irreproducible, unbacktestable. Invert it: **compute the shortlist, then use the LLM to interrogate it.**

### Nightly pipeline
1. **Universe** — Nifty 500 constituents (free NSE CSV, cached weekly, also gives sector mapping).
2. **Bulk OHLCV** — one batched `yf.download` per ~50 tickers, held **in memory**, not written to Atlas.
3. **Score** on rules suited to a 2–10 day hold:

| Dimension | Rule | Why for swing |
|---|---|---|
| Trend | Close > EMA20 > EMA50, EMA50 rising, weekly close > weekly EMA20 | Don't fight the tide for two weeks |
| Momentum | RSI 50–68 (**not** >70); MACD hist > 0 and rising | Room to run; avoids buying exhaustion |
| Relative strength | 20d/60d return vs `^NSEI` + sector; RS slope > 0 | Institutional money is already there |
| Volume | 5d/20d volume > 1.2; turnover > ₹5 Cr | You must be able to exit in 10 days |
| Volatility fit | ATR% roughly 1.5–6% | Enough range to reach a target, not a lottery |
| Structure | Within 3% of a 20-day breakout, or a controlled pullback to EMA20 | Gives a real entry zone and a tight stop |
| Exclusions | Results within 5 days, F&O ban, circuits, illiquid | Removes the trades that blow up |

4. **Keep the top 8–10**, store only those rows (~400 bytes each, TTL 30 days).
5. **Then** send them to Gemini for what it's genuinely good at: news, catalysts, order wins, promoter/regulatory events, sector narrative, red flags.

Sub-features: setup tagging (`BREAKOUT` / `PULLBACK` / `REVERSAL` / `MOMENTUM_CONTINUATION`) so you learn which archetype works for you; sector-rotation view so you don't end up with five positions in one sector; `/screener` on demand.

---

# F5 — Regime filter

One daily computation that gates everything. In a downtrend, swing longs fail regardless of setup quality.

Signals (all free): Nifty vs 200 DMA and 20 DMA slope · **breadth** (% of Nifty 500 above 50 DMA — free from F4's in-memory data) · India VIX level + 1-yr percentile · FII/DII 5-day cumulative trend.

| Regime | Behaviour |
|---|---|
| RISK_ON | full size, up to max positions |
| NEUTRAL | half size, tighter stops, fewer positions |
| RISK_OFF | **no new longs**; manage existing; deploy into GOLDBEES dips instead (→ F8) |

Storage: one small document per day (~150 bytes), TTL 400 days so F9 can replay regimes.

---

# F6 — Event & earnings guard

An earnings gap is the most common way a good 2–10 day setup becomes a 12% loss overnight. Today it's a soft prompt instruction ([prompt.txt:150-156](prompt.txt#L150-L156)) with no data behind it.

- **Results calendar** — NSE results/filings endpoints. **Hard block** on new entries when results fall inside the intended hold window (default 5 trading days).
- **Corporate announcements** — poll NSE for your watchlist + open positions: board meetings, dividends, splits, rating actions, order wins. Feed to the LLM as *verified Tier-1 evidence* instead of hoping search finds it.
- **F&O expiry** — computed (last Thursday, holiday-adjusted); flag the two days before.
- **RBI MPC dates** — small static yearly calendar; flag banking/NBFC positions.
- **NSE holiday master** — fixes `WEAKNESSES.md` W9 and stops junk holiday reports (and the rows they create).

---

# F7 — SIP / mutual fund tracker

Your three autopay SIPs currently have **zero** software support. This tracks them without ever suggesting you interfere — your prompt's autopilot rule is respected: report, don't meddle.

**Free data:** `https://api.mfapi.in/mf/{scheme_code}` — no key, no registration, full daily NAV history, sourced from AMFI. Official fallback: `https://portal.amfiindia.com/spages/NAVAll.txt`.

- Scheme codes for Navi Nifty 50 Index, Parag Parikh Flexi Cap, MO Nifty Midcap 150 (Direct-Growth) resolved once, then stored.
- Contribution ledger → invested, units, current value, absolute return, and **XIRR** (the only correct return measure for a SIP).
- Benchmark comparison vs Nifty 50 / Midcap 150, plus rolling returns.
- **Fund-health watch** — expense-ratio change, AUM shift, sustained benchmark underperformance, mandate/manager change. Speaks up only on material events.
- Monthly SIP-day note: "₹X debited across 3 SIPs — autopilot, no action", with updated XIRR.
- Asset-allocation view: equity MF vs gold vs Nasdaq vs stocks vs cash, with drift flags.

Storage: 3 funds × 12 contributions/year × ~300 bytes = **~11 KB/year**. NAV history is fetched on demand, never stored.

---

# F8 — GOLDBEES / MON100 dip-buy engine

Section 21 of your v2 prompt specifies this behaviour precisely; no code implements it. Self-contained and immediately useful.

**Free data:** `GOLDBEES.NS`, `MON100.NS`, `^NDX`, `INR=X` via yfinance.

**Tiered deployment** so you don't dump a month's allocation on a 1% wobble:

| Condition | Deploy |
|---|---|
| Mild dip (−2% from 20d high, RSI < 55) | 33% of month's allocation |
| Good dip (−4%, at/below 20 DMA, RSI < 45) | 50% |
| Strong dip (−7%, near 50 DMA, RSI < 35) | 100% of remainder |
| Month-end with budget unspent | deploy the rest — dip-waiting must not become never-buying |

Plus a **monthly budget tracker** per ETF (allocated / deployed / remaining / days left).

**MON100 decomposition** — required by your own prompt and genuinely worth money: split the move into Nasdaq-100 change, USD/INR change, and **ETF premium/discount**. Alert when the premium is unusually wide, because buying at a fat premium loses money even when the index rises. Report it plainly: *"NDX −1.2%, INR −0.4% → your rupee cost fell only 0.8%."*

**GOLDBEES:** track vs domestic gold and vs its own NAV; monitor gold-vs-MON100 allocation drift (flag only, no auto-rebalance — per your rules).

Storage: one status document per ETF per day (~200 bytes), TTL 90 days → **~50 KB/year**.

---

# F9 — Backtest & expectancy engine (runs on your PC)

The feature that tells you whether any of this works — and the one that must **not** live on the free cluster.

**Run it locally.** Your laptop has free disk and free CPU; Atlas has 512 MB and a 10 GB/week transfer cap. Cache OHLCV as local Parquet, run the replay locally, and push **only the summary** (~2 KB per run) to Mongo so the dashboard can show it.

- Replay the F4 screener over 2–5 years, point-in-time (no lookahead), simulating zone fills, ATR stops, T1/T2/T3 partials and the 10-day cap.
- Metrics: win rate, avg R, **expectancy per trade**, profit factor, max drawdown, avg holding days, exit-reason distribution.
- Slice by setup archetype, regime (F5), sector, market cap, holding period.
- **Regime attribution** — returns with the F5 filter on vs off. This usually justifies F5 by itself.
- Parameter sweeps (stop 1.0/1.5/2.0 × ATR, max hold 5/7/10 days, RSI band) with walk-forward validation: tune on 2021–2024, verify untouched on 2025–2026.

---

# F10 — Trade journal (your real trades, net of costs)

`virtual_portfolio` tracked the *AI's* paper trades and is being deleted. This tracks **yours**.

- Real fills: dates, prices, qty, and actual costs — brokerage, STT, exchange, GST, SEBI, stamp duty → **net** P&L and net R.
- **Plan-vs-execution diff**: did you enter in the zone? exit at target or early? move the stop? Which deviations helped, which cost money.
- Tax buckets: STCG (delivery <1 yr) vs speculative vs F&O, with a running FY tally and an LTCG countdown on anything near a year.
- Discipline metrics: rule violations, oversized positions, revenge trades — your `docs/14_Trading_Psychology.yaml` material, fed back with evidence.
- **Weekly Sunday digest**: trades taken, expectancy, biggest mistake, and which screener candidates you skipped that then worked.

Storage: ~1 KB/trade → **~200 KB/year**. Sacred tier, never auto-deleted.

---

# F11 — Two-model agreement gate

You already hold both Gemini and Groq keys. Use the second adversarially instead of decoratively.

- Gemini produces the structured recommendation (grounded search).
- **Groq/Llama runs a critic pass**: "here are the deterministic numbers and the proposed trade — try to falsify it. Which gate fails? What's the bear case? Is the stop below real support? Is R:R honest?"
- Emit `BUY` only when both agree; on disagreement downgrade to `WAIT` and show the objection. Log the agreement rate and check via F10 whether agreed trades actually do better.

Free tiers (verified Sep 2026): Gemini Flash ~10 RPM / 1,500 req/day; Groq ~30 RPM / 14,400 req/day. Your usage is ~15 calls/day.

---

# F12 — Institutional footprint tracker

Extends your FII/DII work from aggregate to stock-specific — all free NSE endpoints.

- **Bulk & block deals** — flag when a watchlist/held stock has a large buyer.
- **Insider (SEBI PIT) disclosures** — promoter buying is among the better swing signals.
- **Shareholding-pattern deltas** — quarterly FII/DII/promoter changes.
- **Delivery % vs its own baseline** — your v2 prompt explicitly says a fixed 40% threshold is wrong and it must be relative to the stock's own history ([qmaf_v2_personalized.md:846-859](features/intraday/templates/qmaf_v2_personalized.md#L846-L859)). Compute the 60-day baseline and the current 5-day average.
- **OI build-up classification** — price↑/OI↑ (fresh longs) vs price↑/OI↓ (short covering); the prompt demands the distinction, nothing computes it.

---

# F13 — Alert quality

You currently truncate reports at 3,800 chars and regex markdown tables into text ([scheduler/service.py:240-265](features/scheduler/service.py#L240-L265)) — the wrong format for a phone glance.

- **Chart images** (`mplfinance` — you have no plotting library at all today): candles + EMA20/50 + volume, with entry zone, T1/T2/T3 and stop drawn. One image replaces the wall of text.
- **One morning digest** instead of N messages: ranked table (symbol, setup, entry, stop, T1, R:R, score), detail on demand.
- **Inline buttons** — `Took it` / `Skipped` / `Snooze` → auto-fills the journal (F10), so it maintains itself.
- **Pre-close check (15:05 IST)** — one message covering all open positions: gap risk, trailing-stop updates, anything hitting day 10 tomorrow.
- **Custom price alerts** — "ping me when GOLDBEES is 2% below its 20 DMA" as a stored rule.
- **Quiet hours & rate caps** — nothing non-critical outside market hours.

---

# F14 — Data-source health

Every data failure currently degrades silently into `[…] Unavailable` inside a prompt (`WEAKNESSES.md` W7).

- `GET /system/health/datasources` — per source (yfinance, NSE option chain, NSE FII/DII, RSS feeds, mfapi, Gemini, Groq): last success, last failure, 24 h/7 d success rate, last error.
- A **`data_confidence`** score on every recommendation, from which sources actually succeeded. Low confidence forces `WAIT` — your QMAF rules already require this.
- Telegram alert when a critical source fails N times in a row.
- Startup self-check: Gemini model IDs validated, Mongo indexes present, `knowledge_chunks` count > 0, env vars set, holiday calendar loaded, **storage under 70%**.

Stored in a capped collection — ~1 MB ceiling forever.

---

# F15 — Telegram command surface

| Command | Does |
|---|---|
| `/positions` | open swing positions: R, days held, distance to target/stop |
| `/track` | add a manual position (entry/target/SL) — your kept feature |
| `/screener` | run F4 now, ranked candidates |
| `/risk <sym> <entry> <stop>` | exact quantity, ₹ risk, resulting portfolio heat |
| `/sip` | SIP status, XIRR, next debit |
| `/dip` | GOLDBEES + MON100 dip status and remaining monthly budget |
| `/journal [week\|month]` | expectancy, win rate, mistakes |
| `/regime` | today's regime and what it permits |
| **`/storage [days]`** | usage + age-based cleanup with dry-run (F2) |
| `/health` | data-source health, last job runs |

---

# F16 — Broker API as data feed (free with an account)

The biggest remaining data-integrity upgrade, at ₹0. yfinance is delayed and patchy; NSE scraping gets blocked from cloud IPs (`WEAKNESSES.md` W7).

**Researched, September 2026:**

| Broker | API cost | Market data | Verdict |
|---|---|---|---|
| **Angel One SmartAPI** | Free | Live + historical | ✅ best free upgrade |
| **Fyers API** | Free | Quotes + historical (minute, ~1–2 yrs) | ✅ good alternative |
| **Dhan (DhanHQ)** | Free | Yes (some data packs may cost) | ✅ fine |
| **Upstox** | Free | Yes | ₹10/executed order via API until 31 Mar 2026 |
| **Zerodha Kite Connect** | Personal tier **free but excludes market data**; full Connect **₹500/mo per key** | Paid tier only (historical now bundled) | ⚠️ pay only if you want Zerodha's ecosystem |

**Recommendation:** open a free Angel One or Fyers account purely as a *data feed*, and keep executing wherever you like. Exchange-grade prices and reliable history at zero cost. Put it behind the same interface as `fetch_for_verification()` so yfinance stays as fallback.

---

# F17 — Expert analytics engine

The jump from "AI that talks like an analyst" to "analyst". Your prompt already *names* Wyckoff, VSA, Market Profile, RS-line slope, IV rank, PEG-vs-median and Supertrend — **none of them are computed**, so the model improvises them (`WEAKNESSES.md` W18). This feature computes them.

**Full formulas, thresholds and interpretation bands are in `ANALYTICS.md`.** Summary of what gets added, in build order:

### Tier 1 — biggest edge per hour (~1 day)
- **ADX / DI± — trend strength.** The missing filter. RSI and MACD are noise in a chop market; `ADX < 20` should veto a breakout trade outright. Probably the single highest-value addition in this document.
- **RS Rating — percentile rank vs the whole universe** (not a raw ratio). The best-documented swing factor; buy the top decile, and it's free once the screener already holds Nifty 500 data.
- **Algorithmic swing pivots** (fractal highs/lows). Your own rule is "stop below the last swing low" — this is what makes that rule executable instead of decorative.
- **PE vs 5-year median + real PEG.** Your valuation gate *requires* this number ([prompt.txt:103](prompt.txt#L103)) and nothing produces it, so the gate is currently decided by guesswork.
- **Base/consolidation detection + 52-week-high proximity.** Defines the entry zone and the true breakout level.

### Tier 2 — depth (~2 days)
- **Volume profile — POC / VAH / VAL.** Real volume-at-price levels, which beat round-number pivots. `prompt.txt:65` already promises Market Profile.
- **Quantified VSA / effort-vs-result.** `range/ATR` against `volume/avg` classifies absorption, no-demand, no-supply and climax **numerically** — Wyckoff becomes measurable instead of narrated.
- **OBV / CMF / U-D volume ratio** with divergence detection.
- **Anchored VWAP** from the last earnings date or the breakout candle — how desks actually frame value, versus session VWAP which is meaningless for a 6-day hold.
- **Earnings surprise history + post-earnings drift (PEAD).** One of the few documented multi-week edges, and its window is exactly 2–10 days.
- **Piotroski F-score, Altman Z-score.** Cheap quality/solvency screens that keep value traps out of a swing book.
- **IV rank/percentile, futures basis, rollover %, OI build-up classification.** The derivative context your prompt demands.
- **Pivot points + Fibonacci levels** from the prior week/month.

### Tier 3 — feedback loop
- **MAE / MFE per trade** (maximum adverse/favourable excursion) — tells you whether your stops are too tight or your targets too far. This is the analytic that actually improves a system.
- **Expectancy, R-distribution, fractional Kelly** sizing from your own measured edge.
- **Monte Carlo on the R-distribution** → probability of a given drawdown, risk of ruin.

Every one of these is computable from free OHLCV, the free NSE endpoints you already touch, or your own journal. **Storage cost:** a per-symbol analytics document is ~1.5 KB and only the shortlist is stored (TTL 30 days) — ~200 KB/year.

---

# F18 — Data freshness & timestamp discipline

Today nothing knows how old its own inputs are, and reports never say (`WEAKNESSES.md` W19). Your framework demands this discipline explicitly ([qmaf_v2_personalized.md:201-249](features/intraday/templates/qmaf_v2_personalized.md#L201-L249)); the code gives it nothing to work with.

- **Every input becomes a stamped envelope** — `{value, source, captured_at, age_seconds, state}` where state is `LIVE | DELAYED | LAST_CLOSE | STALE | UNAVAILABLE`.
- **Freshness budget per data type**, enforced in code, not requested in prose:

| Data | Budget during session | Outside session |
|---|---|---|
| Price / quote | ≤ 15 min | last close, labelled |
| Indicators | as-of last close, always labelled | same |
| Option chain | ≤ 30 min | T-1 |
| FII/DII | T-1 | T-1 |
| Fundamentals | ≤ 1 quarter | ≤ 1 quarter |
| News catalysts | ≤ 5 days | ≤ 5 days |

- **Market-session state machine** — `PRE_OPEN / OPEN / CLOSED / POST / HOLIDAY / WEEKEND` — so a Sunday report can no longer read like a Tuesday 09:20 one, and the `Status: Live / Closed` header field ([prompt.txt:219](prompt.txt#L219)) becomes something the system actually knows.
- **Stale ⇒ consequence.** Beyond budget: lower `data_confidence`, label the field, and if a *binding* input is stale, force `WAIT` rather than trading on it.
- **Every recommendation carries `as_of`** and the age of its worst input, so a report you open two days later can't be mistaken for current.
- **Point-in-time correctness** for the backtest, so it can't peek at the future.

---

# F19 — Swing paper portfolio

The `virtual_portfolio` you built, re-tuned for swing (`WEAKNESSES.md` W13). Worth keeping precisely *because* you trade swing: it's how you find out whether the AI's picks deserve real money, without risking any.

- Every validated AI recommendation is paper-traded through the **same lifecycle as a real position** (F1): entry-zone fill simulation, ATR-based sizing from F3 (not the hardcoded ₹20,000), T1/T2/T3 partials, trailing stop, 10-day time exit, R-multiple on close.
- **Side-by-side comparison** — AI paper trades vs your real trades vs your manual picks, on the same expectancy maths. You'll learn quickly whether to follow the AI, invert it, or use it only as a screener.
- **Shadow mode for rule changes** — when you tune the screener or a stop rule, run the new rules in paper alongside the old ones before switching.
- Storage: one bounded document per paper trade (~1.5 KB), so ~150 KB/year even at 100 paper trades.

---

## What I'd build first

1. **F2 storage manager + the cadence re-tune** (`WEAKNESSES.md` W13) — stops the bleeding, and you stop losing your track record. Every feature survives.
2. **F1 swing lifecycle (real + manual + paper)** — nothing can be measured until positions live past one day.
3. **F18 freshness discipline** — cheap, and every later improvement is worthless on stale inputs.
4. **F17 Tier 1 analytics** — ADX alone will stop a meaningful share of bad trades.
5. **F3 sizing + heat** — cheapest protection against a large loss.
6. **F7 + F8 SIP/ETF engines** — automates the half of your portfolio with no code at all.

Then **F4 screener** → **F9 backtest** → **F5 regime**, in that order, because each makes the next meaningful.

Step-by-step build instructions, code, endpoints and verification are in `IMPLEMENTATION.md`.

---

### Sources for researched claims

- [Kite Connect pricing](https://zerodha.com/products/api/) · [Zerodha free personal APIs](https://zerodha.com/z-connect/updates/free-personal-apis-from-kite-connect) · [historical data bundled](https://kite.trade/forum/discussion/14806/historical-data-is-now-free-with-base-kite-connect-subscription) · [India trading APIs 2026](https://indianbrokertest.com/best-trading-apis-in-india/)
- [MFAPI.in](https://www.mfapi.in/) · [docs](https://www.mfapi.in/docs/)
- [Gemini models](https://ai.google.dev/gemini-api/docs/models) · [free-tier limits](https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/) · [structured output](https://ai.google.dev/gemini-api/docs/structured-output) · [Groq free tier](https://tokenmix.ai/blog/groq-free-tier-limits-2026)
- [Atlas free-cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations/) · [Atlas service limits](https://www.mongodb.com/docs/atlas/reference/atlas-limits/)
- [openchart](https://github.com/marketcalls/openchart) · [nselib](https://github.com/RuchiTanmay/nselib)
