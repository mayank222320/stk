from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from features.gemini.schemas import GeminiRequest, GeminiSwitchRequest, GeminiError
from features.gemini.service import generate_with_gemini_fallback, gemini_manager
from core.database import mongo

router = APIRouter(prefix="/gemini", tags=["Gemini"])

@router.get("/status", summary="Check Gemini API Status", description="Returns the current active Gemini API key, fallback models, and the status/errors of all loaded API keys.")
def get_gemini_api_status():
    return gemini_manager.status()

@router.post("/switch", summary="Switch Active Gemini API Key", description="Switches the active Gemini API key to the specified key ID or number.")
def switch_active_gemini_api_key(request: GeminiSwitchRequest):
    try:
        key = gemini_manager.switch(request.key)
        return {"status": "success", "active": key.name}
    except GeminiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/clear-errors", summary="Clear API Errors", description="Clears all recorded errors for Gemini API keys.")
def clear_gemini_errors():
    gemini_manager.last_errors.clear()
    return {"status": "success"}

@router.post("/generate", summary="Generate AI Chat Response", description="Processes a text prompt through the Gemini model with automatic fallback across API keys if rate limits are hit.")
async def generate_ai_chat_response(request: GeminiRequest):
    try:
        return await generate_with_gemini_fallback(request.prompt, request.model, request.use_search)
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

class GeminiModelRequest(BaseModel):
    model: str
    user_id: int = 0

@router.get("/model", summary="Get User Model", description="Get preferred model for user")
async def get_user_model_api(user_id: int = 0):
    if mongo.db is None:
        return {"model": "gemini-3.5-flash"}
    doc = await mongo.db.users.find_one({"user_id": user_id})
    return {"model": doc.get("preferred_model", "gemini-3.5-flash") if doc else "gemini-3.5-flash"}

@router.post("/model", summary="Set User Model", description="Set preferred model for user")
async def set_user_model_api(request: GeminiModelRequest):
    if mongo.db is None:
        raise HTTPException(status_code=500, detail="Database not connected")
    await mongo.db.users.update_one(
        {"user_id": request.user_id},
        {"$set": {"preferred_model": request.model}},
        upsert=True,
    )
    return {"status": "success", "model": request.model}
