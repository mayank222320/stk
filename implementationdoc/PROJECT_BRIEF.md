# StockAI — Project Brief & Handoff

**Written:** 3 September 2026 · updated 4 September 2026

> ## 👉 If you are an AI agent about to write code: read [`AGENT_GUIDE.md`](AGENT_GUIDE.md) first, not this file.
>
> It is the executor's entry point: the seven rules you must never break, the code conventions, and **22 step-by-step work packages** — each naming exactly which doc sections to read, which files to create, which to modify, which to leave alone, and how to verify you're done. One package per session.
>
> Track state in [`PROGRESS.md`](PROGRESS.md).
>
> **Do not read all thirteen documents.** They total ~6,500 lines. Each work package names the few sections it needs.

**This file** orients a human, or an agent that wants the scope and constraints in one page.

---

## What this project is

A personal, single-user **Indian-markets swing-trading assistant**. FastAPI backend + Telegram bot (aiogram) + MongoDB Atlas, using Gemini for research and Groq/Llama as a second model, with market data from yfinance and free NSE endpoints. A Vercel-hosted dashboard consumes the same API.

**Entry point:** [main.py](main.py) — lifespan wires Mongo, APScheduler jobs, and Telegram polling, then mounts one router per feature folder under [features/](features/).

**It is used by exactly one person.** No multi-tenancy, no user management, no scaling concerns. Optimise for correctness, low cost and low storage — not for generality.

---

## Confirmed scope (do not re-litigate)

| Decision | Detail |
|---|---|
| **Horizon** | **Swing only: 2–10 trading days. 10 is a hard cap.** No intraday trading. Positional beyond 1 month is out of scope for stocks. |
| **All existing features are kept** | Manual position tracking, in-session monitoring and alerts, and the virtual portfolio all stay — **converted to swing cadence**, not deleted. |
| **Also in scope** | 3 autopay mutual-fund SIPs (report-only, autopilot) and GOLDBEES + MON100 dip-buying with a monthly budget. |
| **Cost** | **Free resources only.** Paid options are researched and priced, with a recommendation, in `IMPLEMENTATION.md` Appendix C. |
| **Storage** | **MongoDB Atlas M0 — 512 MB, 10 GB/week transfer.** Storage is a first-class design constraint, not an afterthought. |
| **Analysis standard** | Must rise to **professional level** — every metric the prompt names must be *computed*, or marked `UNAVAILABLE`. |

---

## The four problems worth knowing before you touch anything

1. **A per-minute write loop is filling the cluster.** `custom_stock_minute_scan` runs every minute for 8 hours and writes unconditionally — ~960 rows/day, ~150 MB/year of data nobody reads. The scheduled deletions added to cope were wiping `performance_log`, which *is* the track record and cannot be recomputed. **The fix is cadence + tiered retention, not feature removal.**
2. **A 2–10 day trade is graded the same afternoon it's issued**, so the system's own hit-rate statistic is meaningless and no position is followed past day one.
3. **The LLM outranks the code's own arithmetic.** The prompt says "Gemini live_price is the authoritative CMP", and the model is asked to *produce* RSI/MACD/OHLCV that the code already computes exactly.
4. **The analysis is retail-grade and undated.** No trend-strength filter, no relative-strength ranking, no swing pivots, no volume profile, no valuation history, and nothing knows how old its own inputs are.

None of these is a rewrite. Items 1–3 are roughly a weekend of work.

---

## Document map

Read in this order. Each has one job.

| # | Document | What it gives you |
|---|---|---|
| 1 | **`PROJECT_BRIEF.md`** (this) | Scope, constraints, non-negotiables, reading order |
| 2 | **`WEAKNESSES.md`** | 20 findings ranked by trading cost, each with `file:line` references, plus a fix-order table |
| 3 | **`FEATURES.md`** | 19 features with impact, effort and **storage cost** per feature; priority map |
| 4 | **`IMPLEMENTATION.md`** | Phased build plan with working code sketches, endpoints, env vars, verification checklists, and the free/paid resource research |
| 4b | **`ENGINEERING.md`** | **Read before writing code.** Target structure (`domain`/`adapters`/`features`), the one-directional dependency rule enforced in CI, coding standards, the test table covering money-critical paths, strangler migration plan, and **22 PR-sized work packages** |
| 5 | **`ANALYTICS.md`** | The professional calculation spec: formulas, threshold bands, and the veto ladder that turns numbers into decisions |
| 6 | **`PROMPTS.md`** | The two prompts in full — **swing special** and **general** — each opening with an explicit Data Source Manifest, plus worked examples and the wiring table |
| 7 | **`KNOWLEDGE_AND_PROMPTS.md`** | Drop-in YAML knowledge modules (incl. a failure library) and the RAG retrieval fix. Its Part 3 is superseded by `PROMPTS.md` |
| 8 | **`LLM_ORCHESTRATION.md`** | Using Gemini + Groq robustly on free tiers: model routing, quota budget, error taxonomy, key cooldowns, circuit breaker, two-call pattern, critic gate, caching |
| 9 | **`ALERTS_AND_BOT.md`** | The full alert event catalogue (priorities, dedupe, quiet hours) and the Telegram command menu |
| 10 | **`NEWS_FAST_LANE.md`** | Cutting news latency from ~10–55 min to ~1–3 min with free sources — **additive**, the existing scanner is not changed |
| 11 | **`RECOMMENDATION_ENGINE.md`** | Research-backed robustness: why the LLM layer **cannot be backtested**, which edges have India-specific evidence, triple-barrier + meta-labeling, the full validation protocol, sample-size limits and alpha decay. Fully sourced |

Existing repo context worth reading: [prompt.txt](prompt.txt) (to be retired), [features/intraday/templates/qmaf_v2_personalized.md](features/intraday/templates/qmaf_v2_personalized.md) (the good framework, to be distilled), and [docs/](docs/) (18 theory YAML modules indexed into Mongo for RAG, plus 3 PDFs that must stay local).

---

## Build order

| Phase | Theme | Time | Doc |
|---|---|---|---|
| **E** | **Engineering foundations** — `domain`/`adapters` skeleton, CI guardrails, **fix the async event-loop bug**, correct indicators | ~4 h | **`ENGINEERING.md`** (WP1–4) |
| 0 | Safety & hygiene — API auth, IST clock, NSE holidays, model validation, failure alerts | ~4 h | `IMPLEMENTATION.md` |
| 1 | Convert intraday + virtual to swing cadence (event-only writes) | ~3 h | `IMPLEMENTATION.md` |
| 2 | Storage & retention manager — "clear older than N days" from bot + UI | ~5 h | `IMPLEMENTATION.md` |
| 3 | Data integrity + freshness/timestamps | ~1.5 d | `IMPLEMENTATION.md` |
| **C** | **LLM orchestration** — error taxonomy, key cooldowns, circuit breaker, two-call pattern, critic gate, caching | ~5 h | `LLM_ORCHESTRATION.md` |
| **B** | **Knowledge modules + the two prompts** | ~5 h | `PROMPTS.md`, `KNOWLEDGE_AND_PROMPTS.md` |
| 4 | Swing lifecycle — real, manual and paper positions | ~1.5 d | `IMPLEMENTATION.md` |
| **D** | **Alert catalogue + Telegram menu** | ~5 h | `ALERTS_AND_BOT.md` |
| A | Expert analytics (Tier 1, then Tier 2) | 1 d + 2 d | `ANALYTICS.md` |
| 5–9 | Screener + regime · risk + journal · SIP/ETF · backtest (local) · polish | ~7 d | `IMPLEMENTATION.md` |

**Weekend one = Phases 0, 1, 2.** That locks the API, removes the storage bomb, and gives you a safe cleanup control — the three things that are actively costing you something today.

Lettered phases (A–D) are cross-cutting specs with their own documents; insert them where the table shows.

---

## Non-negotiables for anyone implementing this

1. **Deterministic data wins.** Computed numbers are authoritative. The LLM supplies judgement, catalysts and narrative — never prices or indicator values.
2. **Compute it or mark it `UNAVAILABLE`.** No narrating a Wyckoff phase, IV rank or PE-vs-median that was never calculated.
3. **Gates before ranking.** Hard vetoes first, then score the survivors. Never average conflicting signals into a decision.
4. **`WAIT` is a valid answer.** Remove every instruction that pushes toward a confident directional call.
5. **Never auto-delete the track record.** Closed positions, the trade journal, monthly rollups and the SIP ledger are sacred; age-based sweeps must skip them, and deleting them requires an explicit confirmation phrase.
6. **Aggregate before deleting.** Monthly rollups run first, so purging detail never costs you the statistics.
7. **Don't store what you can recompute.** OHLCV history stays off Atlas; the backtest runs locally and pushes only a summary.
8. **Event-driven writes.** Persist on state change, not on a timer.
9. **Everything is timestamped.** Every input carries its age; stale binding inputs force `WAIT`.
10. **10 days is a hard cap.** Every actionable call states a horizon inside 2–10 trading days.
11. **The core loop must never require an LLM to be up.** Gates, sizing, tracking, alerts and dip detection are arithmetic. The LLM adds narrative and a second opinion on top of a system that already works without it — that's what makes it robust on free infrastructure.
12. **Tell the model everything explicitly.** Both prompts open with a Data Source Manifest naming every source, its tier, its freshness and its failure behaviour. Every data block must print `UNAVAILABLE` on failure rather than being omitted — an absent block invites the model to fill the gap from memory.
13. **Two independent models must agree before a BUY.** Gemini decides, Groq red-teams. Disagreement downgrades to `WAIT` with the objection shown.
14. **Every alert answers four things:** what happened, at what price, what to do, by when. Deduped once per event per position per day, with priorities — an alert that always screams gets ignored.
15. **Pure logic separated from I/O, dependencies pointing one way.** `domain/` holds maths and rules and imports nothing from the project; `adapters/` own all I/O; `features/` only orchestrate. Enforced by a CI test, not by discipline. This is what makes the money-critical paths testable without a database or a network.
16. **Never sync I/O inside `async def`.** Every blocking library goes through an executor with a timeout, inside its adapter. Getting this wrong is what currently freezes the Telegram bot during scans.
17. **Strangler migration, never a rewrite.** New code goes in the new structure; old code migrates when touched; route paths stay stable so the dashboard never breaks; every work package ends green.

---

## Quick facts verified for this brief (September 2026)

- **Gemini free tier:** Flash/Flash-Lite only — Pro models left the free tier on 1 Apr 2026. `gemini-3.6-flash` is the current stable Flash (GA 21 Jul 2026); the project currently defaults to `gemini-3.5-flash`. Limits ≈ 10 RPM / 250k TPM / 1,500 requests per day. Structured output (`responseMimeType` + `responseSchema`) is supported and, on Gemini 3, composes with Google Search grounding.
- **Groq free tier:** ≈ 30 RPM / 14,400 requests per day — ample for a second-opinion critic model.
- **Atlas M0:** 512 MB storage, 500 connections, 500 collections, 10 GB in / 10 GB out per week. Shared tiers don't support `compact`, so prefer TTL and dropping collections over mass deletes.
- **Free Indian broker data APIs:** Angel One SmartAPI, Fyers, Dhan and Upstox all offer free API access including market data. Zerodha's free "Personal" tier **excludes** market data; the ₹500/month Kite Connect plan includes live and historical.
- **Free mutual-fund NAV:** `https://api.mfapi.in/mf/{scheme_code}` — no key, no registration, full history, AMFI-sourced.
- **Render free tier:** web services spin down after 15 minutes idle (30–60 s cold start), so in-process cron is unreliable there; cron-job.org gives 7 free jobs at 1-minute granularity and is more punctual than GitHub Actions.

Sources for each are listed at the foot of `IMPLEMENTATION.md` and `FEATURES.md`.
