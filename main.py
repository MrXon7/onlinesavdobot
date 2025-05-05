import os
import logging
from pathlib import Path
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    # InputFile,
    # InputMediaPhoto,
    # InputMediaVideo,
    # InputMediaAudio,
    # InputMediaDocument
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from typing import List, Dict, Optional
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web
import asyncio
import requests
import threading

# Sozlamalar
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Fetch the bot token from environment variables
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS').split(',')))  # Replace with actual admin IDs
WEB_URL= os.getenv('WEB_URL')  # Replace with your web URL
WEBHOOK_PATH = "/webhook"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv('PORT', 8000))
WEBHOOK_URL = f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com{WEBHOOK_PATH}"

# Loglarni sozlash
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ma'lumotlar bazasi
import psycopg2
from psycopg2 import sql
from typing import Set

# PostgreSQL ulanish uchun
DATABASE_URL = os.getenv('DATABASE_URL')

def init_db():
    """Ma'lumotlar bazasi va jadvalni yaratish"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_blocked BOOLEAN DEFAULT FALSE,
            last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        conn.commit()
    except Exception as e:
        print(f"Database initialization error: {e}")
    finally:
        if conn:
            cur.close()
            conn.close()

def add_user(user: types.User):
    """Foydalanuvchi qo'shish yoki yangilash"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        # INSERT yoki UPDATE qilish (UPSERT)
        cur.execute("""
        INSERT INTO users (user_id, username, first_name, last_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) 
        DO UPDATE SET 
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            last_active = CURRENT_TIMESTAMP
        """, (user.id, user.username, user.first_name, user.last_name))
        
        conn.commit()
    except Exception as e:
        print(f"Error adding/updating user: {e}")
    finally:
        if conn:
            cur.close()
            conn.close()

def get_active_users() -> Set[int]:
    """Bloklanmagan faol foydalanuvchilarni olish"""
    active_users = set()
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        cur = conn.cursor()
        
        cur.execute("SELECT user_id FROM users WHERE is_blocked = FALSE")
        active_users = {row[0] for row in cur.fetchall()}
        
    except Exception as e:
        print(f"Error fetching active users: {e}")
    finally:
        if conn:
            cur.close()
            conn.close()
    
    return active_users

# Bot va dispatcher
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Klaviaturalar
def make_web_keyboard(user_id: int) -> Optional[ReplyKeyboardMarkup]:
    """WebApp tugmalari bilan klaviatura yaratish"""
    try:
        keyboard=[]
        keyboard.append([KeyboardButton(
                text="🏪Savdoni boshlash",
                web_app=WebAppInfo(url=f"{WEB_URL}?telegram_id={user_id}")
            )])
      
        return ReplyKeyboardMarkup(
            keyboard=keyboard,
            resize_keyboard=True,
            one_time_keyboard=False,
            input_field_placeholder="Web sahifani tanlang"
        )
    except Exception as e:
        logger.error(f"Klaviaturani yaratishda xato: {e}")
#         return None

def make_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="broadcast")],
        # [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")]
    ])

# Handlers
@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    try:
        if message.from_user.id in ADMIN_IDS:
            await message.answer("Admin paneli:", reply_markup=make_admin_keyboard())
        else:
            await message.answer(
                "Assalomu alaykum!\n🛍Xarid qilishni boshlash uchun quyidagi tugmani bosing."
            )
        add_user(message.from_user)
    except Exception as e:
        logger.error(f"Xato: {e}")


# ================== ADMIN FUNKSIYALARI ==================

# Yangi holatlar klassini yaratamiz
class BroadcastState(StatesGroup):
    waiting_for_message = State()

async def send_media_to_user(user_id: int, message: Message):
    """Foydalanuvchiga turli xil media yuborish"""
    try:
        if message.content_type == ContentType.TEXT:
            await bot.send_message(user_id, message.text)
        elif message.content_type == ContentType.PHOTO:
            photo = message.photo[-1]  # Eng yuqori sifatli rasm
            await bot.send_photo(user_id, photo.file_id, caption=message.caption)
        elif message.content_type == ContentType.VIDEO:
            await bot.send_video(user_id, message.video.file_id, caption=message.caption)
        elif message.content_type == ContentType.VIDEO_NOTE:
            await bot.send_video_note(user_id, message.video_note.file_id)
        elif message.content_type == ContentType.AUDIO:
            await bot.send_audio(user_id, message.audio.file_id, caption=message.caption)
        elif message.content_type == ContentType.DOCUMENT:
            await bot.send_document(user_id, message.document.file_id, caption=message.caption)
        else:
            await bot.send_message(user_id, "Kechirasiz, bu turdagi xabar yuborish imkoni hozircha mavjud emas.")
    except Exception as e:
        raise e

# Admin panelida "Xabar yuborish" tugmasi bosilganda
@dp.callback_query(F.data == "broadcast")
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.message.answer(
            "📢 Barcha foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yuboring:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_broadcast")]
            ])
        )
        await state.set_state(BroadcastState.waiting_for_message)
        await callback.answer()
    except Exception as e:
        logger.error(f"Xato yuz berdi (start_broadcast): {e}")

# Admin xabar yuborganida (reply qilish shart emas)
@dp.message(BroadcastState.waiting_for_message, F.from_user.id.in_(ADMIN_IDS))
async def process_broadcast_all_types(message: Message, state: FSMContext):
    try:
        await message.answer("Xabar foydalanuvchilarga yuborilmoqda...")
        
        stats = {"success": 0, "blocked": 0, "failed": 0}
        active_users = get_active_users()
        total_users = len(active_users)
        processed = 0
        
        for user_id in active_users:
            try:
                await send_media_to_user(user_id, message)
                stats["success"] += 1
            except Exception as e:
                if "bot was blocked by the user" in str(e):
                    stats["blocked"] += 1
                else:
                    stats["failed"] += 1
                logger.error(f"Xabar yuborishda xato (user_id: {user_id}): {e}")
            
            processed += 1
            if processed % 10 == 0:  # Har 10ta xabardan keyin progress yangilash
                await message.edit_text(
                    f"Xabar yuborilmoqda...\n"
                    f"Progress: {processed}/{total_users}\n"
                    f"✅ {stats['success']} ❌ {stats['blocked']} ⚠️ {stats['failed']}"
                )
            await asyncio.sleep(0.1)  # Limitlardan qochish uchun
        
        await message.answer(
            f"📊 Xabar yuborish yakunlandi:\n"
            f"Jami foydalanuvchilar: {total_users}\n"
            f"✅ Muvaffaqiyatli: {stats['success']}\n"
            f"❌ Bloklangan: {stats['blocked']}\n"
            f"⚠️ Boshqa xatolar: {stats['failed']}"
        )
        await state.clear()
        
    except Exception as e:
        logger.error(f"Xabar yuborishda asosiy xato: {e}")
        await message.answer(f"Xatolik yuz berdi: {str(e)}")
        await state.clear()

# Bekor qilish tugmasi
@dp.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.message.answer("Xabar yuborish bekor qilindi.")
        await state.clear()
        await callback.answer()
    except Exception as e:
        logger.error(f"Xato yuz berdi (cancel_broadcast): {e}")


# ================== BOSHQALAR ==================

@dp.message()
async def unknown_command(message: Message):
    try:
        if message.from_user.id in ADMIN_IDS:
            await message.answer("Noto'g'ri buyruq! Admin paneli uchun /start ni bosing.")
        else:
            keyboard = make_web_keyboard(message.from_user.id)
            if keyboard:
                await message.answer(
                    "Noto'g'ri buyruq! Iltimos, menyudan biror tugmani tanlang yoki /start ni bosing.",
                    reply_markup=keyboard
                )
            else:
                await message.answer("Noto'g'ri buyruq! Iltimos, /start ni bosing.")
    except Exception as e:
        logger.error(f"Xato yuz berdi (unknown_command): {e}")



# ____________________________Webhook sozlamalari
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook o'rnatildi: {WEBHOOK_URL}")

async def health_check(request):
    return web.Response(text="OK")

app = web.Application()
app.on_startup.append(on_startup)
SimpleRequestHandler(dp, bot=bot).register(app, path=WEBHOOK_PATH)
app.router.add_get("/health", health_check)


# ___________________________Uxlab qolishni oldini olish

def keep_alive():
    try:
        requests.get("https://onlinesavdobot.onrender.com/health")
    except:
        pass
    threading.Timer(300, keep_alive).start()  # Har 5 daqiqada

# Ishga tushganda
keep_alive()

# Render Uchun Run
if __name__ == "__main__":
    init_db()
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

# # Koyep uchun Run
# if __name__ == '__main__':
#     port = int(os.environ.get('PORT', 8000))
#     web.run_app(app, host="0.0.0.0", port=port)


