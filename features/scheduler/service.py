# scheduler feature: morning & evening automated routines
# New flow:
#   STEP 1 — Gemini (Search) → generates today's dynamic watchlist
#   STEP 2 — yfinance → fetches numeric data for cross-verification only
#   STEP 3 — Gemini cross-check: if divergence found, Gemini re-verifies; Gemini data wins
#   STEP 4 — Gemini deep research → full QMAF recommendation per stock

import json
import re
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore

from core.config import (
    SCHEDULER_MORNING_TIME,
    SCHEDULER_EVENING_TIME,
    SCHEDULER_TIMEZONE,
    MAX_WATCHLIST_STOCKS,
)
from core.database import mongo
from features.market_data.service import (
    fetch_for_verification,
    cross_check,
    format_verification_block,
)
from features.market_data.technical_indicators import (
    fetch_technical_indicators,
    fetch_option_chain,
    fetch_fii_dii_flows,
    format_technical_block,
    format_option_chain_block,
    format_fii_dii_block,
)
from features.knowledge_base.service import get_simple_rag_chunks, format_rag_context
from features.gemini.service import generate_with_gemini_fallback
from features.notifications.service import broadcast
from features.performance.service import log_recommendation, evaluate_day
from features.chat_memory.service import save_morning_alert
from features.bot.setup import bot
from core.config import USER_ID


# ─────────────────────── Scheduler instance ───────────────────────
scheduler = AsyncIOScheduler(timezone=SCHEDULER_TIMEZONE)


def start_scheduler() -> None:
    morning_h, morning_m = _parse_time(SCHEDULER_MORNING_TIME)
    evening_h, evening_m = _parse_time(SCHEDULER_EVENING_TIME)

    scheduler.add_job(
        morning_routine,
        CronTrigger(hour=morning_h, minute=morning_m, timezone=SCHEDULER_TIMEZONE),
        id="morning_routine",
        replace_existing=True,
    )
    scheduler.add_job(
        evening_routine,
        CronTrigger(hour=evening_h, minute=evening_m, timezone=SCHEDULER_TIMEZONE),
        id="evening_routine",
        replace_existing=True,
    )
    scheduler.add_job(
        intraday_scan_routine,
        CronTrigger(day_of_week='mon-fri', hour='9-14', minute='20,50', timezone=SCHEDULER_TIMEZONE),
        id='intraday_scan',
        replace_existing=True,
    )
    scheduler.add_job(
        news_scanner_routine,
        CronTrigger(day_of_week='mon-fri', hour='9-15', minute='*/5', timezone=SCHEDULER_TIMEZONE),
        id='news_scanner',
        replace_existing=True,
    )
    scheduler.add_job(
        custom_stock_minute_scan,
        CronTrigger(day_of_week='mon-fri', hour='3-10', minute='*', timezone='UTC'),
        id='custom_minute_scan',
        replace_existing=True,
    )
    scheduler.start()
    print(
        f"[Scheduler] Started — Morning: {SCHEDULER_MORNING_TIME}, "
        f"Evening: {SCHEDULER_EVENING_TIME} ({SCHEDULER_TIMEZONE})"
    )


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[Scheduler] Stopped.")


# ═══════════════════════════════════════════════════════════════════
#  MORNING ROUTINE
# ═══════════════════════════════════════════════════════════════════
async def morning_routine() -> None:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    print(f"[Morning] Starting at {today}")

    # ── Check for weekends ─────────────────────────────────────────
    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        print("[Morning] Weekend detected. Market closed.")
        message = "🛑 <b>Market Closed</b>\n\nToday is a weekend. The NSE is closed, so no morning stock recommendations will be generated. See you on Monday!"
        if USER_ID:
            try:
                await bot.send_message(chat_id=int(USER_ID), text=message, parse_mode="HTML")
            except Exception as e:
                print(f"[Morning] Failed to send weekend msg: {e}")
        await broadcast(text="Market closed for the weekend.", title="🛑 Weekend", ntfy_priority="default")
        return

    # ── STEP 1: Gemini generates today's dynamic watchlist ─────────
    symbols = await _gemini_generate_watchlist()
    if not symbols:
        print("[Morning] Gemini returned empty watchlist — aborting.")
        return

    print(f"[Morning] Watchlist for today: {', '.join(symbols)}")

    # ── Save today's watchlist to DB for evening routine ──────────
    if mongo.db is not None:
        await mongo.db.daily_watchlist.replace_one(
            {"date": today},
            {"date": today, "symbols": symbols, "created_at": datetime.now(timezone.utc)},
            upsert=True,
        )

    # ── Process each symbol ───────────────────────────────────────
    for symbol in symbols:
        await _process_symbol(symbol, today)
        # Add a delay between symbols to prevent Gemini API rate limits
        await asyncio.sleep(5)


async def _gemini_generate_watchlist() -> list[str]:
    """
    STEP 1: Ask Gemini (with live Google Search) to identify today's best
    NSE stocks based on current market conditions, momentum, news, and technicals.
    Returns a clean list of NSE ticker symbols.
    """
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    prompt = f"""You are a senior Indian stock market analyst with live market access.

Today is {today} (IST).

Task: Identify the TOP {MAX_WATCHLIST_STOCKS} NSE-listed stocks most suitable for today's trading session.

Selection criteria (mandatory):
1. Strong price momentum or key technical breakout/breakdown in progress
2. Significant news catalyst today (earnings, order win, promoter activity, corporate action)
3. Above-average volume and delivery percentage
4. Clear actionable setup — not sideways/range-bound
5. Must pass QMAF valuation guard: PEG < 1.5 or P/E < 1.2x 5-year median

For each stock provide a ONE-LINE rationale.

Respond STRICTLY in this JSON format (no extra text, no markdown):
{{
  "watchlist": [
    {{"symbol": "RELIANCE", "reason": "Breakout above 200 DMA on 3x volume with gas block win"}},
    {{"symbol": "INFY", "reason": "Earnings beat, delivery surge, bullish MACD crossover"}}
  ]
}}
"""
    try:
        result = await generate_with_gemini_fallback(prompt, model=None, use_search=True)
        text = result["text"].strip()

        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]

        data = json.loads(text)
        watchlist = data.get("watchlist", [])
        symbols = [
            item["symbol"].strip().upper()
            for item in watchlist
            if isinstance(item, dict) and "symbol" in item
        ]
        return symbols[:MAX_WATCHLIST_STOCKS]
    except json.JSONDecodeError:
        # Fallback: extract symbols with regex if JSON parse fails
        matches = re.findall(r'"symbol"\s*:\s*"([A-Z0-9&]+)"', result["text"])
        return [m.upper() for m in matches[:MAX_WATCHLIST_STOCKS]]
    except Exception as exc:
        print(f"[Morning] Watchlist generation failed: {exc}")
        return []


async def _process_symbol(symbol: str, today: str) -> None:
    print(f"[Morning] Processing {symbol}...")

    # ── STEP 2: yfinance verification fetch ───────────────────────
    yf_data = await fetch_for_verification(symbol)

    # ── STEP 3 & 4: Retry loop for Gemini calls ───────────────────
    max_retries = 3
    gemini_research = None
    report_text = ""
    
    for attempt in range(max_retries):
        try:
            gemini_research = await _gemini_research_stock(symbol, yf_data)
            if not gemini_research:
                print(f"[Morning] Gemini research returned None for {symbol} on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                    continue
                print(f"[Morning] Gemini research failed for {symbol} after {max_retries} attempts — skipping.")
                return

            report_text = await _gemini_deep_recommendation(symbol, gemini_research, yf_data)
            if report_text and not report_text.startswith("❌"):
                break # Success!
                
            print(f"[Morning] Recommendation failed for {symbol} on attempt {attempt + 1}: {report_text}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
        except Exception as e:
            print(f"[Morning] Exception on attempt {attempt + 1} for {symbol}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
                
    if not gemini_research or not report_text or report_text.startswith("❌"):
        print(f"[Morning] Skipping {symbol} due to repeated AI failures.")
        return

    # ── Broadcast ─────────────────────────────────────────────────
    # 1. Convert Markdown bold to HTML bold
    fmt_report = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", report_text)
    
    # 2. Convert Markdown table to readable list for Telegram
    fmt_report = fmt_report.replace("|-------|-------|-------|", "").replace("|---|---|---|", "")
    def format_table_row(match):
        col1, col2, col3 = [c.strip() for c in match.groups()]
        if "Level" in col1 and "Price" in col2:
            return "" # Skip header
        return f"• <b>{col1}</b>: {col2}\n  <i>{col3}</i>"
    fmt_report = re.sub(r"\|(.*?)\|(.*?)\|(.*?)\|", format_table_row, fmt_report)
    
    # 3. Clean up double empty lines caused by header removal
    fmt_report = fmt_report.replace("\n\n\n", "\n\n")

    # 4. Truncate cleanly at a newline to avoid slicing in the middle of a word
    if len(fmt_report) > 3800:
        cut_idx = fmt_report.rfind('\n', 0, 3800)
        if cut_idx == -1: cut_idx = 3800
        fmt_report = fmt_report[:cut_idx] + "\n...\n\n[View full report on StockAI Dashboard]"
        
    # 5. Telegram only supports &amp;, &lt;, &gt;, and &quot;. html.escape produces &#x27; which breaks it.
    safe_report_text = fmt_report.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    # 6. Restore the HTML tags we safely injected for formatting
    safe_report_text = safe_report_text.replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
    safe_report_text = safe_report_text.replace('&lt;i&gt;', '<i>').replace('&lt;/i&gt;', '</i>')
    
    message = f"📊 <b>Morning Report — {symbol}</b>\n\n{safe_report_text}"
    if USER_ID:
        try:
            await bot.send_message(chat_id=int(USER_ID), text=message, parse_mode="HTML")
        except Exception as exc:
            print(f"[Morning] Telegram failed for {symbol}: {exc}")

    await broadcast(
        text=f"Morning Report — {symbol}\n\n{report_text}",
        title=f"📊 {symbol} — Stock Alert",
        ntfy_priority="max",
    )

    # ── Persist to DB ─────────────────────────────────────────────
    await save_morning_alert(symbol, report_text)

    parsed = _parse_recommendation_fields(report_text)
    await log_recommendation(
        symbol=symbol,
        recommendation=parsed.get("recommendation", "UNKNOWN"),
        entry_zone=parsed.get("entry_zone", "N/A"),
        target=parsed.get("target", "N/A"),
        stop_loss=parsed.get("stop_loss", "N/A"),
        trade_type=parsed.get("trade_type", "N/A"),
        timeframe=parsed.get("timeframe", "N/A"),
        reason=parsed.get("reason", "See full report"),
        raw_ai_output=report_text,
        market_data_snapshot=yf_data,
    )

    # Auto-log virtual paper trade
    try:
        from features.portfolio.service import log_virtual_trade
        entry_price = gemini_research.get('live_price') or 0.0
        await log_virtual_trade(
            symbol=symbol,
            recommendation=parsed.get('recommendation', 'UNKNOWN'),
            entry_price=float(entry_price),
            target=parsed.get('target', 'N/A'),
            stop_loss=parsed.get('stop_loss', 'N/A'),
            date=today,
        )
    except Exception as e:
        print(f'[Portfolio] Failed to log virtual trade for {symbol}: {e}')

    print(f"[Morning] Done for {symbol}")


async def _gemini_research_stock(symbol: str, yf_data: dict) -> dict | None:
    """
    STEP 3a: Gemini performs live research on the stock.
    Returns a structured research dict including Gemini's own price finding.
    If divergence vs yfinance is significant, Gemini re-verifies in the same call.
    """
    yf_price = yf_data.get("cmp")
    yf_block = (
        f"yfinance verification data:\n"
        f"  CMP: ₹{yf_price}\n"
        f"  High: ₹{yf_data.get('high')}\n"
        f"  Low:  ₹{yf_data.get('low')}\n"
        f"  Volume: {yf_data.get('volume')}\n"
        f"  52W High: ₹{yf_data.get('week_52_high')}\n"
        f"  52W Low:  ₹{yf_data.get('week_52_low')}\n"
        f"  Source: {yf_data.get('source', 'unavailable')}\n"
        if yf_price
        else "yfinance data: unavailable — use your live research as the sole data source."
    )

    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    prompt = f"""You are a senior Indian market analyst with live market access.

Today is {today} (IST).
Stock: {symbol} (NSE)

{yf_block}

Tasks:
1. Fetch the CURRENT live price, today's OHLCV, delivery %, and volume from NSE/BSE.
2. Cross-check your live price against the yfinance CMP above.
   - If they match within 2%: confirm the data is consistent.
   - If they diverge by more than 2%: explicitly state which source is more accurate
     and why, then use YOUR researched live price as authoritative.
3. Gather: RSI, MACD, EMA 20/50/200, Wyckoff phase, Volume Spread Analysis signal.
4. Gather: Recent news, earnings, FII/DII activity, promoter action, OI data.

Respond in this EXACT valid JSON format (no markdown, no preamble, use double quotes, no trailing commas):
{{
  "live_price": 0.0,
  "price_source": "gemini_research",
  "data_verification": "match",
  "verification_note": "string",
  "ohlcv": {{"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}},
  "delivery_pct": 0.0,
  "rsi": 0.0,
  "macd_signal": "bullish",
  "ema_trend": "above_all",
  "wyckoff_phase": "unknown",
  "vsa_signal": "description",
  "key_news": ["news item 1"],
  "fii_dii": "unknown",
  "overall_bias": "bullish"
}}
"""
    try:
        result = await generate_with_gemini_fallback(prompt, model=None, use_search=True)
        text = result["text"].strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
            
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            import ast
            # Fallback for Python-style dicts (e.g. True instead of true, single quotes)
            return ast.literal_eval(text)
    except Exception as exc:
        raw_text = result.get("text", "N/A") if "result" in locals() else "N/A"
        print(f"[Research] Failed for {symbol}: {exc}\nRaw output: {raw_text}")
        return None


async def _gemini_deep_recommendation(
    symbol: str, research: dict, yf_data: dict
) -> str:
    """
    STEP 4: With verified research data, yfinance cross-check, pre-calculated
    technical indicators, NSE option chain, and FII/DII flows — generate the
    full structured recommendation using prompt.txt QMAF framework.
    """
    master_prompt = ""
    try:
        master_prompt = Path("prompt.txt").read_text(encoding="utf-8")
    except Exception:
        pass

    # ── Parallel fetch: indicators + option chain + FII/DII ──────────
    indicators, option_chain, fii_dii = await asyncio.gather(
        fetch_technical_indicators(symbol),
        fetch_option_chain(symbol),
        fetch_fii_dii_flows(),
        return_exceptions=True,
    )
    # Handle any gather exceptions gracefully
    if isinstance(indicators,  Exception): indicators  = {"error": str(indicators)}
    if isinstance(option_chain, Exception): option_chain = {"error": str(option_chain)}
    if isinstance(fii_dii,     Exception): fii_dii     = {"error": str(fii_dii)}

    # ── Build verification note ──────────────────────────────────────
    gemini_price = research.get("live_price")
    check = cross_check(symbol, gemini_price, yf_data)
    verification_block = format_verification_block(symbol, check)

    # ── Pull RAG context ─────────────────────────────────────────────
    rag_keywords = [symbol, research.get("wyckoff_phase", ""), "volume spread", "support resistance"]
    rag_chunks = await get_simple_rag_chunks(rag_keywords, top_k=3)
    rag_context = format_rag_context(rag_chunks)

    research_block      = json.dumps(research, indent=2)
    tech_block          = format_technical_block(indicators)    if isinstance(indicators,  dict) else "[Technical Indicators] N/A"
    option_chain_block  = format_option_chain_block(option_chain) if isinstance(option_chain, dict) else "[Option Chain] N/A"
    fii_dii_block       = format_fii_dii_block(fii_dii)         if isinstance(fii_dii,     dict) else "[FII/DII Flows] N/A"
    today               = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = f"""{master_prompt}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MORNING ANALYSIS TASK — {today}
Stock: {symbol} (NSE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GEMINI LIVE RESEARCH (authoritative — use as primary source):
{research_block}

{verification_block}

{tech_block}

{option_chain_block}

{fii_dii_block}

{rag_context}

INSTRUCTIONS:
- The technical indicator values above are mathematically calculated from real OHLCV data.
- Cross-verify them against your Gemini search results. If any value differs by > 2%, flag it.
- Use the ATR-based stop-loss (atr_stop_loss_1_5x) as the baseline for your stop-loss placement.
- Incorporate the Option Chain data (PCR, Max Pain, key OI strikes) into your support/resistance analysis.
- Use the FII/DII flow data to assess institutional sentiment.
- Follow the QMAF Mandatory Output structure exactly.
- Gemini live_price is the authoritative CMP.
"""
    try:
        result = await generate_with_gemini_fallback(prompt, model=None, use_search=False)
        return result["text"]
    except Exception as exc:
        return f"❌ Recommendation generation failed for {symbol}: {exc}"


# ═══════════════════════════════════════════════════════════════════
#  EVENING ROUTINE
# ═══════════════════════════════════════════════════════════════════
async def evening_routine() -> None:
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    print(f"[Evening] Starting at {today}")

    # ── Check for weekends ─────────────────────────────────────────
    if now.weekday() >= 5:
        print("[Evening] Weekend detected. Market closed.")
        message = "🛑 <b>Market Closed</b>\n\nEnjoy your weekend! No evening performance evaluations will run today."
        if USER_ID:
            try:
                await bot.send_message(chat_id=int(USER_ID), text=message, parse_mode="HTML")
            except Exception as e:
                pass
        return

    # Load today's symbols from DB (set by morning routine)
    symbols: list[str] = []
    if mongo.db is not None:
        doc = await mongo.db.daily_watchlist.find_one({"date": today})
        if doc:
            symbols = doc.get("symbols", [])

    if not symbols:
        print("[Evening] No watchlist found for today — aborting.")
        msg = (
            "📋 <b>Evening Calibration</b> — No Data\n\n"
            "No morning watchlist was found for today. "
            "This usually means the morning routine did not run or the market was closed."
        )
        if USER_ID:
            try:
                await bot.send_message(chat_id=int(USER_ID), text=msg, parse_mode="HTML")
            except Exception:
                pass
        return

    results_summary = []
    cards = []
    for symbol in symbols:
        # Use yfinance for closing price verification in the evening
        yf_data = await fetch_for_verification(symbol)
        if yf_data.get("error"):
            results_summary.append(f"{symbol}: data unavailable")
            cards.append(f"⚠️ <b>{symbol}</b> — Data unavailable")
            continue

        result = await evaluate_day(
            symbol=symbol,
            day_high=yf_data.get("high") or 0,
            day_low=yf_data.get("low") or 0,
            day_close=yf_data.get("cmp") or 0,
        )

        if "error" not in result:
            outcome = result['result']
            icon = "✅" if outcome == "PASS" else "❌"
            results_summary.append(f"{symbol}: {outcome} — {result['notes']}")
            cards.append(
                f"{icon} <b>{symbol}</b> — {outcome}\n"
                f"   📌 {result['notes']}"
            )
        else:
            err = result['error']
            results_summary.append(f"{symbol}: {err}")
            # Only show skipped in a clean way, not the raw error string
            if "No morning recommendation" in err:
                cards.append(f"⏭️ <b>{symbol}</b> — No morning recommendation (skipped)")
            else:
                cards.append(f"⚠️ <b>{symbol}</b> — {err}")

    ist_now = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")
    if results_summary:
        summary_text = (
            f"📋 <b>Evening Calibration Report</b>\n"
            f"🕒 {ist_now}\n"
            f"────────────────────\n"
            + "\n".join(cards)
        )
        if USER_ID:
            try:
                await bot.send_message(chat_id=int(USER_ID), text=summary_text, parse_mode="HTML")
            except Exception as exc:
                print(f"[Evening] Telegram failed: {exc}")

        await broadcast(
            text="Evening Calibration\n" + "\n".join(results_summary),
            title="📋 Evening Calibration",
            ntfy_priority="default",
        )

    print(f"[Evening] Done. {len(results_summary)} symbols evaluated.")


async def intraday_scan_routine() -> None:
    from features.intraday.service import run_intraday_scan
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return  # Skip weekends
    print(f'[Intraday] Running scan at {now.isoformat()}')
    results = await run_intraday_scan()
    print(f'[Intraday] Scan complete: {len(results)} stocks checked')

async def news_scanner_routine() -> None:
    from features.news_scanner.service import run_news_scanner
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return  # Skip weekends
    await run_news_scanner()

async def custom_stock_minute_scan() -> None:
    now = datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return
    if not (3 <= now.hour <= 10):
        return
    if mongo.db is None:
        return
        
    today = now.strftime("%Y-%m-%d")
    docs = await mongo.db.performance_log.find({"date": today, "is_custom": True}).to_list(length=None)
    if not docs:
        return
        
    symbols = [doc["symbol"] for doc in docs]
    from features.intraday.service import run_intraday_scan
    await run_intraday_scan(symbols_override=symbols)


# ─────────────────────── Parsing helpers ───────────────────────
def _parse_recommendation_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    patterns = {
        "recommendation": r"Recommendation\s*:\s*([A-Z]+)",
        "entry_zone": r"Entry[^:]*:\s*([\d,.\-–₹\s]+)",
        "target": r"Target[^:]*:\s*([\d,.\-–₹\s]+)",
        "stop_loss": r"Stop-Loss[^:]*:\s*([\d,.\-–₹\s]+)",
        "trade_type": r"Trade Type\s*:\s*(\w+(?:\s\w+)?)",
        "timeframe": r"Timeframe\s*:\s*(.+?)(?:\n|$)",
        "reason": r"Details\s*:\s*(.+?)(?:\n\n|\Z)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            fields[field] = match.group(1).strip()[:500]
    return fields


def _parse_time(time_str: str) -> tuple[int, int]:
    try:
        h, m = time_str.strip().split(":")
        return int(h), int(m)
    except Exception:
        return 9, 20
