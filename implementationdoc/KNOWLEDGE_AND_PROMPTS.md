# StockAI — Knowledge Base & Prompt Rewrite

**Written:** 3 September 2026
**Answers:** "do the YAML docs need updating, or the prompt?" — **both, but in different ways.**
**Companions:** `WEAKNESSES.md` W11/W14/W20 · `ANALYTICS.md` (the numbers these rules interpret) · `IMPLEMENTATION.md` Phase B (build steps)

Everything here is drop-in content: new YAML files for `docs/`, and replacement prompt files. After adding YAML, re-index with `python -m features.knowledge_base.indexer`.

---

## Direct answer

| Asset | Verdict | Why |
|---|---|---|
| **`docs/00`–`18` theory YAMLs** | **Keep as-is. Do not rewrite.** | Wyckoff, Dow, candlesticks, Elliott, psychology — a frontier model already knows this theory. Retrieving prose costs tokens without adding accuracy. Harmless to keep; not worth effort to expand. |
| **`docs/13_Risk_Management.yaml`** | **Exemplar — copy its shape** | It's the one module with *numbers* (0.5/1/2% risk, "survive first"). That's what makes a chunk useful to a decision engine. |
| **The 3 PDFs in `docs/`** | Keep local, **never index** | ~12 MB. The indexer only globs `*.yaml` — keep it that way (`WEAKNESSES.md` W20, Atlas M0 budget). |
| **New YAML modules** | **Add 7** (below) | Nothing currently encodes *this system's* rules: swing playbook, metric thresholds, freshness, sizing, SIP/ETF policy, chat standards, and its own past mistakes. |
| **Retrieval** | **Must be fixed** | Currently queried with the ticker symbol against generic theory (`WEAKNESSES.md` W14) — returns near-random chunks. Tag chunks, query by intent. |
| **`prompt.txt`** | **Retire it** | It instructs "NEVER refuse", "MUST NOT use disclaimers", "Always be confident" ([prompt.txt:4-6](prompt.txt#L4-L6)) — a direct BUY bias — and it's the prompt your morning reports actually use. |
| **`qmaf_v2_personalized.md`** | **Distill into the new core** | Far better content (WAIT/NO TRADE, data integrity, 2–10 day default) but 1,900 lines, and ~40% is now redundant because the numbers are computed. |

---

# PART 1 — New YAML modules

## `docs/19_Swing_Playbook.yaml`

The rulebook the system currently lacks: what a valid 2–10 day setup *is*.

```yaml
module:
  id: 19
  name: Swing Trading Playbook (2-10 days)
  tags: [swing, setup, entry, exit, stop, target, core]
horizon:
  min_days: 2
  max_days: 10
  hard_cap: 10
  rule: >
    Every actionable call states a validity horizon within 2-10 trading days.
    At day 10 the position is force-reassessed, never silently held.
setups:
  breakout:
    definition: Close above a base high formed over 15-40 days
    requires:
      - base_quality in [A, B]
      - adx_14 > 25
      - volume_ratio > 1.5 on the breakout candle
      - vsa_class not in [NO_DEMAND, DISTRIBUTION_SUPPLY]
      - rs_rating >= 70
    entry_zone: breakout_level to breakout_level * 1.02
    do_not_chase: extension_atr > 3 from ema_20
    stop: below base low, or 1.5x ATR, whichever is tighter but outside noise
    targets: [1.5R, 2.5R, 4R]     # cap T3 at a realistic ATR-based distance
    invalidation: close back inside the base on above-average volume
    typical_hold_days: 3-8
  pullback:
    definition: Uptrend retracing to EMA20 / prior swing high / value area
    requires:
      - structure == HH_HL
      - adx_14 > 20
      - rsi_14 between 40 and 55
      - vsa_class in [NO_SUPPLY, ABSORPTION_STOPPING_VOLUME] preferred
      - weekly_trend == above_weekly_ema20
    entry_zone: ema_20 +/- 1%, or the 38.2-50% fib of the last leg
    stop: below last_swing_low
    targets: [prior swing high, 1.618 extension, measured move]
    invalidation: close below last_swing_low, or EMA50 breach on volume
    typical_hold_days: 4-10
  reversal:
    definition: Downtrend exhaustion with a confirmed higher low
    requires:
      - vsa_class == SELLING_CLIMAX in the last 10 sessions
      - a confirmed higher low after the climax
      - obv_divergence == positive
      - piotroski >= 5     # do not catch a falling knife with weak fundamentals
    stop: below the climax low
    note: Lowest win rate of the three. Half size only.
    typical_hold_days: 5-10
  momentum_continuation:
    definition: Leadership stock resuming after a shallow 3-8 day rest
    requires: [rs_rating >= 85, adx_14 between 25 and 40, pct_from_52w_high < 8]
    typical_hold_days: 2-6
management:
  after_t1: book 40%, move stop to breakeven
  after_t2: book 40%, trail remainder below prior swing low or EMA20
  trailing: ratchet upward only, never loosen a stop
  time_exit: at day 10 force EXIT or a documented re-entry decision
  overnight: swing positions are held overnight by design; gap risk is priced in via sizing
forbidden:
  - Intraday square-off advice ("exit before 3:10 PM") for a swing position
  - Averaging down on a losing position
  - Widening a stop after entry
  - Entering when results fall inside the intended hold window
  - Any recommendation without a stop and a stated invalidation
```

## `docs/20_Expert_Calculations.yaml`

Mirrors `ANALYTICS.md` so the model interprets every number **the same way every time** — this is what stops "RSI 58 is bullish" one day and "neutral" the next.

```yaml
module:
  id: 20
  name: Expert Calculation Thresholds
  tags: [calculations, thresholds, interpretation, core]
  rule: >
    These bands are authoritative. Never invent a different interpretation.
    If a metric is absent from the input, state UNAVAILABLE - never estimate it.
adx_14:
  bands:
    - {max: 20,  label: no_trend,      action: VETO breakout and momentum setups}
    - {max: 25,  label: forming,       action: half size, require confirmation}
    - {max: 40,  label: healthy_trend, action: preferred zone for 2-10 day swings}
    - {max: 100, label: very_strong,   action: no fresh entry, manage existing}
rs_rating:
  bands:
    - {min: 85, label: leadership,  action: preferred}
    - {min: 70, label: acceptable,  action: allowed with strong structure}
    - {min: 50, label: laggard,     action: score penalty, needs a catalyst}
    - {min: 0,  label: weak,        action: VETO long}
rsi_14:
  bands:
    - {max: 30, label: oversold}
    - {max: 45, label: bearish_momentum}
    - {max: 55, label: neutral}
    - {max: 68, label: bullish_momentum, note: preferred entry band for swing}
    - {max: 72, label: extended}
    - {max: 100, label: overbought, action: do not initiate}
vsa_class:
  bullish: [ABSORPTION_STOPPING_VOLUME, NO_SUPPLY, PROFESSIONAL_BUYING]
  bearish: [DISTRIBUTION_SUPPLY, NO_DEMAND]
  late:    [CLIMACTIC_BUYING]
  capitulation: [SELLING_CLIMAX]
  rule: NO_DEMAND or DISTRIBUTION_SUPPLY on a breakout candle VETOES the breakout
atr_pct:
  tradeable_range: [1.5, 6.0]
  outside: VETO - too illiquid/dead below, too erratic above
extension_atr:
  do_not_chase_above: 3.0
  rule: Wait for a pullback toward VWAP/EMA20 instead of a market order
delivery_ratio:      # 5-day avg vs the stock's OWN 60-day baseline
  accumulation_above: 1.3
  churn_below: 0.8
  rule: Never use a fixed 40% delivery threshold across all stocks
oi_buildup:
  price_up_oi_up:     long_buildup       # genuine strength
  price_up_oi_down:   short_covering     # NOT genuine strength
  price_down_oi_up:   short_buildup
  price_down_oi_down: long_unwinding
iv_rank:
  crush_risk_above: 80
  rule: Flag volatility-crush risk before events; avoid long options at high IV rank
valuation_gate:
  pass_if: peg < 1.5 OR trailing_pe <= 1.2 * pe_median_5y
  unverified_if: pe_history_quarters < 12
  rule: An unverifiable gate is UNVERIFIED, never PASS
quality_screens:
  piotroski_veto_at_or_below: 3
  altman_z_distress_below: 1.8
structure:
  HH_HL: uptrend - longs allowed
  LH_LL: downtrend - VETO long
  RANGE: require adx_14 > 25 before trusting a breakout
```

## `docs/21_Data_Freshness_Rules.yaml`

```yaml
module:
  id: 21
  name: Data Freshness & Timestamp Rules
  tags: [data, freshness, timestamp, integrity, core]
states: [LIVE, DELAYED, LAST_CLOSE, STALE, UNAVAILABLE]
budgets_in_session_seconds:
  quote: 900
  option_chain: 1800
  fii_dii: 129600        # T-1 is normal
  fundamentals: 7776000  # one quarter
  news: 432000           # 5 days
  indicators: null       # always LAST_CLOSE - never call these live
session_states: [PRE_OPEN, OPEN, POST, CLOSED, HOLIDAY, WEEKEND]
rules:
  - Never describe data as live, real-time or exchange-verified unless its state is LIVE.
  - Always open an analysis with AS-OF timestamp and SESSION state.
  - Indicators are computed from the last daily close. Say so.
  - A STALE or UNAVAILABLE binding input (quote, indicators) forces WAIT.
  - Each non-binding stale input reduces data_confidence by 2.
  - Outside market hours, every price is LAST_CLOSE. State the date of that close.
  - Never fill a missing value from memory or from a general sense of the price.
  - If a number is not in the supplied data blocks, it is UNAVAILABLE.
```

## `docs/22_Position_Sizing_Rules.yaml`

Extends `13_Risk_Management.yaml` with executable arithmetic.

```yaml
module:
  id: 22
  name: Position Sizing & Portfolio Risk
  tags: [sizing, risk, portfolio, kelly, core]
per_trade_risk_pct: {conservative: 0.5, normal: 1.0, aggressive: 2.0, never_exceed: 2.0}
formula:
  risk_amount: capital * risk_pct / 100 * regime_multiplier
  quantity: floor(risk_amount / (entry - stop))
  notional: quantity * entry
caps:
  max_portfolio_heat_pct: 5.0     # sum of open risk across all positions
  max_open_positions: 5
  max_per_sector: 2
  max_single_stock_pct: 15
  correlation_warn_above: 0.7     # 60-day vs an existing position
regime_multiplier: {RISK_ON: 1.0, NEUTRAL: 0.5, RISK_OFF: 0.0}
kelly:
  formula: f = win_rate - (1 - win_rate) / (avg_win_r / avg_loss_r)
  use: half_kelly
  cap_at: per_trade_risk_pct.never_exceed
  min_sample_trades: 30
rules:
  - Never size from analytical confidence. Size from stop distance and volatility.
  - Capital deployed is not exposure. State notional separately when leveraged.
  - Reject any trade whose R:R to T1 is below 1:2.
  - If a gate was bypassed by exception, reduce size and say by how much.
  - After 3 consecutive losses or a 10% account drawdown, halve size for a week.
```

## `docs/23_SIP_ETF_Playbook.yaml`

```yaml
module:
  id: 23
  name: SIP & ETF Accumulation Playbook
  tags: [sip, etf, gold, mon100, mutual_fund, allocation]
fixed_sips:
  status: AUTOPILOT
  funds: [Navi Nifty 50 Index Direct Growth, Parag Parikh Flexi Cap Direct Growth,
          Motilal Oswal Nifty Midcap 150 Index Direct Growth]
  rules:
    - Never recommend pausing, stopping or timing these based on market conditions.
    - Report XIRR, invested value and current value. Do not give price targets.
    - Escalate only on fund-level issues - expense ratio hike, manager or mandate
      change, AUM collapse, SEBI action, sustained multi-year benchmark lag.
dip_buy_etfs:
  instruments: [GOLDBEES, MON100]
  tiers:
    - {name: MILD_DIP,   pct_below_20d_high: 2, rsi_below: 55, deploy_pct: 33}
    - {name: GOOD_DIP,   pct_below_20d_high: 4, rsi_below: 45, deploy_pct: 50,
       extra: at or below 20 DMA}
    - {name: STRONG_DIP, pct_below_20d_high: 7, rsi_below: 35, deploy_pct: 100,
       extra: near 50 DMA}
    - {name: MONTH_END_DEPLOY, condition: days_left <= 2 and budget remaining,
       deploy_pct: 100, note: Dip-waiting must never become never-buying}
mon100_decomposition:
  required: true
  components: [ndx_move_pct, inr_move_pct, premium_or_tracking_pct]
  rules:
    - Always separate index move, currency move and ETF premium/discount.
    - Flag premium above 1.5% - buying a wide premium loses money even if NDX rises.
    - Never conflate Nasdaq performance with rupee-denominated return.
goldbees:
  track: [price vs domestic gold, premium/discount to NAV, allocation drift vs MON100]
  rule: Flag allocation drift; never auto-instruct rebalancing unless asked.
```

## `docs/24_Chat_Reply_Standards.yaml`

The expert-level reply discipline (see Part 3 for the prose version used in the prompt).

```yaml
module:
  id: 24
  name: Expert Chat Reply Standards
  tags: [chat, reply, communication, standards, core]
always:
  - Answer the actual question in the first line. No preamble.
  - State data vintage explicitly ("as of 3 Sep close"), never imply live data.
  - Separate CONFIRMED / INFERRED / UNVERIFIED evidence.
  - Show the calculation, not just the conclusion
    ("stop 1196 = entry 1238 - 1.5 x ATR 28.4").
  - Give the falsifier - the specific condition that would break the thesis.
  - Use a zone when the evidence does not support a single number.
  - Check open positions and portfolio heat before proposing a new entry.
  - Lock every actionable answer to the 2-10 day horizon, or say it is out of scope.
  - Quote net-of-cost figures for anything actionable (brokerage, STT, GST, tax bucket).
  - Close with the single thing to watch next session.
never:
  - Never answer a price question from memory. No fetched price means say so.
  - Never validate a poor trade idea to please the user. State the bad R:R plainly.
  - Never produce a directional call just because one was requested - WAIT is an answer.
  - Never give multi-month or long-term price targets on stocks.
  - Never claim to have learned from past trades unless a trade log is in context.
  - Never present a remembered tax rate, session timing or fee as currently verified.
  - Never list a data source that was not actually used in this response.
length:
  factual_question: 1-2 sentences
  educational: 3-4 short paragraphs maximum
  follow_up: reference prior analysis, do not repeat the full structure
  full_analysis: use the structured output contract
clarifying_questions:
  max_per_reply: 1
  only_if: the answer materially changes based on the response
expertise_adaptation:
  beginner: plain language, expand acronyms once
  intermediate: standard depth (default)
  expert: dense, institutional vocabulary, no hand-holding
```

## `docs/25_Failure_Library.yaml`

**The most valuable module long-term.** Your v2 prompt claims "continuous self-correction and adaptive learning" ([qmaf_v2_personalized.md:1382-1403](features/intraday/templates/qmaf_v2_personalized.md#L1382-L1403)) but admits it cannot persist learning. This is how it actually persists: seed it now, then append one entry per losing trade from the journal (F10), and retrieve it by setup tag before every new recommendation.

```yaml
module:
  id: 25
  name: Failure Library (grows from the trade journal)
  tags: [failure, lessons, postmortem, core]
  maintenance: >
    Append one entry per losing or badly-managed trade. Retrieve entries matching
    the candidate's setup_type before finalising any recommendation.
seed_entries:
  - id: F001
    pattern: Breakout bought while ADX < 20
    outcome: Price returned into the base within 2 sessions
    lesson: Trend strength is a precondition, not a nice-to-have. VETO below 20.
    tags: [breakout, adx, chop]
  - id: F002
    pattern: Entry taken 4+ ATR above EMA20 on a gap-up
    outcome: Stopped out on the mean-reversion day
    lesson: Extension > 3 ATR means wait for the pullback. Never chase a gap.
    tags: [chase, extension, gap]
  - id: F003
    pattern: Position held through an earnings date inside the hold window
    outcome: Gap through the stop; realised loss far exceeded 1R
    lesson: Results within 5 trading days is a hard veto. Stops do not work across gaps.
    tags: [earnings, gap_risk, event]
  - id: F004
    pattern: Rising price with falling OI read as strength
    outcome: Short covering exhausted; no follow-through
    lesson: Price up + OI down is not accumulation. Require long build-up.
    tags: [derivatives, oi, false_strength]
  - id: F005
    pattern: MON100 bought on a Nasdaq dip while the ETF traded at a wide premium
    outcome: Index recovered but the premium compressed; rupee return lagged
    lesson: Decompose index vs currency vs premium before deploying.
    tags: [mon100, etf, premium]
  - id: F006
    pattern: Recommendation issued on a stale quote after a failed fetch
    outcome: Entry zone and stop were both wrong by more than 2%
    lesson: A stale binding input forces WAIT. Never trade an unverified price.
    tags: [data, freshness, integrity]
```

---

# PART 2 — Fix retrieval (this is what makes the modules useful)

Adding modules changes nothing if retrieval can't find them. Currently the scheduler queries with the **ticker symbol** against generic theory ([scheduler/service.py:426](features/scheduler/service.py#L426)) — "RELIANCE" appears in none of your notes, so results are close to random, and failures return `[]` silently ([knowledge_base/service.py:65-67](features/knowledge_base/service.py#L65-L67)).

**1. Tag chunks at index time** — in `features/knowledge_base/indexer.py`:

```python
tags = (data.get("module", {}) or {}).get("tags", []) or [module_name.lower()]
docs = [{"source": module_name, "chunk_index": i, "text": chunk,
         "tags": tags, "module_id": (data.get("module") or {}).get("id"),
         "indexed_at": datetime.now(timezone.utc)} for i, chunk in enumerate(chunks)]
await col.create_index([("tags", 1)])
await col.create_index([("text", "text")], name="knowledge_text_index")
```

**2. Retrieve by intent, not by symbol:**

```python
INTENT_TAGS = {
    "swing_analysis":  ["swing", "setup", "calculations", "thresholds", "sizing", "failure"],
    "position_update": ["swing", "exit", "stop", "sizing", "failure"],
    "sip_etf":         ["sip", "etf", "gold", "mon100", "allocation"],
    "chat":            ["chat", "standards", "core"],
}

async def get_rag_context(intent: str, setup_type: str | None = None, top_k: int = 6) -> str:
    tags = INTENT_TAGS.get(intent, ["core"])
    if setup_type:
        tags.append(setup_type.lower())
    cur = mongo.db.knowledge_chunks.find({"tags": {"$in": tags}}, {"_id": 0, "text": 1, "source": 1})
    chunks = await cur.limit(top_k).to_list(None)
    if not chunks:
        await alert_ops("RAG empty", f"no chunks for intent={intent} — did the indexer run?")
    return format_rag_context(chunks)
```

**3. Assert at startup** that `knowledge_chunks` is non-empty and log one sample retrieval — so "RAG contributing nothing" can never again look identical to "nothing relevant".

**Storage:** all 25 modules ≈ **under 1 MB**. Sacred tier, never swept.

---

# PART 3 — Prompt rewrite

> ## ⚠️ SUPERSEDED BY `PROMPTS.md`
>
> **Use the two-version design in [`PROMPTS.md`](PROMPTS.md), not the sketch below.**
>
> | Final design | Purpose |
> |---|---|
> | `prompts/swing.md` (~330 lines) | **SWING SPECIAL** — actionable 2–10 day analysis and position management |
> | `prompts/general.md` (~290 lines) | **GENERAL** — chat, education, ETFs/SIPs/mutual funds, macro, charts and documents |
>
> Both are long and fully explicit by design, and both open with the same **Data Source Manifest** naming every live source, what it provides, its reliability tier, its freshness, and what to do when it fails. `PROMPTS.md` carries the complete text of both, plus worked examples and the wiring table.
>
> Everything from here to PART 4 is retained only as **component reference** — the earlier core-plus-three-tails sketch. Its content is folded into the two files above. Do not implement it separately.

## Architecture (superseded — content removed to avoid ambiguity)

The earlier sketch here proposed a shared core plus three task tails. **It has been removed**
so there is no chance of implementing the wrong design. The final two-prompt text lives in
[`PROMPTS.md`](PROMPTS.md) — build from that file only.

What carried over from the sketch into the final prompts: identity and scope, the DATA
AUTHORITY block, freshness rules, the veto ladder reference, "WAIT is valid", conflicting-
evidence handling, the failure-library check, risk and sizing, tax and costs, and the output
contract. All of it is present in `prompts/swing.md` and `prompts/general.md`.

**Token effect of the final design:** ~330 lines + data blocks, roughly 6-8k tokens per swing
call. Comfortable against free-tier Flash (250k TPM, 1,500 req/day) at ~35 calls/day — and far
cheaper than today's position-update path, which ships the 1,900-line v2 prompt. See
`LLM_ORCHESTRATION.md` §2.


# PART 4 — Expert-level reply standards, expanded

The behaviours that separate a desk analyst from a chatbot — most are missing today because `prompt.txt` optimises for sounding confident.

| Standard | What it looks like | Currently |
|---|---|---|
| **Answer first** | "No — R:R is 1:1.2, below your 1:2 rule." then the reasoning | ✅ partly (conciseness rules exist) |
| **Data vintage always** | "as of 3 Sep close; markets closed" | ❌ no session/timestamp awareness |
| **Show the arithmetic** | "stop 1196 = 1238 − 1.5 × ATR 28.4" | ❌ conclusions without workings |
| **State the falsifier** | "this breaks on a close below 1196, or if results land before 12 Sep" | ⚠️ invalidation asked for, rarely numeric |
| **Zones over false precision** | "target zone 1305–1320" when structure is ambiguous | ✅ in v2, unused in practice |
| **Portfolio-aware** | "you already hold 2 banks; this makes it 3 — heat would hit 4.5%" | ❌ nothing reads open positions in chat |
| **Horizon-locked** | "over 2–10 days: … ; beyond that I won't forecast" | ⚠️ stated, not enforced |
| **Net of costs** | "T1 nets ≈ ₹2,840 after STT/GST/brokerage, before STCG" | ❌ never computed |
| **Refuses on stale data** | "I don't have a fetched price — last close 3 Sep was ₹1,238" | ❌ answers from memory |
| **Anti-sycophancy** | "that's a chase — 4 ATR extended; wait for the EMA20 retest" | ❌ prompt says "always be confident" |
| **Owns past errors** | "my 28 Aug BUY was wrong — I under-weighted the earnings gap" | ⚠️ claimed, unsupported |
| **One thing to watch** | "watch whether it holds 1,240 on volume tomorrow" | ❌ |

---

# PART 5 — Migration checklist

1. [ ] Add `docs/19`–`25` YAML files.
2. [ ] Extend the indexer to write `tags` + `module_id`; create the `tags` index.
3. [ ] Re-index: `python -m features.knowledge_base.indexer`. Confirm chunk count rose.
4. [ ] Replace symbol-keyword RAG with `get_rag_context(intent, setup_type)`.
5. [ ] Add startup assertion: `knowledge_chunks` non-empty; log one sample retrieval.
6. [ ] Create `prompts/swing.md` and `prompts/general.md` from `PROMPTS.md`.
7. [ ] Add `pick_prompt(intent)` and route every call site per the `PROMPTS.md` wiring table.
8. [ ] Ensure every data block emits an explicit `UNAVAILABLE` line on failure — an absent block invites the model to fill the gap from memory.
9. [ ] Load both prompts once at startup and cache them (they're static; keeps the cacheable prefix byte-identical).
10. [ ] Delete `prompt.txt`; archive `qmaf_v2_personalized.md` under `docs/archive/`.
11. [ ] Wire the response schema (`IMPLEMENTATION.md` 3.2) into every swing call.
12. [ ] Wire the failure-library append into trade-journal close (F10).
13. [ ] Apply the orchestration layer from `LLM_ORCHESTRATION.md` (error taxonomy, key cooldowns, circuit breaker, two-call pattern, Groq critic gate).

**Verification**
- [ ] Ask "what is SBI price?" with no fetched quote → it refuses to recall a number.
- [ ] Feed a candidate with ADX 14 → output is `WAIT`, veto named as trend strength.
- [ ] Feed results-in-3-days → `WAIT`, veto named as event risk.
- [ ] Run on a Sunday → session `WEEKEND`, all inputs `LAST_CLOSE`, no `BUY`.
- [ ] Ask for a 6-month target → declines, offers the 2–10 day view instead.
- [ ] Propose an obviously bad trade → it says so rather than agreeing.
- [ ] Confirm no response contains a Wyckoff phase or IV rank that wasn't supplied.
