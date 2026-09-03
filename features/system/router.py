from fastapi import APIRouter
from core.database import mongo
from pydantic import BaseModel

router = APIRouter(tags=["System"])

@router.get("/", summary="System Health Check", description="Root endpoint to verify the backend server is running correctly.")
def get_system_health_check():
    return {"message": "Welcome to Stock Server Backend. FastAPI is integrated with Telegram Bot."}

@router.get("/mongo/status", summary="MongoDB Connection Status", description="Verifies the connection state of the MongoDB database.")
async def get_database_connection_status():
    return await mongo.status()

class PromptVersionRequest(BaseModel):
    version: str

@router.get("/system/prompt-version", summary="Get Prompt Version", description="Gets the active AI prompt version")
async def get_prompt_version():
    if mongo.db is None:
        return {"version": "v2_institutional"}
    settings = await mongo.db.settings.find_one({"_id": "global_settings"}) or {}
    return {"version": settings.get("qmaf_prompt_version", "v2_institutional")}

@router.post("/system/prompt-version", summary="Set Prompt Version", description="Sets the active AI prompt version")
async def set_prompt_version(req: PromptVersionRequest):
    if mongo.db is None:
        return {"status": "error", "message": "DB not connected"}
    
    if req.version not in ["v1_compact", "v2_institutional"]:
        return {"status": "error", "message": "Invalid version"}
        
    await mongo.db.settings.update_one(
        {"_id": "global_settings"},
        {"$set": {"qmaf_prompt_version": req.version}},
        upsert=True
    )
    return {"status": "success", "version": req.version}
