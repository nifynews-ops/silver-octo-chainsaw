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
    InlineKeyboardButton, WebAppInfo, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
from contextlib import contextmanager
from aiohttp import web

# ============ КОНФИГУРАЦИЯ ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "8611133731:AAHH2x7RJl2_fvRd6QzwoXzgL2f-DMmBVhE")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://web-production-fd96.up.railway.app/webapp")
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
    except Exception as e:
        logger.error(f"Database error: {e}")
        conn.rollback()
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

class Broadcast(StatesGroup):
    message = State()
    
class AdBroadcast(StatesGroup):
    text = State()
    photo = State()
    button_text = State()
    button_url = State()

# ============ УТИЛИТЫ ============
def generate_anon_id():
    animals = ["Пингвин", "Кот", "Лиса", "Волк", "Медведь", "Енот", "Панда", "Тигр", "Дельфин", "Сова"]
    return f"{random.choice(animals)}#{random.randint(1000, 9999)}"

def check_ban(user_id):
    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT ban_until FROM users WHERE user_id = ?", 
                (user_id,)
            ).fetchone()
            if user and user['ban_until']:
                ban_until = datetime.fromisoformat(user['ban_until'])
                if datetime.now() < ban_until:
                    return ban_until
    except Exception as e:
        logger.error(f"Check ban error: {e}")
    return None

def check_forbidden_content(text):
    if not text:
        return False
    patterns = [
        r'@\w+',
        r't\.me/',
        r'https?://',
        r'\b\d{10,}\b',
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
    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT warnings FROM users WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            
            warnings = user['warnings'] if user else 0
            
            conn.execute(
                "UPDATE users SET ban_until = ?, warnings = warnings + 1 WHERE user_id = ?",
                (ban_until.isoformat(), user_id)
            )
            conn.execute(
                "INSERT INTO bans (user_id, reason, banned_until) VALUES (?, ?, ?)",
                (user_id, reason, ban_until.isoformat())
            )
            return ban_until, warnings + 1
    except Exception as e:
        logger.error(f"Apply ban error: {e}")
        return ban_until, 0

def get_partner_id(user_id):
    try:
        with get_db() as conn:
            dialog = conn.execute(
                "SELECT user1_id, user2_id FROM dialogs WHERE (user1_id = ? OR user2_id = ?) AND status = 'active'",
                (user_id, user_id)
            ).fetchone()
            if dialog:
                return dialog['user2_id'] if dialog['user1_id'] == user_id else dialog['user1_id']
    except Exception as e:
        logger.error(f"Get partner error: {e}")
    return None

def end_dialog(user_id):
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE dialogs SET status = 'ended' WHERE (user1_id = ? OR user2_id = ?) AND status = 'active'",
                (user_id, user_id)
            )
    except Exception as e:
        logger.error(f"End dialog error: {e}")

def get_user_info(user_id):
    try:
        with get_db() as conn:
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    except Exception as e:
        logger.error(f"Get user info error: {e}")
        return None

def unban_user(user_id):
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET ban_until = NULL WHERE user_id = ?",
                (user_id,)
            )
            return True
    except Exception as e:
        logger.error(f"Unban error: {e}")
        return False

def get_all_users():
    try:
        with get_db() as conn:
            users = conn.execute("SELECT user_id FROM users").fetchall()
            return [u['user_id'] for u in users]
    except Exception as e:
        logger.error(f"Get all users error: {e}")
        return []

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
        [InlineKeyboardButton(text="👥 Активные диалоги", callback_data="admin_dialogs")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📣 Рассылка рекламы", callback_data="admin_ad_broadcast")],
        [InlineKeyboardButton(text="🔓 Разбанить пользователя", callback_data="admin_unban")]
    ])

def profile_keyboard(user_id):
    ban_until = check_ban(user_id)
    if ban_until:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔓 Подать заявку на разбан", callback_data="request_unban")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
        ])
    else:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
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
            f"Причина: Нарушение правил анонимности\n\n"
            f"Для разбана нажмите 👤 Профиль → 🔓 Подать заявку",
            parse_mode="HTML",
            reply_markup=main_menu()
        )
        return
    
    try:
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
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@router.callback_query(F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    try:
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
    except Exception as e:
        logger.error(f"Process gender error: {e}")
        await callback.answer("Ошибка. Попробуйте снова.")

@router.callback_query(F.data.startswith("age_"))
async def process_age(callback: CallbackQuery, state: FSMContext):
    try:
        age_group = callback.data.split("_")[1]
        data = await state.get_data()
        
        anon_id = generate_anon_id()
        user_id = callback.from_user.id
        
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (user_id, anon_id, gender, age_group, interests) VALUES (?, ?, ?, ?, ?)",
                (user_id, anon_id, data.get('gender', 'other'), age_group, "")
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
    except Exception as e:
        logger.error(f"Process age error: {e}")
        await callback.answer("Ошибка регистрации. Попробуйте /start")

@router.callback_query(F.data == "search_bot")
async def search_bot(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    ban_until = check_ban(user_id)
    if ban_until:
        await callback.answer("Вы заблокированы!", show_alert=True)
        return
    
    if get_partner_id(user_id):
        await callback.answer("Сначала завершите текущий диалог!", show_alert=True)
        return
    
    try:
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            
            if not user:
                await callback.answer("Сначала зарегистрируйтесь через /start", show_alert=True)
                return
            
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
    except Exception as e:
        logger.error(f"Search bot error: {e}")
        await callback.answer("Ошибка поиска. Попробуйте снова.")

@router.callback_query(F.data == "cancel_search")
async def cancel_search(callback: CallbackQuery):
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM queue WHERE user_id = ?", (callback.from_user.id,))
        
        await callback.message.edit_text("❌ Поиск отменён", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Cancel search error: {e}")

@router.message(F.text & ~F.text.startswith('/'))
async def handle_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Проверяем FSM состояния для админ-команд
    current_state = await state.get_state()
    
    # Обработка рассылки
    if current_state == Broadcast.message:
        users = get_all_users()
        success = 0
        fail = 0
        
        for uid in users:
            try:
                await bot.send_message(uid, f"📢 <b>Рассылка:</b>\n\n{message.text}", parse_mode="HTML")
                success += 1
                await asyncio.sleep(0.05)
            except:
                fail += 1
        
        await message.answer(
            f"✅ Рассылка завершена!\n\n"
            f"Отправлено: {success}\n"
            f"Не удалось: {fail}",
            reply_markup=admin_keyboard()
        )
        await state.clear()
        return
    
    # Обработка рекламной рассылки
    if current_state == AdBroadcast.text:
        await state.update_data(ad_text=message.text)
        await message.answer(
            "📸 Теперь отправьте фото для рекламы\n\n"
            "Или отправьте /skip чтобы пропустить"
        )
        await state.set_state(AdBroadcast.photo)
        return
    
    if current_state == AdBroadcast.button_text:
        await state.update_data(button_text=message.text)
        await message.answer("🔗 Теперь отправьте URL для кнопки")
        await state.set_state(AdBroadcast.button_url)
        return
    
    if current_state == AdBroadcast.button_url:
        data = await state.get_data()
        users = get_all_users()
        success = 0
        fail = 0
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=data['button_text'], url=message.text)]
        ])
        
        for uid in users:
            try:
                if data.get('photo_id'):
                    await bot.send_photo(
                        uid,
                        photo=data['photo_id'],
                        caption=f"📣 <b>Реклама:</b>\n\n{data['ad_text']}",
                        reply_markup=kb,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(
                        uid,
                        f"📣 <b>Реклама:</b>\n\n{data['ad_text']}",
                        reply_markup=kb,
                        parse_mode="HTML"
                    )
                success += 1
                await asyncio.sleep(0.05)
            except:
                fail += 1
        
        await message.answer(
            f"✅ Рекламная рассылка завершена!\n\n"
            f"Отправлено: {success}\n"
            f"Не удалось: {fail}",
            reply_markup=admin_keyboard()
        )
        await state.clear()
        return
    
    # Обычная обработка сообщений
    ban_until = check_ban(user_id)
    if ban_until:
        return
    
    if check_forbidden_content(message.text):
        ban_until, warnings = apply_ban(user_id, 14, "Попытка деанонимизации")
        
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
            f"⚠️ Предупреждений: {warnings}/3",
            parse_mode="HTML"
        )
        return
    
    partner_id = get_partner_id(user_id)
    if partner_id:
        try:
            await bot.send_message(partner_id, message.text)
            
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
                "❌ Не удалось отправить сообщение",
                reply_markup=main_menu()
            )
    else:
        await message.answer(
            "❌ Сначала найдите собеседника!",
            reply_markup=main_menu()
        )

@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == AdBroadcast.photo:
        photo_id = message.photo[-1].file_id
        await state.update_data(photo_id=photo_id)
        await message.answer("✏️ Введите текст для кнопки")
        await state.set_state(AdBroadcast.button_text)
        return
    
    await message.answer("❌ Отправка фото в чате не поддерживается")

@router.callback_query(F.data == "end_chat")
async def end_chat(callback: CallbackQuery):
    try:
        user_id = callback.from_user.id
        partner_id = get_partner_id(user_id)
        
        end_dialog(user_id)
        
        await callback.message.edit_text("👋 Диалог завершён", reply_markup=main_menu())
        
        if partner_id:
            try:
                await bot.send_message(
                    partner_id,
                    "👋 Собеседник завершил диалог",
                    reply_markup=main_menu()
                )
            except:
                pass
    except Exception as e:
        logger.error(f"End chat error: {e}")

@router.callback_query(F.data == "next_chat")
async def next_chat(callback: CallbackQuery):
    try:
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
    except Exception as e:
        logger.error(f"Next chat error: {e}")

@router.callback_query(F.data == "report")
async def report_user(callback: CallbackQuery):
    try:
        user_id = callback.from_user.id
        partner_id = get_partner_id(user_id)
        
        if partner_id:
            partner_info = get_user_info(partner_id)
            
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🚨 <b>ЖАЛОБА</b>\n\n"
                        f"От: {user_id}\n"
                        f"На: {partner_id}\n"
                        f"ID: {partner_info['anon_id'] if partner_info else 'N/A'}",
                        parse_mode="HTML"
                    )
                except:
                    pass
            
            await callback.answer("✅ Жалоба отправлена", show_alert=True)
        else:
            await callback.answer("❌ Нет активного диалога", show_alert=True)
    except Exception as e:
        logger.error(f"Report error: {e}")

@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    try:
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (callback.from_user.id,)
            ).fetchone()
            
            if not user:
                await callback.answer("Сначала зарегистрируйтесь через /start", show_alert=True)
                return
            
            dialogs_count = conn.execute(
                "SELECT COUNT(*) FROM dialogs WHERE (user1_id = ? OR user2_id = ?) AND status = 'ended'",
                (callback.from_user.id, callback.from_user.id)
            ).fetchone()[0]
            
            gender_names = {"male": "Мужской 👨", "female": "Женский 👩", "other": "Другой ⚧️"}
            
            ban_until = check_ban(callback.from_user.id)
            ban_text = ""
            if ban_until:
                ban_text = f"\n\n🚫 <b>ЗАБЛОКИРОВАН</b>\nДо: {ban_until.strftime('%d.%m.%Y %H:%M')}"
            
            await callback.message.edit_text(
                f"👤 <b>Ваш профиль</b>\n\n"
                f"🎭 ID: <code>{user['anon_id']}</code>\n"
                f"👥 Пол: {gender_names.get(user['gender'], 'Не указан')}\n"
                f"🎂 Возраст: {user['age_group']}\n"
                f"💬 Завершённых диалогов: {dialogs_count}\n"
                f"⚠️ Предупреждений: {user['warnings']}/3\n"
                f"📅 Регистрация: {user['created_at'][:10]}"
                f"{ban_text}",
                reply_markup=profile_keyboard(callback.from_user.id),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Show profile error: {e}")

@router.callback_query(F.data == "request_unban")
async def request_unban(callback: CallbackQuery):
    try:
        user_id = callback.from_user.id
        user_info = get_user_info(user_id)
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🔓 <b>ЗАЯВКА НА РАЗБАН</b>\n\n"
                    f"От пользователя: {user_id}\n"
                    f"ID: {user_info['anon_id'] if user_info else 'N/A'}\n\n"
                    f"Используйте: /unban {user_id}",
                    parse_mode="HTML"
                )
            except:
                pass
        
        await callback.answer(
            "✅ Заявка на разбан отправлена администраторам!\n"
            "Ожидайте решения.",
            show_alert=True
        )
    except Exception as e:
        logger.error(f"Request unban error: {e}")

@router.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery):
    await callback.message.edit_text("🎭 Главное меню:", reply_markup=main_menu())

@router.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery):
    await callback.message.edit_text(
        "📋 <b>ПРАВИЛА ЧАТА</b>\n\n"
        "🚫 <b>ЗАПРЕЩЕНО:</b>\n"
        "• Называть своё настоящее имя\n"
        "• Писать @username или ссылки\n"
        "• Делиться номерами телефонов\n"
        "• Оскорбления и спам\n\n"
        "⚖️ <b>НАКАЗАНИЯ:</b>\n"
        "1️⃣ Первое → Бан 3 дня\n"
        "2️⃣ Второе → Бан 2 недели\n"
        "3️⃣ Третье → Перманент",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
        ]),
        parse_mode="HTML"
    )

# ============ АДМИН ============
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
    
    try:
        with get_db() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active_dialogs = conn.execute("SELECT COUNT(*) FROM dialogs WHERE status = 'active'").fetchone()[0]
            total_bans = conn.execute("SELECT COUNT(*) FROM bans").fetchone()[0]
            in_queue = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
            
            await callback.message.edit_text(
                f"📊 <b>СТАТИСТИКА</b>\n\n"
                f"👥 Пользователей: {total_users}\n"
                f"💬 Диалогов: {active_dialogs}\n"
                f"🚫 Банов: {total_bans}\n"
                f"⏳ В очереди: {in_queue}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
                ]),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Admin stats error: {e}")

@router.callback_query(F.data == "admin_bans")
async def admin_bans_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    try:
        with get_db() as conn:
            bans = conn.execute("""
                SELECT u.anon_id, b.reason, b.banned_until
                FROM bans b
                JOIN users u ON b.user_id = u.user_id
                ORDER BY b.banned_at DESC
                LIMIT 10
            """).fetchall()
            
            text = "📝 <b>ПОСЛЕДНИЕ БАНЫ</b>\n\n"
            
            if bans:
                for ban in bans:
                    text += f"• {ban[0]}\n  {ban[1]}\n  До: {ban[2][:16] if ban[2] else 'N/A'}\n\n"
            else:
                text += "Нет банов"
            
            await callback.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
                ]),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Admin bans error: {e}")

@router.callback_query(F.data == "admin_dialogs")
async def admin_dialogs_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    try:
        with get_db() as conn:
            dialogs = conn.execute("""
                SELECT u1.anon_id, u2.anon_id
                FROM dialogs d
                JOIN users u1 ON d.user1_id = u1.user_id
                JOIN users u2 ON d.user2_id = u2.user_id
                WHERE d.status = 'active'
                LIMIT 10
            """).fetchall()
            
            text = "👥 <b>АКТИВНЫЕ ДИАЛОГИ</b>\n\n"
            
            if dialogs:
                for dialog in dialogs:
                    text += f"• {dialog[0]} ↔️ {dialog[1]}\n"
            else:
                text += "Нет активных диалогов"
            
            await callback.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
                ]),
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Admin dialogs error: {e}")

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.message.edit_text(
        "📢 <b>РАССЫЛКА</b>\n\n"
        "Отправьте текст сообщения для рассылки всем пользователям:"
    )
    await state.set_state(Broadcast.message)

@router.callback_query(F.data == "admin_ad_broadcast")
async def admin_ad_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.message.edit_text(
        "📣 <b>РЕКЛАМНАЯ РАССЫЛКА</b>\n\n"
        "Отправьте текст рекламы:"
    )
    await state.set_state(AdBroadcast.text)

@router.callback_query(F.data == "admin_unban")
async def admin_unban_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    
    await callback.message.edit_text(
        "🔓 <b>РАЗБАНИТЬ ПОЛЬЗОВАТЕЛЯ</b>\n\n"
        "Используйте команду:\n"
        "<code>/unban [user_id]</code>\n\n"
        "Пример: <code>/unban 123456789</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
        ]),
        parse_mode="HTML"
    )

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /unban [user_id]")
            return
        
        user_id = int(args[1])
        
        if unban_user(user_id):
            await message.answer(f"✅ Пользователь {user_id} разбанен")
            
            try:
                await bot.send_message(
                    user_id,
                    "🎉 <b>Вы были разбанены!</b>\n\n"
                    "Теперь вы можете пользоваться ботом.\n"
                    "Не нарушайте правила!",
                    parse_mode="HTML",
                    reply_markup=main_menu()
                )
            except:
                pass
        else:
            await message.answer("❌ Ошибка разбана")
    except Exception as e:
        logger.error(f"Unban command error: {e}")
        await message.answer(f"❌ Ошибка: {e}")

@router.message(Command("skip"))
async def cmd_skip(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state == AdBroadcast.photo:
        await message.answer("✏️ Введите текст для кнопки")
        await state.set_state(AdBroadcast.button_text)

@router.callback_query(F.data == "back_admin")
async def back_admin(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔐 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        "Выберите действие:",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )

# ============ WEBAPP ============
WEBAPP_HTML = '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Анонимный чат</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, sans-serif; background: #fff; height: 100vh; overflow: hidden; }
        #app { display: flex; flex-direction: column; height: 100vh; }
        #search-screen { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        .search-animation { width: 100px; height: 100px; border: 5px solid rgba(255,255,255,0.3); border-top-color: white; border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .search-text { margin-top: 30px; font-size: 24px; font-weight: 500; }
        .search-subtext { margin-top: 10px; font-size: 14px; opacity: 0.8; }
        #chat-screen { display: none; flex-direction: column; height: 100vh; }
        .chat-header { background: #f8f9fa; padding: 15px 20px; border-bottom: 1px solid #e0e0e0; }
        .chat-header h2 { font-size: 18px; color: #333; margin-bottom: 5px; }
        .chat-status { font-size: 12px; color: #28a745; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 20px; background: #fff; }
        .message { margin-bottom: 15px; animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .message.sent { text-align: right; }
        .message-bubble { display: inline-block; max-width: 70%; padding: 12px 16px; border-radius: 18px; word-wrap: break-word; }
        .message.received .message-bubble { background: #f0f0f0; color: #333; border-bottom-left-radius: 4px; }
        .message.sent .message-bubble { background: #007bff; color: white; border-bottom-right-radius: 4px; }
        .message-time { font-size: 11px; color: #999; margin-top: 5px; }
        .action-buttons { display: flex; gap: 10px; padding: 10px 20px; background: #f8f9fa; border-top: 1px solid #e0e0e0; }
        .action-btn { flex: 1; padding: 10px; border: 1px solid #ddd; background: white; border-radius: 8px; font-size: 14px; cursor: pointer; }
        .action-btn:active { background: #e0e0e0; }
        .action-btn.danger { color: #dc3545; border-color: #dc3545; }
        .chat-input-container { padding: 15px; background: #f8f9fa; border-top: 1px solid #e0e0e0; display: flex; gap: 10px; }
        #message-input { flex: 1; padding: 12px 16px; border: 1px solid #ddd; border-radius: 24px; font-size: 15px; outline: none; }
        #message-input:focus { border-color: #007bff; }
        #send-btn { width: 48px; height: 48px; border: none; background: #007bff; color: white; border-radius: 50%; font-size: 20px; cursor: pointer; }
        #send-btn:active { background: #0056b3; }
        .system-message { text-align: center; color: #999; font-size: 13px; padding: 10px; margin: 10px 0; }
    </style>
</head>
<body>
    <div id="app">
        <div id="search-screen">
            <div class="search-animation"></div>
            <div class="search-text">Ищем собеседника...</div>
            <div class="search-subtext">Это может занять время</div>
        </div>
        <div id="chat-screen">
            <div class="chat-header">
                <h2 id="partner-name">Собеседник</h2>
                <div class="chat-status">● Онлайн</div>
            </div>
            <div class="chat-messages" id="messages">
                <div class="system-message">🎭 Диалог начат. Соблюдайте правила!</div>
            </div>
            <div class="action-buttons">
                <button class="action-btn" onclick="nextChat()">⏭ Следующий</button>
                <button class="action-btn danger" onclick="endChat()">❌ Завершить</button>
            </div>
            <div class="chat-input-container">
                <input type="text" id="message-input" placeholder="Введите сообщение..." onkeypress="handleKeyPress(event)">
                <button id="send-btn" onclick="sendMessage()">▶</button>
            </div>
        </div>
    </div>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        tg.enableClosingConfirmation();
        const userId = tg.initDataUnsafe?.user?.id || Math.floor(Math.random() * 1000000);
        const API_URL = window.location.origin;
        let partnerId = null;
        let partnerName = '';
        let checkInterval = null;

        async function startSearch() {
            try {
                const response = await fetch(`${API_URL}/api/search`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId })
                });
                const data = await response.json();
                if (response.status === 403) { tg.showAlert('Вы заблокированы!'); tg.close(); return; }
                if (data.found) {
                    partnerId = data.partner_id;
                    partnerName = data.partner_name || 'Анонимный собеседник';
                    showChatScreen();
                } else {
                    setTimeout(startSearch, 2000);
                }
            } catch (error) {
                console.error('Search error:', error);
                setTimeout(startSearch, 3000);
            }
        }

        function showChatScreen() {
            document.getElementById('search-screen').style.display = 'none';
            document.getElementById('chat-screen').style.display = 'flex';
            document.getElementById('partner-name').textContent = partnerName;
            document.getElementById('message-input').focus();
            checkInterval = setInterval(checkDialogStatus, 3000);
        }

        async function checkDialogStatus() {
            try {
                const response = await fetch(`${API_URL}/api/check?user_id=${userId}`);
                const data = await response.json();
                if (!data.active) {
                    clearInterval(checkInterval);
                    addSystemMessage('Собеседник покинул чат');
                    setTimeout(() => tg.close(), 2000);
                }
            } catch (error) {
                console.error('Check status error:', error);
            }
        }

        async function sendMessage() {
            const input = document.getElementById('message-input');
            const text = input.value.trim();
            if (!text) return;
            addMessage(text, true);
            input.value = '';
            try {
                const response = await fetch(`${API_URL}/api/send`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId, text: text })
                });
                if (response.status === 403) {
                    tg.showAlert('Вы заблокированы за нарушение правил!');
                    setTimeout(() => tg.close(), 1000);
                }
                if (!response.ok) addSystemMessage('Не удалось отправить');
            } catch (error) {
                console.error('Send error:', error);
                addSystemMessage('Ошибка отправки');
            }
        }

        function addMessage(text, isSent) {
            const messagesDiv = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${isSent ? 'sent' : 'received'}`;
            const now = new Date();
            const time = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0');
            messageDiv.innerHTML = `<div class="message-bubble">${escapeHtml(text)}</div><div class="message-time">${time}</div>`;
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        function addSystemMessage(text) {
            const messagesDiv = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'system-message';
            messageDiv.textContent = text;
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function handleKeyPress(event) {
            if (event.key === 'Enter') sendMessage();
        }

        async function nextChat() {
            if (confirm('Начать поиск нового собеседника?')) {
                await endDialogAPI();
                location.reload();
            }
        }

        async function endChat() {
            if (confirm('Завершить диалог?')) {
                await endDialogAPI();
                tg.close();
            }
        }

        async function endDialogAPI() {
            try {
                await fetch(`${API_URL}/api/end`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ user_id: userId })
                });
            } catch (error) {
                console.error('End dialog error:', error);
            }
        }

        startSearch();
        window.addEventListener('beforeunload', () => {
            if (checkInterval) clearInterval(checkInterval);
        });
    </script>
</body>
</html>'''

async def webapp_handler(request):
    return web.Response(text=WEBAPP_HTML, content_type='text/html')

async def admin_panel_web(request):
    admin_token = request.query.get('token')
    if admin_token != ADMIN_TOKEN:
        return web.Response(text='<h1>Access Denied</h1><p>Invalid admin token</p>', content_type='text/html', status=403)
    
    try:
        with get_db() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active_dialogs = conn.execute("SELECT COUNT(*) FROM dialogs WHERE status = 'active'").fetchone()[0]
            total_bans = conn.execute("SELECT COUNT(*) FROM bans").fetchone()[0]
            in_queue = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        
        html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Admin Panel</title>
<style>
* {{margin:0;padding:0;box-sizing:border-box;}}
body {{font-family:-apple-system,sans-serif;background:#f0f2f5;padding:20px;}}
.container {{max-width:1200px;margin:0 auto;}}
.header {{background:#fff;padding:30px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);margin-bottom:30px;}}
h1 {{color:#1a1a1a;margin-bottom:10px;}}
.stats {{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin-bottom:30px;}}
.stat-card {{background:#fff;padding:25px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);border-left:4px solid;}}
.stat-card:nth-child(1) {{border-left-color:#667eea;}}
.stat-card:nth-child(2) {{border-left-color:#28a745;}}
.stat-card:nth-child(3) {{border-left-color:#dc3545;}}
.stat-card:nth-child(4) {{border-left-color:#ffc107;}}
.stat-card h3 {{font-size:13px;color:#666;text-transform:uppercase;margin-bottom:10px;}}
.stat-card .number {{font-size:36px;font-weight:bold;color:#1a1a1a;}}
.refresh-btn {{background:#667eea;color:white;padding:12px 24px;border:none;border-radius:8px;cursor:pointer;font-weight:500;}}
.refresh-btn:hover {{background:#5568d3;}}
</style></head><body>
<div class="container">
    <div class="header">
        <h1>🔐 Админ-панель</h1>
        <button class="refresh-btn" onclick="location.reload()">🔄 Обновить</button>
    </div>
    <div class="stats">
        <div class="stat-card"><h3>👥 Пользователей</h3><div class="number">{total_users}</div></div>
        <div class="stat-card"><h3>💬 Диалогов</h3><div class="number">{active_dialogs}</div></div>
        <div class="stat-card"><h3>🚫 Банов</h3><div class="number">{total_bans}</div></div>
        <div class="stat-card"><h3>⏳ В очереди</h3><div class="number">{in_queue}</div></div>
    </div>
</div>
<script>setTimeout(()=>location.reload(),30000);</script>
</body></html>'''
        
        return web.Response(text=html, content_type='text/html')
    except Exception as e:
        logger.error(f"Admin panel web error: {e}")
        return web.Response(text='<h1>Error</h1>', status=500)

async def webapp_search(request):
    try:
        data = await request.json()
        user_id = data.get('user_id')
        
        if not user_id:
            return web.json_response({'error': 'No user_id'}, status=400)
        
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                return web.json_response({'error': 'User not registered'}, status=404)
            
            if check_ban(user_id):
                return web.json_response({'error': 'User banned'}, status=403)
            
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
                
                try:
                    await bot.send_message(
                        partner_id,
                        f"✅ Собеседник найден! (WebApp)\n\nНачните общение с {user['anon_id']}",
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
    try:
        data = await request.json()
        user_id = data.get('user_id')
        text = data.get('text', '')
        
        if not user_id or not text:
            return web.json_response({'error': 'Invalid data'}, status=400)
        
        if check_forbidden_content(text):
            apply_ban(user_id, 14, "Запрещённый контент (WebApp)")
            return web.json_response({'error': 'Banned'}, status=403)
        
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
    try:
        user_id = int(request.query.get('user_id'))
        partner_id = get_partner_id(user_id)
        return web.json_response({'active': partner_id is not None, 'partner_id': partner_id})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def webapp_end_dialog(request):
    try:
        data = await request.json()
        user_id = data.get('user_id')
        partner_id = get_partner_id(user_id)
        end_dialog(user_id)
        
        if partner_id:
            try:
                await bot.send_message(partner_id, "👋 Собеседник завершил диалог", reply_markup=main_menu())
            except:
                pass
        
        return web.json_response({'success': True})
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500)

async def init_webapp():
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
    try:
        init_db()
        logger.info("✅ Database initialized")
        
        dp.include_router(router)
        
        app = await init_webapp()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        
        logger.info(f"✅ Server started on port {PORT}")
        logger.info(f"✅ WebApp: {WEBAPP_URL}/webapp")
        logger.info(f"✅ Admin: {WEBAPP_URL}/admin?token={ADMIN_TOKEN}")
        
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Startup error: {e}")
