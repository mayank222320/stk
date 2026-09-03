from fastapi import APIRouter
from features.news_scanner.service import run_news_scanner, get_news_alerts

router = APIRouter(prefix="/news-scanner", tags=["News Scanner"])


@router.post("/trigger")
async def trigger_scan():
    """Manually trigger the news scanner (for testing)."""
    await run_news_scanner()
    return {"status": "scan complete"}


@router.get("/alerts")
async def list_alerts(limit: int = 20):
    """Return recent breaking news alerts for the dashboard."""
    return await get_news_alerts(limit=limit)
