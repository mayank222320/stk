from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from features.grok.service import grok_manager, get_sentiment, GrokError

router = APIRouter(prefix="/grok", tags=["Grok"])

class SwitchRequest(BaseModel):
    key: str | int

class SentimentRequest(BaseModel):
    symbol: str

@router.get("/status")
def status():
    return grok_manager.status()

@router.post("/switch")
def switch_key(req: SwitchRequest):
    try:
        grok_manager.switch(req.key)
        return {"status": "success", "active": grok_manager.active_key.name}
    except GrokError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sentiment")
async def sentiment(req: SentimentRequest):
    return await get_sentiment(req.symbol)
