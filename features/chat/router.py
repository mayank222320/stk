from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import json
from typing import Optional
from pydantic import BaseModel
from features.gemini.service import (
    generate_with_gemini_fallback,
    generate_with_gemini_vision,
    format_gemini_answer,
    GeminiError,
    stream_with_gemini_fallback,
    gemini_manager,
)
from features.chat_memory.service import save_turn, get_history, get_last_morning_alert
from features.knowledge_base.service import get_simple_rag_chunks, format_rag_context
from features.chat.article_fetcher import fetch_article_text
from core.database import mongo
from core.config import DEFAULT_GEMINI_MODEL
from pathlib import Path

router = APIRouter(prefix="/chat", tags=["Web Chat"])

class ChatRequest(BaseModel):
    message: str
    display_message: Optional[str] = None   # Short label shown in chat UI
    news_url: Optional[str] = None          # If set, full article will be fetched
    user_id: int = 0  # Default user for web UI
    session_id: str = "default"

def _build_context_prompt(history: list[dict], user_message: str, alert_ctx: str, rag_ctx: str = "") -> str:
    turns = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)
    alert_section = f"\n--- Recent Morning Alert ---\n{alert_ctx}\n---\n" if alert_ctx else ""
    rag_section = f"\n{rag_ctx}\n" if rag_ctx else ""
    try:
        master_prompt = Path("prompt.txt").read_text(encoding="utf-8")
        framework_section = f"\n--- SYSTEM PROMPT & STOCK ANALYSIS FRAMEWORK ---\n{master_prompt}\n---\n"
    except Exception:
        framework_section = ""
    return (
        f"{framework_section}"
        f"{alert_section}"
        f"{rag_section}"
        f"{turns}\n"
        f"USER: {user_message}\nASSISTANT:"
    )

@router.post("/", summary="Send Message to AI", description="Sends a message to the AI trading assistant with RAG context and memory.")
async def send_chat_message(request: ChatRequest):
    uid = request.user_id
    user_text = request.message
    session_id = request.session_id
    # What gets shown in the UI bubble (short). If not provided, fall back to first 200 chars.
    display_text = request.display_message or user_text[:200]
    
    await save_turn(uid, "user", display_text, session_id)
    
    history = await get_history(uid, last_n=10, session_id=session_id)
    alert_ctx = await get_last_morning_alert()
    
    rag_chunks = await get_simple_rag_chunks([user_text], top_k=3)
    rag_ctx = format_rag_context(rag_chunks)
    
    full_prompt = _build_context_prompt(history[:-1], user_text, alert_ctx, rag_ctx)
    
    try:
        result = await generate_with_gemini_fallback(full_prompt, model=DEFAULT_GEMINI_MODEL)
        answer = format_gemini_answer(result)
    except GeminiError as exc:
        answer = f"Gemini error: {exc}"
        
    await save_turn(uid, "assistant", answer, session_id)
    
    return {"reply": answer}

@router.post("/stream", summary="Stream Message from AI", description="Streams a response token-by-token using Server-Sent Events (SSE).")
async def stream_chat_message(request: ChatRequest):
    uid = request.user_id
    user_text = request.message
    session_id = request.session_id
    # What gets shown in the UI bubble (short). If not provided, fall back to first 200 chars.
    display_text = request.display_message or user_text[:200]
    
    await save_turn(uid, "user", display_text, session_id)
    
    history = await get_history(uid, last_n=10, session_id=session_id)
    alert_ctx = await get_last_morning_alert()
    
    rag_chunks = await get_simple_rag_chunks([user_text], top_k=3)
    rag_ctx = format_rag_context(rag_chunks)
    
    # ── Full article fetch ─────────────────────────────────────────────────
    full_article_section = ""
    if request.news_url:
        article_text = await fetch_article_text(request.news_url)
        if article_text:
            full_article_section = (
                f"\n--- FULL ARTICLE TEXT (fetched from source) ---\n"
                f"{article_text}\n"
                f"--- END ARTICLE ---\n"
            )
    # ──────────────────────────────────────────────────────────────────────
    
    # Inject full article into user_text context if available
    enriched_message = user_text
    if full_article_section:
        enriched_message = f"{user_text}{full_article_section}"
    
    full_prompt = _build_context_prompt(history[:-1], enriched_message, alert_ctx, rag_ctx)
    
    async def event_generator():
        full_text = ""
        try:
            async for chunk in stream_with_gemini_fallback(full_prompt, model=DEFAULT_GEMINI_MODEL):
                full_text += chunk
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        except Exception as e:
            err_msg = f"\n[Streaming Error: {e}]"
            yield f"data: {json.dumps({'chunk': err_msg})}\n\n"
            full_text += err_msg
        finally:
            await save_turn(uid, "assistant", full_text, session_id)
            
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@router.get("/sessions", summary="Get Chat Sessions", description="Retrieves a list of recent chat sessions for the user.")
async def get_chat_sessions(user_id: int = 0):
    from features.chat_memory.service import get_sessions
    return await get_sessions(user_id)

@router.get("/history", summary="Get Chat History", description="Retrieves the conversational history for the web UI.")
async def get_chat_history(user_id: int = 0, limit: int = 50, session_id: str = "default"):
    return await get_history(user_id, last_n=limit, session_id=session_id)

@router.post("/clear", summary="Clear Chat History", description="Instantly wipes all chat history for a user.")
async def clear_chat_history(user_id: int = 0, session_id: Optional[str] = None):
    from features.chat_memory.service import clear_all_turns
    await clear_all_turns(user_id, session_id)
    return {"status": "success"}

# Supported MIME types for vision analysis
_VISION_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "application/pdf"
}

@router.post(
    "/analyze-file",
    summary="Analyze Image or PDF with AI Vision",
    description=(
        "Accepts a multipart file (JPEG, PNG, WEBP, GIF, PDF) plus an optional text prompt. "
        "Passes the file to Gemini Vision and returns the AI analysis. "
        "The exchange is saved to chat memory."
    ),
)
async def analyze_file(
    file: UploadFile = File(...),
    message: str = Form(default=""),
    user_id: int = Form(default=0),
    session_id: str = Form(default="default"),
):
    mime = file.content_type or ""
    if mime not in _VISION_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{mime}'. Allowed: JPEG, PNG, WEBP, GIF, PDF."
        )

    raw_bytes = await file.read()

    # If no caption provided, use a smart default based on file type
    if message.strip():
        prompt = message.strip()
    elif mime == "application/pdf":
        prompt = (
            "Analyze this document. Extract all relevant financial data, "
            "stock information, company metrics, or trading signals. "
            "Apply QMAF rules where applicable and give actionable insights."
        )
    else:
        prompt = (
            "You are StockAI (QMAF-Advisor), an elite Indian markets analyst. "
            "Analyze this chart image using your full expertise: identify the pattern, "
            "key support/resistance levels, technical indicators (RSI, MACD, volume, candle patterns), "
            "Wyckoff phase if applicable, and give a clear recommendation with entry, target, and stop-loss."
        )

    # Retrieve active model preference for this user
    user_model: Optional[str] = None
    if mongo.db is not None:
        doc = await mongo.db.users.find_one({"user_id": user_id})
        if doc:
            user_model = doc.get("preferred_model")

    try:
        result = await generate_with_gemini_vision(
            image_bytes=raw_bytes,
            mime_type=mime,
            prompt=prompt,
            model=user_model,
        )
        answer = format_gemini_answer(result)
    except GeminiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Persist to memory so the follow-up text chat has context
    display_label = f"[FILE: {file.filename}]" + (f" {message}" if message.strip() else "")
    await save_turn(user_id, "user", display_label, session_id)
    await save_turn(user_id, "assistant", answer, session_id)

    return {"reply": answer, "model": result.get("model"), "file": file.filename}
