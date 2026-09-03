# AGENT GUIDE — read this first, before any other document

**For:** the AI agent or developer implementing this project.
**Written:** 4 September 2026

If you are an AI agent that has just been given this repository: **stop and read this whole file.** It is ~500 lines. Do not read the other twelve documents yet — you don't need them, and reading them all will fill your context with material irrelevant to your current task.

---

# 1. What you are working on

A **personal swing-trading assistant** for Indian stock markets (NSE/BSE), used by exactly one person.

- FastAPI backend + Telegram bot (aiogram) + MongoDB Atlas free tier
- Gemini for research, Groq/Llama as a second opinion
- Market data from yfinance and free NSE/BSE endpoints
- A Vercel dashboard consumes the same API

**Hard scope rules that never change:**

| Rule | Detail |
|---|---|
| Horizon | **Swing only: 2–10 trading days. 10 is a hard cap.** No intraday trading |
| Features | **Nothing gets deleted.** Existing features are converted to swing timing, not removed |
| Cost | **Free resources only.** Never add a paid service |
| Storage | **Atlas M0 = 512 MB.** Storage cost is part of every design decision |
| Users | **One user.** No multi-tenancy, no user management, no scaling work |

---

# 2. The seven rules you must never break

Violating any of these does real damage. They are not style preferences.

### 🚫 1. Never delete or mass-delete trading history
`swing_positions`, `paper_positions`, `trade_journal`, `monthly_rollups`, `sip_contributions`, `recommendations`, `knowledge_chunks`, `failure_library` are **SACRED**. They cannot be recomputed from anywhere. Age-based cleanup must skip them. This has already cost the user their track record once.

### 🚫 2. Never call synchronous I/O inside `async def`
`yfinance`, `nsepython`, `requests`, `feedparser` are all synchronous. Calling them directly in an `async` function blocks the entire event loop and freezes the Telegram bot. Always go through `run_in_executor` **inside the adapter**. This is a live bug you will be fixing in WP3.

### 🚫 3. Never let the LLM decide a number the code can compute
Prices, indicators, ratios, position sizes, R:R — all computed in `domain/calc/`. The LLM supplies judgement, news and narrative only. If you find yourself writing a prompt that asks for RSI, stop: you are reintroducing the project's biggest bug.

### 🚫 4. Never write `except Exception: pass`
Six of these already exist and one silently broke a feature for months. If a failure is acceptable, log it and record it to source health. Never let a swallowed error produce a trading decision.

### 🚫 5. Never rewrite a working feature
This system is used for real trading. Use the **strangler** approach: new code in the new structure, old code migrates only when a work package requires touching it. Keep existing route paths working (add aliases if you rename handlers) so the dashboard never breaks.

### 🚫 6. Never invent a threshold, formula, or rule
Every number is already specified. ADX bands, RSI bands, position sizing, dip tiers, veto conditions — all in `ANALYTICS.md`. If you cannot find a value, **ask the user**. Do not guess.

### 🚫 7. Never backtest the LLM layer
An LLM's training data contains the future relative to any historical date, so an LLM backtest is contaminated and will look falsely good. Backtest the deterministic rules only.

---

# 3. ⚠️ The code in the documents is a SKETCH, not production code

This matters more than anything else in this guide.

Every code block in `IMPLEMENTATION.md`, `ANALYTICS.md`, `LLM_ORCHESTRATION.md`, `NEWS_FAST_LANE.md` and `ENGINEERING.md` is **illustrative**. Sketches:

- omit imports
- use `...` and `# existing logic unchanged` as placeholders
- omit error handling, timeouts and type hints
- reference helper functions that do not exist yet (`watched_symbols()`, `get_history()`, `_fmt()`)
- may contain small inconsistencies between documents

**Your job is to write complete, correct, production code that satisfies the *intent* of the sketch.** Never paste a sketch verbatim and call the task done.

For every sketch you implement, you must add: real imports, full type hints, error handling with the typed exceptions from `core/errors.py`, a timeout on every outbound call, structured logging, and a test if the code touches money (see §6).

If a sketch references a function that does not exist, **create it properly** or ask. Do not stub it with `pass`.

---

# 4. How to pick up work — the loop

Do exactly this, every session:

```
1. Read PROGRESS.md          → find the first unchecked work package
2. Read ONLY the doc sections that package lists (§7 below)
3. Implement it
4. Run the verification command for that package
5. Tick it in PROGRESS.md, commit
6. Stop. Next package = next session.
```

**One work package per session. One commit per package.** Do not chain packages — a half-finished refactor across two features is how a live trading system breaks.

Create `PROGRESS.md` on your first session by copying the checklist in §8.

## 4.1 Context discipline (important for you specifically)

The twelve documents total ~6,000 lines. **You cannot hold that, and you don't need to.**

- Read only the sections named in your work package.
- `PROJECT_BRIEF.md` is a 120-line map — read it once, early.
- Never read `PROMPTS.md` (767 lines of prompt text) unless you are doing WP10.
- Never read all of `IMPLEMENTATION.md` (1,400 lines). Jump to the phase your package names.

## 4.2 When something is ambiguous

In order:

1. **Check the authority map** — `ENGINEERING.md` §8 says which document decides which kind of question.
2. **Apply the conflict order:** data integrity → risk control → engineering standards → features → performance → convenience. A lower concern never overrides a higher one.
3. **Prefer the smaller change.** If two readings are possible, take the one that touches less code.
4. **Ask the user.** A short question costs a minute. A wrong guess in a trading system costs money. Never invent a threshold or a business rule to unblock yourself.

---

# 5. Code conventions — follow these exactly, do not invent your own

## 5.1 Where code goes

```
core/        infrastructure: config, clock, errors, logging, auth, freshness, retention
domain/      PURE logic — maths and rules. Imports NOTHING from this project.
             domain/models/  pydantic models
             domain/calc/    indicators, sizing, xirr, costs, barriers
             domain/rules/   veto ladder, setup classification, scoring, validation
adapters/    ALL outbound I/O, one module per external system
             adapters/market/  yfinance, nse, bse, broker  (all satisfy one Protocol)
             adapters/news/    rss, gnews, filings
             adapters/funds/   mfapi, amfi
             adapters/llm/     gemini, groq
             adapters/notify/  telegram, ntfy, emailjs
             adapters/repo/    the ONLY code that touches MongoDB
features/    orchestration only: "when X happens, do Y then Z"
prompts/     static prompt files (swing.md, general.md)
scripts/     backtest, indexer, one-off migrations
tests/       unit / rules / adapters / integration
```

## 5.2 The dependency rule — absolute

```
features/  → may import core, domain, adapters
adapters/  → may import core, domain/models
domain/    → imports NOTHING from this project (stdlib + pandas + pydantic only)
core/      → imports stdlib + third-party only
```

Plus: **no feature imports another feature.** Cross-feature needs go through `domain/` (logic) or `adapters/repo/` (data). There are currently 8 function-local imports working around circular dependencies; this rule removes the need for all of them.

A CI test enforces this (`ENGINEERING.md` §2). If it fails, your import is wrong — do not weaken the test.

## 5.3 Non-negotiable code patterns

| Rule | Correct |
|---|---|
| Blocking I/O | `await loop.run_in_executor(pool, partial(sync_fn, args))`, inside the adapter |
| Timeouts | every outbound call has one. No exceptions |
| Errors | raise from `core/errors.py`: `DataUnavailable`, `DataStale`, `LLMUnavailable`, `ValidationFailed`, `ConfigError` |
| Logging | `log.info("event_name", extra={"symbol": ..., "run_id": ...})`. Never `print()` |
| Config | read via `settings()` from `core/config.py`. Never `os.getenv` in a feature |
| Import-time | no module-level `.env` reads, network calls, or DB access. Use `@lru_cache` factories |
| Time | pass the clock in. Never default a parameter to `datetime.now()` |
| Dates | date **keys** and display strings use IST via `core/timeutils.py`. Store raw datetimes in UTC |
| Boundaries | pydantic models at HTTP/Mongo/LLM boundaries. Dicts only inside a function |
| Idempotency | writes that can fire twice get a **unique index**, and you catch `DuplicateKeyError` |
| Money maths | lives in `domain/calc/` as a pure function, with a test |

## 5.4 Commit format

```
WP<n>: <short imperative summary>

- what changed
- why (reference the doc section)

Verified: <the command you ran and its result>
```

One work package per commit. Never commit a failing tree.

---

# 6. Testing — what you must test, and what you can skip

Target ~40 high-value tests total. **Not** high coverage. Every test below runs with no database and no network, because `domain/` is pure.

**You must add a test when your package touches any of these:**

| Area | Test |
|---|---|
| Veto ladder / gates | table test, one row per hard veto, asserting `WAIT` + named reason |
| Indicators | golden values vs a published reference (RSI, ATR, ADX) |
| Position grading | target and stop touched same day ⇒ **stop wins** |
| Position sizing | risk ≈ capital × risk% within one share; heat cap blocks the 6th position |
| Retention/cleanup | **sacred collections never swept at any age** |
| Freshness | stale binding input forces `WAIT` |
| Recommendation validation | level ordering, R:R < 1:2 rejected, probabilities sum to 100 |
| Fill simulation | entry zone never touched ⇒ `CANCELLED`, not graded |
| XIRR / cost model | known-answer examples |

**You can skip tests for:** routers that only call one service function, formatting helpers, prompt text, Telegram message layout.

**Run before every commit:**
```bash
ruff check . && mypy domain/ core/ && pytest -q
```

---

# 7. Work packages — do them in this order

Each entry gives you everything needed. **Read only the sections listed.**

Format: `Goal · Read · Create · Modify · Do NOT touch · Done when · Verify`

---

### WP1 — Skeleton and guardrails
- **Goal:** create the empty layer structure and make CI enforce it.
- **Read:** `ENGINEERING.md` §1, §2
- **Create:** `domain/{models,calc,rules}/__init__.py`, `adapters/{market,news,funds,llm,notify,repo}/__init__.py`, `tests/test_architecture.py`, `.github/workflows/ci.yml`, `pyproject.toml` (ruff + mypy config)
- **Modify:** nothing
- **Do NOT touch:** any existing `features/` file
- **Done when:** `pytest tests/test_architecture.py` passes on the empty tree; CI runs on push
- **Verify:** `ruff check . && mypy domain/ core/ && pytest -q`

### WP2 — `core/` foundations
- **Goal:** typed config, IST clock, structured logging, typed errors — with no import-time side effects.
- **Read:** `ENGINEERING.md` §3.2–3.5 · `IMPLEMENTATION.md` §0.2 (IST helper)
- **Create:** `core/errors.py`, `core/logging.py`, `core/timeutils.py`
- **Modify:** `core/config.py` → pydantic `Settings` + `@lru_cache settings()`; remove the `connetion_string` typo fallback
- **Do NOT touch:** feature logic yet
- **Done when:** app boots; a missing required env var raises one clear error at startup, not a `None` later
- **Verify:** `python -c "from core.config import settings; print(settings().max_hold_days)"` → `10`

### WP3 — Market data adapter (fixes the live bug)
- **Goal:** all synchronous market I/O behind async adapters with executors and timeouts.
- **Read:** `ENGINEERING.md` §0.1, §3.1, §3.8
- **Create:** `adapters/market/base.py` (Protocol), `adapters/market/yfinance.py`, `adapters/market/nse.py`
- **Modify:** [features/intraday/service.py:61-68](features/intraday/service.py#L61-L68) and [features/portfolio/service.py:66-76](features/portfolio/service.py#L66-L76) — replace direct `yf.Ticker` calls with `await` on the adapter. **Change only those lines.**
- **Do NOT touch:** the surrounding scan logic, cron schedules, alert text
- **Done when:** `grep -rn "yf\.Ticker" features/` returns nothing
- **Verify:** trigger a scan and send the bot a message at the same time — it must reply immediately

### WP4 — Indicators in `domain/calc/`
- **Goal:** correct Wilder-smoothed indicators, and a weekly trend that actually works.
- **Read:** `IMPLEMENTATION.md` §0.4 · `ANALYTICS.md` §A1 (ADX)
- **Create:** `domain/calc/indicators.py` (rsi, atr, macd, bbands, adx), `tests/unit/test_indicators.py`
- **Modify:** `features/market_data/technical_indicators.py` — import from `domain/calc`; **delete both `import pandas_ta` blocks**; compute `weekly_trend` unconditionally
- **Do NOT touch:** the prompt-formatting functions in that file
- **Done when:** `weekly_trend` returns a real value, not `"N/A"`; RSI/ATR match a public chart within rounding
- **Verify:** `pytest tests/unit/test_indicators.py -q`

### WP5 — API auth and guarded cleanup
- **Goal:** close the open API; make deletion safe.
- **Read:** `IMPLEMENTATION.md` §0.1 · §2.4 (cleanup with dry-run)
- **Create:** `core/auth.py`
- **Modify:** `main.py` (add `dependencies=[Depends(require_token)]` to every router except `system_router`); replace the `DELETE .../all` endpoints with calls to `cleanup(dry_run=True)`
- **Do NOT touch:** `GET /` — Render's port scanner needs it open
- **Done when:** `curl <url>/performance/hit-rate` → 401; with `X-API-Token` → 200
- **Verify:** both curl calls; then confirm the dashboard still works after adding the token to its env

### WP6 — Cadence conversion (removes the storage bomb)
- **Goal:** 3 in-session checks + EOD instead of per-minute, writing only on state change.
- **Read:** `IMPLEMENTATION.md` Phase 1 (all of it) · `WEAKNESSES.md` W1
- **Create:** nothing new
- **Modify:** `features/scheduler/service.py` (replace the two cron registrations), `features/intraday/service.py` (make the insert conditional on state change)
- **Do NOT touch:** alert wording, the manual `/track` flow, `virtual_portfolio` data (migrate it in WP12)
- **Done when:** after one full trading day, `position_events` has single-digit rows, not hundreds
- **Verify:** `GET /storage/stats` before and after; drop `intraday_scans` only after confirming nothing reads it

### WP7 — Storage and retention manager
- **Goal:** tiered retention plus the "clear older than N days" control, from bot and UI.
- **Read:** `IMPLEMENTATION.md` Phase 2 (all) · `FEATURES.md` F2
- **Create:** `core/retention.py`, `features/storage/{service,router}.py`, `tests/unit/test_retention.py`
- **Modify:** `features/bot/handlers.py` (add `/storage`), `main.py` (mount the router, call `ensure_storage_indexes()`)
- **Do NOT touch:** existing `chat_history` TTL behaviour (already correct)
- **Done when:** `/storage 60` shows a dry-run preview and deletes nothing until confirmed; a sweep at any age leaves sacred collections untouched
- **Verify:** `pytest tests/unit/test_retention.py -q` — the sacred-collection test must pass

### WP8 — Freshness and data authority
- **Goal:** every input carries its age; stale binding inputs force `WAIT`; the LLM stops outranking computed numbers.
- **Read:** `IMPLEMENTATION.md` §3.1, §3.5 · `ANALYTICS.md` §H
- **Create:** `core/freshness.py`, `tests/unit/test_freshness.py`
- **Modify:** adapters to return `Stamped`; `features/scheduler/service.py` — remove "Gemini live_price is the authoritative CMP" and the research fields that ask for numbers
- **Do NOT touch:** `prompts/` (WP10 handles prompt text)
- **Done when:** running on a weekend reports `SESSION: WEEKEND`, every input `LAST_CLOSE`, and no `BUY` is emitted
- **Verify:** `pytest tests/unit/test_freshness.py -q`

### WP9 — LLM orchestration
- **Goal:** Gemini and Groq calls that survive rate limits, bad models and outages.
- **Read:** `LLM_ORCHESTRATION.md` §3, §4, §5, §6, §7, §8
- **Create:** `adapters/llm/{base,gemini,groq}.py` with error taxonomy, `KeyState` cooldowns, `Breaker`
- **Modify:** `features/gemini/service.py` → thin wrapper over the adapter (keep the public function names so callers don't break)
- **Do NOT touch:** the `/gemini` Telegram dashboard behaviour
- **Done when:** an invalid model ID is caught at startup; a simulated 429 rotates keys with no user-visible failure; a total outage still lets the screener and alerts run
- **Verify:** set `DEFAULT_GEMINI_MODEL=nonexistent-model` → startup logs it and drops it, app still boots

### WP10 — Prompts and knowledge modules
- **Goal:** two explicit prompts live; RAG that actually retrieves relevant chunks.
- **Read:** `PROMPTS.md` (all — this is the one time you read it) · `KNOWLEDGE_AND_PROMPTS.md` Parts 1, 2, 5
- **Create:** `prompts/swing.md`, `prompts/general.md`, `docs/19`–`25_*.yaml`
- **Modify:** `features/knowledge_base/indexer.py` (write `tags`), retrieval to use intent tags, call sites to use `pick_prompt(intent)`
- **Delete:** `prompt.txt`; move `qmaf_v2_personalized.md` → `docs/archive/`
- **Do NOT touch:** the chart code-block convention in the general prompt — the dashboard renders those
- **Done when:** asking "what is SBI at?" with no fetched quote makes the model refuse to recall a number
- **Verify:** run `python -m features.knowledge_base.indexer`; confirm chunk count rose and one sample retrieval returns relevant text

### WP11 — Swing position lifecycle
- **Goal:** positions that live 2–10 days, with fill simulation and R-multiple grading.
- **Read:** `IMPLEMENTATION.md` Phase 4 (all) · `FEATURES.md` F1
- **Create:** `features/swing/{service,router}.py`, `domain/models/position.py`, `domain/calc/barriers.py`, `adapters/repo/positions.py`, `tests/unit/test_lifecycle.py`
- **Modify:** rename the manual-track functions per `IMPLEMENTATION.md` §1.3, keeping old route paths as aliases
- **Do NOT touch:** `evaluate_day` yet — remove it only once the new grading is proven
- **Done when:** a position survives >1 day, is tracked daily, force-exits at day 10, and one whose zone was never touched ends `CANCELLED`
- **Verify:** `pytest tests/unit/test_lifecycle.py -q`; `daily[]` never exceeds 10 entries

### WP12 — Paper portfolio
- **Goal:** AI picks paper-traded through the identical lifecycle.
- **Read:** `FEATURES.md` F19 · `IMPLEMENTATION.md` §1.4 (migration)
- **Create:** `features/swing/paper.py`
- **Modify:** migrate `virtual_portfolio` → `paper_positions`, then drop the old collection
- **Done when:** expectancy can be reported split by source (AI / manual / paper)
- **Verify:** the migration script prints a count that matches the old collection

### WP13 — Alerts and bot menu
- **Goal:** the right alert at the right priority, deduped, with a discoverable menu.
- **Read:** `ALERTS_AND_BOT.md` (all — it's 260 lines)
- **Create:** `core/alerts.py` (catalogue + `send_alert`), `adapters/notify/*`
- **Modify:** `main.py` (`setMyCommands`), `features/bot/handlers.py` (`/menu`, `/help`, guided `/track`)
- **Do NOT touch:** existing `/gemini` and `/memory` handlers — they work
- **Done when:** two stop breaches on one position in a day produce **one** alert; `/menu` reaches every feature in two taps
- **Verify:** every command in `setMyCommands` responds; a P2 alert at 21:00 IST queues instead of pushing

### WP14 — Analytics Tier 1
- **Goal:** the metrics that actually gate trades.
- **Read:** `ANALYTICS.md` §A1, §B1, §B3, §C1, §C2, §F1 · §1.1 of `RECOMMENDATION_ENGINE.md` (for `PEAD_52W`)
- **Create:** `domain/calc/{adx,relative_strength,pivots,base_quality,valuation}.py`, tests for each
- **Modify:** `features/market_data/technical_indicators.py` to surface them in the prompt block
- **Done when:** each metric matches a public chart or a hand calculation for 3 symbols
- **Verify:** `pytest tests/unit/ -q`

### WP15 — Veto ladder
- **Goal:** the risk control, as a pure testable function.
- **Read:** `ANALYTICS.md` §J · `ENGINEERING.md` §4 (the test shape)
- **Create:** `domain/rules/veto_ladder.py`, `domain/rules/validation.py`, `tests/rules/test_veto_ladder.py`
- **Done when:** the parametrised table test passes for every hard veto, and a clean setup passes
- **Verify:** `pytest tests/rules -q` — **this is the most important test in the project**

### WP16 — Screener and regime
- **Read:** `IMPLEMENTATION.md` Phase 5 · `FEATURES.md` F4, F5
- **Create:** `features/screener/{service,router}.py`, `domain/rules/scoring.py`, `adapters/repo/screener.py`
- **Done when:** ranked candidates come back with reasons, illiquid names and results-in-5-days are excluded, and `RISK_OFF` blocks new entries
- **Verify:** run it; confirm every excluded symbol has a named reason

### WP17 — Risk engine
- **Read:** `IMPLEMENTATION.md` Phase 6 (sizing) · `ANALYTICS.md` §I2
- **Create:** `features/risk/{service,router}.py`, `domain/calc/sizing.py`, `tests/unit/test_sizing.py`
- **Done when:** risk ≈ capital × risk% within one share; the 6th position is blocked by the heat cap
- **Verify:** `pytest tests/unit/test_sizing.py -q`

### WP18 — Journal and review
- **Read:** `FEATURES.md` F10 · `ANALYTICS.md` §I1 · `RECOMMENDATION_ENGINE.md` §6.2 (decay alerts)
- **Create:** `features/performance/journal.py`, `domain/calc/costs.py`
- **Done when:** net-of-cost R-multiples, MAE/MFE, and a rolling-50 expectancy alert all work
- **Verify:** `pytest tests/unit/test_costs.py -q`

### WP19 — Investments (SIP + ETF)
- **Read:** `IMPLEMENTATION.md` Phase 7 (all) · `FEATURES.md` F7, F8
- **Create:** `features/investments/{service,router}.py`, `adapters/funds/mfapi.py`, `domain/calc/xirr.py`, `domain/rules/dip_tiers.py`
- **Done when:** XIRR matches an independent calculation; dip tiers fire on a historical GOLDBEES drawdown; MON100 decomposition satisfies `ndx + inr + premium ≈ etf move`
- **Verify:** `pytest tests/unit/test_xirr.py -q`

### WP20 — News fast lane
- **Read:** `NEWS_FAST_LANE.md` §6 first (the manual-trigger fix), then §2–§5
- **Create:** `features/news_scanner/fast_lane.py`, `adapters/news/{filings,gnews}.py`, `GET /news/latest`
- **Modify:** `features/market_data/news_fetcher.py` (conditional GET, `entries[:15]`), `features/news_scanner/service.py` (`pending_ai` queue)
- **Do NOT touch:** `POST /news-scanner/trigger` behaviour — the cron job depends on it
- **Done when:** calling `GET /news/latest` right after a cron scan **returns articles** instead of nothing
- **Verify:** run the cron scan, then immediately call the endpoint

### WP21 — Backtest (local only)
- **Read:** `RECOMMENDATION_ENGINE.md` §0, §4 **before writing any code** · `IMPLEMENTATION.md` Phase 8
- **Create:** `scripts/backtest.py`, local Parquet cache under `data/` (gitignored)
- **Do NOT:** backtest the LLM layer. Do NOT store OHLCV in Atlas. Do NOT sweep parameters.
- **Done when:** the shuffled-signal control gives ~0 expectancy, and you have 8–10 OOS windows with the trial count logged
- **Verify:** run the control first — if it shows an edge on shuffled data, you have a lookahead bug

### WP22 — Analytics Tier 2
- **Read:** `ANALYTICS.md` §C3, §D1, §D2, §F2, §F3, §G
- **Create:** `domain/calc/{volume_profile,vsa,flow,pead,quality,derivatives}.py` + tests
- **Done when:** every metric named in the prompt is either computed or explicitly `UNAVAILABLE`
- **Verify:** grep the prompt for metric names; each must map to a real computed field

---

# 8. `PROGRESS.md` — create this on your first session

```markdown
# Progress

## Foundations (do first)
- [ ] WP1  Skeleton and guardrails
- [ ] WP2  core/ foundations
- [ ] WP3  Market adapter (fixes live event-loop bug)
- [ ] WP4  Indicators in domain/calc

## Safety and storage
- [ ] WP5  API auth and guarded cleanup
- [ ] WP6  Cadence conversion
- [ ] WP7  Storage and retention manager

## Data integrity
- [ ] WP8  Freshness and data authority
- [ ] WP9  LLM orchestration
- [ ] WP10 Prompts and knowledge modules

## Trading core
- [ ] WP11 Swing position lifecycle
- [ ] WP12 Paper portfolio
- [ ] WP13 Alerts and bot menu
- [ ] WP14 Analytics Tier 1
- [ ] WP15 Veto ladder

## Engine
- [ ] WP16 Screener and regime
- [ ] WP17 Risk engine
- [ ] WP18 Journal and review
- [ ] WP19 Investments (SIP + ETF)
- [ ] WP20 News fast lane

## Later
- [ ] WP21 Backtest (local)
- [ ] WP22 Analytics Tier 2

## Notes / blockers
(record anything you had to ask the user, and their answer, so the next session knows)
```

Update it at the end of every session. It is how a fresh agent session knows where things stand.

---

# 9. Document map — what to open for what

**Never read all of these.** Open only what your work package names.

| Document | Lines | Open it when |
|---|---|---|
| `AGENT_GUIDE.md` | 500 | now, fully |
| `PROJECT_BRIEF.md` | 120 | once, early — the scope and non-negotiables |
| `WEAKNESSES.md` | 295 | you want the reason a fix exists, with `file:line` |
| `FEATURES.md` | 494 | you need a feature's intent and storage budget |
| `IMPLEMENTATION.md` | 1,440 | **jump to the phase your WP names.** Never read whole |
| `ENGINEERING.md` | 445 | structure, standards, testing, layering questions |
| `ANALYTICS.md` | 444 | any formula, threshold or the veto ladder |
| `PROMPTS.md` | 767 | **only WP10** |
| `KNOWLEDGE_AND_PROMPTS.md` | 475 | only WP10 (YAML modules, retrieval fix) |
| `LLM_ORCHESTRATION.md` | 402 | only WP9 |
| `ALERTS_AND_BOT.md` | 263 | only WP13 |
| `NEWS_FAST_LANE.md` | 378 | only WP20 |
| `RECOMMENDATION_ENGINE.md` | 340 | WP14 (`PEAD_52W`) and WP21 (validation) |

---

# 10. Definition of done — check every one, every package

- [ ] Layer boundaries respected (`pytest tests/test_architecture.py`)
- [ ] No synchronous I/O inside `async def`; every outbound call has a timeout
- [ ] No `except Exception: pass`; failures logged and recorded
- [ ] Pure logic in `domain/`, with a test that needs no DB or network
- [ ] Typed models at every boundary
- [ ] Unique index on any write that could fire twice
- [ ] A test from §6 if the package touches money
- [ ] App boots, bot replies, existing dashboard routes still work
- [ ] `ruff check . && mypy domain/ core/ && pytest -q` all green
- [ ] `PROGRESS.md` updated, one commit made

---

# 11. If you are stuck

1. Re-read the work package entry — it names the exact sections you need.
2. Check `ENGINEERING.md` §8 for which document has authority.
3. Apply the conflict order: data integrity → risk control → engineering → features → performance → convenience.
4. Prefer the smaller change.
5. **Ask the user.** Say what you're trying to do, the two options you see, and which you'd pick. Do not guess a threshold, a formula, or a business rule.

**The one thing worse than asking a question is inventing a trading rule.** This system moves real money.
