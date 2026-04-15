import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, WebAppInfo
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
from contextlib import contextmanager

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "8611133731:AAHH2x7RJl2_fvRd6QzwoXzgL2f-DMmBVhE")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-railway-app.railway.app/webapp")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "8279786578").split(",")))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# ============ БАЗА ДАННЫХ ============
@contextmanager
def get_db():
    conn = sqlite3.connect('chat.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                anon_id TEXT UNIQUE,
                gender TEXT,
                age_group TEXT,
                interests TEXT,
                ban_until TEXT,
                warnings INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS dialogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER,
                user2_id INTEGER,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                reason TEXT,
                banned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                banned_until TEXT
            );
            
            CREATE TABLE IF NOT EXISTS queue (
                user_id INTEGER PRIMARY KEY,
                gender TEXT,
                age_group TEXT,
                is_webapp INTEGER DEFAULT 0,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')

# ============ FSM STATES ============
class Registration(StatesGroup):
    gender = State()
    age = State()
    interests = State()

# ============ УТИЛИТЫ ============
def generate_anon_id():
    import random
    animals = ["Пингвин", "Кот", "Лиса", "Волк", "Медведь", "Енот", "Панда", "Тигр"]
    return f"{random.choice(animals)}#{random.randint(1000, 9999)}"

def check_ban(user_id):
    with get_db() as conn:
        user = conn.execute(
            "SELECT ban_until FROM users WHERE user_id = ?", 
            (user_id,)
        ).fetchone()
        if user and user['ban_until']:
            ban_until = datetime.fromisoformat(user['ban_until'])
            if datetime.now() < ban_until:
                return ban_until
    return None

def check_forbidden_content(text):
    """Проверка на запрещенный контент"""
    patterns = [
        r'@\w+',  # юзернеймы
        r't\.me/',  # ссылки на телеграм
        r'https?://',  # любые ссылки
        r'\b\d{10,}\b',  # номера телефонов
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def apply_ban(user_id, days, reason):
    ban_until = datetime.now() + timedelta(days=days)
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET ban_until = ?, warnings = warnings + 1 WHERE user_id = ?",
            (ban_until.isoformat(), user_id)
        )
        conn.execute(
            "INSERT INTO bans (user_id, reason, banned_until) VALUES (?, ?, ?)",
            (user_id, reason, ban_until.isoformat())
        )
    return ban_until

def get_partner_id(user_id):
    with get_db() as conn:
        dialog = conn.execute(
            "SELECT user1_id, user2_id FROM dialogs WHERE (user1_id = ? OR user2_id = ?) AND status = 'active'",
            (user_id, user_id)
        ).fetchone()
        if dialog:
            return dialog['user2_id'] if dialog['user1_id'] == user_id else dialog['user1_id']
    return None

def end_dialog(user_id):
    with get_db() as conn:
        conn.execute(
            "UPDATE dialogs SET status = 'ended' WHERE (user1_id = ? OR user2_id = ?) AND status = 'active'",
            (user_id, user_id)
        )

# ============ КЛАВИАТУРЫ ============
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Искать в боте", callback_data="search_bot")],
        [InlineKeyboardButton(text="✨ Искать через WebApp", web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="❓ Правила", callback_data="rules")]
    ])

def gender_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨 Мужской", callback_data="gender_male")],
        [InlineKeyboardButton(text="👩 Женский", callback_data="gender_female")],
        [InlineKeyboardButton(text="⚧️ Другой", callback_data="gender_other")]
    ])

def age_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="13-17", callback_data="age_13-17")],
        [InlineKeyboardButton(text="18-24", callback_data="age_18-24")],
        [InlineKeyboardButton(text="25-34", callback_data="age_25-34")],
        [InlineKeyboardButton(text="35+", callback_data="age_35+")]
    ])

def chat_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Следующий", callback_data="next_chat")],
        [InlineKeyboardButton(text="❌ Завершить", callback_data="end_chat")],
        [InlineKeyboardButton(text="🚨 Пожаловаться", callback_data="report")]
    ])

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📝 Логи банов", callback_data="admin_bans")],
        [InlineKeyboardButton(text="👥 Активные диалоги", callback_data="admin_dialogs")]
    ])

# ============ ОБРАБОТЧИКИ ============
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    ban_until = check_ban(user_id)
    if ban_until:
        await message.answer(f"🚫 Вы заблокированы до {ban_until.strftime('%d.%m.%Y %H:%M')}")
        return
    
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        
        if not user:
            await message.answer(
                "👋 Добро пожаловать в анонимный чат!\n\n"
                "Выберите ваш пол:",
                reply_markup=gender_keyboard()
            )
            await state.set_state(Registration.gender)
        else:
            await message.answer(
                f"🎭 Привет, {user['anon_id']}!\n\n"
                "Выберите действие:",
                reply_markup=main_menu()
            )

@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    await callback.message.edit_text(
        "🎂 Выберите возрастную группу:",
        reply_markup=age_keyboard()
    )
    await state.set_state(Registration.age)

@router.callback_query(F.data.startswith("age_"))
async def process_age(callback: CallbackQuery, state: FSMContext):
    age_group = callback.data.split("_")[1]
    data = await state.get_data()
    
    anon_id = generate_anon_id()
    user_id = callback.from_user.id
    
    with get_db() as conn:
        conn.execute(
            "INSERT INTO users (user_id, anon_id, gender, age_group, interests) VALUES (?, ?, ?, ?, ?)",
            (user_id, anon_id, data['gender'], age_group, "")
        )
    
    await state.clear()
    await callback.message.edit_text(
        f"✅ Регистрация завершена!\n\n"
        f"Ваш анонимный ID: {anon_id}\n\n"
        f"⚠️ ВАЖНО:\n"
        f"• Не называйте своё имя\n"
        f"• Не пишите @username\n"
        f"• Не делитесь контактами\n\n"
        f"За нарушения - бан на 2 недели!",
        reply_markup=main_menu()
    )

@router.callback_query(F.data == "search_bot")
async def search_bot(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if check_ban(user_id):
        await callback.answer("Вы заблокированы!", show_alert=True)
        return
    
    # Добавляем в очередь поиска
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        
        # Проверяем, есть ли подходящий партнёр
        partner = conn.execute(
            "SELECT user_id FROM queue WHERE user_id != ? LIMIT 1",
            (user_id,)
        ).fetchone()
        
        if partner:
            # Создаём диалог
            partner_id = partner['user_id']
            conn.execute("DELETE FROM queue WHERE user_id = ?", (partner_id,))
            conn.execute(
                "INSERT INTO dialogs (user1_id, user2_id) VALUES (?, ?)",
                (user_id, partner_id)
            )
            
            await callback.message.edit_text(
                "✅ Собеседник найден!\n\n"
                "Начните общение:",
                reply_markup=chat_keyboard()
            )
            await bot.send_message(
                partner_id,
                "✅ Собеседник найден!\n\nНачните общение:",
                reply_markup=chat_keyboard()
            )
        else:
            # Добавляем в очередь
            conn.execute(
                "INSERT OR REPLACE INTO queue (user_id, gender, age_group, is_webapp) VALUES (?, ?, ?, 0)",
                (user_id, user['gender'], user['age_group'])
            )
            await callback.message.edit_text(
                "🔍 Ищем собеседника...\n\n"
                "Ожидайте, это может занять некоторое время.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отменить поиск", callback_data="cancel_search")]
                ])
            )

@router.callback_query(F.data == "cancel_search")
async def cancel_search(callback: CallbackQuery):
    with get_db() as conn:
        conn.execute("DELETE FROM queue WHERE user_id = ?", (callback.from_user.id,))
    await callback.message.edit_text("❌ Поиск отменён", reply_markup=main_menu())

@router.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    
    if check_ban(user_id):
        return
    
    # Проверка на запрещенный контент
    if check_forbidden_content(message.text):
        ban_until = apply_ban(user_id, 14, "Попытка деанонимизации")
        await message.answer(
            f"🚫 ВЫ ЗАБЛОКИРОВАНЫ до {ban_until.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Причина: Запрещённый контент (имена, @username, ссылки)"
        )
        return
    
    # Пересылка сообщения партнёру
    partner_id = get_partner_id(user_id)
    if partner_id:
        try:
            await bot.send_message(partner_id, message.text)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    else:
        await message.answer("❌ Сначала найдите собеседника!", reply_markup=main_menu())

@router.callback_query(F.data == "end_chat")
async def end_chat(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = get_partner_id(user_id)
    
    end_dialog(user_id)
    
    await callback.message.edit_text(
        "👋 Диалог завершён",
        reply_markup=main_menu()
    )
    
    if partner_id:
        try:
            await bot.send_message(
                partner_id,
                "👋 Собеседник завершил диалог",
                reply_markup=main_menu()
            )
        except:
            pass

@router.callback_query(F.data == "next_chat")
async def next_chat(callback: CallbackQuery):
    await end_chat(callback)
    await search_bot(callback)

@router.callback_query(F.data == "report")
async def report_user(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = get_partner_id(user_id)
    
    if partner_id:
        # Уведомляем админов
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🚨 ЖАЛОБА\n\n"
                    f"От: {user_id}\n"
                    f"На: {partner_id}"
                )
            except:
                pass
        
        await callback.answer("✅ Жалоба отправлена", show_alert=True)
    else:
        await callback.answer("❌ Нет активного диалога", show_alert=True)

@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (callback.from_user.id,)
        ).fetchone()
        
        await callback.message.edit_text(
            f"👤 Ваш профиль:\n\n"
            f"🎭 ID: {user['anon_id']}\n"
            f"👥 Пол: {user['gender']}\n"
            f"🎂 Возраст: {user['age_group']}\n"
            f"⚠️ Предупреждений: {user['warnings']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
            ])
        )

@router.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎭 Главное меню:",
        reply_markup=main_menu()
    )

@router.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery):
    await callback.message.edit_text(
        "📋 ПРАВИЛА:\n\n"
        "🚫 ЗАПРЕЩЕНО:\n"
        "• Называть своё имя\n"
        "• Писать @username\n"
        "• Делиться контактами\n"
        "• Отправлять ссылки\n"
        "• Оскорбления, спам\n\n"
        "⚠️ НАКАЗАНИЯ:\n"
        "1-е нарушение: Бан 3 дня\n"
        "2-е нарушение: Бан 2 недели\n"
        "3-е нарушение: Перманент",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
        ])
    )

# ============ АДМИН-КОМАНДЫ ============
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    await message.answer(
        "🔐 АДМИН-ПАНЕЛЬ",
        reply_markup=admin_keyboard()
    )

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_dialogs = conn.execute("SELECT COUNT(*) FROM dialogs WHERE status = 'active'").fetchone()[0]
        total_bans = conn.execute("SELECT COUNT(*) FROM bans").fetchone()[0]
        
        await callback.message.edit_text(
            f"📊 СТАТИСТИКА:\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"💬 Активных диалогов: {active_dialogs}\n"
            f"🚫 Всего банов: {total_bans}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
            ])
        )

@router.callback_query(F.data == "back_admin")
async def back_admin(callback: CallbackQuery):
    await callback.message.edit_text("🔐 АДМИН-ПАНЕЛЬ", reply_markup=admin_keyboard())

# ============ WEBAPP API ============
from aiohttp import web

async def webapp_handler(request):
    with open('webapp/index.html', 'r', encoding='utf-8') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def webapp_search(request):
    """API для поиска через WebApp"""
    data = await request.json()
    user_id = data.get('user_id')
    
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not user:
            return web.json_response({'error': 'User not found'}, status=404)
        
        # Ищем партнёра
        partner = conn.execute(
            "SELECT user_id FROM queue WHERE user_id != ? LIMIT 1",
            (user_id,)
        ).fetchone()
        
        if partner:
            partner_id = partner['user_id']
            conn.execute("DELETE FROM queue WHERE user_id = ?", (partner_id,))
            conn.execute(
                "INSERT INTO dialogs (user1_id, user2_id) VALUES (?, ?)",
                (user_id, partner_id)
            )
            
            # Уведомляем партнёра в боте
            try:
                await bot.send_message(
                    partner_id,
                    "✅ Собеседник найден! (WebApp)",
                    reply_markup=chat_keyboard()
                )
            except:
                pass
            
            return web.json_response({'found': True, 'partner_id': partner_id})
        else:
            conn.execute(
                "INSERT OR REPLACE INTO queue (user_id, gender, age_group, is_webapp) VALUES (?, ?, ?, 1)",
                (user_id, user['gender'], user['age_group'])
            )
            return web.json_response({'found': False})

async def webapp_send_message(request):
    """API для отправки сообщений из WebApp"""
    data = await request.json()
    user_id = data.get('user_id')
    text = data.get('text')
    
    if check_forbidden_content(text):
        apply_ban(user_id, 14, "Запрещённый контент")
        return web.json_response({'error': 'Banned'}, status=403)
    
    partner_id = get_partner_id(user_id)
    if partner_id:
        try:
            await bot.send_message(partner_id, text)
            return web.json_response({'success': True})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)
    
    return web.json_response({'error': 'No active dialog'}, status=404)

async def webapp_get_messages(request):
    """Long polling для получения сообщений в WebApp"""
    user_id = int(request.query.get('user_id'))
    
    # Здесь должна быть логика long polling
    # Упрощённая версия - просто проверяем партнёра
    partner_id = get_partner_id(user_id)
    
    return web.json_response({
        'partner_id': partner_id,
        'active': partner_id is not None
    })

async def init_webapp():
    app = web.Application()
    app.router.add_get('/webapp', webapp_handler)
    app.router.add_post('/api/search', webapp_search)
    app.router.add_post('/api/send', webapp_send_message)
    app.router.add_get('/api/messages', webapp_get_messages)
    return app

# ============ ЗАПУСК ============
async def main():
    init_db()
    
    dp.include_router(router)
    
    # Запуск веб-сервера для WebApp
    app = await init_webapp()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8080)))
    await site.start()
    
    logger.info("WebApp server started")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
