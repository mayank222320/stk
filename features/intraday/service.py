from datetime import datetime, timezone, timedelta
import re
from core.database import mongo
from core.config import USER_ID
from features.bot.setup import bot
from features.notifications.service import broadcast
from bson import ObjectId
import yfinance as yf


def _parse_price(text: str) -> float:
    """Parse price strings like '₹1,315', '1270-1285', '1,270–1,285' → first float found."""
    nums = re.findall(r'[\d.]+', str(text).replace(',', ''))
    if nums:
        return float(nums[0])
    return 0.0

async def run_intraday_scan(symbols_override: list[str] | None = None) -> list[dict]:
    if mongo.db is None:
        return []
        
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scan_time = datetime.now(timezone.utc)
    
    if symbols_override is not None:
        symbols = symbols_override
        print(f"[Intraday] Scanning {len(symbols)} custom symbols: {', '.join(symbols)}")
    else:
        # Load today's watchlist
        wl_doc = await mongo.db.daily_watchlist.find_one({"date": today})
        if not wl_doc:
            print(f"[Intraday] No watchlist found for {today}")
            return [{"error": "No watchlist found for today. Morning routine must run first.", "date": today}]
        symbols = wl_doc.get("symbols", [])
        print(f"[Intraday] Scanning {len(symbols)} symbols: {', '.join(symbols)}")
    
    results = []
    
    for symbol in symbols:
        symbol = symbol.strip().upper()  # BUG FIX 1: always normalize to uppercase

        # Load today's performance_log recommendation
        rec_doc = await mongo.db.performance_log.find_one({
            "date": today, 
            "symbol": symbol,  # now always uppercase, matches log_recommendation
        })

        # Parse target and stop_loss if rec available
        target = 0.0
        stop_loss = 0.0
        entry_zone = "N/A"
        if rec_doc:
            target = _parse_price(rec_doc.get("target", "0"))
            stop_loss = _parse_price(rec_doc.get("stop_loss", "0"))
            entry_zone = rec_doc.get("entry_zone", "N/A")
            print(f"[Intraday] {symbol} -> rec={rec_doc.get('recommendation')} target={target} sl={stop_loss}")
        else:
            print(f"[Intraday] {symbol} -> No performance_log entry found (morning rec may have failed)")
            
        # BUG FIX 2: Use interval='1m' to get LIVE intraday price, not previous close
        try:
            ticker = yf.Ticker(f"{symbol}.NS")
            info = ticker.history(period="1d", interval="1m")  # live 1-min bars
            if info.empty:
                # Fallback: try BSE suffix
                ticker = yf.Ticker(f"{symbol}.BO")
                info = ticker.history(period="1d", interval="1m")
            if info.empty:
                print(f"[Intraday] {symbol} -> yfinance returned empty data")
                results.append({"symbol": symbol, "status": "DATA_UNAVAILABLE", "price": 0, "date": today,
                                 "scan_time": scan_time.isoformat(), "alerted": False,
                                 "target": str(target), "stop_loss": str(stop_loss), "entry_zone": entry_zone, "id": ""})
                continue
            price = float(info['Close'].iloc[-1])  # latest 1-min close = live price
            print(f"[Intraday] {symbol} -> live price INR {price:.2f}")
            
            # Day stats from 1-min bars
            day_high = float(info['High'].max()) if not info.empty else 0.0
            day_low = float(info['Low'].min()) if not info.empty else 0.0
            # VWAP = sum(typical_price * volume) / sum(volume)
            typical = (info['High'] + info['Low'] + info['Close']) / 3
            vwap_val = float((typical * info['Volume']).sum() / info['Volume'].sum()) if info['Volume'].sum() > 0 else 0.0

            # P&L vs entry
            entry_price = float(rec_doc.get("entry_price", 0) or 0) if rec_doc else 0.0
            quantity = int(rec_doc.get("quantity", 0) or 0) if rec_doc else 0
            unrealized_pnl_pct = round((price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0.0
            unrealized_pnl_inr = round((price - entry_price) * quantity, 2) if quantity > 0 and entry_price > 0 else 0.0

            # Progress % from SL to T1 (0=at SL, 100=at T1)
            t1_val = float(rec_doc.get("t1") or rec_doc.get("target", 0) or 0) if rec_doc else target
            range_total = t1_val - stop_loss if (t1_val > stop_loss and stop_loss > 0) else 1
            progress_pct = round(max(0, min(100, (price - stop_loss) / range_total * 100)), 1) if range_total > 0 else 0.0

        except Exception as e:
            print(f"[Intraday] {symbol} -> yfinance error: {e}")
            results.append({"symbol": symbol, "status": "DATA_UNAVAILABLE", "price": 0, "date": today,
                             "scan_time": scan_time.isoformat(), "alerted": False,
                             "target": str(target), "stop_loss": str(stop_loss), "entry_zone": entry_zone, "id": ""})
            continue
            
        # Check condition (only if we have valid target & SL from rec)
        status = 'SAFE'
        if rec_doc and target > 0 and stop_loss > 0:
            recommendation = str(rec_doc.get("recommendation", "")).upper()
            if recommendation in ["BUY", "ACCUMULATE"]:
                if price >= target:
                    status = 'TARGET_HIT'
                elif price <= stop_loss:
                    status = 'SL_BREACHED'
            elif recommendation in ["SELL", "AVOID"]:
                if price <= target:
                    status = 'TARGET_HIT'
                elif price >= stop_loss:
                    status = 'SL_BREACHED'
        
        print(f"[Intraday] {symbol} → status={status}")
                
        # Send Telegram if breach and not already alerted today
        alerted = False
        if status in ['TARGET_HIT', 'SL_BREACHED']:
            prev_alert = await mongo.db.intraday_scans.find_one({
                "date": today,
                "symbol": symbol,
                "status": status,
                "alerted": True
            })
            if not prev_alert:
                alerted = True
                status_icon = "🎯" if status == "TARGET_HIT" else "⛔"
                status_label = "TARGET HIT 🎉" if status == "TARGET_HIT" else "STOP-LOSS BREACHED"
                recommendation = str(rec_doc.get("recommendation", "N/A")).upper() if rec_doc else "N/A"
                ist_time = datetime.now(timezone.utc).strftime("%I:%M %p UTC")
                hold_duration = rec_doc.get('hold_duration', 'Intraday') if rec_doc else 'Intraday'
                msg = (
                    f'{status_icon} <b>Intraday Alert — {symbol}</b>\n'
                    f'🕒 {ist_time}\n'
                    f'────────────────────\n'
                    f'<b>Status:</b> {status_label}\n'
                    f'<b>Recommendation:</b> {recommendation}\n'
                    f'<b>Live Price:</b> ₹{price:,.2f}\n'
                    f'<b>Entry Zone:</b> {entry_zone}\n'
                    f'<b>Target:</b> ₹{target:,.2f} | <b>SL:</b> ₹{stop_loss:,.2f}'
                )
                
                if status == 'TARGET_HIT':
                    if hold_duration == 'Intraday':
                        msg += f'\n<b>💰 Action:</b> Book FULL profits now. Square off before 3:10 PM.'
                    elif hold_duration == 'Swing':
                        msg += f'\n<b>💰 Action:</b> Book 50% profits. Trail SL to entry zone.'
                    elif hold_duration == 'Positional':
                        msg += f'\n<b>💰 Action:</b> Book 40% (T1). Hold rest with trailing SL.'
                elif status == 'SL_BREACHED':
                    msg += f'\n<b>🚪 Action:</b> EXIT immediately. Stop-loss discipline is non-negotiable.'
                
                if USER_ID:
                    try:
                        await bot.send_message(chat_id=int(USER_ID), text=msg, parse_mode="HTML")
                        print(f"[Intraday] Alert sent for {symbol} ({status})")
                    except Exception as e:
                        print(f"[Intraday] Telegram failed for {symbol}: {e}")

                # ntfy push notification
                ntfy_title = f"{'🎯 Target Hit' if status == 'TARGET_HIT' else '⛔ Stop-Loss Breached'} — {symbol}"
                ntfy_body = (
                    f"Price: ₹{price:,.2f} | Target: ₹{target:,.2f} | SL: ₹{stop_loss:,.2f}\n"
                    f"Action: {'Book profits' if status == 'TARGET_HIT' else 'EXIT immediately'}"
                )
                try:
                    await broadcast(
                        text=ntfy_body,
                        title=ntfy_title,
                        ntfy_priority="default",
                    )
                except Exception as e:
                    print(f"[Intraday] ntfy failed for {symbol}: {e}")
        
        hold_duration = rec_doc.get('hold_duration', 'Intraday') if rec_doc else 'Intraday'
        
        # Save scan to DB
        doc = {
            "symbol": symbol,
            "date": today,
            "scan_time": scan_time,
            "price": round(price, 2),
            "status": status,
            "alerted": alerted,
            "target": str(target),
            "stop_loss": str(stop_loss),
            "entry_zone": entry_zone,
            "hold_duration": hold_duration,
            "day_high": round(day_high, 2),
            "day_low": round(day_low, 2),
            "vwap": round(vwap_val, 2),
            "entry_price": entry_price,
            "quantity": quantity,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "unrealized_pnl_inr": unrealized_pnl_inr,
            "progress_pct": progress_pct,
            "t1": str(t1_val),
            "t2": str(rec_doc.get("t2", "")) if rec_doc else "",
            "t3": str(rec_doc.get("t3", "")) if rec_doc else "",
        }
        result = await mongo.db.intraday_scans.insert_one(doc)
        
        results.append({
            "id": str(result.inserted_id),
            "symbol": symbol,
            "date": today,
            "scan_time": scan_time.isoformat(),
            "price": round(price, 2),
            "status": status,
            "alerted": alerted,
            "target": str(target),
            "stop_loss": str(stop_loss),
            "entry_zone": entry_zone,
            "hold_duration": hold_duration,
            "day_high": round(day_high, 2),
            "day_low": round(day_low, 2),
            "vwap": round(vwap_val, 2),
            "entry_price": entry_price,
            "quantity": quantity,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "unrealized_pnl_inr": unrealized_pnl_inr,
            "progress_pct": progress_pct,
            "t1": str(t1_val),
            "t2": str(rec_doc.get("t2", "")) if rec_doc else "",
            "t3": str(rec_doc.get("t3", "")) if rec_doc else "",
        })
        
    return results

async def add_custom_track(
    symbol: str,
    entry_price: float,
    target: float,
    stop_loss: float,
    stock_name: str = "",
    direction: str = "BUY",
    hold_duration: str = "Intraday",
    quantity: int = 0,
    t1: float = 0.0,
    t2: float = 0.0,
    t3: float = 0.0,
    notes: str = "",
) -> dict:
    """Add a custom stock to today's intraday watchlist and create a performance_log entry."""
    if mongo.db is None:
        return {"error": "MongoDB not connected"}
    
    if t1 == 0.0 and target > 0:
        t1 = target
    
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = symbol.strip().upper()
    
    # 1. Add to daily_watchlist (upsert — create if not exists, append symbol if already exists)
    await mongo.db.daily_watchlist.update_one(
        {"date": today},
        {
            "$setOnInsert": {"date": today, "created_at": datetime.now(timezone.utc)},
            "$addToSet": {"symbols": symbol}
        },
        upsert=True
    )
    
    # 2. Check if an active custom track already exists for this symbol today
    existing = await mongo.db.performance_log.find_one({
        "date": today, 
        "symbol": symbol, 
        "is_custom": True,
        "result": {"$ne": "CLOSED"}
    })
    if existing:
        return {"status": "already_tracking", "symbol": symbol, "message": f"{symbol} is already being tracked today."}
    
    # 3. Insert a mock performance_log entry so the intraday scanner can grade it
    doc = {
        "date": today,
        "logged_at": datetime.now(timezone.utc),
        "symbol": symbol,
        "stock_name": stock_name or symbol,
        "recommendation": direction.upper(),
        "entry_zone": str(entry_price),
        "target": str(target),
        "stop_loss": str(stop_loss),
        "trade_type": "INTRADAY",
        "timeframe": "Intraday",
        "reason": f"Manually tracked by user. Entry: {entry_price}, Target: {target}, SL: {stop_loss}",
        "raw_ai_output": f"Custom track for {symbol}. Entry: {entry_price}. Target: {target}. Stop-Loss: {stop_loss}.",
        "market_data_snapshot": {},
        "result": None,
        "calibration_score": None,
        "evaluated_at": None,
        "is_custom": True,
        "hold_duration": hold_duration,
        "entry_price": entry_price,
        "quantity": quantity,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "notes": notes,
        "direction": direction,
    }
    result = await mongo.db.performance_log.insert_one(doc)
    
    return {
        "status": "success",
        "symbol": symbol,
        "stock_name": stock_name or symbol,
        "entry_price": entry_price,
        "target": target,
        "stop_loss": stop_loss,
        "direction": direction.upper(),
        "id": str(result.inserted_id),
        "message": f"{symbol} is now being tracked for today's intraday session."
    }

async def update_custom_track(symbol: str, updates: dict) -> dict:
    """Edit an existing custom tracked stock for today."""
    if mongo.db is None:
        return {"error": "MongoDB not connected"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = symbol.strip().upper()
    # Only allow updating safe fields, not symbol/date/is_custom
    allowed = {"entry_price", "target", "stop_loss", "t1", "t2", "t3",
               "quantity", "notes", "hold_duration", "direction", "stock_name"}
    safe = {k: v for k, v in updates.items() if k in allowed}
    if not safe:
        return {"error": "No valid fields to update"}
    result = await mongo.db.performance_log.update_one(
        {"date": today, "symbol": symbol, "is_custom": True},
        {"$set": safe}
    )
    if result.matched_count == 0:
        return {"error": f"No active custom track found for {symbol}"}
    return {"status": "updated", "symbol": symbol, "fields": list(safe.keys())}

async def close_position(symbol: str, exit_price: float) -> dict:
    """Mark a custom tracked stock as manually closed."""
    if mongo.db is None:
        return {"error": "MongoDB not connected"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = symbol.strip().upper()
    doc = await mongo.db.performance_log.find_one(
        {"date": today, "symbol": symbol, "is_custom": True}
    )
    if not doc:
        return {"error": f"No active custom track found for {symbol}"}
    entry_price = float(doc.get("entry_price", 0) or 0)
    quantity = int(doc.get("quantity", 0) or 0)
    final_pnl_pct = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0.0
    final_pnl_inr = round((exit_price - entry_price) * quantity, 2) if quantity > 0 else 0.0
    await mongo.db.performance_log.update_one(
        {"_id": doc["_id"]},
        {"$set": {
            "result": "CLOSED",
            "exit_price": exit_price,
            "final_pnl_pct": final_pnl_pct,
            "final_pnl_inr": final_pnl_inr,
            "closed_at": datetime.now(timezone.utc),
        }}
    )
    return {
        "status": "closed", "symbol": symbol,
        "exit_price": exit_price, "final_pnl_pct": final_pnl_pct, "final_pnl_inr": final_pnl_inr,
    }

async def get_all_custom_tracks() -> dict:
    """Return all custom tracked stocks for today — active and closed."""
    if mongo.db is None:
        return {"active": [], "closed": []}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor = mongo.db.performance_log.find({"date": today, "is_custom": True})
    docs = await cursor.to_list(length=None)
    # For each active position, get the latest scan price
    active, closed = [], []
    for d in docs:
        d["id"] = str(d.pop("_id"))
        for field in ["logged_at", "evaluated_at", "closed_at"]:
            if d.get(field):
                d[field] = d[field].isoformat()
        # Get latest scan price
        latest_scan = await mongo.db.intraday_scans.find_one(
            {"date": today, "symbol": d["symbol"]},
            sort=[("scan_time", -1)]
        )
        if latest_scan:
            d["current_price"] = latest_scan.get("price", 0)
            d["day_high"] = latest_scan.get("day_high")
            d["day_low"] = latest_scan.get("day_low")
            d["vwap"] = latest_scan.get("vwap")
            d["unrealized_pnl_pct"] = latest_scan.get("unrealized_pnl_pct")
            d["unrealized_pnl_inr"] = latest_scan.get("unrealized_pnl_inr")
            d["progress_pct"] = latest_scan.get("progress_pct")
            d["status"] = latest_scan.get("status", "SAFE")
            d["last_scan_time"] = latest_scan["scan_time"].isoformat() if latest_scan.get("scan_time") else None
        else:
            d["current_price"] = d.get("entry_price", 0)
            d["status"] = "NO_SCAN_YET"
            try:
                sl_val = float(d.get("stop_loss", 0))
                target_val = float(d.get("t1") or d.get("target", 0))
                price_val = float(d.get("entry_price", 0))
                range_total = target_val - sl_val
                if range_total > 0:
                    d["progress_pct"] = round(max(0, min(100, (price_val - sl_val) / range_total * 100)), 1)
                else:
                    d["progress_pct"] = 0.0
            except:
                d["progress_pct"] = 0.0
        if d.get("result") == "CLOSED":
            closed.append(d)
        else:
            active.append(d)
    return {"active": active, "closed": closed}

async def get_ai_update(symbol: str) -> dict:
    """Get a comprehensive QMAF-Advisor AI action recommendation for a tracked stock."""
    from features.gemini.service import generate_with_gemini_fallback
    if mongo.db is None:
        return {"error": "MongoDB not connected"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = symbol.strip().upper()
    doc = await mongo.db.performance_log.find_one(
        {"date": today, "symbol": symbol, "is_custom": True}
    )
    if not doc:
        return {"error": f"No custom track found for {symbol}"}
    
    # Get latest scan
    latest_scan = await mongo.db.intraday_scans.find_one(
        {"date": today, "symbol": symbol},
        sort=[("scan_time", -1)]
    )
    current_price = latest_scan.get("price", 0) if latest_scan else doc.get("entry_price", 0)
    day_high = latest_scan.get("day_high", "N/A") if latest_scan else "N/A"
    day_low = latest_scan.get("day_low", "N/A") if latest_scan else "N/A"
    vwap = latest_scan.get("vwap", "N/A") if latest_scan else "N/A"
    entry = doc.get("entry_price", 0)
    t1 = doc.get("t1") or doc.get("target", 0)
    t2 = doc.get("t2", "N/A")
    t3 = doc.get("t3", "N/A")
    sl = doc.get("stop_loss", 0)
    hold = doc.get("hold_duration", "Intraday")
    notes = doc.get("notes", "") or 'None'
    
    # Fetch prompt version from settings (default to v2)
    settings = await mongo.db.settings.find_one({"_id": "global_settings"}) or {}
    prompt_version = settings.get("qmaf_prompt_version", "v2_institutional")
    
    from features.intraday.prompts import PROMPT_V1_COMPACT, get_v2_prompt
    
    if prompt_version == "v1_compact":
        prompt_template = PROMPT_V1_COMPACT
    else:
        prompt_template = get_v2_prompt()
        
    prompt = prompt_template.format(
        symbol=symbol,
        hold=hold,
        entry=entry,
        current_price=current_price,
        day_high=day_high,
        day_low=day_low,
        vwap=vwap,
        t1=t1,
        t2=t2,
        t3=t3,
        sl=sl,
        notes=notes
    )

    try:
        # Enable search so Gemini can fetch live news and prices
        result = await generate_with_gemini_fallback(prompt, model=None, use_search=True)
        return {"advice": result["text"].strip(), "symbol": symbol, "generated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"error": str(e)}

async def untrack_stock(symbol: str) -> bool:
    """Completely untrack a stock for today: removes from watchlist, performance_log, and deletes today's scans."""
    if mongo.db is None:
        return False
        
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    symbol = symbol.strip().upper()
    
    try:
        # 1. Remove from today's watchlist
        await mongo.db.daily_watchlist.update_one(
            {"date": today},
            {"$pull": {"symbols": symbol}}
        )
        # 2. Remove from today's performance log
        await mongo.db.performance_log.delete_many({"date": today, "symbol": symbol})
        # 3. Delete today's intraday scans for this symbol
        await mongo.db.intraday_scans.delete_many({"date": today, "symbol": symbol})
        
        return True
    except Exception as e:
        print(f"[Intraday] Failed to untrack {symbol}: {e}")
        return False


async def get_stock_scans(symbol: str, date: str | None = None) -> list[dict]:
    """Get all 30-min scan snapshots for a specific symbol on a given date (default today)."""
    if mongo.db is None:
        return []
    query_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor = mongo.db.intraday_scans.find(
        {"symbol": symbol.upper(), "date": query_date}
    ).sort("scan_time", 1)
    docs = await cursor.to_list(length=None)
    for d in docs:
        d["id"] = str(d.pop("_id"))
        if d.get("scan_time"):
            d["scan_time"] = d["scan_time"].isoformat()
    return docs

async def get_todays_scans() -> list[dict]:
    if mongo.db is None:
        return []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor = mongo.db.intraday_scans.find({"date": today}).sort("scan_time", -1)
    docs = await cursor.to_list(length=None)
    for d in docs:
        d["id"] = str(d.pop("_id"))  # consistent with get_stock_scans
        if d.get("scan_time"):
            d["scan_time"] = d["scan_time"].isoformat()
    return docs

async def get_scan_history(days: int = 7) -> list[dict]:
    if mongo.db is None:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cursor = mongo.db.intraday_scans.find({
        "scan_time": {"$gte": cutoff},
        "alerted": True
    }).sort("scan_time", -1)
    docs = await cursor.to_list(length=None)
    for d in docs:
        d["_id"] = str(d["_id"])
        if d.get("scan_time"):
            d["scan_time"] = d["scan_time"].isoformat()
    return docs

async def delete_scan(scan_id: str) -> bool:
    if mongo.db is None:
        return False
    try:
        result = await mongo.db.intraday_scans.delete_one({"_id": ObjectId(scan_id)})
        return result.deleted_count > 0
    except Exception as exc:
        print(f"[Intraday] delete_scan failed for id={scan_id}: {exc}")
        return False

async def clear_all_scans() -> int:
    if mongo.db is None:
        return 0
    result = await mongo.db.intraday_scans.delete_many({})
    return result.deleted_count

