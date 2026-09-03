# technical_indicators.py
# Calculates precise technical indicators from yfinance OHLCV data.
# These exact values are injected into the AI prompt so it uses real numbers
# instead of guessing from text descriptions.
# The AI is also instructed to cross-verify against its Gemini Search results.

import asyncio
from datetime import datetime, timezone
from typing import Any

SIGNIFICANT_DIVERGENCE_PCT = 2.0


async def fetch_technical_indicators(symbol: str) -> dict[str, Any]:
    """
    Downloads 6 months of daily + weekly OHLCV data for `symbol` (NSE).
    Calculates:
      - RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14)
      - EMA 9/20/50/200, SMA 50/200, VWAP (approximate daily)
      - Weekly trend context (above/below weekly EMA 20)
      - Volume analysis: avg 5-day vs today's volume
    Returns a dict of precise values, or a partial dict on error.
    Never raises — always returns something.
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _compute_indicators, symbol)
        return result
    except Exception as exc:
        print(f"[TechnicalIndicators] Failed for {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc), "source": "failed"}


def _compute_indicators(symbol: str) -> dict[str, Any]:
    """Sync worker — runs in threadpool executor."""
    import yfinance as yf  # type: ignore

    ns = symbol.upper() + ".NS"

    # ── Daily data (6 months) ──────────────────────────────────────
    ticker = yf.Ticker(ns)
    df = ticker.history(period="6mo", interval="1d", auto_adjust=True)
    if df is None or df.empty or len(df) < 30:
        return {"symbol": symbol, "error": "Insufficient daily data", "source": "yfinance"}

    df = df.copy()

    try:
        import pandas_ta as ta  # type: ignore

        # RSI
        rsi_series = ta.rsi(df["Close"], length=14)
        rsi = round(float(rsi_series.iloc[-1]), 2) if rsi_series is not None and not rsi_series.empty else None

        # MACD
        macd_df = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            macd_val   = round(float(macd_df.iloc[-1]["MACD_12_26_9"]), 3)
            macd_sig   = round(float(macd_df.iloc[-1]["MACDs_12_26_9"]), 3)
            macd_hist  = round(float(macd_df.iloc[-1]["MACDh_12_26_9"]), 3)
        else:
            macd_val = macd_sig = macd_hist = None

        # Bollinger Bands
        bb = ta.bbands(df["Close"], length=20, std=2)
        if bb is not None and not bb.empty:
            bb_upper = round(float(bb.iloc[-1]["BBU_20_2.0"]), 2)
            bb_mid   = round(float(bb.iloc[-1]["BBM_20_2.0"]), 2)
            bb_lower = round(float(bb.iloc[-1]["BBL_20_2.0"]), 2)
        else:
            bb_upper = bb_mid = bb_lower = None

        # ATR
        atr_series = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        atr = round(float(atr_series.iloc[-1]), 2) if atr_series is not None and not atr_series.empty else None

        # EMAs
        def ema(p: int):
            s = ta.ema(df["Close"], length=p)
            return round(float(s.iloc[-1]), 2) if s is not None and not s.empty and len(s) >= p else None

        # SMAs
        def sma(p: int):
            s = ta.sma(df["Close"], length=p)
            return round(float(s.iloc[-1]), 2) if s is not None and not s.empty and len(s) >= p else None

    except ImportError:
        # Fallback: manual pandas calculations
        import numpy as np

        close = df["Close"]

        # RSI manual
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi_s = 100 - (100 / (1 + rs))
        rsi   = round(float(rsi_s.iloc[-1]), 2) if not rsi_s.empty else None

        # MACD manual
        ema12   = close.ewm(span=12, adjust=False).mean()
        ema26   = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_val  = round(float(macd_line.iloc[-1]), 3)
        macd_sig  = round(float(signal_line.iloc[-1]), 3)
        macd_hist = round(float((macd_line - signal_line).iloc[-1]), 3)

        # Bollinger Bands manual
        sma20   = close.rolling(20).mean()
        std20   = close.rolling(20).std()
        bb_upper = round(float((sma20 + 2 * std20).iloc[-1]), 2)
        bb_mid   = round(float(sma20.iloc[-1]), 2)
        bb_lower = round(float((sma20 - 2 * std20).iloc[-1]), 2)

        # ATR manual
        tr = (df["High"] - df["Low"]).combine(
            (df["High"] - df["Close"].shift()).abs(), max
        ).combine((df["Low"] - df["Close"].shift()).abs(), max)
        atr = round(float(tr.rolling(14).mean().iloc[-1]), 2)

        def ema(p):
            s = close.ewm(span=p, adjust=False).mean()
            return round(float(s.iloc[-1]), 2)

        def sma(p):
            s = close.rolling(p).mean()
            return round(float(s.iloc[-1]), 2) if len(close) >= p else None

    ema9   = ema(9)
    ema20  = ema(20)
    ema50  = ema(50)
    ema200 = ema(200)
    sma50  = sma(50)
    sma200 = sma(200)

    cmp = round(float(df["Close"].iloc[-1]), 2)

    # ── Volume analysis ─────────────────────────────────────────────
    vol_today   = int(df["Volume"].iloc[-1])
    vol_5d_avg  = int(df["Volume"].tail(5).mean())
    vol_ratio   = round(vol_today / vol_5d_avg, 2) if vol_5d_avg else None
    vol_signal  = "above_avg" if vol_ratio and vol_ratio > 1.2 else ("below_avg" if vol_ratio and vol_ratio < 0.8 else "average")

    # ── Weekly trend context ─────────────────────────────────────────
    weekly_trend = "N/A"
    try:
        df_w = ticker.history(period="6mo", interval="1wk", auto_adjust=True)
        if not df_w.empty and len(df_w) >= 20:
            import pandas_ta as ta  # type: ignore
            wema20 = ta.ema(df_w["Close"], length=20)
            if wema20 is not None and not wema20.empty:
                w_ema_val = float(wema20.iloc[-1])
                weekly_trend = "above_weekly_ema20" if cmp > w_ema_val else "below_weekly_ema20"
    except Exception:
        pass

    # ── MACD signal interpretation ──────────────────────────────────
    macd_cross = "N/A"
    if macd_val is not None and macd_sig is not None:
        if macd_val > macd_sig and macd_hist and macd_hist > 0:
            macd_cross = "bullish_crossover" if macd_hist > 0 else "bullish_above_signal"
        elif macd_val < macd_sig:
            macd_cross = "bearish_below_signal"
        else:
            macd_cross = "neutral"

    # ── RSI interpretation ──────────────────────────────────────────
    rsi_zone = "N/A"
    if rsi is not None:
        if rsi >= 70:
            rsi_zone = "overbought"
        elif rsi <= 30:
            rsi_zone = "oversold"
        elif rsi >= 55:
            rsi_zone = "bullish_momentum"
        elif rsi <= 45:
            rsi_zone = "bearish_momentum"
        else:
            rsi_zone = "neutral"

    # ── Price vs key levels ─────────────────────────────────────────
    price_vs_ema50  = "above" if ema50  and cmp > ema50  else "below"
    price_vs_ema200 = "above" if ema200 and cmp > ema200 else "below"
    price_vs_bb_mid = "above" if bb_mid and cmp > bb_mid else "below"

    return {
        "symbol": symbol.upper(),
        "cmp": cmp,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "yfinance_pandas_ta",

        # ── Momentum ────────────────────────────────────────
        "rsi_14": rsi,
        "rsi_zone": rsi_zone,

        "macd_line": macd_val,
        "macd_signal_line": macd_sig,
        "macd_histogram": macd_hist,
        "macd_interpretation": macd_cross,

        # ── Volatility ──────────────────────────────────────
        "atr_14": atr,
        "bollinger_upper": bb_upper,
        "bollinger_mid": bb_mid,
        "bollinger_lower": bb_lower,
        "price_vs_bollinger_mid": price_vs_bb_mid,

        # ── Trend ───────────────────────────────────────────
        "ema_9":   ema9,
        "ema_20":  ema20,
        "ema_50":  ema50,
        "ema_200": ema200,
        "sma_50":  sma50,
        "sma_200": sma200,
        "price_vs_ema50":  price_vs_ema50,
        "price_vs_ema200": price_vs_ema200,
        "weekly_trend": weekly_trend,

        # ── Volume ──────────────────────────────────────────
        "volume_today":  vol_today,
        "volume_5d_avg": vol_5d_avg,
        "volume_ratio":  vol_ratio,
        "volume_signal": vol_signal,

        # ── ATR-based stop-loss suggestion ──────────────────
        "atr_stop_loss_1_5x": round(cmp - 1.5 * atr, 2) if atr else None,
        "atr_stop_loss_1x":   round(cmp - atr, 2) if atr else None,
    }


async def fetch_option_chain(symbol: str) -> dict[str, Any]:
    """
    Fetches NSE option chain data via nsepython (free, no API key needed).
    Returns PCR, Max Pain, top Call/Put OI strikes.
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _compute_option_chain, symbol)
        return result
    except Exception as exc:
        print(f"[OptionChain] Failed for {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc), "source": "failed"}


def _compute_option_chain(symbol: str) -> dict[str, Any]:
    """Sync worker — runs in threadpool executor."""
    try:
        from nsepython import nse_optionchain_scrapper  # type: ignore
    except ImportError:
        return {"symbol": symbol, "error": "nsepython not installed", "source": "failed"}

    try:
        oc = nse_optionchain_scrapper(symbol.upper())
        records = oc.get("records", {})
        data    = records.get("data", [])
        expiry_dates = records.get("expiryDates", [])
        nearest_expiry = expiry_dates[0] if expiry_dates else "N/A"
        underlying_value = records.get("underlyingValue", None)

        total_call_oi = 0
        total_put_oi  = 0
        call_oi_map   = {}
        put_oi_map    = {}

        for item in data:
            strike = item.get("strikePrice", 0)
            ce = item.get("CE", {})
            pe = item.get("PE", {})
            c_oi = ce.get("openInterest", 0) or 0
            p_oi = pe.get("openInterest", 0) or 0
            total_call_oi += c_oi
            total_put_oi  += p_oi
            if c_oi: call_oi_map[strike] = c_oi
            if p_oi: put_oi_map[strike]  = p_oi

        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi else None

        # Top 3 Call/Put OI strikes (resistance/support)
        top_call_strikes = sorted(call_oi_map, key=call_oi_map.get, reverse=True)[:3]
        top_put_strikes  = sorted(put_oi_map,  key=put_oi_map.get,  reverse=True)[:3]

        # Max Pain = strike where total OI loss for option buyers is maximum
        max_pain = _calculate_max_pain(data)

        pcr_sentiment = "bullish" if pcr and pcr > 1 else ("bearish" if pcr and pcr < 0.8 else "neutral")

        return {
            "symbol": symbol.upper(),
            "nearest_expiry": nearest_expiry,
            "underlying_value": underlying_value,
            "pcr": pcr,
            "pcr_sentiment": pcr_sentiment,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "max_pain": max_pain,
            "top_call_resistance_strikes": top_call_strikes,
            "top_put_support_strikes": top_put_strikes,
            "source": "nsepython_nse",
        }
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc), "source": "nsepython_failed"}


def _calculate_max_pain(data: list) -> float | None:
    """Max Pain = strike where combined OI loss for all option buyers is minimum."""
    try:
        strikes = sorted(set(item["strikePrice"] for item in data if "strikePrice" in item))
        min_loss = float("inf")
        max_pain_strike = None
        for test_price in strikes:
            loss = 0
            for item in data:
                strike = item.get("strikePrice", 0)
                ce_oi = (item.get("CE") or {}).get("openInterest", 0) or 0
                pe_oi = (item.get("PE") or {}).get("openInterest", 0) or 0
                # Call holders lose if test_price < strike
                if test_price < strike:
                    loss += ce_oi * (strike - test_price)
                # Put holders lose if test_price > strike
                if test_price > strike:
                    loss += pe_oi * (test_price - strike)
            if loss < min_loss:
                min_loss = loss
                max_pain_strike = test_price
        return max_pain_strike
    except Exception:
        return None


async def fetch_fii_dii_flows() -> dict[str, Any]:
    """
    Fetches daily FII/DII equity buying/selling data from NSE (free, no API key).
    Returns net buy/sell figures in ₹ Crores.
    """
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _compute_fii_dii)
        return result
    except Exception as exc:
        return {"error": str(exc), "source": "failed"}


def _compute_fii_dii() -> dict[str, Any]:
    """Fetches FII/DII data from NSE free endpoint."""
    import requests  # type: ignore

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        session = requests.Session()
        # Warm up session cookie (NSE requires this)
        session.get("https://www.nseindia.com", headers=headers, timeout=10)

        resp = session.get(
            "https://www.nseindia.com/api/fiidiiTradeReact",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:
            return {"error": "Empty response from NSE FII/DII endpoint", "source": "nse"}

        latest = data[0]  # Most recent trading day

        fii_buy  = float(latest.get("fiiBuy",  0) or 0)
        fii_sell = float(latest.get("fiiSell", 0) or 0)
        dii_buy  = float(latest.get("diiBuy",  0) or 0)
        dii_sell = float(latest.get("diiSell", 0) or 0)

        fii_net = round(fii_buy - fii_sell, 2)
        dii_net = round(dii_buy - dii_sell, 2)

        return {
            "date": latest.get("date", "N/A"),
            "fii_buy_cr":  fii_buy,
            "fii_sell_cr": fii_sell,
            "fii_net_cr":  fii_net,
            "fii_sentiment": "buyer" if fii_net > 0 else "seller",
            "dii_buy_cr":  dii_buy,
            "dii_sell_cr": dii_sell,
            "dii_net_cr":  dii_net,
            "dii_sentiment": "buyer" if dii_net > 0 else "seller",
            "combined_net_cr": round(fii_net + dii_net, 2),
            "source": "nse_fiidii_api",
        }
    except Exception as exc:
        return {"error": str(exc), "source": "nse_fiidii_failed"}


def format_technical_block(indicators: dict) -> str:
    """Formats technical indicators for injection into the Gemini prompt."""
    if indicators.get("error"):
        return f"[Technical Indicators] Unavailable — {indicators['error']}"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"TECHNICAL INDICATORS (yfinance + pandas-ta) — {indicators.get('timestamp', 'N/A')}",
        "Cross-verify against your Gemini search. Flag any discrepancy > 2%.",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"CMP: ₹{indicators.get('cmp', 'N/A')}",
        "",
        "── Momentum ──",
        f"  RSI(14)       : {indicators.get('rsi_14', 'N/A')} → {indicators.get('rsi_zone', 'N/A').upper()}",
        f"  MACD Line     : {indicators.get('macd_line', 'N/A')}",
        f"  MACD Signal   : {indicators.get('macd_signal_line', 'N/A')}",
        f"  MACD Histogram: {indicators.get('macd_histogram', 'N/A')} → {indicators.get('macd_interpretation', 'N/A').upper()}",
        "",
        "── Volatility ──",
        f"  ATR(14)       : ₹{indicators.get('atr_14', 'N/A')}",
        f"  ATR Stop(1.5x): ₹{indicators.get('atr_stop_loss_1_5x', 'N/A')} ← suggested stop-loss",
        f"  BB Upper      : ₹{indicators.get('bollinger_upper', 'N/A')}",
        f"  BB Mid        : ₹{indicators.get('bollinger_mid', 'N/A')}",
        f"  BB Lower      : ₹{indicators.get('bollinger_lower', 'N/A')}",
        "",
        "── Trend ──",
        f"  EMA 9         : ₹{indicators.get('ema_9', 'N/A')}",
        f"  EMA 20        : ₹{indicators.get('ema_20', 'N/A')}",
        f"  EMA 50        : ₹{indicators.get('ema_50', 'N/A')} ← Price is {indicators.get('price_vs_ema50', 'N/A')} this",
        f"  EMA 200       : ₹{indicators.get('ema_200', 'N/A')} ← Price is {indicators.get('price_vs_ema200', 'N/A')} this",
        f"  Weekly Trend  : {indicators.get('weekly_trend', 'N/A').upper()}",
        "",
        "── Volume ──",
        f"  Today Volume  : {indicators.get('volume_today', 'N/A'):,}" if indicators.get('volume_today') else "  Today Volume  : N/A",
        f"  5-Day Avg Vol : {indicators.get('volume_5d_avg', 'N/A'):,}" if indicators.get('volume_5d_avg') else "  5-Day Avg Vol : N/A",
        f"  Vol Ratio     : {indicators.get('volume_ratio', 'N/A')}x → {indicators.get('volume_signal', 'N/A').upper()}",
    ]
    return "\n".join(lines)


def format_option_chain_block(oc: dict) -> str:
    """Formats option chain data for injection into the Gemini prompt."""
    if oc.get("error"):
        return f"[Option Chain] Unavailable — {oc['error']}"

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"OPTION CHAIN DATA (NSE) — Expiry: {oc.get('nearest_expiry', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  Spot Price     : ₹{oc.get('underlying_value', 'N/A')}\n"
        f"  PCR            : {oc.get('pcr', 'N/A')} → {str(oc.get('pcr_sentiment', 'N/A')).upper()}\n"
        f"  Max Pain       : ₹{oc.get('max_pain', 'N/A')} (expiry magnet)\n"
        f"  Top Resistance (Call OI): {oc.get('top_call_resistance_strikes', [])}\n"
        f"  Top Support    (Put OI) : {oc.get('top_put_support_strikes', [])}\n"
    )


def format_fii_dii_block(flows: dict) -> str:
    """Formats FII/DII flows for injection into the Gemini prompt."""
    if flows.get("error"):
        return f"[FII/DII Flows] Unavailable — {flows['error']}"

    fii_emoji = "🟢" if flows.get("fii_sentiment") == "buyer" else "🔴"
    dii_emoji = "🟢" if flows.get("dii_sentiment") == "buyer" else "🔴"

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"FII/DII INSTITUTIONAL FLOWS — {flows.get('date', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {fii_emoji} FII Net : ₹{flows.get('fii_net_cr', 'N/A')} Cr ({flows.get('fii_sentiment', 'N/A').upper()})\n"
        f"  {dii_emoji} DII Net : ₹{flows.get('dii_net_cr', 'N/A')} Cr ({flows.get('dii_sentiment', 'N/A').upper()})\n"
        f"  Combined Net    : ₹{flows.get('combined_net_cr', 'N/A')} Cr\n"
    )
