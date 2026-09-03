# features/bot/handlers.py — full chat with memory, model persistence, TTL command

from aiogram import types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from features.bot.setup import dp
from core.config import USER_ID, AVAILABLE_MODELS
from features.gemini.schemas import GeminiError
from features.gemini.service import (
    generate_with_gemini_fallback,
    generate_with_gemini_vision,
    format_gemini_answer,
    format_gemini_status,
    gemini_manager,
)
from features.notifications.service import broadcast
from features.chat_memory.service import (
    save_turn,
    get_history,
    get_last_morning_alert,
    get_user_ttl,
    set_user_ttl,
    purge_old_turns,
    clear_all_turns,
)
from core.database import mongo
from features.knowledge_base.service import get_simple_rag_chunks, format_rag_context


# ─────────────────── helpers ───────────────────
async def get_user_model(user_id: int) -> str:
    if mongo.db is None:
        return "gemini-3.5-flash"
    doc = await mongo.db.users.find_one({"user_id": user_id})
    if doc and "preferred_model" in doc:
        return doc["preferred_model"]
    return "gemini-3.5-flash"


async def set_user_model(user_id: int, model: str) -> None:
    if mongo.db is None:
        return
    await mongo.db.users.update_one(
        {"user_id": user_id},
        {"$set": {"preferred_model": model}},
        upsert=True,
    )


def _build_context_prompt(history: list[dict], user_message: str, alert_ctx: str, rag_ctx: str = "") -> str:
    turns = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)
    alert_section = f"\n--- Recent Morning Alert ---\n{alert_ctx}\n---\n" if alert_ctx else ""
    rag_section = f"\n{rag_ctx}\n" if rag_ctx else ""
    return (
        f"{alert_section}"
        f"{rag_section}"
        f"{turns}\n"
        f"USER: {user_message}\nASSISTANT:"
    )


# ─────────────────── /start ───────────────────
@dp.message(CommandStart(), F.from_user.id == int(USER_ID))
async def command_start_handler(message: types.Message) -> None:
    await message.answer(
        f"Hello, <b>{message.from_user.full_name}</b>! 👋\n\n"
        "I am your round-the-clock stock trading desk assistant.\n\n"
        "<b>Commands:</b>\n"
        "/gemini — AI Model & API Settings\n"
        "/memory — view/set data retention period\n"
        "/alerts — show latest morning report\n"
        "Or just send any message to chat with the AI."
    )





# ─────────────────── /memory ───────────────────
@dp.message(Command("memory"), F.from_user.id == int(USER_ID))
async def memory_handler(message: types.Message) -> None:
    text = message.text or ""
    parts = text.split()

    if len(parts) > 1:
        try:
            days = int(parts[1])
            if days < 1 or days > 365:
                await message.answer("Please enter a value between 1 and 365 days.")
                return
            await set_user_ttl(message.from_user.id, days)
            await purge_old_turns(message.from_user.id, days)
            await message.answer(f"✅ Retention set to <b>{days} days</b>. Older chat history deleted.")
            return
        except ValueError:
            await message.answer("Invalid value. Usage: /memory <days>")
            return

    current = await get_user_ttl(message.from_user.id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="7 Days", callback_data="memory_7"),
                InlineKeyboardButton(text="14 Days", callback_data="memory_14"),
            ],
            [
                InlineKeyboardButton(text="30 Days", callback_data="memory_30"),
                InlineKeyboardButton(text="365 Days", callback_data="memory_365"),
            ],
            [InlineKeyboardButton(text="🗑️ Clear All Chat History Now", callback_data="memory_clear")]
        ]
    )
    await message.answer(
        f"🗂️ Current data retention: <b>{current} days</b>\n"
        "Select new retention period or type <code>/memory &lt;days&gt;</code> for custom:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("memory_"))
async def memory_callback(callback: types.CallbackQuery) -> None:
    action = callback.data.split("_")[1]
    
    if action == "clear":
        await clear_all_turns(callback.from_user.id)
        await callback.message.edit_text("✅ <b>All chat history has been permanently deleted.</b>\n(Note: Your trading knowledge base was NOT affected.)")
    else:
        days = int(action)
        await set_user_ttl(callback.from_user.id, days)
        await purge_old_turns(callback.from_user.id, days)
        await callback.message.edit_text(f"✅ Data retention updated to <b>{days} days</b>.\nChat history older than {days} days has been permanently deleted.")
        
    await callback.answer()


# ─────────────────── /alerts ───────────────────
@dp.message(Command("alerts"), F.from_user.id == int(USER_ID))
async def alerts_handler(message: types.Message) -> None:
    text = message.text or ""
    parts = text.split()
    symbol = parts[1].upper() if len(parts) > 1 else None

    alert = await get_last_morning_alert(symbol)
    if not alert:
        label = f"for {symbol}" if symbol else ""
        await message.answer(f"No recent morning alert found {label}.")
        return

    header = f"📊 <b>Latest Alert{' — ' + symbol if symbol else ''}:</b>\n\n"
    await message.answer(header + alert[:3800])


# ─────────────────── /gemini ───────────────────
@dp.message(Command("gemini"), F.from_user.id == int(USER_ID))
async def gemini_handler(message: types.Message) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Check API Status", callback_data="gemini_status")],
            [InlineKeyboardButton(text="🔄 Test API Fallback", callback_data="gemini_test")],
            [InlineKeyboardButton(text="🔑 Switch API Key", callback_data="gemini_keys_menu")],
            [InlineKeyboardButton(text="🧠 Change AI Model", callback_data="gemini_models_menu")]
        ]
    )
    await message.answer("⚙️ <b>AI & API Dashboard</b>\nSelect an option below:", reply_markup=keyboard)


@dp.callback_query(F.data.startswith("gemini_"))
async def gemini_callback(callback: types.CallbackQuery) -> None:
    action = callback.data.split("gemini_")[1]
    
    if action == "status":
        await callback.message.edit_text(format_gemini_status())
        
    elif action == "test":
        await callback.message.edit_text("⏳ Testing Gemini API fallback routing...")
        try:
            result = await generate_with_gemini_fallback("Say 'Gemini API is working perfectly' in one short sentence.")
            await callback.message.edit_text(
                f"✅ <b>Success</b> (Used {result['key']} / {result['model']}):\n\n{format_gemini_answer(result)}"
            )
        except GeminiError as exc:
            await callback.message.edit_text(f"❌ <b>Error:</b>\n{exc}")
            
    elif action == "keys_menu":
        buttons = []
        for i, key_obj in enumerate(gemini_manager.keys):
            active_mark = "✅ " if i == gemini_manager.active_index else " "
            buttons.append([InlineKeyboardButton(text=f"{active_mark}{key_obj.name}", callback_data=f"gemini_use_{i}")])
        buttons.append([InlineKeyboardButton(text="🔙 Back", callback_data="gemini_back")])
        
        await callback.message.edit_text("Select an API key to switch to:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        
    elif action.startswith("use_"):
        idx = int(action.split("_")[1])
        try:
            key = gemini_manager.switch(str(idx + 1))
            await callback.message.edit_text(f"✅ Active Gemini API Key manually switched to: <b>{key.name}</b>")
        except GeminiError as exc:
            await callback.message.edit_text(f"❌ {exc}")
            
    elif action == "models_menu":
        user_model = await get_user_model(callback.from_user.id)
        buttons = []
        for model_id, desc in AVAILABLE_MODELS.items():
            active_mark = "✅ " if model_id == user_model else " "
            buttons.append([InlineKeyboardButton(text=f"{active_mark}{desc}", callback_data=f"gemini_setmodel_{model_id}")])
        buttons.append([InlineKeyboardButton(text="🔙 Back", callback_data="gemini_back")])
        await callback.message.edit_text("🧠 <b>Select AI Model:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    elif action.startswith("setmodel_"):
        model_id = action.split("setmodel_")[1]
        if model_id in AVAILABLE_MODELS:
            await set_user_model(callback.from_user.id, model_id)
            await callback.message.edit_text(f"✅ AI Model changed to: <b>{AVAILABLE_MODELS[model_id]}</b>\n\n(Applies to chat only. Scheduler uses default.)")

    elif action == "back":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Check API Status", callback_data="gemini_status")],
                [InlineKeyboardButton(text="🔄 Test API Fallback", callback_data="gemini_test")],
                [InlineKeyboardButton(text="🔑 Switch API Key", callback_data="gemini_keys_menu")],
                [InlineKeyboardButton(text="🧠 Change AI Model", callback_data="gemini_models_menu")]
            ]
        )
        await callback.message.edit_text("⚙️ <b>AI & API Dashboard</b>\nSelect an option below:", reply_markup=keyboard)
        
    await callback.answer()


# ─────────────────── photo handler (vision) ───────────────────
DEFAULT_VISION_PROMPT = (
    "You are StockAI (QMAF-Advisor), an elite Indian markets analyst.\n"
    "Analyze this image using your full expertise: identify the chart pattern, "
    "key support/resistance levels, technical indicators visible (RSI, MACD, volume, candle patterns), "
    "Wyckoff phase if applicable, and provide a clear actionable recommendation with entry, target, and stop-loss."
)

@dp.message(F.photo, F.from_user.id == int(USER_ID))
async def photo_handler(message: types.Message) -> None:
    uid = message.from_user.id
    caption = message.caption or ""
    prompt = caption.strip() if caption.strip() else DEFAULT_VISION_PROMPT

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Download highest resolution photo from Telegram
    photo = message.photo[-1]  # last = largest
    file = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    image_bytes = file_bytes.read() if hasattr(file_bytes, "read") else bytes(file_bytes)

    user_model = await get_user_model(uid)

    try:
        result = await generate_with_gemini_vision(
            image_bytes=image_bytes,
            mime_type="image/jpeg",
            prompt=prompt,
            model=user_model,
        )
        answer = format_gemini_answer(result)
    except Exception as exc:
        answer = f"⚠️ Vision analysis failed: {exc}"

    # Persist both sides to memory
    await save_turn(uid, "user", f"[IMAGE] {caption}" if caption else "[IMAGE sent]")
    await save_turn(uid, "assistant", answer)

    await message.answer(answer)


# ─────────────────── document handler (PDF / file) ──────────────
SUPPORTED_DOC_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}

@dp.message(F.document, F.from_user.id == int(USER_ID))
async def document_handler(message: types.Message) -> None:
    uid = message.from_user.id
    doc = message.document
    mime = doc.mime_type or ""
    caption = message.caption or ""

    if mime not in SUPPORTED_DOC_MIME:
        await message.answer(
            f"📎 File type <b>{mime or 'unknown'}</b> is not supported for AI analysis.\n"
            "Supported: PDF, JPEG, PNG, WEBP, GIF."
        )
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    file = await message.bot.get_file(doc.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    raw_bytes = file_bytes.read() if hasattr(file_bytes, "read") else bytes(file_bytes)

    prompt = caption.strip() if caption.strip() else (
        "Analyze this document and extract all relevant financial data, "
        "charts, or trading information. Apply QMAF rules where applicable."
    )

    user_model = await get_user_model(uid)

    try:
        result = await generate_with_gemini_vision(
            image_bytes=raw_bytes,
            mime_type=mime,
            prompt=prompt,
            model=user_model,
        )
        answer = format_gemini_answer(result)
    except Exception as exc:
        answer = f"⚠️ Document analysis failed: {exc}"

    await save_turn(uid, "user", f"[FILE: {doc.file_name}] {caption}")
    await save_turn(uid, "assistant", answer)

    await message.answer(answer)


# ─────────────────── main chat handler (text only) ──────────────
@dp.message(F.from_user.id == int(USER_ID))
async def ai_chat_handler(message: types.Message) -> None:
    if not message.text:
        await message.answer(
            "📸 Send a <b>photo</b> or <b>PDF</b> and I'll analyse it.\n"
            "Or type your question and I'll answer it."
        )
        return

    uid = message.from_user.id
    user_text = message.text

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Save user turn to memory
    await save_turn(uid, "user", user_text)

    # Fetch recent history + latest morning alert for context
    history = await get_history(uid, last_n=10)
    alert_ctx = await get_last_morning_alert()

    # Search knowledge base for relevant rules
    rag_chunks = await get_simple_rag_chunks([user_text], top_k=3)
    rag_ctx = format_rag_context(rag_chunks)

    # Build context-aware prompt
    full_prompt = _build_context_prompt(history[:-1], user_text, alert_ctx, rag_ctx)

    # Use user's saved model preference
    user_model = await get_user_model(uid)

    try:
        result = await generate_with_gemini_fallback(full_prompt, model=user_model)
        answer = format_gemini_answer(result)
    except Exception as exc:
        answer = f"⚠️ Gemini error: {exc}"

    await message.answer(answer)

    # Save assistant turn to memory
    await save_turn(uid, "assistant", answer)
