from fastapi import APIRouter
from features.intraday.service import (
    run_intraday_scan, get_todays_scans, get_scan_history, delete_scan,
    clear_all_scans, add_custom_track, get_stock_scans, untrack_stock,
    update_custom_track, close_position, get_all_custom_tracks, get_ai_update
)
from pydantic import BaseModel

class CustomTrackRequest(BaseModel):
    symbol: str
    stock_name: str = ""
    entry_price: float
    target: float
    stop_loss: float
    direction: str = "BUY"
    hold_duration: str = "Intraday"
    quantity: int = 0
    t1: float = 0.0
    t2: float = 0.0
    t3: float = 0.0
    notes: str = ""

class UpdateTrackRequest(BaseModel):
    entry_price: float | None = None
    target: float | None = None
    stop_loss: float | None = None
    t1: float | None = None
    t2: float | None = None
    t3: float | None = None
    quantity: int | None = None
    notes: str | None = None
    hold_duration: str | None = None
    direction: str | None = None
    stock_name: str | None = None

class CloseRequest(BaseModel):
    exit_price: float

router = APIRouter(prefix="/intraday", tags=["Intraday"])

@router.get("/scans/today")
async def todays_scans():
    return await get_todays_scans()

@router.get("/scans/history")
async def scan_history(days: int = 7):
    return await get_scan_history(days)

@router.post("/scan/trigger")
async def trigger_scan():
    scans = await run_intraday_scan()
    return {"status": "success", "scans": scans, "count": len(scans)}

@router.post("/track")
async def track_custom(req: CustomTrackRequest):
    result = await add_custom_track(
        symbol=req.symbol,
        entry_price=req.entry_price,
        target=req.target,
        stop_loss=req.stop_loss,
        stock_name=req.stock_name,
        direction=req.direction,
        hold_duration=req.hold_duration,
        quantity=req.quantity,
        t1=req.t1,
        t2=req.t2,
        t3=req.t3,
        notes=req.notes,
    )
    return result

@router.get("/tracks/all")
async def all_tracks():
    return await get_all_custom_tracks()

@router.put("/track/{symbol}")
async def update_track(symbol: str, req: UpdateTrackRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    return await update_custom_track(symbol, updates)

@router.post("/track/{symbol}/close")
async def close_track(symbol: str, req: CloseRequest):
    return await close_position(symbol, req.exit_price)

@router.get("/track/{symbol}/ai")
async def ai_update(symbol: str):
    return await get_ai_update(symbol)

@router.get("/scans/stock/{symbol}")
async def stock_scan_history(symbol: str, date: str | None = None):
    return await get_stock_scans(symbol, date)

@router.delete("/track/{symbol}")
async def untrack(symbol: str):
    success = await untrack_stock(symbol)
    return {"status": "success" if success else "failed"}

@router.delete("/scans/all")
async def clear_all():
    count = await clear_all_scans()
    return {"status": "success", "deleted": count}

@router.delete("/scans/{scan_id}")
async def delete(scan_id: str):
    success = await delete_scan(scan_id)
    return {"status": "success" if success else "failed"}
