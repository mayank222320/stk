import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.database import mongo
from features.bot.setup import bot, dp
from aiogram.types import BotCommand
import features.bot.handlers  # registers all handlers

from features.gemini.router import router as gemini_router
from features.notifications.router import router as notifications_router
from features.system.router import router as system_router
from features.scheduler.service import start_scheduler, stop_scheduler
from features.chat_memory.service import ensure_ttl_index
from features.performance.router import router as performance_router
from features.chat.router import router as chat_router
from features.market_data.router import router as market_router
from features.grok.router import router as grok_router
from features.portfolio.router import router as portfolio_router
from features.intraday.router import router as intraday_router
from features.news_scanner.router import router as news_scanner_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    await mongo.connect()
    await ensure_ttl_index()          # MongoDB TTL index for chat history
    # Ensure fast deduplication lookups for the news scanner
    if mongo.db is not None:
        await mongo.db.processed_news.create_index(
            [("url", 1), ("processed_at", -1)], background=True
        )
        await mongo.db.news_alerts.create_index([("alerted_at", -1)], background=True)
    start_scheduler()                 # Morning & evening cron jobs
    
    # Set up Telegram Bot Menu Commands
    commands = [
        BotCommand(command="start", description="Welcome & overview"),
        BotCommand(command="alerts", description="View latest morning stock report"),
        BotCommand(command="memory", description="Manage chat memory retention"),
        BotCommand(command="gemini", description="AI Model & API Dashboard"),
    ]
    await bot.set_my_commands(commands)
    
    # Drop any pending updates to prevent Conflict errors between Render and Local
    await bot.delete_webhook(drop_pending_updates=True)
    
    # FIX: Delay polling slightly to give Uvicorn breathing room to bind to $PORT
    # and satisfy Render's startup port scanners.
    async def start_bot_delayed():
        await asyncio.sleep(5) 
        print("Telegram bot starting polling safely...")
        try:
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Bot polling encountered an error: {e}")

    polling_task = asyncio.create_task(start_bot_delayed())
    
    yield
    # ── Shutdown ─────────────────────────────────────────────
    print("Shutting down...")
    stop_scheduler()
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    await bot.session.close()
    await mongo.close()


app = FastAPI(
    title="Stock Server",
    description="Intelligent trading desk assistant — Gemini AI + Telegram + MongoDB",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://stock-ai-henna.vercel.app",
        "https://stock-ai-henna.vercel.app/",
        "https://stock-axjlbvrsc-kalparatnas-projects.vercel.app",
        "https://stock-axjlbvrsc-kalparatnas-projects.vercel.app/"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router)
app.include_router(gemini_router)
app.include_router(notifications_router)
app.include_router(performance_router)
app.include_router(chat_router)
app.include_router(market_router)
app.include_router(grok_router)
app.include_router(portfolio_router)
app.include_router(intraday_router)
app.include_router(news_scanner_router)