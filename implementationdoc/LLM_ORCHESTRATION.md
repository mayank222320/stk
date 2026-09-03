# StockAI — Gemini & Groq Orchestration

**Written:** 3 September 2026
**Goal:** robust, correct results from **free-tier** LLM APIs.
**Companions:** `PROMPTS.md` (the two prompts) · `ANALYTICS.md` (what must be computed, not asked) · `WEAKNESSES.md` W4/W12 · `IMPLEMENTATION.md` 3.2

---

## The three principles

**1. Never ask an LLM for something arithmetic can answer.**
Every number the code can compute must be computed (`ANALYTICS.md`). The LLM's job is judgement, catalysts, narrative and adversarial review. This is the single biggest robustness win — a computed RSI is never wrong, a recalled one often is.

**2. LLM failure must never produce a trade.**
Fail closed. A failed call, an invalid schema, or a disagreement between models produces `WAIT` and a notification — never a silent default and never a guess.

**3. You have far more quota than you need — spend it on verification, not volume.**
The math below shows ~35 Gemini calls a day against thousands available. The right use of that headroom is a *second opinion on every trade*, not more trades.

---

# 1. Model routing

Right model for each job, with explicit settings.

| Task | Provider / model | Tools | Temp | Output | Why |
|---|---|---|---|---|---|
| Qualitative research on a candidate | **Gemini Flash** (`gemini-3.6-flash`) | `google_search` | 0.3 | text + citations | Only path to live news/filings |
| Convert research + computed data → decision | **Gemini Flash** | **none** | 0.1 | JSON via `responseSchema` | Deterministic; no tool latency; strict shape |
| **Critic / red-team the decision** | **Groq Llama 3.3 70B** | none | 0.2 | JSON | *Different provider, different weights* — genuine independence, and 14,400 free calls/day |
| News sentiment (high volume) | **Groq** | none | 0.2 | JSON | Short prompts, high RPD ceiling; already wired |
| Chat / education | **Gemini Flash** (user-selectable) | `google_search` | 0.4 | Markdown | Quality + grounding |
| Chart image / PDF analysis | **Gemini Flash vision** | none | 0.3 | Markdown | Only option available |
| JSON repair | **Gemini Flash-Lite** | none | 0.0 | JSON | Cheapest possible fix-up call |
| Deep reasoning on a contested call | **Gemini Pro** — *only if you add a paid key* | search | 0.2 | JSON | **Pro left the free tier on 1 Apr 2026.** Keep it optional; the system must work without it |

**Never use an LLM for:** indicator values, price, position sizing, R:R arithmetic, gate evaluation, dedupe, or the three in-session position checks. Those are pure code — the checks at 11:30/14:00/15:10 make **zero** LLM calls.

---

# 2. Free-tier quota budget (verified September 2026)

| Provider | Per key | Binding constraint |
|---|---|---|
| **Gemini Flash** | ~10 RPM · 250k TPM · **1,500 requests/day** | **RPM**, not daily |
| **Groq (free)** | ~30 RPM · **14,400 requests/day** | daily is generous |

Your `*_gemini` env-var discovery already supports multiple keys, so daily capacity is 1,500 × key count.

### Realistic daily consumption

| Workload | Gemini | Groq |
|---|---|---|
| Morning: 5 candidates × (research + structure) | 10 | — |
| Morning: critic pass on each | — | 5 |
| Screener qualitative pass (batched, one call) | 1 | — |
| Position advice on demand (~3/day) | 3 | — |
| Evening digest | 1 | — |
| Chat (~20 turns) | 20 | — |
| News scanner | — | ~250 |
| **Total** | **~35** | **~255** |

**Two conclusions:**

1. **Daily quota is a non-issue** (~35 of 1,500+). The real limit is **10 RPM** — so *serialise* Gemini calls and keep the existing `asyncio.sleep(5)` between symbols. Never fan out 5 symbols in parallel to Gemini; you'll hit RPM and burn retries.
2. **The news scanner is 88% of all LLM usage** — 250 Groq calls/day versus 10 Gemini calls for the actual trading decisions. It runs every 5 minutes with up to 3 AI calls per run ([news_scanner/service.py:30](features/news_scanner/service.py#L30)). Fix: only escalate articles whose symbols match your **held positions or today's shortlist**. That cuts it by roughly 80% and makes the remaining alerts far more relevant.

---

# 3. Error taxonomy — the biggest robustness gap today

`generate_with_gemini_fallback` treats every exception identically ([gemini/service.py:232-236](features/gemini/service.py#L232-L236)). A bad model ID therefore costs one failed HTTP call *per key* before moving on, and a rate-limited key gets retried first on the very next call. Classify instead:

```python
from enum import Enum

class ErrClass(Enum):
    BAD_REQUEST = "bad_request"     # 400 — our payload is wrong. Do NOT retry, do NOT rotate.
    AUTH        = "auth"            # 401/403 — key dead/unauthorised. Mark dead for the session.
    NO_MODEL    = "no_model"        # 404 — model doesn't exist. Skip this model on ALL keys.
    RATE        = "rate"            # 429 — cool this key down, try the next one.
    TRANSIENT   = "transient"       # 500/503/timeout — exponential backoff, then next key.
    SAFETY      = "safety"          # content blocked — prompt problem, not a key problem.

def classify(status: int | None, message: str) -> ErrClass:
    m = (message or "").lower()
    if status == 400 or "invalid argument" in m:                  return ErrClass.BAD_REQUEST
    if status in (401, 403) or "api key" in m:                    return ErrClass.AUTH
    if status == 404 or "not found" in m or "not supported" in m:  return ErrClass.NO_MODEL
    if status == 429 or "quota" in m or "rate limit" in m:         return ErrClass.RATE
    if status and status >= 500:                                   return ErrClass.TRANSIENT
    if "safety" in m or "blocked" in m:                            return ErrClass.SAFETY
    return ErrClass.TRANSIENT
```

| Class | Correct handling | Cost of getting it wrong |
|---|---|---|
| `BAD_REQUEST` | fail immediately, alert — it's a code bug | N wasted calls per attempt, forever |
| `AUTH` | mark key dead for the session, next key | repeated failures on a dead key |
| `NO_MODEL` | **break out of the key loop**, next model | N wasted calls per attempt |
| `RATE` | cooldown that key 60s, next key | retrying an exhausted key first |
| `TRANSIENT` | backoff 1s/2s/4s on the same key, then rotate | needless rotation on a blip |
| `SAFETY` | don't rotate — adjust the prompt, alert | pointless key churn |

---

# 4. Key manager with cooldowns and local rate limiting

Track your own RPM/RPD so you never spend a call you already know will fail.

```python
import time
from collections import deque

class KeyState:
    def __init__(self, name: str):
        self.name, self.calls = name, deque()      # timestamps for RPM/RPD windows
        self.cooldown_until, self.dead, self.day_count, self.day = 0.0, False, 0, None

    def available(self, rpm: int = 10, rpd: int = 1500) -> bool:
        now, today = time.time(), today_ist()
        if self.day != today: self.day, self.day_count = today, 0     # reset daily
        if self.dead or now < self.cooldown_until: return False
        while self.calls and now - self.calls[0] > 60: self.calls.popleft()
        return len(self.calls) < rpm and self.day_count < rpd

    def record(self): self.calls.append(time.time()); self.day_count += 1
    def cool(self, secs: int = 60): self.cooldown_until = time.time() + secs

async def call_with_failover(prompt: str, *, models: list[str], schema=None,
                             use_search=False, max_attempts=6) -> dict:
    errors, attempts = [], 0
    for model in models:
        model_dead = False
        for st, key in _keys_by_preference():                 # least-recently-used first
            if model_dead or attempts >= max_attempts: break
            if not st.available():
                errors.append(f"{key.name}: unavailable (cooldown/quota)"); continue
            attempts += 1; st.record()
            try:
                return await _one_call(key.value, prompt, model, schema, use_search)
            except ApiError as e:
                cls = classify(e.status, e.message)
                errors.append(f"{model}/{key.name}: {cls.value}")
                if   cls is ErrClass.NO_MODEL:    model_dead = True      # skip remaining keys
                elif cls is ErrClass.RATE:        st.cool(60)
                elif cls is ErrClass.AUTH:        st.dead = True
                elif cls is ErrClass.BAD_REQUEST: raise                  # our bug — surface it
                elif cls is ErrClass.SAFETY:      raise
                else: await asyncio.sleep(min(4, 2 ** (attempts - 1)))   # transient backoff
    _breaker.record_failure()
    raise LLMUnavailable("all models and keys exhausted: " + " | ".join(errors[:8]))
```

**Validate model IDs at startup** (`IMPLEMENTATION.md` 0.5) so `NO_MODEL` never happens in production — today `models` still offers `gemini-2.5-pro`, which cannot work on a free key.

---

# 5. Circuit breaker

Without one, a provider outage during the morning routine burns every key across every retry, then leaves you with no quota for the rest of the day.

```python
class Breaker:
    def __init__(self, threshold=5, cool_secs=600):
        self.fails, self.open_until = 0, 0.0
        self.threshold, self.cool = threshold, cool_secs
    def allow(self) -> bool: return time.time() >= self.open_until
    def record_failure(self):
        self.fails += 1
        if self.fails >= self.threshold:
            self.open_until = time.time() + self.cool
            self.fails = 0
            asyncio.create_task(alert_ops("LLM circuit open",
                f"provider failing — pausing calls {self.cool//60} min"))
    def record_success(self): self.fails = 0
```

When open: **skip the LLM entirely and still produce value** — the deterministic screener, the veto ladder, position tracking and every alert keep working, because none of those need an LLM. Emit `WAIT` with `data_conflicts: ["LLM unavailable"]` rather than nothing at all.

---

# 6. The two-call pattern

Split grounded research from structured decision-making. Both robustness and cost improve.

```python
async def analyse_candidate(symbol: str, blocks: dict) -> dict:
    # Call 1 — grounded research. Qualitative ONLY (no prices, no indicators).
    research = await call_with_failover(
        build_research_prompt(symbol), models=USABLE, use_search=True)

    # Call 2 — no tools, strict schema, near-deterministic.
    decision = await call_with_failover(
        build_swing_prompt(symbol, {**blocks, "research": research["text"]}),
        models=USABLE, schema=RECO_SCHEMA, use_search=False)
    return decision
```

Why it's better than one call:
- **Isolates failure** — if search is flaky you still get a decision from computed data alone (with lower `data_confidence`).
- **Schema reliability** — tool-calling and strict schemas together are more fragile than either alone.
- **Determinism where it matters** — temp 0.1 on the decision, 0.3 on the research.
- **Auditability** — the research text is stored separately from the decision, so you can see *why* later.

---

# 7. Schema validation, then one cheap repair

Validate in code — never trust the shape (`IMPLEMENTATION.md` 3.2).

```python
ok, errs = validate_reco(reco, cmp=blocks["cmp"])
if not ok:
    # ONE cheap repair attempt on the smallest model, not a full re-analysis
    reco = await call_with_failover(
        f"This JSON violates these rules: {errs}\nReturn corrected JSON only.\n{json.dumps(reco)}",
        models=["gemini-3.6-flash-lite"], schema=RECO_SCHEMA)
    ok, errs = validate_reco(reco, cmp=blocks["cmp"])
if not ok:
    reco = {**reco, "recommendation": "WAIT", "data_conflicts": errs}
    await alert_ops("schema validation failed", str(errs))
```

Repair costs a fraction of a re-run and usually succeeds — the model normally has the reasoning right and the shape wrong. Two failures means `WAIT`, always.

---

# 8. The agreement gate (best use of your spare quota)

A single LLM pass is systematically overconfident — and today's `prompt.txt` explicitly *instructs* confidence. Two independent providers disagreeing is the cheapest reliable signal that a trade is marginal.

```python
CRITIC_PROMPT = """You are a risk manager whose job is to REJECT this swing trade
(2-10 day horizon). Be adversarial but factual. Use ONLY the data given.

COMPUTED DATA (authoritative): {data}
PROPOSED TRADE: {reco}

Return JSON only:
{{"verdict":"APPROVE|DOWNGRADE|REJECT",
  "failed_gates":["..."],
  "strongest_objection":"...",
  "stop_realistic":true|false,
  "rr_honest":true|false,
  "better_action":"..."}}"""

async def gate(reco: dict, data: dict) -> dict:
    if reco["recommendation"] not in ("BUY", "ACCUMULATE"):
        return reco                                    # only gate entries
    try:
        critic = await groq_json(CRITIC_PROMPT.format(data=_slim(data), reco=_slim(reco)))
    except Exception as e:
        reco["data_conflicts"] = reco.get("data_conflicts", []) + [f"critic unavailable: {e}"]
        reco["data_confidence"] = max(1, reco["data_confidence"] - 1)
        return reco                                    # degrade, don't block
    reco["critic"] = critic
    if critic["verdict"] == "REJECT":
        reco.update(recommendation="WAIT",
                    thesis=f'{reco["thesis"]}\n\nBLOCKED BY CRITIC: {critic["strongest_objection"]}')
    elif critic["verdict"] == "DOWNGRADE" or not critic.get("stop_realistic", True):
        reco["data_confidence"] = max(1, reco["data_confidence"] - 2)
        reco["size_multiplier"] = 0.5
    return reco
```

**Rules:** emit `BUY` only on `APPROVE`. `REJECT` → `WAIT`, showing the objection. `DOWNGRADE` → half size. Critic unavailable → proceed with reduced confidence (never block on the critic — that would make Groq a single point of failure).

**Then measure it.** Log `agreement_rate` and compare realised expectancy of approved-versus-downgraded trades in the journal. If agreement adds nothing after 50 trades, drop it — but the prior strongly favours it.

---

# 9. Caching and deduplication

Free quota is not free latency, and repeated identical calls are pure waste.

```python
def research_key(symbol: str, blocks: dict) -> str:
    """Same symbol + same trading day + same underlying data = same answer."""
    h = hashlib.sha256(json.dumps({
        "cmp": blocks["cmp"], "rsi": blocks["rsi_14"], "adx": blocks["adx_14"],
        "vol": blocks["volume_today"]}, sort_keys=True).encode()).hexdigest()[:12]
    return f"{symbol}:{today_ist()}:{h}"
```

Cache research text for the trading day in `llm_cache` (TTL 2 days, ~2 KB/entry). A manual `/analyze RELIANCE` after the morning run then costs **zero** Gemini calls if nothing material changed. Re-running the morning routine after a crash likewise reuses everything.

Never cache the *decision* across days — regime, freshness and portfolio heat all change.

---

# 10. Prompt structure for cache efficiency

Put the **static prompt first, variable data last**:

```
[ prompts/swing.md — ~330 lines, byte-identical every call ]   ← stable prefix
[ REQUEST: symbol, as-of, session ]
[ data blocks ]                                                ← varies
```

Identical prefixes are what make provider-side caching possible, and it also keeps diffs readable when debugging. Never interpolate the date or symbol into the static section.

---

# 11. Determinism and audit trail

Every stored recommendation records how it was produced:

```python
{"model": "gemini-3.6-flash", "key_name": "kmain_gemini", "temperature": 0.1,
 "prompt_version": "swing-v1", "prompt_sha": "a3f9...", "search_grounded": False,
 "critic_model": "llama-3.3-70b-versatile", "critic_verdict": "APPROVE",
 "latency_ms": 4210, "cache_hit": False, "as_of": "2026-09-03T15:47:00+05:30"}
```

~200 bytes per recommendation, and it makes a bad call diagnosable: was it a stale prompt version, a different model, a cache hit, or genuinely bad reasoning? Without this you cannot tell.

---

# 12. Priority budget classes

If quota ever does run short, it must run short on the *right* things.

| Class | Workload | Behaviour when constrained |
|---|---|---|
| **P0 — protected** | position advice on an open trade, morning analysis | always allowed; reserve 200 calls/day |
| **P1 — normal** | screener verification, evening digest | allowed above 20% remaining quota |
| **P2 — discretionary** | ad-hoc chat, `/analyze` on non-shortlist symbols | blocked below 20% remaining |
| **P3 — deferrable** | news sentiment (Groq) | Groq only, never Gemini |

```python
async def budget_allows(cls: str) -> bool:
    used, total = await quota_used_today(), total_daily_capacity()
    remaining = 1 - used/total
    return {"P0": True, "P1": remaining > 0.20,
            "P2": remaining > 0.20, "P3": remaining > 0.05}[cls]
```

---

# 13. Observability

Extend `/health` with an LLM panel, and surface it in `/health` on the bot:

```json
{"gemini": {"keys": [{"name": "kmain_gemini", "state": "ok",
                      "calls_today": 22, "rpm_used": 1, "cooldown_s": 0}],
            "model": "gemini-3.6-flash", "validated": true,
            "circuit": "closed", "cache_hit_rate": 0.31},
 "groq":   {"keys_ok": 4, "calls_today": 187, "state": "ok"},
 "quality":{"schema_fail_rate_7d": 0.02, "repair_success_rate": 0.86,
            "critic_agreement_rate_7d": 0.78,
            "wait_rate_7d": 0.55}}
```

`wait_rate` is the metric to watch. If it's near zero, your gates aren't binding and the model is rationalising trades. Around 40–60% `WAIT` on a shortlist is healthy for swing trading.

---

# 14. What "robust" means here, concretely

| Failure | System behaviour |
|---|---|
| One Gemini key rate-limited | rotates to next key, cools the first 60 s — invisible |
| All Gemini keys exhausted | circuit opens; deterministic screener, tracking and alerts continue; `WAIT` on new entries; you're notified |
| Bad model ID configured | caught at startup, dropped from rotation, logged |
| Search grounding fails | decision still produced from computed data with lower confidence |
| Model returns malformed JSON | one cheap repair call; then `WAIT` |
| Model contradicts a computed price | `DATA CONFLICT` recorded, computed value kept, confidence lowered |
| Groq critic unavailable | proceeds with −1 confidence; never blocks |
| Models disagree on a BUY | downgraded to `WAIT` with the objection shown |
| Provider outage all day | you still get the screener, position alerts, SIP/ETF dip signals — everything that isn't an LLM |

**The system's core loop must never require an LLM to be up.** Gates, sizing, tracking, alerts and dip detection are all arithmetic. The LLM adds narrative and a second opinion on top of a system that already works without it — that is what makes it robust on free infrastructure.

---

## Implementation checklist

1. [ ] `classify()` error taxonomy; replace the uniform `except` in `gemini/service.py`
2. [ ] `KeyState` with cooldown, local RPM/RPD tracking, LRU key preference
3. [ ] Startup model validation; drop invalid IDs; default `gemini-3.6-flash`
4. [ ] `Breaker` circuit breaker + ops alert on open
5. [ ] Two-call pattern (grounded research → schema decision)
6. [ ] `validate_reco()` + single repair call on Flash-Lite
7. [ ] Groq critic + agreement gate on every BUY/ACCUMULATE
8. [ ] `llm_cache` keyed on symbol + trading day + data hash (TTL 2 days)
9. [ ] Static-prefix-first prompt assembly
10. [ ] Audit metadata on every stored recommendation
11. [ ] Priority budget classes
12. [ ] `/health` LLM panel + `wait_rate` and `agreement_rate` metrics
13. [ ] **Restrict news-scanner AI escalation to held + shortlist symbols** (−80% of all LLM usage)

**Verify**
- [ ] Set an invalid model ID → caught at startup, not at runtime
- [ ] Simulate 429 on key 1 → key 2 serves, key 1 cools 60 s, no user-visible failure
- [ ] Simulate total Gemini outage → screener and alerts still run; entries return `WAIT`; you get one notification
- [ ] Force malformed JSON → repair succeeds, or `WAIT` with the reason
- [ ] Feed a deliberately bad trade → critic returns `REJECT`, output becomes `WAIT`
- [ ] Run `/analyze` twice on the same symbol same day → second is a cache hit, zero calls
- [ ] Confirm 7-day `wait_rate` is not ~0 (gates must actually bind)
