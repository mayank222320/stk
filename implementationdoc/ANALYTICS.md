# StockAI — Expert Analytics Specification

**Written:** 3 September 2026
**Purpose:** raise the analysis from competent-retail to professional standard for a **2–10 day swing horizon**.
**Companions:** `WEAKNESSES.md` W18/W19 (the gap) · `FEATURES.md` F17/F18 (the features) · `IMPLEMENTATION.md` Phase A (where it lands in code) · `KNOWLEDGE_AND_PROMPTS.md` (how the model is told to interpret these numbers)

---

## The one rule this document exists to enforce

> **Every number the prompt names must be computed, or explicitly marked `UNAVAILABLE`.**

Today the prompt names Wyckoff phases, VSA signals, Market Profile, RS-line slope, IV rank, PEG-vs-5-year-median, ROC and Supertrend — and the code computes **none of them** ([prompt.txt:62-70](prompt.txt#L62-L70), [:103-117](prompt.txt#L103-L117)). The model fills the gap by narrating. Everything below closes that gap with real arithmetic from free data.

**Design principle: gates, then ranking — never averaging.** Retail systems average signals ("3 bullish, 1 bearish → buy"). Desks run hard vetoes first, then rank the survivors. §J is that ladder.

**A note on what NOT to add:** RSI, MACD and ROC all measure the same thing. Adding a fourth momentum oscillator adds no information. The value below comes from adding **orthogonal families** — trend *strength*, relative strength *ranking*, structure, volume character, valuation context, and data freshness — not more of what you already have.

Each metric is specified as: **formula → threshold bands → how it's used** (`VETO` / `SCORE` / `LEVEL` / `CONTEXT`) → free source.

---

# §A — Trend strength (not just direction)

## A1. ADX / DI± — the single highest-value addition

**Why it matters most:** RSI and MACD are noise in a sideways market. Nothing in the current system can say "this stock isn't trending — skip it", which is why breakout setups get recommended in chop.

```python
def adx(h, l, c, n: int = 14):
    up, dn = h.diff(), -l.diff()
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()                       # Wilder
    pdi = 100 * pd.Series(plus_dm,  index=h.index).ewm(alpha=1/n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=h.index).ewm(alpha=1/n, adjust=False).mean() / atr
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1/n, adjust=False).mean(), pdi, mdi
```

| ADX(14) | Meaning | Action |
|---|---|---|
| < 20 | no trend — chop | **VETO breakout and momentum setups.** Mean-reversion only, if at all |
| 20–25 | trend forming | half size; require confirmation from §B |
| 25–40 | healthy trend | **the sweet spot for 2–10 day swings** |
| > 40 | very strong, often late | no fresh entries; manage existing, expect mean reversion |
| rising + `DI+ > DI−` | strengthening uptrend | SCORE bonus |
| falling from > 40 | trend exhausting | tighten stops |

**Use:** `VETO` + `SCORE`. **Source:** free (OHLCV).

## A2. Supertrend (ATR bands)

Promised in `prompt.txt:68`, never computed. Gives a single trend flip level, useful as a trailing-stop reference.

```python
def supertrend(h, l, c, n=10, mult=3.0):
    atr_ = atr(h, l, c, n); hl2 = (h + l) / 2
    upper, lower = hl2 + mult*atr_, hl2 - mult*atr_
    # standard ratchet: carry the band forward while trend is intact
```

**Use:** `LEVEL` (trailing stop), `CONTEXT`. **Source:** free.

## A3. Multi-timeframe alignment

Daily direction is meaningless if the weekly disagrees. Note the weekly-EMA calc has **never worked** (`WEAKNESSES.md` W6) — fix it first.

| Check | Rule |
|---|---|
| Weekly | close > weekly EMA20 **and** weekly EMA20 rising |
| Daily | close > EMA20 > EMA50 |
| Alignment score | both = full size · daily only = half size · weekly against = **VETO long** |

**Use:** `VETO` + `SCORE`. **Source:** free.

---

# §B — Relative strength, ranked

## B1. RS Rating — percentile rank across the universe

**The best-documented swing factor in existence**, and you already have the data once the screener downloads Nifty 500. A raw ratio is not enough — the edge is in the *ranking*.

```python
def rs_raw(c: pd.Series) -> float:
    """IBD-style weighted return: recent quarter counts double."""
    r = lambda n: c.iloc[-1] / c.iloc[-(n+1)] - 1
    return 0.4*r(63) + 0.2*r(126) + 0.2*r(189) + 0.2*r(252)

# across the universe, on the same date:
scores = {sym: rs_raw(df["Close"]) for sym, df in frames.items() if len(df) > 253}
ranked = pd.Series(scores).rank(pct=True) * 100          # → RS Rating 1..99
```

| RS Rating | Action |
|---|---|
| ≥ 85 | leadership — preferred swing universe |
| 70–85 | acceptable with strong structure |
| 50–70 | SCORE penalty; needs a specific catalyst |
| < 50 | **VETO long.** You're buying a laggard |

## B2. Mansfield RS / RS-line slope

`prompt.txt:116` demands "RS line actively sloping upward" — this computes it.

```python
rs_line = c / nifty_close                                  # ratio series
slope   = np.polyfit(range(20), rs_line.tail(20).values, 1)[0]
mansfield = (rs_line / rs_line.rolling(52*5).mean() - 1) * 100     # zero-centred
```

**Rule:** `slope > 0` required for a long. New RS-line high while price is still in a base is one of the highest-quality pre-breakout tells.

## B3. Risk-adjusted momentum & 52-week-high proximity

```python
risk_adj_mom = (c.iloc[-1]/c.iloc[-64] - 1) / (c.pct_change().tail(63).std() * np.sqrt(252))
pct_from_52w_high = (c.tail(252).max() - c.iloc[-1]) / c.tail(252).max() * 100
```

Momentum near 52-week highs is a well-documented effect. `< 5%` from the high = SCORE bonus; `> 25%` below = penalty for a breakout thesis.

**Use:** `VETO` + `SCORE` (highest single weight). **Source:** free.

---

# §C — Structure & levels

## C1. Algorithmic swing pivots

Your own stop rule is *"below a recent swing low"* ([prompt.txt:106](prompt.txt#L106)). Nothing detects swing lows, so stops are ATR-only or invented. This makes the rule executable.

```python
def swing_points(h, l, k: int = 3):
    """Fractal pivots: a high with k lower highs on both sides (and vice versa)."""
    ph = h[h == h.rolling(2*k+1, center=True).max()].dropna()
    pl = l[l == l.rolling(2*k+1, center=True).min()].dropna()
    return ph, pl

last_swing_low  = pl.iloc[-1]
structure = "HH_HL" if (ph.iloc[-1] > ph.iloc[-2] and pl.iloc[-1] > pl.iloc[-2]) else \
            "LH_LL" if (ph.iloc[-1] < ph.iloc[-2] and pl.iloc[-1] < pl.iloc[-2]) else "RANGE"
```

**Rules:** stop goes below `last_swing_low` (or 1.5×ATR, whichever is *tighter* while still outside noise). `LH_LL` structure = **VETO long**. `RANGE` = require §A1 ADX > 25 before trusting a breakout.

## C2. Base / consolidation detection

Defines the entry zone and the true breakout level — currently both are guessed.

```python
def base_quality(df, lookback: int = 20) -> dict:
    d = df.tail(lookback)
    depth = (d.High.max() - d.Low.min()) / d.Close.iloc[-1] * 100
    contraction = d.High.diff().abs().tail(5).mean() / d.High.diff().abs().head(5).mean()
    return {"days": lookback, "depth_pct": round(depth, 2),
            "breakout_level": round(float(d.High.max()), 2),
            "tight": bool(depth < 8), "contracting": bool(contraction < 0.7),
            "quality": "A" if depth < 8 and contraction < 0.7 else
                       "B" if depth < 15 else "C"}
```

Tight, volume-drying bases (depth < 8%, contracting range) break out with the highest follow-through. Grade C bases = SCORE penalty.

## C3. Volume profile — POC / VAH / VAL

`prompt.txt:65` promises Market Profile. Volume-at-price beats round-number support.

```python
def volume_profile(df, bins: int = 30, lookback: int = 60):
    d = df.tail(lookback)
    typical = (d.High + d.Low + d.Close) / 3
    hist, edges = np.histogram(typical, bins=bins, weights=d.Volume)
    i = int(hist.argmax())
    poc = (edges[i] + edges[i+1]) / 2                      # Point of Control
    order, cum, sel = hist.argsort()[::-1], 0, []
    for j in order:                                        # 70% value area around POC
        sel.append(int(j)); cum += hist[j]
        if cum >= 0.70 * hist.sum(): break
    return {"poc": round(poc,2), "vah": round(float(edges[max(sel)+1]),2),
            "val": round(float(edges[min(sel)]),2),
            "position": "above_value" if d.Close.iloc[-1] > edges[max(sel)+1] else
                        "below_value" if d.Close.iloc[-1] < edges[min(sel)] else "in_value"}
```

**Interpretation:** price above VAH with expanding volume = acceptance, breakout valid. Rejection back into value = failed breakout, exit. POC is the strongest magnet and the best partial-target reference.

## C4. Pivot points, Fibonacci, open gaps

- **Weekly/monthly pivots** (classic + Fibonacci variants) from prior period OHLC — the levels institutional desks actually watch.
- **Fib retracements** (38.2 / 50 / 61.8%) of the last impulse leg from §C1 pivots — pullback entry zones.
- **Unfilled gaps** — locate and size them; they act as magnets and as support/resistance. A trade whose target sits on the far side of a large unfilled gap deserves a lower probability.

**Use:** `LEVEL`. **Source:** free.

---

# §D — Volume & flow character

## D1. Quantified VSA — effort vs result

This is the big one for making Wyckoff/VSA real instead of narrated. Classification is pure arithmetic:

```python
def vsa_classify(df) -> str:
    r  = df.High - df.Low
    rr = (r / atr(df.High, df.Low, df.Close)).iloc[-1]      # effort: range vs ATR
    vr = (df.Volume / df.Volume.rolling(20).mean()).iloc[-1]  # effort: volume vs avg
    cp = ((df.Close - df.Low) / r).iloc[-1]                 # result: close position in bar
    up = df.Close.iloc[-1] > df.Close.iloc[-2]

    if vr > 1.8 and rr < 0.8 and cp > 0.6:  return "ABSORPTION_STOPPING_VOLUME"  # bullish
    if vr > 1.8 and rr < 0.8 and cp < 0.4:  return "DISTRIBUTION_SUPPLY"         # bearish
    if vr > 2.5 and rr > 1.8 and cp > 0.7:  return "CLIMACTIC_BUYING"            # late
    if vr > 2.5 and rr > 1.8 and cp < 0.3:  return "SELLING_CLIMAX"              # capitulation
    if vr < 0.7 and rr < 0.6 and up:        return "NO_DEMAND"                   # bearish
    if vr < 0.7 and rr < 0.6 and not up:    return "NO_SUPPLY"                   # bullish
    if vr > 1.5 and rr > 1.2 and up and cp > 0.7: return "PROFESSIONAL_BUYING"
    return "NEUTRAL"
```

**Rules:** `NO_DEMAND` or `DISTRIBUTION_SUPPLY` on a breakout candle = **VETO** (that's a failing breakout). `ABSORPTION` or `NO_SUPPLY` at a base low = SCORE bonus. `CLIMACTIC_BUYING` = don't chase, wait for the pullback (satisfies the do-not-chase rule at [prompt.txt:109](prompt.txt#L109) with a number instead of a vibe).

## D2. OBV, CMF, up/down volume ratio

```python
obv = (np.sign(c.diff()) * vol).fillna(0).cumsum()
mfm = ((c - l) - (h - c)) / (h - l).replace(0, np.nan)          # Chaikin money flow mult
cmf = (mfm * vol).rolling(21).sum() / vol.rolling(21).sum()
ud_ratio = vol[c.diff() > 0].tail(50).sum() / vol[c.diff() < 0].tail(50).sum()
```

| Signal | Meaning |
|---|---|
| Price at new high, OBV **not** at new high | negative divergence → SCORE penalty |
| `CMF > 0.05` sustained | accumulation |
| `U/D ratio > 1.25` | institutional accumulation (IBD-style measure) |
| `U/D < 0.8` | distribution → VETO long |

## D3. Delivery % relative to its own baseline

Your v2 prompt explicitly says a fixed 40% threshold is wrong and it must be relative to the stock's own history ([qmaf_v2_personalized.md:846-859](features/intraday/templates/qmaf_v2_personalized.md#L846-L859)). Nothing computes it.

```python
delivery_ratio = delivery_5d_avg / delivery_60d_baseline
# > 1.3 = genuine accumulation for THIS stock · < 0.8 = churn/trading interest only
```

**Use:** `SCORE` + soft `VETO`. **Source:** NSE (free, blockable — mark `UNAVAILABLE` when it fails, don't guess).

---

# §E — Volatility & risk sizing inputs

| Metric | Formula | Use |
|---|---|---|
| **ATR%** | `ATR(14) / close × 100` | 1.5–6% tradeable for swing; outside = VETO (too dead or too wild) |
| **ATR percentile** | rank of today's ATR vs 252 days | low percentile + tight base = coiled spring; high = reduce size |
| **BB width squeeze** | `(BBU−BBL)/BBM`, percentile-ranked | bottom decile = squeeze, breakout precursor → SCORE bonus |
| **Historical vol** | `stdev(daily returns, 20) × √252` | position sizing, expected-move sanity check |
| **Extension** | `(close − EMA20)/ATR` | **> 3 ATR = do not chase**, wait for pullback (numeric version of [prompt.txt:109](prompt.txt#L109)) |
| **Gap risk** | count of >2% overnight gaps in 60 days | overnight risk for a multi-day hold; feeds size reduction |
| **Beta / correlation** | 60-day regression vs `^NSEI` | portfolio heat and concentration (F3) |

**Source:** all free from OHLCV.

---

# §F — Fundamentals that matter over 2–10 days

Swing trades don't need a DCF. They need three things: is the valuation gate passable, is there an earnings catalyst, and is this a value trap.

## F1. PE vs 5-year median + real PEG

**Your valuation gate requires this exact figure** ([prompt.txt:103](prompt.txt#L103)) and nothing produces it — so today the gate is decided by guesswork.

```python
# Reconstruct historical PE: price history ÷ rolling 4-quarter trailing EPS
eps_q   = ticker.quarterly_income_stmt.loc["Diluted EPS"]      # newest first
eps_ttm = eps_q.iloc[::-1].rolling(4).sum().dropna()           # trailing 12m EPS by quarter
pe_hist = price_at_quarter_end / eps_ttm
pe_median_5y = float(pe_hist.tail(20).median())                # 20 quarters
pe_now  = info["trailingPE"]
gate_pass = pe_now <= 1.2 * pe_median_5y
peg = pe_now / (info.get("earningsGrowth") or np.nan) / 100    # PEG < 1.5 per your rule
```

**Caveat to state in the output:** yfinance quarterly history is typically 4–5 years, so the median may be computed over fewer than 20 quarters. Report the sample size; if fewer than 12 quarters, mark the gate `UNVERIFIED` rather than `PASS` — your framework forbids silently passing an unverifiable gate.

## F2. Earnings surprise + post-earnings drift (PEAD)

One of the few genuinely documented multi-week edges, and its window is **exactly** your 2–10 day horizon.

```python
dates = ticker.get_earnings_dates(limit=12)      # has Reported EPS, EPS Estimate, Surprise(%)
last_surprise_pct = float(dates["Surprise(%)"].dropna().iloc[0])
days_since = (now_ist().date() - last_report_date).days
gap_day_move = (close_after - close_before) / close_before * 100
```

> **Research note:** PEAD is confirmed in Indian markets (2002–2017, significant and robust to controls for beta, market cap, P/B, illiquidity and idiosyncratic volatility), and it **interacts with the 52-week high** — underreaction is strongest for positive surprises in stocks *close to* their 52-week high, with an additional effect when that high was set recently. Combining §F2 with §B3 gives you the best-evidenced setup available at a 2–10 day horizon. Full sourcing and the `PEAD_52W` archetype definition: `RECOMMENDATION_ENGINE.md` §1.1.

| Condition | Read |
|---|---|
| Surprise > +5%, gap up > 3%, within 10 trading days | **PEAD tailwind** — strongest SCORE bonus in §F |
| Surprise > +5% **and** within 8% of a 52-week high set in the last ~60 sessions | **`PEAD_52W` — highest-conviction setup in the system.** Score above every other factor |
| Negative surprise **and** far from the 52-week high | drift is downward and reinforced — **VETO long** |
| Surprise < −5%, gap down | drift is *down* — VETO long |
| Results **within 5 trading days** ahead | **HARD VETO** — gap risk dominates a 10-day hold |
| 2 consecutive quarters of accelerating YoY EPS growth | SCORE bonus |

## F3. Quality & solvency screens (keep value traps out)

- **Piotroski F-score** (0–9, from `income_stmt` / `balance_sheet` / `cashflow`): ROA > 0 · CFO > 0 · ΔROA > 0 · CFO > ROA (accrual quality) · Δleverage < 0 · Δcurrent ratio > 0 · no new share issuance · Δgross margin > 0 · Δasset turnover > 0. **≤ 3 = VETO long.**
- **Altman Z-score**: `1.2·WC/TA + 1.4·RE/TA + 3.3·EBIT/TA + 0.6·MVE/TL + 1.0·Sales/TA`. **< 1.8 = distress zone**, VETO for swing longs regardless of chart.
- **Promoter pledge / holding change** — from NSE shareholding pattern; rising pledge = VETO.

**Source:** free via yfinance financials + NSE filings. Refresh **once a day**, never in the hot path (W12 — `ticker.info` is slow and rate-limited).

---

# §G — Derivatives context

Not for intraday trading — for reading positioning behind a swing move. Your prompt demands all of this; none is computed.

| Metric | Formula | Read |
|---|---|---|
| **IV Rank** | `(IV − IV₅₂wₘᵢₙ)/(IV₅₂wₘₐₓ − IV₅₂wₘᵢₙ)×100` | > 80 before an event = volatility-crush risk ([prompt.txt:115](prompt.txt#L115)) |
| **IV Percentile** | % of last 252 days with IV below today | more robust than rank to single spikes |
| **Futures basis** | `(fut − spot)/spot × 100` | premium = bullish carry; sharp discount = bearish positioning |
| **Rollover %** | OI rolled to next series vs total | high rollover with price up = conviction longs |
| **OI build-up** | price Δ × OI Δ | ↑↑ long build-up (**real**) · ↑↓ short covering (**fake strength**) · ↓↑ short build-up · ↓↓ long unwinding |
| **PCR, Max Pain** | already implemented | support/resistance context, expiry magnet |

**Long build-up vs short covering is the distinction your prompt insists on** ([prompt.txt:115](prompt.txt#L115)) and it's a two-line calculation once you store yesterday's OI.

**Storage for IV rank:** you need IV history. Store one ATM-IV number per symbol per day (~30 bytes) from the option chain you already fetch — after a year you have a real IV distribution for ₹0 and ~8 KB.

---

# §H — Freshness: the data contract

Full code in `IMPLEMENTATION.md` 3.5. The analytics rules:

1. **Every value carries** `{value, source, captured_at, age_seconds, state}` with state ∈ `LIVE | DELAYED | LAST_CLOSE | STALE | UNAVAILABLE`.
2. **Budgets** (in session): quote ≤ 15 min · option chain ≤ 30 min · FII/DII T-1 · fundamentals ≤ 1 quarter · news ≤ 5 days. Indicators are **always** `LAST_CLOSE` — never describe them as live.
3. **Session state** `PRE_OPEN | OPEN | POST | CLOSED | HOLIDAY | WEEKEND` accompanies every report.
4. **Stale binding input (quote or indicators) ⇒ forced `WAIT`.** Other stale inputs ⇒ `data_confidence − 2` each.
5. **Every stored recommendation carries `as_of` and `worst_input_age_s`** so a report read two days later can't pass as current.
6. **Point-in-time discipline** in the backtest: `score_as_of(day)` may read nothing after `day`. This is the single bug that makes backtests look brilliant.

---

# §I — Trade analytics: the feedback loop

This is what actually improves a system over time, and it needs no external data — only your own closed positions.

## I1. MAE / MFE

```python
# from the position's bounded daily[] array (Phase 4.3)
mae_r = min((d["c"] - fill) / (fill - stop) for d in daily)   # max adverse excursion, in R
mfe_r = max((d["c"] - fill) / (fill - stop) for d in daily)   # max favourable excursion, in R
```

| Pattern across many trades | Diagnosis |
|---|---|
| Winners rarely dip below −0.4R | your stops are **too wide** — tighten and size up |
| Many stop-outs with MAE just past the stop | stops are **too tight**, inside noise — widen to 2×ATR |
| Average MFE ≫ average realised R | you're **exiting too early**, or T1 is too close |
| MFE rarely reaches T2 | T2/T3 are fantasy — recalibrate targets to ATR-realistic distances |

This one table is worth more than any new indicator.

## I2. Expectancy, R-distribution, fractional Kelly

```python
expectancy_r = win_rate*avg_win_r - (1-win_rate)*avg_loss_r     # must be > 0
payoff       = avg_win_r / avg_loss_r
kelly        = win_rate - (1 - win_rate)/payoff
risk_pct     = min(0.5 * kelly * 100, MAX_RISK_PCT)            # half-Kelly, hard-capped
```

Use **half-Kelly, capped** by your configured max risk. Full Kelly is too aggressive for a 30-trade sample, and a small sample overstates the edge.

## I3. Monte Carlo drawdown

Bootstrap-resample your realised R-distribution over the next 100 trades, 10,000 times → distribution of terminal equity and **max drawdown percentiles**. Tells you the drawdown you must be able to sit through, and your risk of ruin at the current size. Run locally (`IMPLEMENTATION.md` Phase 8).

---

# §J — The veto ladder (how it all combines)

Order matters. A stock must survive every gate before it is ranked at all.

### Level 1 — HARD VETOES (any single one ⇒ no trade, no exceptions)
1. Binding input `STALE`/`UNAVAILABLE` (§H)
2. Two deterministic price sources diverge > 2% (`IMPLEMENTATION.md` 3.1c)
3. Regime = `RISK_OFF` (F5)
4. Results within 5 trading days (§F2)
5. `ADX < 20` for a breakout/momentum setup (§A1)
6. Weekly trend against the trade (§A3)
7. Structure = `LH_LL` (§C1)
8. RS Rating < 50 (§B1)
9. Turnover < ₹5 Cr (liquidity — you must be able to exit)
10. Piotroski ≤ 3 or Altman Z < 1.8 (§F3)
11. R:R to T1 < 1:2 after the ATR stop is placed
12. Any QMAF entry gate = `FAIL`; portfolio heat cap breached (F3)

### Level 2 — SOFT PENALTIES (score deductions, size reductions)
RSI > 72 · extension > 3 ATR from EMA20 · ATR% outside 1.5–6% · base grade C · negative OBV/CMF divergence · U/D ratio < 1.0 · delivery ratio < 0.8 · IV rank > 80 pre-event · > 25% below 52-week high on a breakout thesis · `data_confidence` < 6

### Level 3 — RANKING (survivors only, weights to tune via backtest)

| Factor | Weight |
|---|---|
| RS Rating percentile (§B1) | 25 |
| Trend quality: ADX band + MTF alignment (§A) | 20 |
| Structure: base grade + proximity to breakout level (§C2) | 15 |
| Volume character: VSA class + U/D + delivery ratio (§D) | 15 |
| PEAD tailwind / earnings momentum (§F2) | 10 |
| Volatility fit: ATR% band + BB squeeze (§E) | 10 |
| Value-area position (§C3) | 5 |

### Level 4 — TIE-BREAK
Risk-adjusted momentum (§B3) → sector relative strength → tighter stop distance (better R:R).

**Sizing** then comes from §I2 (half-Kelly, capped) × regime multiplier (F5) × §E volatility adjustment — never from analytical confidence alone.

---

## Build order and cost

| Tier | Contents | Effort | Storage |
|---|---|---|---|
| **1** | §A1 ADX · §B1 RS Rating · §C1 swing pivots · §C2 base detection · §F1 PE-vs-median · §B3 52wk proximity | ~1 day | ~0 |
| **2** | §C3 volume profile · §D1 VSA · §D2 OBV/CMF/UD · §F2 PEAD · §F3 quality screens · §G derivatives · anchored VWAP | ~2 days | ~8 KB/yr (IV history) |
| **3** | §I MAE/MFE · expectancy · Kelly · Monte Carlo | ~0.5 day | ~0 |

Everything is computed from data you already fetch or can fetch free. Only shortlist results persist (`analytics_daily`, ~1.5 KB/symbol, TTL 30 days) → **~200 KB/year**.

**Verification for each metric:** compare against a free public chart (TradingView, Chartink, Zerodha) for 3 symbols before trusting it. Wilder-smoothed ADX/RSI/ATR should match to within rounding; if they don't, the smoothing is wrong (`WEAKNESSES.md` W6).
