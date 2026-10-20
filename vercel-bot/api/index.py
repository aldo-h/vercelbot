import html
import logging
import os

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from fastapi import FastAPI, Header, HTTPException, Request

# ===== Настройки из переменных окружения Vercel =====
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPERADMIN_ID = int(os.environ["SUPERADMIN_ID"])
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Хранилище языка (Upstash Redis). Подходят оба варианта названий переменных.
REDIS_URL = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")

# ===== Тексты =====
CHOOSE_LANG = "Тілді таңдаңыз / Выберите язык:"

TEXTS = {
    "kk": {
        "greeting": "👋 Сәлем!\n\nХабарламаңызды жазыңыз, ол әкімшілікке жіберіледі.",
        "sent": "✅ Хабарламаңыз әкімшілікке жіберілді.",
    },
    "ru": {
        "greeting": "👋 Привет!\n\nНапиши своё сообщение, и оно будет передано администрации.",
        "sent": "✅ Ваше сообщение отправлено администрации.",
    },
}

LANG_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="lang:kk"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        ]
    ]
)

# ===== Хранение языка =====
_memory: dict[int, str] = {}  # запасной вариант, если Redis не подключён


async def get_lang(user_id: int) -> str | None:
    if not REDIS_URL:
        return _memory.get(user_id)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{REDIS_URL}/get/lang:{user_id}",
                headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            ) as r:
                data = await r.json()
                return data.get("result")
    except Exception as e:
        logging.warning("Redis get error: %s", e)
        return _memory.get(user_id)


async def set_lang(user_id: int, lang: str) -> None:
    _memory[user_id] = lang
    if not REDIS_URL:
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.get(
                f"{REDIS_URL}/set/lang:{user_id}/{lang}",
                headers={"Authorization": f"Bearer {REDIS_TOKEN}"},
            )
    except Exception as e:
        logging.warning("Redis set error: %s", e)


# ===== Обработчики =====
router = Router()


@router.message(CommandStart())
@router.message(Command("language"))
async def cmd_start(message: Message):
    await message.answer(CHOOSE_LANG, reply_markup=LANG_KB)


@router.callback_query(F.data.startswith("lang:"))
async def choose_lang(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    if lang not in TEXTS:
        await callback.answer()
        return
    await set_lang(callback.from_user.id, lang)
    await callback.message.edit_text(TEXTS[lang]["greeting"])
    await callback.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message, bot: Bot):
    user = message.from_user
    lang = await get_lang(user.id)

    # Если язык ещё не выбран — просим выбрать
    if lang not in TEXTS:
        await message.answer(CHOOSE_LANG, reply_markup=LANG_KB)
        return

    text = html.escape(message.text)

    # Админам: только текст
    admin_text = f"📩 <b>Новое сообщение:</b>\n\n{text}"
    for admin_id in ADMIN_IDS:
        if admin_id == SUPERADMIN_ID:
            continue
        try:
            await bot.send_message(admin_id, admin_text)
        except TelegramAPIError as e:
            logging.warning("Не удалось отправить админу %s: %s", admin_id, e)

    # Суперадмину: текст + username + ID
    username = f"@{user.username}" if user.username else "нет username"
    super_text = (
        "📩 <b>Новое сообщение</b>\n\n"
        f"👤 Имя: {html.escape(user.full_name)}\n"
        f"🔗 Username: {html.escape(username)}\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"💬 Текст:\n{text}"
    )
    try:
        await bot.send_message(SUPERADMIN_ID, super_text)
    except TelegramAPIError as e:
        logging.warning("Не удалось отправить суперадмину: %s", e)

    await message.answer(TEXTS[lang]["sent"])


dp = Dispatcher()
dp.include_router(router)

app = FastAPI()


@app.get("/{full_path:path}")
async def health(full_path: str = ""):
    return {"status": "bot is running"}


@app.post("/{full_path:path}")
async def webhook(
    request: Request,
    full_path: str = "",
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    data = await request.json()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    finally:
        await bot.session.close()
    return {"ok": True}
