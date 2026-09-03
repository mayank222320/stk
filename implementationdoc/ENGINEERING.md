# StockAI — Engineering Standards & Code Architecture

**Written:** 4 September 2026
**Purpose:** how to build everything in the other ten documents without the codebase collapsing under its own weight. Target structure, dependency rules, coding standards, testing strategy, and PR-sized work packages.
**Companions:** `IMPLEMENTATION.md` (what to build, phase by phase) · `PROJECT_BRIEF.md` (scope)

---

# 0. Why this document exists

You're about to roughly double the codebase — swing lifecycle, screener, risk engine, analytics, storage manager, investments, backtest, fast lane. The current structure is *good* (feature folders with `router.py` + `service.py` is the right instinct) but it has no layering rules, and that's already producing symptoms.

## 0.1 What the current code tells us

Five findings from auditing the existing structure. None is a criticism of the choices made under time pressure — they're all the predictable result of missing layer boundaries.

### 🔴 Bug: blocking I/O inside async functions

```python
# features/portfolio/service.py:70-71  — inside `async def get_positions()`
ticker = yf.Ticker(f"{sym}.NS")
info = ticker.history(period="1d")        # ← synchronous network call, no executor
```

Same at [intraday/service.py:62-67](features/intraday/service.py#L62-L67), **inside a loop over symbols**.

`yfinance` is synchronous. Calling it directly in `async def` blocks the **entire event loop** — including Telegram polling. With 5 symbols × 2 network calls, your bot goes unresponsive for seconds to tens of seconds on every scan. Under the old per-minute cadence that was most of the trading day.

The rest of the codebase gets this right (`run_in_executor` in [market_data/service.py:112-118](features/market_data/service.py#L112-L118)), which is exactly why an enforced rule matters — correctness shouldn't depend on remembering.

### 🟠 Eight function-local imports — all circular-dependency workarounds

```python
# features/scheduler/service.py:299, :570, :579, :600
from features.portfolio.service import log_virtual_trade
from features.intraday.service import run_intraday_scan
```

Imports hidden inside functions to dodge import cycles. It works, but it means **features import each other's internals**, so nothing can be tested or moved in isolation. The dependency rule in §2 removes the need entirely.

### 🟠 God functions mixing five concerns

`run_intraday_scan()` ([intraday/service.py:18-231](features/intraday/service.py#L18-L231), ~215 lines) fetches data, computes VWAP and P&L, decides status, formats HTML, sends Telegram, pushes ntfy, and writes Mongo. There is no way to unit-test the status decision without a database, a network and a bot token. The same pattern appears in `_process_symbol` ([scheduler/service.py:200-312](features/scheduler/service.py#L200-L312)), where regex table-munging sits next to orchestration.

### 🟠 Import-time side effects

```python
gemini_manager = GeminiKeyManager(discover_gemini_keys())   # gemini/service.py:98 — reads .env at import
grok_manager   = GrokKeyManager(discover_grok_keys())       # grok/service.py:98
@dp.message(CommandStart(), F.from_user.id == int(USER_ID)) # bot/handlers.py:63 — crashes at import if unset
```

Importing a module shouldn't read the filesystem or crash. This makes tests impossible without a populated `.env`, and a missing `Userid` breaks the whole app at import.

### 🟠 Six silently swallowed exceptions

`except Exception: pass` at [technical_indicators.py:156-157](features/market_data/technical_indicators.py#L156-L157) is why `weekly_trend` has been `"N/A"` forever and nobody knew. Bare `except:` at [portfolio/service.py:118](features/portfolio/service.py#L118). Plus the `sys.modules[__name__] = _PromptModule()` module-replacement hack at [intraday/prompts.py:87-91](features/intraday/prompts.py#L87-L91) — clever, but it makes imports behave in a way no reader expects.

## 0.2 The single principle that fixes all five

> **Separate pure logic from I/O, and make dependencies point in one direction only.**

Everything in §1–§3 is an application of that sentence.

---

# 1. Target structure

Evolve what you have; don't replace it. Feature folders stay — they gain three siblings.

```
core/                     # cross-cutting infrastructure. Imports NOTHING from features/
  config.py               # typed settings, validated at startup
  timeutils.py            # IST clock, session state, trading-day helpers
  database.py             # Mongo connection lifecycle only
  auth.py                 # API token dependency
  errors.py               # typed exception hierarchy
  logging.py              # structured logging setup
  freshness.py            # Stamped envelope, freshness budgets
  retention.py            # storage tiers and policy
  health.py               # source health recording

domain/                   # ★ NEW — pure logic. ZERO I/O. No Mongo, no network, no bot.
  models/                 # pydantic: SwingPosition, Recommendation, Candidate, Stamped…
  calc/                   # indicators.py adx.py relative_strength.py pivots.py
                          # volume_profile.py vsa.py sizing.py xirr.py costs.py barriers.py
  rules/                  # veto_ladder.py setups.py scoring.py validation.py dip_tiers.py
                          # ← the veto ladder lives here, as pure functions

adapters/                 # ★ NEW — all outbound I/O. One module per external system.
  market/    base.py yfinance.py nse.py bse.py broker.py    # all satisfy one Protocol
  news/      rss.py gnews.py filings.py
  funds/     mfapi.py amfi.py
  llm/       base.py gemini.py groq.py                        # key mgmt, retries, breaker
  notify/    telegram.py ntfy.py emailjs.py
  repo/      positions.py recommendations.py journal.py
             screener.py investments.py storage.py            # ← the only Mongo callers

features/                 # use-cases. Orchestration only: compose domain + adapters.
  swing/  screener/  risk/  investments/  news_scanner/
  storage/  performance/  chat/  bot/  system/  scheduler/

prompts/                  # swing.md, general.md  (static, loaded once)
docs/                     # YAML knowledge modules (+ local PDFs, never indexed)
scripts/                  # backtest.py, index_docs.py, migrations/
tests/                    # unit/  rules/  adapters/  integration/
```

**What goes where, in one line each:**

| Layer | Contains | Never contains |
|---|---|---|
| `core/` | infrastructure every layer needs | business rules, feature logic |
| `domain/` | maths, rules, models — deterministic | Mongo, HTTP, Telegram, `datetime.now()` as a default |
| `adapters/` | talking to the outside world | trading decisions |
| `features/` | "when X happens, do Y then Z" | maths, raw Mongo queries, HTTP clients |

---

# 2. The dependency rule (the important part)

```
        ┌──────────┐
        │ main.py  │
        └────┬─────┘
             ▼
        ┌──────────┐        may import: core, domain, adapters
        │ features │        never imports: another feature's service
        └────┬─────┘
      ┌──────┴──────┐
      ▼             ▼
┌──────────┐  ┌──────────┐   adapters may import: core, domain/models
│ adapters │  │  domain  │   domain imports: stdlib, pandas, pydantic — nothing else
└────┬─────┘  └──────────┘
     ▼
┌──────────┐
│   core   │   imports: stdlib + third-party only
└──────────┘
```

**Three rules, and they're absolute:**

1. **`domain/` imports nothing from this project except other `domain/` modules.** If a function needs a database or a network call, it doesn't belong in `domain/`.
2. **No feature imports another feature.** Cross-feature needs go through `domain/` (logic) or `adapters/repo/` (data). This deletes all eight function-local imports.
3. **Only `adapters/repo/` touches Mongo.** Services receive data; they don't query for it.

**Why this specifically:** every one of the five findings in §0.1 becomes structurally impossible. Blocking I/O can't leak into orchestration because I/O lives behind async adapters. Circular imports can't form because dependencies are a DAG. God functions can't form because the maths lives elsewhere. And the veto ladder becomes a pure function you can test with a table of inputs — which is the single most valuable test in the project.

**Enforce it in CI**, not by discipline:

```python
# tests/test_architecture.py
FORBIDDEN = {
    "domain":   ("features.", "adapters.", "core.database", "motor", "aiohttp", "aiogram"),
    "adapters": ("features.",),
}
def test_layer_boundaries():
    for layer, banned in FORBIDDEN.items():
        for path in Path(layer).rglob("*.py"):
            src = path.read_text()
            for bad in banned:
                assert f"import {bad}" not in src and f"from {bad}" not in src, \
                    f"{path} violates layering: imports {bad}"
```

Twenty lines that prevent the architecture from silently decaying.

---

# 3. Coding standards

## 3.1 Async discipline — the rule that fixes the live bug

**Never call synchronous I/O inside `async def`.** Every blocking library (`yfinance`, `nsepython`, `requests`, `feedparser` parsing) goes through an executor, and it happens **in the adapter**, once — not at each call site.

```python
# adapters/market/yfinance.py
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="yf")

async def _run(fn, *a, **kw):
    return await asyncio.get_running_loop().run_in_executor(_POOL, partial(fn, *a, **kw))

async def get_history(symbol: str, period="6mo", interval="1d") -> pd.DataFrame:
    return await _run(_sync_history, symbol, period, interval)     # never blocks the loop
```

Also mandatory:
- **Timeout on every outbound call.** No unbounded waits.
- **Bounded concurrency** — `asyncio.Semaphore` around batch fetches, so 500 symbols don't open 500 sockets.
- **`asyncio.gather(..., return_exceptions=True)`** for fan-out, then handle each result explicitly.
- **Serialise Gemini calls** — RPM is the binding limit (`LLM_ORCHESTRATION.md` §2), so never fan out to the LLM.

## 3.2 No import-time side effects

Nothing at module scope may read `.env`, hit the network, touch Mongo, or raise.

```python
# ❌ gemini/service.py:98 today
gemini_manager = GeminiKeyManager(discover_gemini_keys())

# ✅ lazy, testable, injectable
@lru_cache(maxsize=1)
def get_key_manager() -> KeyManager:
    return KeyManager(discover_gemini_keys())
```

Same for `int(USER_ID)` in aiogram decorators ([bot/handlers.py:63](features/bot/handlers.py#L63)) — use a runtime filter so a missing env var can't break app import.

## 3.3 Typed configuration, validated once at startup

```python
# core/config.py
class Settings(BaseSettings):
    api_token: str
    mongo_uri: str
    telegram_token: str
    user_id: int
    trading_capital: float = 200_000
    risk_pct_per_trade: float = Field(1.0, ge=0.1, le=2.0)
    max_hold_days: int = Field(10, ge=2, le=10)          # your hard cap, enforced by type
    min_rr_to_t1: float = Field(2.0, ge=1.0)
    max_portfolio_heat_pct: float = Field(5.0, ge=1.0, le=20.0)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

@lru_cache(maxsize=1)
def settings() -> Settings: return Settings()            # raises once, at startup, with a clear message
```

Fail fast and loudly on missing required config — never silently degrade. And delete the `connetion_string` typo fallback ([config.py:11](core/config.py#L11)); a typo shouldn't be a supported interface.

## 3.4 Typed errors, and never swallow silently

```python
# core/errors.py
class StockAIError(Exception): ...
class DataUnavailable(StockAIError):      # expected: source down / blocked
    def __init__(self, source: str, reason: str): ...
class DataStale(StockAIError): ...        # expected: outside freshness budget
class LLMUnavailable(StockAIError): ...
class ValidationFailed(StockAIError): ...
class ConfigError(StockAIError): ...
```

Rules:
- **`except Exception: pass` is banned.** If a failure is genuinely acceptable, log it at debug and record it via `health.record(source, ok=False)`.
- **Distinguish expected from unexpected.** `DataUnavailable` is normal (NSE blocks you) → mark `UNAVAILABLE`, lower confidence, continue. An unexpected exception → log with traceback and alert.
- **Never let a failure silently produce a trading decision.** Fail closed to `WAIT`.

The six existing swallows are why `weekly_trend` was broken for months without a single symptom.

## 3.5 Structured logging, not `print()`

```python
# core/logging.py — stdlib only, no new dependency
log = logging.getLogger("stockai")
log.info("position_filled", extra={"symbol": "RELIANCE", "fill": 1248.5,
                                   "job": "swing_tracker", "run_id": run_id})
```

Every scheduled job gets a `run_id` threaded through, so one trading day's decisions can be reconstructed end to end. Keep the persisted `job_runs` record (capped collection) as the durable trail.

## 3.6 Pydantic at boundaries, dataclasses inside

Typed models at every boundary (HTTP, Mongo, LLM output). Plain dicts are fine *inside* a function, never across a boundary — `market_data_snapshot` being an untyped dict is why nobody noticed `pe_ratio` was fetched and then dropped.

## 3.7 Pure calculations take explicit inputs

```python
# ❌ untestable — reaches for the clock and the DB
def should_exit(position): 
    if (datetime.now() - position["fill_date"]).days >= 10: ...

# ✅ pure, deterministic, trivially testable
def should_time_exit(days_held: int, max_hold_days: int) -> bool:
    return days_held >= max_hold_days
```

Never default a parameter to `datetime.now()`. Pass the clock in. This is what makes point-in-time backtesting honest (`RECOMMENDATION_ENGINE.md` §4.4).

## 3.8 One Protocol per data source family

So the free broker feed (`FEATURES.md` F16) drops in without touching a single service:

```python
# adapters/market/base.py
class MarketData(Protocol):
    async def get_quote(self, symbol: str) -> Stamped: ...
    async def get_history(self, symbol: str, period: str, interval: str) -> pd.DataFrame: ...

# features/ just asks for the first source that works
SOURCES: list[MarketData] = [BrokerData(), YFinanceData(), NSEData()]
```

## 3.9 Idempotency on every write that can fire twice

Unique indexes, not queries — a `DuplicateKeyError` is a correct, cheap outcome:

| Write | Idempotency key |
|---|---|
| alerts | `(event, position_id, date)` |
| position events | `(position_id, date, state)` |
| filings seen | `filing_id` |
| job runs | `(job, date)` |
| one live position per symbol | partial unique index on `status ∈ {PENDING_ENTRY, OPEN}` |

---

# 4. Testing strategy — cover the money, not the lines

Aim for ~40 high-value tests, not 90% coverage. Every test below runs with **no database and no network**, because `domain/` is pure.

| Priority | Test | Why |
|---|---|---|
| 🔴 1 | **Veto ladder table test** — one row per hard veto, asserting `WAIT` and the named reason | The single most valuable test in the project. It's your risk control |
| 🔴 2 | **Indicator golden values** — RSI/ATR/ADX vs published references | `WEAKNESSES.md` W6 shipped wrong maths for months |
| 🔴 3 | **Triple-barrier grading** — target and stop both touched same day ⇒ stop wins | Wrong here means every performance stat is wrong |
| 🔴 4 | **Position sizing** — risk ≈ capital × risk% within one share; heat cap blocks the 6th | Sizing errors cost accounts |
| 🔴 5 | **Retention: sacred collections are never swept** at any age | Regression test for the bug that cost your track record |
| 🔴 6 | **Freshness: stale binding input forces `WAIT`** | Prevents trading on stale prices |
| 🟠 7 | **Recommendation validation** — level ordering, R:R < 1:2 rejected, probabilities sum to 100 | Catches malformed LLM output |
| 🟠 8 | **Fill simulation** — zone never touched ⇒ `CANCELLED`, not graded | Honest hit rate depends on it |
| 🟠 9 | **XIRR** vs a known-answer example | Money maths |
| 🟠 10 | **Cost model** — brokerage/STT/GST arithmetic | Net figures you act on |
| 🟠 11 | **Architecture boundaries** (§2) | Stops decay |
| 🟡 12 | **Adapter contract tests** with recorded fixtures | No live network in CI |
| 🟡 13 | **Backtest lookahead control** — shuffled signal ⇒ expectancy ≈ 0 | `RECOMMENDATION_ENGINE.md` §4.3 |

```python
# tests/rules/test_veto_ladder.py — the shape that matters
@pytest.mark.parametrize("ctx,expected_veto", [
    ({**BASE, "adx_14": 16.4, "setup": "BREAKOUT"},   "adx_below_20_on_breakout"),
    ({**BASE, "rs_rating": 42},                        "rs_rating_below_50"),
    ({**BASE, "days_to_results": 3},                   "results_within_5_days"),
    ({**BASE, "structure": "LH_LL"},                   "structure_lh_ll_for_long"),
    ({**BASE, "turnover_cr": 2.1},                     "turnover_below_5cr"),
    ({**BASE, "piotroski": 3},                         "piotroski_at_or_below_3"),
    ({**BASE, "quote_state": "STALE"},                 "binding_input_stale"),
    ({**BASE, "regime": "RISK_OFF"},                   "regime_risk_off"),
])
def test_hard_vetoes(ctx, expected_veto):
    r = evaluate(ctx)                      # pure function, no I/O
    assert r.recommendation == "WAIT"
    assert expected_veto in r.vetoes_fired

def test_clean_setup_passes():
    assert evaluate(BASE).recommendation in ("BUY", "ACCUMULATE")
```

**Free CI** — GitHub Actions on push: `ruff check`, `mypy domain/ core/`, `pytest`. Nothing else needed.

---

# 5. Migration strategy — strangler, never a rewrite

The system is live and you trade with it. **No big-bang refactor.**

**The four rules:**

1. **New code goes in the new structure.** Every feature from `IMPLEMENTATION.md` (swing, screener, risk, investments, storage, analytics) is written correctly from the start. That's most of the work, and it costs nothing extra.
2. **Old code migrates only when you touch it.** Editing `intraday/service.py` for Phase 1? Extract its maths to `domain/calc/`, its yfinance calls to `adapters/market/`, and leave the rest.
3. **Keep route paths stable.** Old paths become thin aliases to new handlers, so the Vercel dashboard never breaks. Rename URLs later, deliberately, once.
4. **Every phase ends green** — app boots, tests pass, bot responds. Never leave the tree half-migrated across a session.

**Migration order — by risk, highest first:**

| Order | Extract | Why first | Phase |
|---|---|---|---|
| 1 | `adapters/market/yfinance.py` with an executor | **fixes the live event-loop bug** (§0.1) | 0 |
| 2 | `domain/calc/indicators.py` | fixes broken maths (W6) and becomes testable | 0 |
| 3 | `core/{errors,logging,timeutils,config}.py` | everything else depends on these | 0 |
| 4 | `adapters/repo/*` for collections you're already changing | unblocks tests without Mongo | 1–2 |
| 5 | `domain/rules/veto_ladder.py` | the highest-value pure module | A |
| 6 | `adapters/llm/*` (key manager, breaker) | `LLM_ORCHESTRATION.md` | C |
| 7 | `adapters/notify/*` | `ALERTS_AND_BOT.md` | D |
| 8 | remaining feature services | opportunistic | ongoing |

---

# 6. Work packages (PR-sized, with acceptance criteria)

Each is one sitting and one commit. Ordered as `IMPLEMENTATION.md` sequences them.

| # | Package | Acceptance criteria |
|---|---|---|
| **WP1** | Skeleton + guardrails | `domain/ adapters/ tests/` exist; `test_architecture.py` passes; ruff + mypy + pytest green in CI |
| **WP2** | `core/` foundations | typed `Settings` raising on missing config; IST helpers; structured logging; error hierarchy; **no import-time side effects anywhere** |
| **WP3** | Market adapter | all yfinance/NSE calls behind async adapters with executors + timeouts; **zero sync I/O inside `async def`** (grep-verified); bot stays responsive during a scan |
| **WP4** | `domain/calc/indicators.py` | Wilder RSI/ATR/ADX matching published values in tests; `weekly_trend` returns a real value; `pandas_ta` imports deleted |
| **WP5** | API auth + guarded cleanup | `require_token` on every router but `GET /`; delete-all endpoints replaced by tiered `cleanup()` with dry-run |
| **WP6** | Cadence conversion | 3 in-session checks + EOD; event-only writes; `intraday_scans` dropped; `position_events` single-digit rows/day |
| **WP7** | Storage manager | tiers + TTL + capped collections; `/storage [days]` with dry-run; **sacred-collection test passes** |
| **WP8** | Freshness + data authority | `Stamped` on every input; session state; stale binding input ⇒ `WAIT`; prompt no longer claims Gemini price authority |
| **WP9** | LLM orchestration | error taxonomy; key cooldowns; circuit breaker; two-call pattern; schema validate + repair; Groq critic gate |
| **WP10** | Prompts + knowledge | `prompts/swing.md` + `general.md` live; 7 YAML modules indexed; tag-based retrieval; `prompt.txt` deleted |
| **WP11** | Swing lifecycle | `swing_positions` with fill simulation, trailing stops, partials, 10-day exit, R-multiple grading; manual `/track` uses the same lifecycle; `daily[]` ≤ 10 entries |
| **WP12** | Paper portfolio | AI picks paper-traded through the identical lifecycle; expectancy split by source |
| **WP13** | Alerts + bot menu | event catalogue with dedupe/quiet hours; `setMyCommands`; `/menu`; action buttons writing to the journal |
| **WP14** | Analytics Tier 1 | ADX, RS Rating percentile, swing pivots, base grade, PE-vs-median, `PEAD_52W`; each verified against a public chart |
| **WP15** | Veto ladder | pure `domain/rules/veto_ladder.py`; the full table test passes |
| **WP16** | Screener + regime | ranked candidates with reasons; exclusions applied; regime gates new entries |
| **WP17** | Risk engine | sizing, portfolio heat, sector caps, correlation warning, drawdown breaker |
| **WP18** | Journal + review | net-of-cost P&L, MAE/MFE, rolling expectancy, alpha-decay alert, weekly digest |
| **WP19** | Investments | mfapi.in NAV + XIRR; GOLDBEES/MON100 dip tiers; MON100 decomposition; monthly budget |
| **WP20** | News fast lane | `GET /news/latest` returns data (not "scan complete"); filings lane; anomaly detector; conditional GET; `pending_ai` queue |
| **WP21** | Backtest (local) | rules-only, LLM excluded; shuffled-signal control ≈ 0; 8–10 OOS windows; trial log; deflated Sharpe |
| **WP22** | Analytics Tier 2 | volume profile, VSA, OBV/CMF, anchored VWAP, PEAD, Piotroski, Altman Z, IV rank |

**WP1–WP4 come before everything else.** They're half a day, they fix a live bug, and every later package is cheaper because of them.

---

# 7. Definition of done (every package)

- [ ] Layer boundaries respected — `test_architecture.py` green
- [ ] No sync I/O inside `async def`; every outbound call has a timeout
- [ ] No `except Exception: pass`; failures logged and recorded to source health
- [ ] Pure logic in `domain/`, with a test that needs no DB or network
- [ ] Typed models at every boundary
- [ ] Writes that can fire twice have a unique index
- [ ] Money-critical paths have a test from the §4 table
- [ ] App boots, bot responds, existing dashboard routes still work
- [ ] `ruff` + `mypy domain/ core/` + `pytest` green

---

# 8. The eleven documents, and what each one governs

So there's never ambiguity about which document decides a question.

| Question | Authority |
|---|---|
| What's in scope? | `PROJECT_BRIEF.md` |
| Is this a real problem, and where in the code? | `WEAKNESSES.md` |
| Should we build this, and what does it cost to store? | `FEATURES.md` |
| What's the build order and the code for it? | `IMPLEMENTATION.md` |
| How is this metric calculated, and what's the threshold? | `ANALYTICS.md` |
| What exactly do we tell the model? | `PROMPTS.md` |
| What knowledge does RAG serve, and how is it retrieved? | `KNOWLEDGE_AND_PROMPTS.md` |
| How do we call Gemini/Groq reliably? | `LLM_ORCHESTRATION.md` |
| What fires an alert, at what priority? | `ALERTS_AND_BOT.md` |
| How do we get news faster? | `NEWS_FAST_LANE.md` |
| Is this edge real, and how do we validate it? | `RECOMMENDATION_ENGINE.md` |
| Where does this code live, and how is it written? | **`ENGINEERING.md`** (this) |

**Conflict resolution order:** data integrity → risk control → engineering standards → features → performance → convenience. A lower concern never overrides a higher one. If `FEATURES.md` wants something that `RECOMMENDATION_ENGINE.md` says can't be validated, the validation constraint wins.
