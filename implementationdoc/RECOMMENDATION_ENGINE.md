# StockAI — Robust Recommendation Engine (research-backed)

**Written:** 4 September 2026
**Question:** how do you make the recommendation engine genuinely robust — not just confident-sounding?
**Companions:** `ANALYTICS.md` (the calculations) · `LLM_ORCHESTRATION.md` (LLM reliability) · `IMPLEMENTATION.md` Phase 8 (backtest) · `FEATURES.md` F4/F9
**Sources:** listed at the end; every claim below is attributed.

---

# 0. Start here — the finding that changes your plan

**You cannot backtest the LLM part of your system. At all.**

An LLM's training corpus contains information from *after* any historical date you test against. Research on this ([Summoning the Oracle to Slay It](https://arxiv.org/pdf/2605.24564), 2026) puts it directly: "financial data and news articles are often available in training corpora without strict temporal boundaries," so the model "learns patterns from future events when processing historical contexts, creating an illusion of predictive power."

Ask Gemini in 2026 to analyse RELIANCE "as of March 2024" and it already knows how March 2024 resolved. Any backtest of an LLM-driven decision is contaminated by construction, and the contamination flatters you — the paper's own practical advice is to "be skeptical of exceptionally high backtest returns; they may indicate temporal contamination."

**What this means concretely for your Phase 8:**

| Layer | Backtestable? | How to validate it |
|---|---|---|
| Deterministic screener + gates + sizing (`ANALYTICS.md`) | ✅ Yes | Walk-forward on historical OHLCV (Part 4) |
| LLM research, thesis, critic verdict | ❌ **Never** | **Forward-only:** paper trading, from today onward |

So the backtest measures the *rules*. The LLM layer gets measured by your paper portfolio (`FEATURES.md` F19) going forward — which is now a validation instrument, not a toy. Log every LLM verdict from day one; in six months you'll have an honest answer about whether it adds value.

Mitigations the paper recommends that you *can* apply: prompts constrained to decision-time information only (your Data Source Manifest already does this), validation on genuinely unseen periods, and **requiring the model to express confidence rather than confident predictions** — which is exactly what `data_confidence` and the `WAIT` verdict are for.

---

# 1. What actually has an edge — for Indian markets, at a 2–10 day horizon

Most "signals" are repackaged momentum. Here's what the evidence supports, specifically for India and specifically at your horizon.

## 1.1 The flagship: positive earnings surprise + proximity to the 52-week high

This is the strongest research-backed combination available to you, and it fits your horizon exactly.

**PEAD exists in India.** Post-earnings-announcement drift was tested on Indian markets over 2002–2017 and found **statistically significant**, robust to sub-period analysis and to controls for beta, market capitalisation, price-to-book, illiquidity and idiosyncratic volatility ([PEAD Anomaly in India](https://www.scirp.org/journal/paperinformation?paperid=88060)).

**And it interacts with the 52-week high.** Investors "underreact to positive (negative) surprises of stocks close to (far from) their 52-week highs, which leads to a stronger subsequent upward (downward) drift." There's also a **recency effect** — if the 52-week high occurred recently rather than long ago, investors are more reluctant to buy after positive news, and that reluctance is "incremental to the proximity effect" ([Momentum Crashes and the 52-Week High](https://www.tandfonline.com/doi/abs/10.1080/0015198X.2023.2183706), *Financial Analysts Journal*).

**Implementable rule** (all inputs free, all in `ANALYTICS.md` already):

```
PEAD_SETUP  =  earnings surprise > +5%
            AND within 10 trading days of the report
            AND price within 8% of the 52-week high
            AND that 52-week high was set within the last ~60 trading days   ← recency
            AND gap-day move > +2% on above-average volume
```

Treat this as its own setup archetype with a **score bonus above every other factor**, because it has direct India-specific evidence at your holding period. The mirror case (negative surprise, far from the high) is a veto for longs.

## 1.2 Be humble about plain momentum in India

Contrary to the popular assumption, a study of NSE 500 constituents over July 2005–June 2016 found **value and momentum anomalies were explained by risk models**, while size and volume anomalies remained significant but had "faded substantially over time."

**So:** keep RS Rating as a *ranking* factor and a *gate* (don't buy laggards), but don't build the engine on the premise that momentum alone pays in India. The event-driven PEAD/52-week-high combination has better evidence than generic momentum here.

## 1.3 Quality and solvency are filters, not signals

Piotroski F-score and Altman Z-score don't predict a 6-day move. Their job is to **remove the left tail** — the value traps and distress cases that turn a −1R stop into a −4R gap. Use them as vetoes (`ANALYTICS.md` §F3), never as ranking factors.

## 1.4 Regime gating is standard practice and worth the effort

Using a long-term moving average to declare "risk-on / risk-off" and **enable or disable entire strategies** is well-established. An alternative regime measure worth considering is the **Hurst exponent**, which distinguishes trending from mean-reverting conditions and can permit trades only during persistent trending states.

Your F5 regime filter already does the 200-DMA/breadth/VIX version. Consider adding Hurst later — it's a cheap, orthogonal read on whether trend-following should be switched on at all.

## 1.5 Gates beat weighted averages

The literature on signal combination supports both gating and weighting, but for a system you need to *understand and trust*, the practical guidance is that "combining simple indicators with statistical measures often produces strategies that are both robust and interpretable."

That's your veto ladder (`ANALYTICS.md` §J). Keep it. Don't replace it with a learned weighting scheme you can't audit — especially at your sample size (Part 5).

---

# 2. Architecture: primary signal → meta-label → size

Your design already matches a well-known structure. Worth naming it, because that tells you how to evaluate each layer.

## 2.1 The triple-barrier method — you're already using it

López de Prado's **triple-barrier method** labels an outcome by whichever of three barriers is hit first: a **profit target**, a **stop loss**, or a **time limit**.

Your swing design — T1 / stop / 10-day cap — **is** the triple-barrier setup. That means:

- Your labels are already correct for evaluation. Grade by *which barrier was hit*, not by close-price P&L.
- Your `close_reason` field (`TARGET` / `STOP` / `TIME_EXIT`) is the label. Keep it clean; it's the foundation of everything measurable.
- The time barrier is not a nuisance to be worked around — it's what makes the label well-defined and prevents "still holding, might come back" from polluting your statistics.

## 2.2 Meta-labeling — what your Groq critic actually is

**Meta-labeling** is a two-model structure: a primary model decides the **side** (long/short), and a secondary model decides **whether to act and at what size**. The secondary model raises precision without touching the primary's logic.

Your Gemini-decides → Groq-red-teams design (`LLM_ORCHESTRATION.md` §8) is meta-labeling. Which tells you three things:

1. **The division of labour is right.** The primary should be free to generate candidates; the meta-layer earns its keep by *filtering* them.
2. **Measure the meta-layer separately.** Track precision with and without the critic. If approved trades don't outperform downgraded ones after ~100 samples, the critic isn't earning its call.
3. **The meta-layer should also set size**, not just take/skip. `DOWNGRADE → half size` is exactly the intended behaviour.

## 2.3 The five layers, and what validates each

| Layer | What it does | Validation method |
|---|---|---|
| **1. Candidate generation** | deterministic screener over Nifty 500 | walk-forward backtest |
| **2. Gates (veto ladder)** | hard vetoes, then soft penalties | backtest with gates on vs off |
| **3. Meta-label (LLM + critic)** | qualitative filter, take/skip/half-size | **forward-only** paper trading |
| **4. Sizing** | half-Kelly, capped, regime-scaled | Monte Carlo on the R-distribution |
| **5. Measurement** | triple-barrier labels, R-multiples, MAE/MFE | rolling expectancy, decay monitor |

**The critical property:** layers 1, 2, 4 and 5 work with the LLM completely offline. That's what makes the system robust rather than dependent on a free API tier (`PROJECT_BRIEF.md` non-negotiable #11).

---

# 3. Measure R-multiples, not win rate

Win rate is the most misleading number in trading. A 70%-win-rate system with 1:0.4 payoff loses money; a 35%-win-rate system with 1:4 payoff prints.

| Metric | Why it matters | Where |
|---|---|---|
| **Expectancy in R** = `W×avgWin_R − (1−W)×avgLoss_R` | the only number that answers "should I keep trading this?" | `ANALYTICS.md` §I2 |
| **Profit factor** | gross win ÷ gross loss | |
| **MAE / MFE** | are stops too tight? are targets too far? | `ANALYTICS.md` §I1 |
| **Exit-reason distribution** | too many TIME_EXITs means your targets are unrealistic for a 10-day hold | `close_reason` |
| **Expectancy by setup archetype** | one archetype usually carries the whole system | `by_setup` |
| **Expectancy by regime** | tells you whether the regime filter is worth its complexity | `by_regime` |
| **Expectancy AI vs manual** | whether to follow the engine, invert it, or use it only to screen | `by_source` |

`MAE/MFE` deserves particular attention. If your winners rarely dip below −0.4R, your stops are too wide and you're under-sized. If most stop-outs breach by a hair, your stops sit inside the noise. Neither is visible from a win rate.

---

# 4. Validation protocol — the part almost everyone gets wrong

## 4.1 Walk-forward, with enough windows

Walk-forward analysis is the accepted standard: optimise on a window, validate on the *next* period, roll forward. The **stitched out-of-sample curve — not the in-sample result — is the evidence.**

Practical requirements from the literature:
- **8–10 out-of-sample windows minimum.** Fewer and you're reading noise.
- **Efficiency ratio** (OOS performance ÷ IS performance) **above 0.5** is the threshold for "plausibly robust rather than curve-fit."
- A swing strategy producing ~2 trades/month "may need several years of history before each out-of-sample segment contains enough activity to evaluate." At your rate, **use 4–5 years minimum.**
- Check **consistency across windows** — max drawdown, profit factor, trade count, win rate — not just the aggregate.

## 4.2 Count your trials — this is the one people skip

**The probability of selecting an overfit strategy grows rapidly with the number of trials** (Bailey, López de Prado et al., *Probability of Backtest Overfitting*, JCF 2017). If you test 60 threshold combinations and keep the best, the winner is probably noise dressed as an edge.

Two defences:
- **Log every trial.** Every parameter variant you evaluate, in a file. Count them. The count is an input to honest interpretation.
- **Deflate the result.** The [Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) (Bailey & López de Prado, 2014) corrects a Sharpe ratio for selection bias, number of trials, sample length and non-normality. If your strategy's Sharpe doesn't survive deflation, you don't have a strategy.

**Simplest practical rule:** decide your parameters from *reasoning* (ATR-based stops, 1:2 R:R, 10-day cap — all justified independently), then run **one** backtest to check they're not broken. Don't sweep. The sweep is what manufactures the overfit.

## 4.3 Walk-forward is not sufficient on its own

An important limitation: walk-forward "only tests a single price path," and repeatedly splitting the data "creates an increase in data leakage." Complement it:

| Test | What it catches |
|---|---|
| **Monte Carlo permutation** | shuffle returns → does the edge survive? If yes on shuffled data, you have a bug |
| **Noise test** | add small random noise to prices → a real edge degrades gracefully, a fitted one collapses |
| **Variance / start-date test** | shift the start date by ±N days → results should be similar, not wildly different |
| **Parameter plateau check** | plot performance across the parameter range. **Choose a plateau, never a peak** — a peak surrounded by bad values is curve-fitting |
| **Shuffled-signal control** | randomise the entry signal, keep everything else → expectancy must collapse to ~0. If it doesn't, you have look-ahead leakage |
| **CPCV** | Combinatorial Purged Cross-Validation gives *multiple* paths instead of one, and purges overlapping labels |

The shuffled-signal control is the cheapest and catches the worst bug class. Run it first.

## 4.4 No lookahead, ever

`score_as_of(day)` may read nothing dated after `day`. This includes subtle leaks: today's index constituents applied to 2021 (survivorship), restated financials, adjusted-close series that embed future splits, and earnings dates known only after the fact. Assume you have a leak until the shuffled-signal control says otherwise.

---

# 5. Sample size — and the patience it demands

## 5.1 The thresholds

| Trades | What you can say |
|---|---|
| < 30 | **nothing.** You cannot distinguish skill from luck |
| 30 | the bare minimum for any inference (central limit theorem) |
| ~109 | 70% confidence at a 5% margin of error (Cochran) |
| 100 | "your data becomes usable" |
| 200 | "convincing" |
| 500 | strong statistical significance |

## 5.2 Correlated trades destroy your effective sample size

This is the subtlety that matters most for you: **"a backtest with 300 low-quality, highly correlated trades may be worse than 80 clean, independent trades."**

Five positions in the same sector on the same day, all triggered by the same regime, are **not five observations — they're closer to one.** Which reframes your risk limits:

> **Your concentration caps (max 2 per sector, max 5 positions, correlation warning above 0.7) are not just risk management. They are a statistical requirement for ever learning anything from your own track record.**

That's a genuinely useful reason to hold the limits when a tempting sixth setup appears.

## 5.3 Your realistic timeline

At 2–5 swing trades per week, ~100 closed trades takes **six to twelve months**. So:

- Don't change the rules based on 15 trades. That's noise, and reacting to it is how systems get destroyed.
- Set a review cadence — **quarterly**, or every 50 closed trades, whichever is later.
- Until then, the paper portfolio (F19) accumulates the sample faster than your real money does, at no risk. This is its real purpose.

---

# 6. Live degradation and alpha decay

## 6.1 Backtest-to-live gaps are large and mostly about execution

Performance degradation live "is often driven by execution-related factors that traditional drift detection frameworks fail to observe" — execution assumptions in a backtest "quietly diverge from real-world market behavior, eroding alpha." The published gaps are brutal: strategies showing ~+20% annual in backtest have delivered −28% and −38% live.

Defences, all of which you can implement:
- **Require the day's range to actually touch your entry zone** before counting a fill (`IMPLEMENTATION.md` Phase 4.3 already does this).
- **Assume ~0.1% slippage**, and model the full Indian cost stack — brokerage, STT, exchange, GST, SEBI, stamp duty (`IMPLEMENTATION.md` 4.2 / Phase 6).
- **If stop and target are both touched the same day, assume the stop hit first.** Always.
- **Never assume a fill at the exact stop price** during a gap — gap losses exceed 1R, which is why the earnings veto exists.
- **Paper trade before live.** The gap between paper and live is smaller than between backtest and live, but non-zero.

## 6.2 Alpha decays — monitor it

"A trading signal loses half its predictive power on a similar curve to a radioactive isotope's half-life." Alpha exists because of an information asymmetry; the decay happens as that information propagates and others compete it away.

So a working system today is not a working system forever. Build the monitor:

```python
# rolling, not lifetime — lifetime expectancy hides a dying edge
expectancy_50  = expectancy(last_n=50)
expectancy_all = expectancy(last_n=None)

if expectancy_50 < 0 and expectancy_all > 0:
    alert("ALPHA_DECAY_WARNING: last 50 trades negative while lifetime positive")
if expectancy_50 < 0.5 * expectancy_all:
    alert("edge halved on a rolling basis — review setup archetypes")
```

Also track expectancy **by archetype** — usually one archetype dies while others hold, and lifetime aggregates conceal it entirely.

---

# 7. What to consider that this project currently doesn't

Beyond everything in `ANALYTICS.md`, the research suggests these:

| # | Consideration | Why | Effort |
|---|---|---|---|
| 1 | **PEAD + 52-week-high setup as its own archetype** | strongest India-specific evidence at your horizon (§1.1) | 4 h |
| 2 | **Trial log + Deflated Sharpe** | overfit probability rises with trial count; without a count you can't interpret anything | 3 h |
| 3 | **Shuffled-signal control run** | catches look-ahead leakage, the worst bug class | 2 h |
| 4 | **Monte Carlo permutation + noise + start-date tests** | walk-forward tests only one price path | 4 h |
| 5 | **Parameter plateau plots** | choose a plateau, never a peak | 2 h |
| 6 | **Effective sample size** (correlation-adjusted trade count) | 300 correlated trades < 80 independent ones | 3 h |
| 7 | **Rolling expectancy + alpha-decay alerts** | edges have half-lives | 2 h |
| 8 | **Meta-layer precision tracking** (critic on vs off) | proves whether the critic earns its call | 2 h |
| 9 | **Forward-only LLM validation register** | the only honest way to evaluate the LLM layer (§0) | 2 h |
| 10 | **Hurst exponent as a second regime read** | orthogonal trending-vs-mean-reverting signal | 3 h |
| 11 | **Exit-reason distribution monitor** | too many TIME_EXITs = unrealistic targets | 1 h |
| 12 | **Implementation-shortfall log** | recorded fill vs planned entry, per trade — measures your own execution drift | 2 h |

Items 1, 3 and 9 are the highest value: one adds a documented edge, one protects you from fooling yourself, and one makes the LLM layer honestly measurable.

---

# 8. Anti-patterns — the ways this goes wrong

| Anti-pattern | Why it's fatal | Correct approach |
|---|---|---|
| Backtesting the LLM layer | training data contains the future (§0) | rules backtested; LLM forward-validated only |
| Optimising until it looks good | overfit probability rises with trials | reason out parameters, run **one** test |
| Judging by win rate | ignores payoff ratio entirely | expectancy in R |
| Averaging conflicting signals | "3 bullish 1 bearish → buy" is not analysis | gates first, then rank |
| Changing rules after 15 trades | that's noise | quarterly, or 50 trades |
| Adding a 4th momentum indicator | RSI/MACD/ROC measure the same thing | add *orthogonal* families |
| Lifetime performance only | conceals a decaying edge | rolling windows |
| Ignoring trade correlation | collapses effective sample size | concentration caps as a statistical rule |
| Assuming stop fills at the stop price | gaps exceed 1R | earnings veto + gap-risk sizing |
| Trusting a single price path | walk-forward's core limitation | permutation, noise, variance tests |
| Believing a great backtest | published live gaps are −28%, −38% | conservative fills, costs, paper first |

---

# 9. Concrete changes to this project

Additions to what's already specified in the other documents:

**Phase 8 (backtest) — revise the scope**
1. [ ] Backtest **rules only**; explicitly exclude the LLM layer, and note why in the code
2. [ ] Run the shuffled-signal control **before** trusting any result
3. [ ] Require 8–10 OOS windows over 4–5 years; report efficiency ratio (target > 0.5)
4. [ ] Log every trial to `backtest_trials.jsonl`; report the count with every result
5. [ ] Add Monte Carlo permutation, noise, and start-date shift tests
6. [ ] Plot parameter sensitivity; document that a plateau was chosen, not a peak
7. [ ] Compute a deflated Sharpe alongside the raw one

**`ANALYTICS.md` — add the researched setup**
8. [ ] `PEAD_52W` archetype per §1.1, with a score bonus above other factors
9. [ ] Mirror-case veto: negative surprise while far from the 52-week high
10. [ ] Effective-sample-size calculation (correlation-adjusted)

**Measurement — the feedback loop**
11. [ ] Rolling expectancy (50-trade window) + alpha-decay alerts
12. [ ] Expectancy split by archetype, regime, and source (AI / manual / paper)
13. [ ] Exit-reason distribution monitor
14. [ ] Meta-layer precision: critic-approved vs critic-downgraded outcomes
15. [ ] Implementation-shortfall log: planned entry vs actual fill

**Discipline — written into the system, not left to willpower**
16. [ ] No rule changes below 50 closed trades; enforce a quarterly review cadence
17. [ ] Concentration caps documented as a *statistical* requirement, not only a risk one
18. [ ] Forward-only LLM validation register from day one

---

## Sources

**Backtest integrity and validation**
- [Summoning the Oracle to Slay It: Mitigating Look-Ahead Bias in Financial Backtesting with Large Language Models](https://arxiv.org/pdf/2605.24564) — LLM training-data contamination
- [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) — Bailey & López de Prado (2014)
- [Deflated Sharpe ratio (overview)](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio) · [Probability of backtest overfitting — framework and code](https://github.com/Neyt/How-To-Backtest-Correctly)
- [Advances in Financial Machine Learning — triple-barrier, meta-labeling, CPCV](https://gildan-bonus-content.s3.amazonaws.com/GIL2476_AdvancesFinancial/GIL2476_AdvancesFinancial_BonusPDF.pdf) — López de Prado
- [Walk-Forward Optimization: how it works and its limitations](https://blog.quantinsti.com/walk-forward-optimization-introduction/) · [Walk-forward vs backtesting: best practices](https://surmount.ai/blogs/walk-forward-analysis-vs-backtesting-pros-cons-best-practices) · [Robustness testing guide](https://www.buildalpha.com/robustness-testing-guide/) · [Walk-forward optimization (overview)](https://en.wikipedia.org/wiki/Walk_forward_optimization)
- [AlgoXpert: a rigorous IS/WFA/OOS protocol for mitigating overfitting](https://arxiv.org/pdf/2603.09219) · [GT-Score: a robust objective function for reducing overfitting](https://arxiv.org/pdf/2602.00080)

**Edges and anomalies (India-specific where possible)**
- [Post-Earnings-Announcement Drift Anomaly in India: A Test of Market Efficiency](https://www.scirp.org/journal/paperinformation?paperid=88060) — significant PEAD, 2002–2017, robust to controls
- [Momentum Crashes and the 52-Week High](https://www.tandfonline.com/doi/abs/10.1080/0015198X.2023.2183706) — *Financial Analysts Journal* — proximity and recency effects on drift
- [A review of the Post-Earnings-Announcement Drift](https://www.sciencedirect.com/science/article/pii/S2214635020303750)
- NSE 500 anomaly persistence (2005–2016): value and momentum explained by risk models; size and volume faded

**Sample size, decay, execution**
- [How Many Trades Are Enough? Statistical significance in backtesting](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05) · [Sample size in trading: why 100 trades is the minimum](https://www.edgeflo.com/blog/sample-size-trading) · [Statistical power analysis in backtesting](https://questdb.com/glossary/statistical-power-analysis-in-backtesting-models/)
- [Alpha decay: why your edge has a half-life](https://backtestbrewery.github.io/posts/alpha-decay-trading.html) · [Alpha decay in trading](https://www.tradingengineeringlab.com/alpha-decay-in-trading-why-strategies-stop-working-over-time/) · [Detecting drift in live trading (TCA)](https://kx.com/blog/drift-detections-blind-spot-how-live-tca-insights-help-firms-win-the-race-against-alpha-decay/)

**Signal combination and regime**
- [Enhancing trading strategies with a Hurst-based regime filter](https://pyquantlab.medium.com/enhancing-trading-strategies-with-a-hurst-based-regime-filter-ac6639be43cf)
- [Coopetitive soft gating ensemble](https://arxiv.org/pdf/1807.01020) · [Ensemble methods for stock and crypto trading (FinRL contests)](https://arxiv.org/html/2501.10709v1) · [Finding the signal in the market noise](https://www.sophie-ai-finance.com/articles/signal-noise-comprehensive-analysis-filtering-techniques-quantitative-trading)
