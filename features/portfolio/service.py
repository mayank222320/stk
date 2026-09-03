from datetime import datetime, timezone
from core.database import mongo
from bson import ObjectId
import yfinance as yf
import asyncio

async def log_virtual_trade(symbol, recommendation, entry_price, target, stop_loss, date) -> None:
    if mongo.db is None:
        return
        
    doc = {
        "symbol": symbol,
        "date": date,
        "recommendation": recommendation,
        "entry_price": entry_price,
        "target": target,
        "stop_loss": stop_loss,
        "current_price": entry_price,
        "status": "open",
        "pnl_pct": 0.0,
        "pnl_inr": 0.0,
        "trade_size": 20000,
        "logged_at": datetime.now(timezone.utc),
        "closed_at": None
    }
    await mongo.db["virtual_portfolio"].insert_one(doc)

async def update_position_price(symbol: str, current_price: float) -> None:
    if mongo.db is None:
        return
        
    cursor = mongo.db["virtual_portfolio"].find({"symbol": symbol, "status": "open"})
    docs = await cursor.to_list(length=None)
    
    for doc in docs:
        entry = doc.get("entry_price", 0)
        if entry > 0:
            if doc.get("recommendation", "").upper() in ["BUY", "ACCUMULATE"]:
                pnl_pct = ((current_price - entry) / entry) * 100
            elif doc.get("recommendation", "").upper() in ["SELL", "AVOID"]:
                pnl_pct = ((entry - current_price) / entry) * 100
            else:
                pnl_pct = 0.0
                
            pnl_inr = (doc.get("trade_size", 20000) * pnl_pct) / 100
            
            await mongo.db["virtual_portfolio"].update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "current_price": current_price,
                    "pnl_pct": pnl_pct,
                    "pnl_inr": pnl_inr
                }}
            )

async def get_positions() -> dict:
    if mongo.db is None:
        return {"open": [], "closed": [], "summary": {}}
        
    cursor = mongo.db["virtual_portfolio"].find({})
    docs = await cursor.to_list(length=None)
    
    # Update live prices for open positions
    open_symbols = set([d["symbol"] for d in docs if d["status"] == "open"])
    if open_symbols:
        try:
            # simple batch yf fetch or async loop
            # to prevent hanging, just do it one by one
            for sym in open_symbols:
                ticker = yf.Ticker(f"{sym}.NS")
                info = ticker.history(period="1d")
                if not info.empty:
                    current = info['Close'].iloc[-1]
                    await update_position_price(sym, current)
        except Exception:
            pass
            
    # fetch again after update
    cursor = mongo.db["virtual_portfolio"].find({})
    docs = await cursor.to_list(length=None)
    
    open_pos = []
    closed_pos = []
    total_invested = 0
    current_value = 0
    total_pnl_inr = 0
    
    for d in docs:
        d["_id"] = str(d["_id"])
        if d.get("logged_at"):
            d["logged_at"] = d["logged_at"].isoformat()
        if d.get("closed_at"):
            d["closed_at"] = d["closed_at"].isoformat()
            
        if d["status"] == "open":
            open_pos.append(d)
            total_invested += d.get("trade_size", 0)
            current_value += d.get("trade_size", 0) + d.get("pnl_inr", 0)
            total_pnl_inr += d.get("pnl_inr", 0)
        else:
            closed_pos.append(d)
            
    summary = {
        "total_invested": total_invested,
        "current_value": current_value,
        "total_pnl_inr": total_pnl_inr,
        "total_pnl_pct": (total_pnl_inr / total_invested * 100) if total_invested else 0.0
    }
    
    return {"open": open_pos, "closed": closed_pos, "summary": summary}

async def delete_position(position_id: str) -> bool:
    if mongo.db is None:
        return False
    try:
        result = await mongo.db["virtual_portfolio"].delete_one({"_id": ObjectId(position_id)})
        return result.deleted_count > 0
    except:
        return False

async def clear_all_positions() -> int:
    if mongo.db is None:
        return 0
    result = await mongo.db["virtual_portfolio"].delete_many({})
    return result.deleted_count
