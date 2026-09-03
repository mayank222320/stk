# Progress Tracker

**How to use this file:** at the start of a session, find the first unchecked package. Read only the doc sections that package names in `AGENT_GUIDE.md` §7. Implement it. Run its verification command. Tick it here, add a dated note, commit. **Then stop** — one package per session.

**Status:** not started · **Last updated:** 4 September 2026

---

## Foundations — do these first (~half a day total)

WP3 fixes a live bug: the Telegram bot freezes during scans because `yfinance` is called synchronously inside `async def`.

- [ ] **WP1** — Skeleton and guardrails (`domain/`, `adapters/`, architecture test, CI)
- [ ] **WP2** — `core/` foundations (typed settings, IST clock, logging, errors)
- [ ] **WP3** — Market data adapter · **fixes the live event-loop bug**
- [ ] **WP4** — Indicators in `domain/calc/` · fixes the permanently-broken weekly trend

## Safety and storage (~half a day)

WP5 closes an API that is currently open to the internet with working delete-all endpoints.

- [ ] **WP5** — API auth and guarded cleanup
- [ ] **WP6** — Cadence conversion · removes ~99% of storage growth
- [ ] **WP7** — Storage and retention manager · `/storage [days]` with dry-run

## Data integrity (~2 days)

- [ ] **WP8** — Freshness and data authority · stale input forces `WAIT`
- [ ] **WP9** — LLM orchestration · key cooldowns, circuit breaker, critic gate
- [ ] **WP10** — Prompts and knowledge modules · the two prompts go live

## Trading core (~3 days)

- [ ] **WP11** — Swing position lifecycle · 2–10 day positions, real fills
- [ ] **WP12** — Paper portfolio
- [ ] **WP13** — Alerts and bot menu
- [ ] **WP14** — Analytics Tier 1 · ADX, RS Rating, swing pivots, `PEAD_52W`
- [ ] **WP15** — Veto ladder · *the most important test in the project*

## Engine (~4 days)

- [ ] **WP16** — Screener and regime
- [ ] **WP17** — Risk engine · sizing and portfolio heat
- [ ] **WP18** — Journal and review · net-of-cost R, MAE/MFE, decay alerts
- [ ] **WP19** — Investments · SIP XIRR, GOLDBEES/MON100 dip engine
- [ ] **WP20** — News fast lane · fixes the manual trigger returning nothing

## Later

- [ ] **WP21** — Backtest, local only · **rules only, never the LLM layer**
- [ ] **WP22** — Analytics Tier 2

---

## Session log

Append one entry per session. Keep it short — this is for the next session's benefit.

```
### YYYY-MM-DD — WP<n>
Done:      what actually landed
Verified:  the command you ran, and its result
Deviated:  anything you did differently from the doc, and why
Blocked:   anything you had to ask the user, and their answer
Next:      WP<n+1>
```

---

## Decisions and answers from the user

Record every question you asked and the answer, so no session asks twice.

| Date | Question | Answer |
|---|---|---|
| 2026-09-03 | Keep intraday scanner and virtual portfolio, or drop them? | **Keep both**, converted to swing duration. Nothing gets deleted |
| 2026-09-03 | Manual tracking or options tracking? | **Manual position tracking** — keep it as a first-class swing entry path |
| 2026-09-04 | One prompt or two? | **Two** — `swing.md` (special) and `general.md`, both long and explicit |

---

## Known live issues (fix in the package that owns them)

| Issue | Where | Owner |
|---|---|---|
| 🔴 Sync `yfinance` inside `async def` freezes the bot during scans | [portfolio/service.py:70-71](features/portfolio/service.py#L70-L71), [intraday/service.py:62-67](features/intraday/service.py#L62-L67) | WP3 |
| 🔴 API fully unauthenticated, incl. `DELETE .../all` endpoints | all routers | WP5 |
| 🔴 Per-minute inserts ≈ 150 MB/year on a 512 MB cluster | [scheduler/service.py:585-601](features/scheduler/service.py#L585-L601) | WP6 |
| 🟠 `weekly_trend` has always returned `"N/A"` (`pandas_ta` never installed) | [technical_indicators.py:148-157](features/market_data/technical_indicators.py#L148-L157) | WP4 |
| 🟠 Manual news trigger returns nothing (shares dedupe with the cron job) | [news_scanner/router.py:7-11](features/news_scanner/router.py#L7-L11) | WP20 |
| 🟠 Triggered articles beyond the first 3 are marked processed and never analysed | [news_scanner/service.py:149-173](features/news_scanner/service.py#L149-L173) | WP20 |
| 🟠 Swing trades graded the same afternoon they're issued | [performance/service.py:54-134](features/performance/service.py#L54-L134) | WP11 |
| 🟠 8 function-local imports working around circular dependencies | `features/scheduler`, `features/intraday` | WP1–3 |
