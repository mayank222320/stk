# performance_tracking feature: logs morning recommendations and
# evaluates evening pass/fail against actual market close data.

from datetime import datetime, timezone
from typing import Any
from core.database import mongo


COLLECTION = "performance_log"


async def log_recommendation(
    symbol: str,
    recommendation: str,
    entry_zone: str,
    target: str,
    stop_loss: str,
    trade_type: str,
    timeframe: str,
    reason: str,
    raw_ai_output: str,
    market_data_snapshot: dict[str, Any],
) -> str | None:
    """
    Persist the morning AI recommendation to MongoDB.
    Returns the inserted document id string.
    """
    if mongo.db is None:
        print("[Performance] MongoDB not connected, skipping log.")
        return None

    doc = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "logged_at": datetime.now(timezone.utc),
        "symbol": symbol.upper(),
        "recommendation": recommendation,
        "entry_zone": entry_zone,
        "target": target,
        "stop_loss": stop_loss,
        "trade_type": trade_type,
        "timeframe": timeframe,
        "reason": reason,
        "raw_ai_output": raw_ai_output,
        "market_data_snapshot": market_data_snapshot,
        "result": None,  # filled in by evening scheduler
        "calibration_score": None,
        "evaluated_at": None,
    }

    result = await mongo.db[COLLECTION].insert_one(doc)
    return str(result.inserted_id)


async def evaluate_day(symbol: str, day_high: float, day_low: float, day_close: float) -> dict[str, Any]:
    """
    Evening routine: find today's pending recommendation for a symbol,
    judge pass/fail, and update the record in-place.

    Pass criteria:
      - BUY/ACCUMULATE: close > entry AND (hit target OR never broke stop-loss)
      - SELL/AVOID:     close < entry AND (hit target OR never broke stop-loss)
    """
    if mongo.db is None:
        return {"error": "MongoDB not connected"}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc = await mongo.db[COLLECTION].find_one(
        {"date": today, "symbol": symbol.upper(), "result": None}
    )
    if not doc:
        return {"error": f"No morning recommendation logged for {symbol} on {today} (Skipped)"}

    try:
        entry_low, entry_high = _parse_range(doc.get("entry_zone", ""))
        target_val = _parse_single(doc.get("target", ""))
        sl_val = _parse_single(doc.get("stop_loss", ""))
        rec = doc.get("recommendation", "UNKNOWN").upper()
    except Exception as exc:
        # Mark as SKIPPED so it doesn't get stuck in pending state forever
        await mongo.db[COLLECTION].update_one(
            {"_id": doc["_id"]},
            {"$set": {"result": "SKIPPED", "evaluation_notes": "Cannot parse target/SL (AI formatting error)", "evaluated_at": datetime.now(timezone.utc)}}
        )
        return {"error": f"Could not parse recommendation fields for {symbol}: AI output format mismatch"}

    passed = False
    notes = ""

    if rec in {"BUY", "ACCUMULATE"}:
        sl_hit = day_low <= sl_val
        target_hit = day_high >= target_val
        passed = target_hit and not sl_hit
        notes = (
            f"Target {'✅ HIT' if target_hit else '❌ Missed'}. "
            f"Stop-loss {'⛔ Breached' if sl_hit else '✅ Held'}. "
            f"Close: ₹{day_close:,.2f}"
        )
    elif rec in {"SELL", "AVOID"}:
        sl_hit = day_high >= sl_val
        target_hit = day_low <= target_val
        passed = target_hit and not sl_hit
        notes = (
            f"Target {'✅ HIT' if target_hit else '❌ Missed'}. "
            f"Stop-loss {'⛔ Breached' if sl_hit else '✅ Held'}. "
            f"Close: ₹{day_close:,.2f}"
        )
    else:
        notes = "Non-directional recommendation, no evaluation applied."
        passed = False # Default

    calibration_score = 1 if passed else 0

    await mongo.db[COLLECTION].update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "result": "PASS" if passed and rec in {"BUY", "ACCUMULATE", "SELL", "AVOID"} else ("FAIL" if rec in {"BUY", "ACCUMULATE", "SELL", "AVOID"} else "SKIPPED"),
                "calibration_score": calibration_score,
                "evaluated_at": datetime.now(timezone.utc),
                "evaluation_notes": notes,
                "day_high": day_high,
                "day_low": day_low,
                "day_close": day_close,
            }
        },
    )

    return {
        "symbol": symbol,
        "date": today,
        "result": "PASS" if passed else "FAIL",
        "calibration_score": calibration_score,
        "notes": notes,
    }


async def get_hit_rate(last_n_days: int = 30) -> dict[str, Any]:
    """Return aggregate hit-rate statistics for the last N days."""
    if mongo.db is None:
        return {"error": "MongoDB not connected"}

    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=last_n_days)

    total = await mongo.db[COLLECTION].count_documents(
        {"evaluated_at": {"$gte": cutoff}, "result": {"$ne": None}}
    )
    passed = await mongo.db[COLLECTION].count_documents(
        {"evaluated_at": {"$gte": cutoff}, "result": "PASS"}
    )

    hit_rate = round(passed / total * 100, 2) if total else 0

    return {
        "last_n_days": last_n_days,
        "total_evaluated": total,
        "passed": passed,
        "failed": total - passed,
        "hit_rate_pct": hit_rate,
    }


# ─────────────────────── helpers ───────────────────────
def _parse_range(text: str) -> tuple[float, float]:
    """Parse '940-960' or '₹940–₹960' → (940.0, 960.0)."""
    import re
    nums = re.findall(r"[\d.]+", str(text).replace(",", ""))
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    if len(nums) == 1:
        v = float(nums[0])
        return v, v
    raise ValueError(f"Cannot parse range: {text}")


def _parse_single(text: str) -> float:
    import re
    nums = re.findall(r"[\d.]+", str(text).replace(",", ""))
    if nums:
        return float(nums[0])
    raise ValueError(f"Cannot parse value: {text}")
