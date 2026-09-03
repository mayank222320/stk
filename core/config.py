import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("telegram_token")
USER_ID = os.getenv("Userid")

# ── MongoDB ───────────────────────────────────────────────────
MONGO_CONNECTION_STRING = os.getenv("connection_string") or os.getenv("connetion_string")
MONGO_DATABASE_NAME = os.getenv("MONGO_DATABASE_NAME", "stock")

# ── Gemini ────────────────────────────────────────────────────
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
DEFAULT_GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]
AVAILABLE_MODELS = {
    "gemini-3.5-flash": "Gemini 3.5 Flash (Recommended - Fast & Smart)",
    "gemini-3.1-flash": "Gemini 3.1 Flash (Standard Option)",
    "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite (Super Fast & Light)",
    "gemini-2.5-flash": "Gemini 2.5 Flash (Stable Workhorse)",
}

# ── Notifications ─────────────────────────────────────────────
NTFY_TOPIC = os.getenv("NTFY_TOPIC")
# EmailJS fallback — fill values in .env
EMAILJS_SERVICE_ID = os.getenv("EMAILJS_SERVICE_ID")
EMAILJS_TEMPLATE_ID = os.getenv("EMAILJS_TEMPLATE_ID")
EMAILJS_USER_ID = os.getenv("EMAILJS_USER_ID")
EMAILJS_PRIVATE_KEY = os.getenv("EMAILJS_PRIVATE_KEY")

# ── Scheduler ─────────────────────────────────────────────────
# Watchlist is DYNAMIC — Gemini picks the best stocks each morning.
# MAX_WATCHLIST_STOCKS controls how many stocks Gemini selects per day.
MAX_WATCHLIST_STOCKS: int = int(os.getenv("MAX_WATCHLIST_STOCKS", "5"))
SCHEDULER_MORNING_TIME = os.getenv("SCHEDULER_MORNING_TIME", "09:20")   # HH:MM IST
SCHEDULER_EVENING_TIME = os.getenv("SCHEDULER_EVENING_TIME", "15:35")   # HH:MM IST
SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Asia/Kolkata")


# ── Data Retention / TTL ──────────────────────────────────────
CHAT_HISTORY_TTL_DAYS: int = int(os.getenv("CHAT_HISTORY_TTL_DAYS", "25"))
