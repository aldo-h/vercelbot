import html
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.types import Message, Update
from fastapi import FastAPI, Header, HTTPException, Request

# Все настройки берутся из переменных окружения Vercel
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPERADMIN_ID = int(os.environ["SUPERADMIN_ID"])
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    name = html.escape(message.from_user.first_name or "ученик")
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Напиши своё сообщение, и оно будет передано администрации."
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_user_text(message: Message, bot: Bot):
    user = message.from_user
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

    await message.answer("✅ Ваше сообщение отправлено администрации.")


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
