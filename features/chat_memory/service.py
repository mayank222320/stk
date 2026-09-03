# chat_memory feature: stores and retrieves per-user conversational turns
# and surfaces the most recent morning alert as context.

from datetime import datetime, timezone, timedelta
from typing import Any
from core.database import mongo
from core.config import CHAT_HISTORY_TTL_DAYS

CHAT_COLLECTION = "chat_history"
ALERTS_COLLECTION = "morning_alerts"

# In-memory fallback if MongoDB is disconnected
IN_MEMORY_HISTORY: list[dict[str, Any]] = []


async def save_turn(user_id: int, role: str, content: str, session_id: str = "default") -> None:
    """
    Persist a single conversation turn (user or assistant) tied to a session.
    The TTL index on 'expires_at' automatically purges old records.
    """
    if mongo.db is None:
        IN_MEMORY_HISTORY.append({
            "user_id": user_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc),
        })
        return

    ttl_days = await get_user_ttl(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    await mongo.db[CHAT_COLLECTION].insert_one(
        {
            "user_id": user_id,
            "session_id": session_id,
            "role": role,        # "user" | "assistant"
            "content": content,
            "created_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
        }
    )


async def get_history(user_id: int, last_n: int = 10, session_id: str = "default") -> list[dict[str, str]]:
    """
    Retrieve the last N turns for a user in a specific session.
    Returns list of {"role": ..., "content": ...} dicts.
    """
    if mongo.db is None:
        user_hist = [d for d in IN_MEMORY_HISTORY if d["user_id"] == user_id and d.get("session_id", "default") == session_id]
        user_hist.sort(key=lambda x: x["created_at"])
        docs = user_hist[-last_n:] if last_n else user_hist
        return [{"role": d["role"], "content": d["content"]} for d in docs]

    cursor = (
        mongo.db[CHAT_COLLECTION]
        .find({"user_id": user_id, "session_id": session_id}, {"_id": 0, "role": 1, "content": 1, "created_at": 1})
        .sort("created_at", -1)
        .limit(last_n)
    )
    docs = [doc async for doc in cursor]
    docs.reverse()  # oldest first
    return [{"role": d["role"], "content": d["content"]} for d in docs]


async def get_sessions(user_id: int) -> list[dict[str, Any]]:
    """
    Retrieve a list of unique sessions for a user, sorted by most recent activity.
    Returns: [{"session_id": "...", "title": "...", "updated_at": "..."}]
    """
    if mongo.db is None:
        return []

    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$sort": {"created_at": 1}},
        {"$group": {
            "_id": "$session_id",
            "first_message": {"$first": "$content"},
            "updated_at": {"$last": "$created_at"}
        }},
        {"$sort": {"updated_at": -1}},
        {"$limit": 50}
    ]
    
    sessions = []
    async for doc in mongo.db[CHAT_COLLECTION].aggregate(pipeline):
        # Create a short title from the first message
        content = doc.get("first_message", "")
        title = content[:40] + "..." if len(content) > 40 else content
        # Clean up Markdown/file tags for display
        title = title.replace("📎 ", "").replace("🔍 ", "").strip()
        if not title:
            title = "New Chat"
        # Give the default session a friendly name
        if doc["_id"] == "default":
            title = "Default Chat"
            
        sessions.append({
            "session_id": doc["_id"],
            "title": title,
            "updated_at": doc["updated_at"].isoformat()
        })
    
    # Always guarantee the "default" session appears at the top
    has_default = any(s["session_id"] == "default" for s in sessions)
    if not has_default:
        from datetime import datetime, timezone
        sessions.insert(0, {
            "session_id": "default",
            "title": "Default Chat",
            "updated_at": datetime.now(timezone.utc).isoformat()
        })
    else:
        # Move default to top
        default_entry = next(s for s in sessions if s["session_id"] == "default")
        sessions = [default_entry] + [s for s in sessions if s["session_id"] != "default"]
        
    return sessions


async def get_last_morning_alert(symbol: str | None = None) -> str:
    """
    Pull the most recent morning alert text from the DB for injection into chat context.
    Optionally filter by symbol.
    """
    if mongo.db is None:
        return ""

    query: dict[str, Any] = {}
    if symbol:
        query["symbol"] = symbol.upper()

    doc = await mongo.db[ALERTS_COLLECTION].find_one(
        query, sort=[("logged_at", -1)]
    )
    if not doc:
        return ""
    return doc.get("report_text", "")


async def save_morning_alert(symbol: str, report_text: str) -> None:
    """Save a morning alert report to the DB for later context retrieval."""
    if mongo.db is None:
        return
    await mongo.db[ALERTS_COLLECTION].insert_one(
        {
            "symbol": symbol.upper(),
            "report_text": report_text,
            "logged_at": datetime.now(timezone.utc),
        }
    )


# ─────────────────────── TTL management ───────────────────────
async def get_user_ttl(user_id: int) -> int:
    """Return user's custom TTL in days, or global default."""
    if mongo.db is None:
        return CHAT_HISTORY_TTL_DAYS
    doc = await mongo.db.users.find_one({"user_id": user_id}, {"ttl_days": 1})
    if doc and "ttl_days" in doc:
        return int(doc["ttl_days"])
    return CHAT_HISTORY_TTL_DAYS


async def set_user_ttl(user_id: int, days: int) -> None:
    """Let a user customize their own data retention window."""
    if mongo.db is None:
        return
    await mongo.db.users.update_one(
        {"user_id": user_id},
        {"$set": {"ttl_days": days}},
        upsert=True,
    )


async def ensure_ttl_index() -> None:
    """
    Create the TTL index on chat_history.expires_at if it doesn't already exist.
    MongoDB Atlas will auto-delete documents once expires_at is in the past.
    Call this once at app startup.
    """
    if mongo.db is None:
        return
    try:
        await mongo.db[CHAT_COLLECTION].create_index(
            "expires_at",
            expireAfterSeconds=0,
            background=True,
        )
        print("[TTL] chat_history TTL index ensured.")
    except Exception as exc:
        print(f"[TTL] Could not create TTL index: {exc}")


async def purge_old_turns(user_id: int, days: int) -> None:
    """Immediately delete chat turns older than N days and update remaining documents."""
    if mongo.db is None:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    # 1. Delete older than cutoff
    await mongo.db[CHAT_COLLECTION].delete_many({
        "user_id": user_id, 
        "created_at": {"$lt": cutoff}
    })
    
    # 2. Update remaining documents' expires_at to match the new TTL policy
    new_expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    await mongo.db[CHAT_COLLECTION].update_many(
        {"user_id": user_id},
        {"$set": {"expires_at": new_expires_at}}
    )

async def clear_all_turns(user_id: int, session_id: str | None = None) -> None:
    """Instantly wipe all chat history for a user (or specific session)."""
    if mongo.db is None:
        global IN_MEMORY_HISTORY
        if session_id:
            IN_MEMORY_HISTORY = [d for d in IN_MEMORY_HISTORY if not (d["user_id"] == user_id and d.get("session_id") == session_id)]
        else:
            IN_MEMORY_HISTORY = [d for d in IN_MEMORY_HISTORY if d["user_id"] != user_id]
        return
        
    query = {"user_id": user_id}
    if session_id:
        query["session_id"] = session_id
        
    await mongo.db[CHAT_COLLECTION].delete_many(query)
