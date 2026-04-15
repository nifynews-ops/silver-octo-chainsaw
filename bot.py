import asyncio
import logging
import os
import re
import random
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
from aiohttp import web

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "8611133731:AAHH2x7RJl2_fvRd6QzwoXzgL2f-DMmBVhE")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://web-production-fd96.up.railway.app/")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "8279786578").split(",")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "secret_admin_token_12345")
PORT = int(os.getenv("PORT", 8080))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# ============ БАЗА ДАННЫХ ============
@contextmanager
def get_db():
    conn = sqlite3.connect('chat.db', check_same_thread=False)
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
            
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dialog_id INTEGER,
                sender_id INTEGER,
                text TEXT,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')

# ============ FSM STATES ============
class Registration(StatesGroup):
    gender = State()
    age = State()

# ============ УТИЛИТЫ ============
def generate_anon_id():
    animals = ["Пингвин", "Кот", "Лиса", "Волк", "Медведь", "Енот", "Панда", "Тигр", "Дельфин", "Сова"]
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
        r'vk\.com',
        r'instagram',
        r'whatsapp',
        r'viber',
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def apply_ban(user_id, days, reason):
    ban_until = datetime.now() + timedelta(days=days)
    with get_db() as conn:
        warnings = conn.execute(
            "SELECT warnings FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]
        
        conn.execute(
            "UPDATE users SET ban_until = ?, warnings = warnings + 1 WHERE user_id = ?",
            (ban_until.isoformat(), user_id)
        )
        conn.execute(
            "INSERT INTO bans (user_id, reason, banned_until) VALUES (?, ?, ?)",
            (user_id, reason, ban_until.isoformat())
        )
    return ban_until, warnings + 1

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

def get_user_info(user_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

# ============ КЛАВИАТУРЫ ============
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Искать в боте", callback_data="search_bot")],
        [InlineKeyboardButton(text="✨ Искать через WebApp", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp"))],
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
        [
            InlineKeyboardButton(text="⏭ Следующий", callback_data="next_chat"),
            InlineKeyboardButton(text="❌ Завершить", callback_data="end_chat")
        ],
        [InlineKeyboardButton(text="🚨 Пожаловаться", callback_data="report")]
    ])

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📝 Логи банов", callback_data="admin_bans")],
        [InlineKeyboardButton(text="👥 Активные диалоги", callback_data="admin_dialogs")]
    ])

# ============ ОБРАБОТЧИКИ КОМАНД ============
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    ban_until = check_ban(user_id)
    if ban_until:
        await message.answer(
            f"🚫 <b>Вы заблокированы!</b>\n\n"
            f"⏰ До: {ban_until.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Причина: Нарушение правил анонимности",
            parse_mode="HTML"
        )
        return
    
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        
        if not user:
            await message.answer(
                "👋 <b>Добро пожаловать в Анонимный Чат!</b>\n\n"
                "🎭 Здесь вы можете общаться полностью анонимно\n\n"
                "Для начала выберите ваш пол:",
                reply_markup=gender_keyboard(),
                parse_mode="HTML"
            )
            await state.set_state(Registration.gender)
        else:
            await message.answer(
                f"🎭 <b>Привет, {user['anon_id']}!</b>\n\n"
                f"Выберите действие:",
                reply_markup=main_menu(),
                parse_mode="HTML"
            )

# ============ РЕГИСТРАЦИЯ ============
@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    
    gender_emoji = {"male": "👨", "female": "👩", "other": "⚧️"}
    
    await callback.message.edit_text(
        f"{gender_emoji.get(gender, '👤')} <b>Пол выбран</b>\n\n"
        f"🎂 Теперь выберите возрастную группу:",
        reply_markup=age_keyboard(),
        parse_mode="HTML"
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
        f"✅ <b>Регистрация завершена!</b>\n\n"
        f"🎭 Ваш анонимный ID: <code>{anon_id}</code>\n\n"
        f"⚠️ <b>ВАЖНЫЕ ПРАВИЛА:</b>\n"
        f"• ❌ Не называйте своё имя\n"
        f"• ❌ Не пишите @username\n"
        f"• ❌ Не делитесь контактами\n"
        f"• ❌ Не отправляйте ссылки\n\n"
        f"🚫 За нарушения - <b>бан на 2 недели!</b>",
        reply_markup=main_menu(),
        parse_mode="HTML"
    )

# ============ ПОИСК СОБЕСЕДНИКА ============
@router.callback_query(F.data == "search_bot")
async def search_bot(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    ban_until = check_ban(user_id)
    if ban_until:
        await callback.answer("Вы заблокированы!", show_alert=True)
        return
    
    # Проверяем, не в диалоге ли уже
    if get_partner_id(user_id):
        await callback.answer("Сначала завершите текущий диалог!", show_alert=True)
        return
    
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        
        # Ищем партнёра в очереди
        partner = conn.execute(
            "SELECT user_id FROM queue WHERE user_id != ? LIMIT 1",
            (user_id,)
        ).fetchone()
        
        if partner:
            partner_id = partner['user_id']
            partner_info = conn.execute("SELECT anon_id FROM users WHERE user_id = ?", (partner_id,)).fetchone()
            
            # Удаляем из очереди
            conn.execute("DELETE FROM queue WHERE user_id = ?", (partner_id,))
            
            # Создаём диалог
            conn.execute(
                "INSERT INTO dialogs (user1_id, user2_id) VALUES (?, ?)",
                (user_id, partner_id)
            )
            
            await callback.message.edit_text(
                f"✅ <b>Собеседник найден!</b>\n\n"
                f"Начните общение с <code>{partner_info['anon_id']}</code>\n\n"
                f"💬 Просто отправьте сообщение",
                reply_markup=chat_keyboard(),
                parse_mode="HTML"
            )
            
            try:
                await bot.send_message(
                    partner_id,
                    f"✅ <b>Собеседник найден!</b>\n\n"
                    f"Начните общение с <code>{user['anon_id']}</code>\n\n"
                    f"💬 Просто отправьте сообщение",
                    reply_markup=chat_keyboard(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Error notifying partner: {e}")
        else:
            # Добавляем в очередь
            conn.execute(
                "INSERT OR REPLACE INTO queue (user_id, gender, age_group, is_webapp) VALUES (?, ?, ?, 0)",
                (user_id, user['gender'], user['age_group'])
            )
            
            await callback.message.edit_text(
                "🔍 <b>Ищем собеседника...</b>\n\n"
                "⏳ Ожидайте, это может занять некоторое время",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отменить поиск", callback_data="cancel_search")]
                ]),
                parse_mode="HTML"
            )

@router.callback_query(F.data == "cancel_search")
async def cancel_search(callback: CallbackQuery):
    with get_db() as conn:
        conn.execute("DELETE FROM queue WHERE user_id = ?", (callback.from_user.id,))
    
    await callback.message.edit_text(
        "❌ Поиск отменён",
        reply_markup=main_menu()
    )

# ============ ОБРАБОТКА СООБЩЕНИЙ ============
@router.message(F.text)
async def handle_message(message: Message):
    user_id = message.from_user.id
    
    # Проверка бана
    ban_until = check_ban(user_id)
    if ban_until:
        return
    
    # Проверка на запрещенный контент
    if check_forbidden_content(message.text):
        ban_until, warnings = apply_ban(user_id, 14, "Попытка деанонимизации")
        
        # Уведомляем партнёра
        partner_id = get_partner_id(user_id)
        if partner_id:
            try:
                await bot.send_message(
                    partner_id,
                    "⚠️ Собеседник нарушил правила и был заблокирован",
                    reply_markup=main_menu()
                )
            except:
                pass
            end_dialog(user_id)
        
        await message.answer(
            f"🚫 <b>ВЫ ЗАБЛОКИРОВАНЫ!</b>\n\n"
            f"⏰ До: {ban_until.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"❌ Причина: Запрещённый контент\n"
            f"(имена, @username, ссылки, контакты)\n\n"
            f"⚠️ Предупреждений: {warnings}/3\n\n"
            f"После 3-го предупреждения - перманентный бан!",
            parse_mode="HTML"
        )
        return
    
    # Пересылка сообщения партнёру
    partner_id = get_partner_id(user_id)
    if partner_id:
        try:
            await bot.send_message(partner_id, message.text)
            
            # Сохраняем в БД
            with get_db() as conn:
                dialog = conn.execute(
                    "SELECT id FROM dialogs WHERE (user1_id = ? OR user2_id = ?) AND status = 'active'",
                    (user_id, user_id)
                ).fetchone()
                if dialog:
                    conn.execute(
                        "INSERT INTO messages (dialog_id, sender_id, text) VALUES (?, ?, ?)",
                        (dialog['id'], user_id, message.text)
                    )
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await message.answer(
                "❌ Не удалось отправить сообщение. Собеседник возможно покинул чат.",
                reply_markup=main_menu()
            )
    else:
        await message.answer(
            "❌ Сначала найдите собеседника!",
            reply_markup=main_menu()
        )

# ============ УПРАВЛЕНИЕ ДИАЛОГОМ ============
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
    user_id = callback.from_user.id
    partner_id = get_partner_id(user_id)
    
    end_dialog(user_id)
    
    if partner_id:
        try:
            await bot.send_message(
                partner_id,
                "👋 Собеседник начал поиск нового партнёра",
                reply_markup=main_menu()
            )
        except:
            pass
    
    await search_bot(callback)

@router.callback_query(F.data == "report")
async def report_user(callback: CallbackQuery):
    user_id = callback.from_user.id
    partner_id = get_partner_id(user_id)
    
    if partner_id:
        partner_info = get_user_info(partner_id)
        
        # Уведомляем админов
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🚨 <b>ЖАЛОБА</b>\n\n"
                    f"От пользователя: {user_id}\n"
                    f"На пользователя: {partner_id}\n"
                    f"ID собеседника: {partner_info['anon_id']}\n\n"
                    f"Используйте /ban {partner_id} для блокировки",
                    parse_mode="HTML"
                )
            except:
                pass
        
        await callback.answer("✅ Жалоба отправлена администратору", show_alert=True)
    else:
        await callback.answer("❌ Нет активного диалога", show_alert=True)

# ============ ПРОФИЛЬ ============
@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (callback.from_user.id,)
        ).fetchone()
        
        dialogs_count = conn.execute(
            "SELECT COUNT(*) FROM dialogs WHERE (user1_id = ? OR user2_id = ?) AND status = 'ended'",
            (callback.from_user.id, callback.from_user.id)
        ).fetchone()[0]
        
        gender_names = {"male": "Мужской 👨", "female": "Женский 👩", "other": "Другой ⚧️"}
        
        await callback.message.edit_text(
            f"👤 <b>Ваш профиль</b>\n\n"
            f"🎭 ID: <code>{user['anon_id']}</code>\n"
            f"👥 Пол: {gender_names.get(user['gender'], 'Не указан')}\n"
            f"🎂 Возраст: {user['age_group']}\n"
            f"💬 Завершённых диалогов: {dialogs_count}\n"
            f"⚠️ Предупреждений: {user['warnings']}/3\n"
            f"📅 Регистрация: {user['created_at'][:10]}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
            ]),
            parse_mode="HTML"
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
        "📋 <b>ПРАВИЛА ЧАТА</b>\n\n"
        "🚫 <b>ЗАПРЕЩЕНО:</b>\n"
        "• Называть своё настоящее имя\n"
        "• Писать @username или ссылки на соцсети\n"
        "• Делиться номерами телефонов\n"
        "• Отправлять любые ссылки\n"
        "• Оскорбления и спам\n\n"
        "⚖️ <b>НАКАЗАНИЯ:</b>\n"
        "1️⃣ Первое нарушение → Бан на 3 дня\n"
        "2️⃣ Второе нарушение → Бан на 2 недели\n"
        "3️⃣ Третье нарушение → Перманентный бан\n\n"
        "✅ Соблюдайте правила и приятного общения!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
        ]),
        parse_mode="HTML"
    )

# ============ АДМИН-ПАНЕЛЬ ============
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ У вас нет доступа")
        return
    
    await message.answer(
        "🔐 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_dialogs = conn.execute("SELECT COUNT(*) FROM dialogs WHERE status = 'active'").fetchone()[0]
        total_bans = conn.execute("SELECT COUNT(*) FROM bans").fetchone()[0]
        in_queue = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        
        await callback.message.edit_text(
            f"📊 <b>СТАТИСТИКА</b>\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"💬 Активных диалогов: {active_dialogs}\n"
            f"🚫 Всего банов: {total_bans}\n"
            f"⏳ В очереди: {in_queue}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
            ]),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "admin_bans")
async def admin_bans_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with get_db() as conn:
        bans = conn.execute("""
            SELECT u.anon_id, b.reason, b.banned_at, b.banned_until
            FROM bans b
            JOIN users u ON b.user_id = u.user_id
            ORDER BY b.banned_at DESC
            LIMIT 10
        """).fetchall()
        
        text = "📝 <b>ПОСЛЕДНИЕ БАНЫ</b>\n\n"
        
        if bans:
            for ban in bans:
                text += f"• {ban[0]}\n"
                text += f"  Причина: {ban[1]}\n"
                text += f"  До: {ban[3][:16] if ban[3] else 'N/A'}\n\n"
        else:
            text += "Нет банов"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
            ]),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "admin_dialogs")
async def admin_dialogs_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    with get_db() as conn:
        dialogs = conn.execute("""
            SELECT d.id, u1.anon_id, u2.anon_id, d.created_at
            FROM dialogs d
            JOIN users u1 ON d.user1_id = u1.user_id
            JOIN users u2 ON d.user2_id = u2.user_id
            WHERE d.status = 'active'
            LIMIT 10
        """).fetchall()
        
        text = "👥 <b>АКТИВНЫЕ ДИАЛОГИ</b>\n\n"
        
        if dialogs:
            for dialog in dialogs:
                text += f"• {dialog[1]} ↔️ {dialog[2]}\n"
                text += f"  Начат: {dialog[3][:16]}\n\n"
        else:
            text += "Нет активных диалогов"
        
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
            ]),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "back_admin")
async def back_admin(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔐 <b>АДМИН-ПАНЕЛЬ</b>",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )

@router.message(Command("ban"))
async def ban_user(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /ban [user_id] [дней] [причина]")
        return
    
    try:
        user_id = int(args[1])
        days = int(args[2]) if len(args) > 2 else 14
        reason = " ".join(args[3:]) if len(args) > 3 else "Админ-бан"
        
        ban_until, warnings = apply_ban(user_id, days, reason)
        
        await message.answer(
            f"✅ Пользователь {user_id} заблокирован до {ban_until.strftime('%d.%m.%Y %H:%M')}"
        )
        
        try:
            await bot.send_message(
                user_id,
                f"🚫 Вы заблокированы администратором\n\n"
                f"Причина: {reason}\n"
                f"До: {ban_until.strftime('%d.%m.%Y %H:%M')}"
            )
        except:
            pass
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ============ WEBAPP API ============
async def webapp_handler(request):
    """Главная страница WebApp"""
    import os
    file_path = os.path.join(os.path.dirname(__file__), 'webapp', 'index.html')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return web.Response(text=f.read(), content_type='text/html')
    except FileNotFoundError:
        return web.Response(text='WebApp not found', status=404)

async def admin_panel_web(request):
    """Веб-интерфейс админ-панели"""
    admin_token = request.query.get('token')
    
    if admin_token != ADMIN_TOKEN:
        return web.Response(text='Access denied', status=403)
    
    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_dialogs = conn.execute("SELECT COUNT(*) FROM dialogs WHERE status = 'active'").fetchone()[0]
        total_bans = conn.execute("SELECT COUNT(*) FROM bans").fetchone()[0]
        in_queue = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        
        recent_bans = conn.execute("""
            SELECT u.anon_id, b.reason, b.banned_at, b.banned_until 
            FROM bans b 
            JOIN users u ON b.user_id = u.user_id 
            ORDER BY b.banned_at DESC 
            LIMIT 15
        """).fetchall()
        
        active_users = conn.execute("""
            SELECT anon_id, gender, age_group, warnings, created_at
            FROM users
            WHERE ban_until IS NULL OR ban_until < datetime('now')
            ORDER BY created_at DESC
            LIMIT 20
        """).fetchall()
        
        bans_html = ""
        for ban in recent_bans:
            bans_html += f"""
            <tr>
                <td><strong>{ban[0]}</strong></td>
                <td>{ban[1]}</td>
                <td>{ban[2][:16] if ban[2] else ''}</td>
                <td>{ban[3][:16] if ban[3] else ''}</td>
            </tr>
            """
        
        users_html = ""
        for user in active_users:
            users_html += f"""
            <tr>
                <td><strong>{user[0]}</strong></td>
                <td>{user[1]}</td>
                <td>{user[2]}</td>
                <td>{user[3]}</td>
                <td>{user[4][:10] if user[4] else ''}</td>
            </tr>
            """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Admin Panel</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #f0f2f5;
                padding: 20px;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
            }}
            .header {{
                background: white;
                padding: 30px;
                border-radius: 12px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 30px;
            }}
            h1 {{
                color: #1a1a1a;
                margin-bottom: 10px;
            }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .stat-card {{
                background: white;
                padding: 25px;
                border-radius: 12px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                border-left: 4px solid;
            }}
            .stat-card:nth-child(1) {{ border-left-color: #667eea; }}
            .stat-card:nth-child(2) {{ border-left-color: #28a745; }}
            .stat-card:nth-child(3) {{ border-left-color: #dc3545; }}
            .stat-card:nth-child(4) {{ border-left-color: #ffc107; }}
            .stat-card h3 {{
                font-size: 13px;
                color: #666;
                text-transform: uppercase;
                margin-bottom: 10px;
            }}
            .stat-card .number {{
                font-size: 36px;
                font-weight: bold;
                color: #1a1a1a;
            }}
            .section {{
                background: white;
                padding: 25px;
                border-radius: 12px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 30px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 14px;
                text-align: left;
                border-bottom: 1px solid #e0e0e0;
            }}
            th {{
                background: #f8f9fa;
                font-weight: 600;
            }}
            tr:hover {{ background: #f8f9fa; }}
            .refresh-btn {{
                background: #667eea;
                color: white;
                padding: 12px 24px;
                border: none;
                border-radius: 8px;
                cursor: pointer;
                font-weight: 500;
            }}
            .refresh-btn:hover {{ background: #5568d3; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔐 Админ-панель</h1>
                <button class="refresh-btn" onclick="location.reload()">🔄 Обновить</button>
            </div>
            
            <div class="stats">
                <div class="stat-card">
                    <h3>👥 Пользователей</h3>
                    <div class="number">{total_users}</div>
                </div>
                <div class="stat-card">
                    <h3>💬 Диалогов</h3>
                    <div class="number">{active_dialogs}</div>
                </div>
                <div class="stat-card">
                    <h3>🚫 Банов</h3>
                    <div class="number">{total_bans}</div>
                </div>
                <div class="stat-card">
                    <h3>⏳ В очереди</h3>
                    <div class="number">{in_queue}</div>
                </div>
            </div>
            
            <div class="section">
                <h2>📝 Последние баны</h2>
                <table>
                    <tr>
                        <th>Пользователь</th>
                        <th>Причина</th>
                        <th>Забанен</th>
                        <th>До</th>
                    </tr>
                    {bans_html or '<tr><td colspan="4" style="text-align:center;">Нет банов</td></tr>'}
                </table>
            </div>
            
            <div class="section">
                <h2>👤 Активные пользователи</h2>
                <table>
                    <tr>
                        <th>ID</th>
                        <th>Пол</th>
                        <th>Возраст</th>
                        <th>Предупреждения</th>
                        <th>Регистрация</th>
                    </tr>
                    {users_html or '<tr><td colspan="5" style="text-align:center;">Нет пользователей</td></tr>'}
                </table>
            </div>
        </div>
        <script>
            setTimeout(() => location.reload(), 30000);
        </script>
    </body>
    </html>
    """
    
    return web.Response(text=html, content_type='text/html')

async def webapp_search(request):
    """API для поиска через WebApp"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        
        if not user_id:
            return web.json_response({'error': 'No user_id'}, status=400)
        
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                return web.json_response({'error': 'User not registered'}, status=404)
            
            # Проверяем бан
            if check_ban(user_id):
                return web.json_response({'error': 'User banned'}, status=403)
            
            # Ищем партнёра
            partner = conn.execute(
                "SELECT user_id FROM queue WHERE user_id != ? LIMIT 1",
                (user_id,)
            ).fetchone()
            
            if partner:
                partner_id = partner['user_id']
                partner_info = conn.execute("SELECT anon_id FROM users WHERE user_id = ?", (partner_id,)).fetchone()
                
                conn.execute("DELETE FROM queue WHERE user_id = ?", (partner_id,))
                conn.execute(
                    "INSERT INTO dialogs (user1_id, user2_id) VALUES (?, ?)",
                    (user_id, partner_id)
                )
                
                # Уведомляем партнёра в боте
                try:
                    await bot.send_message(
                        partner_id,
                        f"✅ Собеседник найден! (WebApp)\n\n"
                        f"Начните общение с {user['anon_id']}",
                        reply_markup=chat_keyboard()
                    )
                except:
                    pass
                
                return web.json_response({
                    'found': True,
                    'partner_id': partner_id,
                    'partner_name': partner_info['anon_id']
                })
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO queue (user_id, gender, age_group, is_webapp) VALUES (?, ?, ?, 1)",
                    (user_id, user['gender'], user['age_group'])
                )
                return web.json_response({'found': False})
    except Exception as e:
        logger.error(f"WebApp search error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def webapp_send_message(request):
    """API для отправки сообщений из WebApp"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        text = data.get('text', '')
        
        if not user_id or not text:
            return web.json_response({'error': 'Invalid data'}, status=400)
        
        # Проверка на запрещенный контент
        if check_forbidden_content(text):
            apply_ban(user_id, 14, "Запрещённый контент (WebApp)")
            return web.json_response({'error': 'Banned for forbidden content'}, status=403)
        
        partner_id = get_partner_id(user_id)
        if partner_id:
            try:
                await bot.send_message(partner_id, text)
                return web.json_response({'success': True})
            except Exception as e:
                return web.json_response({'error': str(e)}, status=500)
        
        return web.json_response({'error': 'No active dialog'}, status=404)
    except Exception as e:
        logger.error(f"WebApp send message error: {e}")
        return web.json_response({'error': str(e)}, status=500)

async def webapp_check_dialog(request):
    """Проверка статуса диалога"""
    try:
        user_id = int(request.query.get('user_id'))
        partner_id = get_partner_id(user_id)
        
        return web.json_response({
            'active': partner_id is not None,
            'partner_id': partner_id
        })
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def webapp_end_dialog(request):
    """Завершение диалога из WebApp"""
    try:
        data = await request.json()
        user_id = data.get('user_id')
        
        partner_id = get_partner_id(user_id)
        end_dialog(user_id)
        
        if partner_id:
            try:
                await bot.send_message(
                    partner_id,
                    "👋 Собеседник завершил диалог",
                    reply_markup=main_menu()
                )
            except:
                pass
        
        return web.json_response({'success': True})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def init_webapp():
    """Инициализация веб-сервера"""
    app = web.Application()
    app.router.add_get('/webapp', webapp_handler)
    app.router.add_get('/admin', admin_panel_web)
    app.router.add_post('/api/search', webapp_search)
    app.router.add_post('/api/send', webapp_send_message)
    app.router.add_get('/api/check', webapp_check_dialog)
    app.router.add_post('/api/end', webapp_end_dialog)
    return app

# ============ ЗАПУСК ============
async def main():
    # Инициализация БД
    init_db()
    logger.info("Database initialized")
    
    # Подключаем роутер
    dp.include_router(router)
    
    # Запуск веб-сервера
    app = await init_webapp()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"WebApp server started on port {PORT}")
    logger.info(f"WebApp URL: {WEBAPP_URL}/webapp")
    logger.info(f"Admin panel: {WEBAPP_URL}/admin?token={ADMIN_TOKEN}")
    
    # Запуск бота
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
