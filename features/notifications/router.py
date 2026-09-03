from fastapi import APIRouter
import aiohttp
from features.bot.setup import bot
from core.config import USER_ID, NTFY_TOPIC

router = APIRouter(tags=["Notifications"])

@router.post("/notify", summary="Broadcast Push Notification to Mobile", description="Sends a push notification to both Telegram (if configured) and ntfy.sh. Returns delivery status for each channel.")
async def broadcast_push_notification_to_mobile(text: str):
    results = {}

    if USER_ID:
        try:
            await bot.send_message(chat_id=USER_ID, text=f"🔔 <b>Notification:</b>\n{text}")
            results["telegram"] = {"status": "success"}
        except Exception as e:
            results["telegram"] = {"status": "error", "message": str(e)}
    else:
        results["telegram"] = {"status": "skipped", "message": "Userid not found in .env"}

    if NTFY_TOPIC:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://ntfy.sh/{NTFY_TOPIC}"
                async with session.post(url, data=text.encode("utf-8")) as resp:
                    if resp.status >= 400:
                        error_text = await resp.text()
                        results["ntfy"] = {"status": "error", "message": error_text}
                    else:
                        results["ntfy"] = {"status": "success"}
        except Exception as e:
            results["ntfy"] = {"status": "error", "message": str(e)}
    else:
        results["ntfy"] = {"status": "skipped", "message": "NTFY_TOPIC not found in .env"}

    return {"status": "processed", "results": results}
