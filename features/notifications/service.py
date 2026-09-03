# notifications feature: ntfy.sh push  +  EmailJS fallback

import aiohttp
from core.config import NTFY_TOPIC, EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, EMAILJS_USER_ID, EMAILJS_PRIVATE_KEY


async def send_ntfy_notification(text: str, title: str = "Stock Alert", priority: str = "high") -> bool:
    """
    Send a push notification via ntfy.sh.
    Returns True on success, False on failure.
    """
    if not NTFY_TOPIC:
        return False

    try:
        safe_text = text[:4000] if len(text) > 4000 else text
        headers = {
            "Title": title,
            "Priority": priority,   # min | low | default | high | max
            "Tags": "chart_increasing",
        }
        async with aiohttp.ClientSession() as session:
            url = f"https://ntfy.sh/{NTFY_TOPIC}"
            async with session.post(url, data=safe_text.encode("utf-8"), headers=headers) as resp:
                if resp.status < 400:
                    return True
                body = await resp.text()
                print(f"[ntfy] HTTP {resp.status}: {body}")
                return False
    except Exception as exc:
        print(f"[ntfy] Failed: {exc}")
        return False


async def send_emailjs_notification(subject: str, message: str) -> bool:
    """
    EmailJS fallback notification using the EmailJS REST API.
    Requires EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, EMAILJS_USER_ID in .env
    """
    if not all([EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, EMAILJS_USER_ID]):
        print("[EmailJS] Credentials not configured — skipping email fallback.")
        return False

    payload = {
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "user_id": EMAILJS_USER_ID,
        "template_params": {
            "subject": subject,
            "message": message,
        },
    }

    if EMAILJS_PRIVATE_KEY:
        payload["accessToken"] = EMAILJS_PRIVATE_KEY

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.emailjs.com/api/v1.0/email/send",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                print(f"[EmailJS] HTTP {resp.status}: {body}")
                return False
    except Exception as exc:
        print(f"[EmailJS] Failed: {exc}")
        return False


async def broadcast(
    text: str,
    title: str = "Stock Alert",
    ntfy_priority: str = "high",
) -> dict[str, str]:
    """
    Primary: ntfy.sh  —  Fallback: EmailJS
    Returns status dict.
    """
    results: dict[str, str] = {}

    ntfy_ok = await send_ntfy_notification(text, title=title, priority=ntfy_priority)
    results["ntfy"] = "success" if ntfy_ok else "failed"

    if not ntfy_ok:
        email_ok = await send_emailjs_notification(subject=title, message=text)
        results["emailjs"] = "success" if email_ok else "failed"
    else:
        results["emailjs"] = "skipped"

    return results
