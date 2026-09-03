# StockAI — Alerts & Telegram Bot Design

**Written:** 3 September 2026
**Companions:** `FEATURES.md` F1/F13/F15 · `IMPLEMENTATION.md` Phase 1/4 · `PROMPTS.md` · `LLM_ORCHESTRATION.md`

Two things here: a complete **alert event catalogue** (what fires, when, on which channel, at what priority) and a proper **Telegram menu** so every command is discoverable.

Both channels already exist and work — `bot.send_message` and `broadcast()` → ntfy.sh with EmailJS fallback ([notifications/service.py](../features/notifications/service.py)). What's missing is a catalogue, priorities, dedupe and per-event configuration.

---

# PART 1 — Alert event catalogue

## Priority → channel mapping

ntfy accepts `min | low | default | high | max`. Map deliberately, because an alert that always screams gets ignored.

| Priority | ntfy | Telegram | When to use | Phone behaviour |
|---|---|---|---|---|
| **P0 URGENT** | `max` | yes, with 🚨 | money is moving right now — you must act | breaks through silent mode |
| **P1 ACTION** | `high` | yes | act today, not this minute | normal notification |
| **P2 INFO** | `default` | yes | worth knowing, no action | normal |
| **P3 DIGEST** | `low` | batched into one message | routine confirmation | quiet |
| **P4 SILENT** | none | none — DB only | audit trail | nothing |

---

## 1. Position events — your tracker and swing positions

The core of the system. These fire from the 3 in-session checks (11:30, 14:00, 15:10) and the EOD tracker, and **only on state change** (`IMPLEMENTATION.md` 1.2).

| Event | Trigger | Priority | Message must contain |
|---|---|---|---|
| **STOP_BREACHED** | price ≤ stop (or trailing stop) | **P0** | symbol, live price, stop, loss in R and ₹, "EXIT — stop discipline is non-negotiable" |
| **TARGET_HIT_T1** | price ≥ T1, not yet booked | **P0** | book 40%, move stop to breakeven (give the number) |
| **TARGET_HIT_T2** | price ≥ T2 | **P0** | book 40%, trail remainder below prior swing low |
| **TARGET_HIT_T3** | price ≥ T3 | **P0** | close remaining, realised R |
| **GAP_RISK_PRE_OPEN** | 09:10 check — likely open beyond stop | **P0** | previous close, indicated open, stop, "decide before 09:15" |
| **INVALIDATION_TRIGGERED** | close < EMA50 on >1.5× volume, or the stated invalidation event | **P1** | which condition fired, current R, suggested action |
| **TIME_EXIT_DUE** | `days_held ≥ max_hold_days` (10) | **P1** | days held, current R, "EXIT or document a re-entry" |
| **NEAR_STOP** | within 0.75% of stop | **P1** | distance, "stop may trigger this session" |
| **NEAR_TARGET** | within 0.75% of T1/T2 | **P2** | distance, what to do on the touch |
| **FILLED** | day's range touched the entry zone | **P2** | fill price, quantity, risk in ₹, stop, targets |
| **TRAILING_STOP_MOVED** | ratchet upward | **P3** | old → new stop, locked-in R |
| **PARTIAL_BOOKED** | 40% booked at a target | **P2** | booked qty, realised R, remaining position |
| **SETUP_EXPIRED** | `PENDING_ENTRY` untouched for 2 days | **P3** | "setup expired unfilled — no trade taken" |
| **THESIS_BROKEN** | qualitative invalidation (news/filing) | **P1** | what changed, source, suggested action |

**Manual tracker note:** everything above applies identically to positions you add yourself via `/track` — same lifecycle, same alerts, same grading. `source` is just `MANUAL` instead of `AI`.

---

## 2. Event-risk alerts — the ones that save real money

An earnings gap is the most common way a good 2–10 day trade becomes a 12% loss.

| Event | Trigger | Priority | Note |
|---|---|---|---|
| **EARNINGS_APPROACHING** | held position with results within 5 trading days | **P0** | Highest-value alert in this document. "Reduce or exit before results — stops don't work across gaps." |
| **EARNINGS_TOMORROW** | results next trading day | **P0** | decide today |
| **CORPORATE_ANNOUNCEMENT** | NSE filing on a held/watchlist symbol | **P1** | headline, category, link |
| **INSIDER_OR_BULK_DEAL** | PIT disclosure or bulk/block deal on a held symbol | **P2** | buyer/seller, quantity, direction |
| **FNO_BAN** | held symbol enters F&O ban | **P2** | cash-only until lifted |
| **EXPIRY_PROXIMITY** | F&O expiry within 2 trading days | **P2** | volatility warning |
| **RBI_MPC_NEAR** | MPC within 7 days and a banking/NBFC position open | **P2** | rate-sensitive exposure |

---

## 3. Portfolio and risk alerts

| Event | Trigger | Priority |
|---|---|---|
| **DRAWDOWN_CIRCUIT_BREAKER** | 3 consecutive losses, or −10% account drawdown | **P0** — "halve size for one week" |
| **HEAT_CAP_BREACH** | total open risk would exceed 5% | **P1** — blocks the entry, says by how much |
| **CONCENTRATION_WARNING** | 3rd position in one sector | **P2** |
| **CORRELATION_WARNING** | 60-day correlation > 0.7 with an open position | **P3** |

---

## 4. Opportunity alerts

| Event | Trigger | Priority | Note |
|---|---|---|---|
| **MORNING_DIGEST** | 09:20 IST on trading days | **P2** | ONE message: ranked candidates table + open positions + regime. Not N separate messages. |
| **CANDIDATE_TRIGGERED** | a shortlisted candidate touches its entry zone in-session | **P1** | entry, stop, quantity, R:R — ready to act |
| **ETF_DIP** | GOLDBEES/MON100 reaches GOOD_DIP or better | **P1** | tier, % below 20-day high, RSI, deploy amount, remaining budget |
| **MON100_PREMIUM_WARNING** | premium to iNAV > 1.5% | **P2** | "index is cheap but the ETF isn't — consider waiting" |
| **MONTH_END_DEPLOY** | ≤2 days left with ETF budget unspent | **P1** | "deploy the remainder — dip-waiting must not become never-buying" |
| **SIP_DAY** | SIP debit date | **P3** | amount debited, updated XIRR, "autopilot — no action" |
| **REGIME_FLIP** | RISK_ON ↔ RISK_OFF | **P1** | what changed (breadth, 200 DMA, VIX), what it permits |
| **BREAKING_NEWS** | news scanner: confidence ≥ 85, symbol identified | **P1** | exists today — restrict to held/watchlist symbols to cut noise |
| **EXCHANGE_FILING** | NSE/BSE filing on a watched symbol, high-impact category | **P1** | **Typically 5–30 min ahead of any news article** — see `NEWS_FAST_LANE.md`. Fires without an AI call |
| **UNEXPLAINED_MOVE** | watched symbol: >3× volume and >35% of a normal day's range in 5 min | **P1** | Price moves before the headline exists. "Up 3.2% on 4× volume — no filing or news found yet" |
| **EVENING_DIGEST** | 15:45 IST | **P2** | fills, targets, stops, tomorrow's time exits |

---

## 5. System and ops alerts

Silent failure is what let the storage problem grow unnoticed.

| Event | Trigger | Priority |
|---|---|---|
| **BOT_POLLING_DIED** | external watchdog sees no heartbeat | **P0** |
| **JOB_FAILURE** | any scheduled job raises | **P1** — job name + traceback head |
| **JOB_DID_NOT_RUN** | expected job missing from `job_runs` by a deadline | **P1** — catches the Render spin-down case |
| **LLM_QUOTA_EXHAUSTED** | all Gemini keys rate-limited | **P1** |
| **DATA_SOURCE_DEGRADED** | critical source fails 3 consecutive times | **P2** |
| **STORAGE_WARNING** | Atlas ≥ 70% of 512 MB | **P2** |
| **STORAGE_URGENT** | ≥ 85% | **P1** |
| **STORAGE_AUTOCLEAN** | ≥ 90% — safe tier swept automatically | **P1** |
| **EMPTY_WATCHLIST** | screener returned nothing | **P2** — "no setup passed the gates today" is a valid result, but you must be told |

---

## Delivery rules

**Dedupe** — one alert per `(event, position, trading day)`. Enforce with a unique index, not a query:

```python
await mongo.db.alerts_sent.create_index(
    [("event", 1), ("position_id", 1), ("date", 1)], unique=True)

async def send_alert(event: str, priority: str, position_id, text: str, title: str) -> bool:
    try:
        await mongo.db.alerts_sent.insert_one({
            "event": event, "position_id": position_id, "date": today_ist(),
            "priority": priority, "at": now_ist()})
    except DuplicateKeyError:
        return False                       # already sent today — silently skip
    if not await _allowed_now(priority):   # quiet hours + rate cap
        return False
    if priority in ("P0", "P1", "P2"):
        await bot.send_message(int(USER_ID), text, parse_mode="HTML")
    if priority != "P4":
        await broadcast(text=_plain(text), title=title, ntfy_priority=NTFY[priority])
    return True
```

**Quiet hours** — P0 always delivers. P1 delivers 08:00–22:00 IST. P2/P3 only 09:00–16:00 IST on trading days; outside that they queue into the next digest.

**Rate caps** — max 4 alerts per symbol per day and 25 total per day. On breach, collapse the rest into one digest line. Prevents an alert storm on a volatile day from training you to ignore the phone.

**Configurable** — every event has an on/off and a priority override in `settings.alerts`, editable from `/alerts` and `PUT /alerts/config`. Defaults above; P0 events cannot be disabled from the bot (only via the API), because those are the ones that cost money when missed.

**Message quality** — every actionable alert answers: *what happened · at what price · what to do · by when*. Attach the chart PNG (`FEATURES.md` F13) on P0/P1 position events.

---

# PART 2 — Telegram bot menu

Today the bot registers 4 commands ([main.py:40-46](../main.py#L40-L46)) and there's no discoverable menu. Here's the full surface.

## `setMyCommands` — the native slash-command list

```python
commands = [
    BotCommand(command="menu",      description="📋 All features — tap to browse"),
    BotCommand(command="positions", description="📊 Open positions — R, days held, targets"),
    BotCommand(command="track",     description="➕ Track a new position (entry/target/SL)"),
    BotCommand(command="screener",  description="🔍 Today's ranked swing candidates"),
    BotCommand(command="analyze",   description="🔬 Full swing analysis of a symbol"),
    BotCommand(command="risk",      description="🧮 Position size from entry + stop"),
    BotCommand(command="regime",    description="🌡 Market regime — what's allowed today"),
    BotCommand(command="sip",       description="💰 SIP status and XIRR"),
    BotCommand(command="dip",       description="🥇 GOLDBEES / MON100 dip status"),
    BotCommand(command="journal",   description="📈 Expectancy, win rate, mistakes"),
    BotCommand(command="alerts",    description="🔔 Configure which alerts fire"),
    BotCommand(command="storage",   description="💾 Storage usage + clear old data"),
    BotCommand(command="health",    description="🩺 Data sources, jobs, API quota"),
    BotCommand(command="help",      description="❓ How to use each command"),
]
await bot.set_my_commands(commands)
```

Telegram shows at most ~14 cleanly, so `/menu` carries the long tail.

## `/menu` — inline category menu

```
📋 StockAI — what would you like?

[ 📊 Positions ]  [ 🔍 Find trades ]
[ 💰 Investments ] [ 📈 Performance ]
[ 🔔 Alerts ]      [ ⚙️ System ]
```

Each category expands in place (`edit_text`), with a `🔙 Back` button:

**📊 Positions** — `/positions` open positions · `/track` add one · `/update <sym>` edit levels · `/close <sym> <price>` close · `/untrack <sym>` remove · `/ai <sym>` AI advice on a held position · `/paper` paper portfolio vs real

**🔍 Find trades** — `/screener` ranked candidates · `/analyze <sym>` full swing analysis · `/chart <sym>` chart image · `/regime` market regime · `/risk <sym> <entry> <stop>` exact quantity

**💰 Investments** — `/sip` SIP status + XIRR · `/dip` ETF dip status + remaining budget · `/allocation` asset mix + drift

**📈 Performance** — `/journal [week|month]` expectancy and mistakes · `/history` closed trades · `/hitrate` win rate by setup type

**🔔 Alerts** — `/alerts` list events with on/off toggles · `/alerts test` send a test push · `/quiet <on|off>` quiet hours

**⚙️ System** — `/health` sources, jobs, LLM quota · `/storage [days]` usage + age-based cleanup · `/gemini` model & API dashboard *(exists)* · `/memory [days]` chat retention *(exists)* · `/start` welcome

## Guided flows for multi-input commands

`/track` with no arguments should walk you through it rather than demanding syntax:

```
➕ Track a position

Send as one line:
SYMBOL ENTRY TARGET STOPLOSS [QTY]

Example:  RELIANCE 1240 1310 1195 20

Or tap a candidate from today's screener:
[ CUMMINSIND 4,190 ]  [ PERSISTENT 5,840 ]
[ 🔙 Cancel ]
```

Parse `/track RELIANCE 1240 1310 1195 20` directly when arguments are supplied. Same pattern for `/risk` and `/analyze`.

## Action buttons on alerts

Turn every alert into a one-tap action, which also keeps the journal populated for free:

| Alert | Buttons |
|---|---|
| TARGET_HIT_T1 | `✅ Booked 40%` · `📈 Hold all` · `🔒 Trail stop` |
| STOP_BREACHED | `✅ Exited` · `⏸ Still holding` |
| CANDIDATE_TRIGGERED | `➕ Track it` · `⏭ Skip` · `🔬 Analyse first` |
| MORNING_DIGEST | `🔬 Analyse #1` · `➕ Track #1` · `🔍 Full screener` |
| ETF_DIP | `✅ Deployed ₹X` · `⏭ Skip this dip` |
| TIME_EXIT_DUE | `✅ Exited` · `🔁 Re-entry (state why)` |

`✅ Booked` / `✅ Exited` / `⏭ Skip` write straight into `trade_journal` — so plan-versus-execution tracking (`FEATURES.md` F10) maintains itself instead of needing discipline.

## `/help` and first-run

`/help` lists commands grouped as in `/menu` with one-line explanations and one example each. `/start` gives a 5-line orientation plus the `/menu` keyboard — no wall of text.

---

## Implementation checklist

1. [ ] `core/alerts.py` — event catalogue as a dict: `{event: (priority, default_on, template)}`
2. [ ] `send_alert()` with the unique-index dedupe above
3. [ ] Quiet hours + per-symbol/day rate caps
4. [ ] `settings.alerts` config document; `/alerts` handler with toggle buttons; `GET/PUT /alerts/config`
5. [ ] Replace ad-hoc `bot.send_message` calls in the tracker with `send_alert()`
6. [ ] `setMyCommands` list above in `main.py` lifespan
7. [ ] `/menu` handler + category callbacks (`menu_positions`, `menu_find`, …)
8. [ ] Guided flows for `/track`, `/risk`, `/analyze`
9. [ ] Action buttons on P0/P1 alerts → journal writes
10. [ ] `/help` grouped listing
11. [ ] External watchdog for BOT_POLLING_DIED and JOB_DID_NOT_RUN (cron-job.org hitting `/health/ping`)

**Verify**
- [ ] Two stop breaches on the same position in one day → one alert, not two
- [ ] P2 alert at 21:00 IST → queued to the next digest, not pushed
- [ ] `/menu` reachable in two taps from any screen; every button works
- [ ] Every command in `setMyCommands` exists and responds
- [ ] `✅ Booked 40%` creates a `trade_journal` entry
- [ ] Killing a scheduled job produces JOB_FAILURE on both channels
- [ ] Alert storm test: 40 events in one day → capped at 25, remainder digested
