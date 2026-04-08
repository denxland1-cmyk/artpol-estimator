"""
ARTPOL — Бот-замерщик (@artpol_zamer_bot)
Пошаговая форма замера → готовое сообщение в группу "Замеры АРТПОЛ".
Менеджер копирует → вставляет в бот-сметчик.
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, InputMediaPhoto,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "0"))

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ============================================================
# FSM — шаги формы
# ============================================================

STEPS = [
    "client_name",      # 0
    "client_phone",     # 1
    "object_type",      # 2  кнопки
    "city_or_oblast",   # 3  кнопки
    "address",          # 4
    "entrance",         # 4a (только для квартир)
    "apartment_num",    # 4b (только для квартир)
    "coordinates",      # 5
    "floor",            # 6
    "area",             # 7
    "thickness",        # 8
    "base_type",        # 9  кнопки
    "keramzit",         # 10 кнопки → если да: площадь + толщина
    "keramzit_area",    # 11
    "keramzit_thick",   # 12
    "mesh",             # 13 кнопки → если да: площадь мат. + площадь работы
    "mesh_material",    # 14
    "mesh_work",        # 15
    "sand_removal",     # 16 кнопки
    "extra_work",       # 17 текст / пропустить
    "deadline",         # 18 текст / пропустить
    "photos",           # 19 фото (необязательно, сколько угодно)
    "confirm",          # 20 подтверждение
]

PROMPTS = {
    "client_name":    "👤 <b>Шаг 1/16</b>\nВведи <b>имя заказчика</b>:",
    "client_phone":   "📞 <b>Шаг 2/16</b>\nВведи <b>телефон заказчика</b>:",
    "object_type":    "🏠 <b>Шаг 3/16</b>\nТип объекта:",
    "city_or_oblast": "📍 <b>Шаг 4/16</b>\nГород или за городом:",
    "address":        "🏘 <b>Шаг 5/18</b>\nВведи <b>адрес объекта</b>:\n(населённый пункт, улица, дом)",
    "entrance":       "🚪 <b>Шаг 5.1/18</b>\nВведи <b>номер подъезда</b>:",
    "apartment_num":  "🏠 <b>Шаг 5.2/18</b>\nВведи <b>номер квартиры</b>:",
    "coordinates":    "🗺 <b>Шаг 6/18</b>\nВведи <b>координаты объекта</b>:\n(скопируй из Яндекс.Карт, например: 56.310043, 43.953282)",
    "floor":          "🏢 <b>Шаг 7/16</b>\nВведи <b>этаж</b>:\n(число)",
    "area":           "📐 <b>Шаг 8/16</b>\nВведи <b>площадь</b> (м²):\n(число, например: 63 или 63.5)",
    "thickness":      "📏 <b>Шаг 9/16</b>\nВведи <b>средний слой</b> (мм):\n(число, например: 90 или 90.5)",
    "base_type":      "🧱 <b>Шаг 10/16</b>\nОснование под заливку:",
    "keramzit":       "🟤 <b>Шаг 11/16</b>\nКерамзит:",
    "keramzit_area":  "🟤 Площадь керамзита (м²):",
    "keramzit_thick": "🟤 Толщина слоя керамзита (мм):",
    "mesh":           "🔲 <b>Шаг 12/16</b>\nСетка + арм. плёнка (без керамзита):",
    "mesh_material":  "🔲 Площадь материала сетки (м²):",
    "mesh_work":      "🔲 Площадь работы по укладке сетки (м²):",
    "sand_removal":   "🚛 <b>Шаг 13/16</b>\nВывоз остатков песка:",
    "extra_work":     "🔧 <b>Шаг 14/16</b>\nДоп. работы:\n(уборка от заказчика, запенивание дыр, уборка от нас и т.д.)\nИли нажми <b>Пропустить</b>",
    "deadline":       "📅 <b>Шаг 15/16</b>\nСроки (примерно):\nИли нажми <b>Пропустить</b>",
    "photos":         "📸 <b>Шаг 16/16</b>\nОтправь <b>фото объекта</b> (сколько угодно).\nКогда закончишь — нажми <b>Готово</b>.\nИли нажми <b>Пропустить</b> если фото нет.",
}

# Хранилище состояний {user_id: {"step": str, "data": dict}}
user_state = {}

# ============================================================
# НУМЕРАЦИЯ ЗАМЕРОВ
# ============================================================

# Замерщики: Telegram user_id → короткое имя
# ⚠️ ВСТАВЬ РЕАЛЬНЫЕ Telegram ID замерщиков
SURVEYORS = {
    1912847671: "Дима",
    1331894090: "Кирилл",
    5225680928: "Володя",
}

# Путь к счётчикам: Railway Volume (/data) или рядом с ботом
COUNTERS_DIR = Path(os.environ.get("COUNTERS_DIR", str(Path(__file__).parent)))
COUNTERS_FILE = COUNTERS_DIR / "counters.json"

def load_counters() -> dict:
    """Загружает счётчики из JSON-файла."""
    if COUNTERS_FILE.exists():
        try:
            with open(COUNTERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.error("Ошибка чтения counters.json, создаю заново")
    # Начальные значения (с учётом замера Димы №28/№7 от 08.04.2026)
    return {
        "global_next": 29,
        "global_year": 2026,
        "surveyors": {
            "Дима":    {"next": 8,  "month": 4, "year": 2026},
            "Володя":  {"next": 15, "month": 4, "year": 2026},
            "Кирилл":  {"next": 8,  "month": 4, "year": 2026},
        }
    }

def save_counters(counters: dict):
    """Сохраняет счётчики в JSON-файл."""
    with open(COUNTERS_FILE, "w", encoding="utf-8") as f:
        json.dump(counters, f, ensure_ascii=False, indent=2)

def get_next_numbers(surveyor_name: str) -> tuple[int, int]:
    """
    Возвращает (общий_номер, личный_номер) и увеличивает счётчики.
    Общий — сбрасывается 1 января.
    Личный — сбрасывается 1-го числа каждого месяца.
    """
    counters = load_counters()
    now = datetime.now()

    # Сброс общего счётчика на новый год
    if counters.get("global_year", now.year) < now.year:
        counters["global_next"] = 1
        counters["global_year"] = now.year

    global_num = counters["global_next"]
    counters["global_next"] = global_num + 1
    counters["global_year"] = now.year

    # Личный счётчик замерщика
    if surveyor_name not in counters["surveyors"]:
        counters["surveyors"][surveyor_name] = {
            "next": 1, "month": now.month, "year": now.year
        }

    sv = counters["surveyors"][surveyor_name]

    # Сброс личного счётчика на новый месяц
    if sv.get("year", now.year) < now.year or sv.get("month", now.month) < now.month:
        sv["next"] = 1
        sv["month"] = now.month
        sv["year"] = now.year

    personal_num = sv["next"]
    sv["next"] = personal_num + 1
    sv["month"] = now.month
    sv["year"] = now.year

    save_counters(counters)
    return global_num, personal_num

# ============================================================
# КЛАВИАТУРЫ
# ============================================================

def kb_object_type():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏢 Квартира", callback_data="obj_квартира"),
         InlineKeyboardButton(text="🏠 Дом", callback_data="obj_дом"),
         InlineKeyboardButton(text="🏭 Коммерция", callback_data="obj_коммерция")],
    ])

def kb_city():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙 Город", callback_data="loc_город"),
         InlineKeyboardButton(text="🌲 За городом", callback_data="loc_за городом")],
    ])

def kb_base():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ЖБ плиты", callback_data="base_ЖБ плиты"),
         InlineKeyboardButton(text="Грунт", callback_data="base_Грунт")],
        [InlineKeyboardButton(text="Тёплые полы", callback_data="base_Тёплые полы"),
         InlineKeyboardButton(text="Другое", callback_data="base_Другое")],
    ])

def kb_yes_no(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}_yes"),
         InlineKeyboardButton(text="❌ Нет", callback_data=f"{prefix}_no")],
    ])

def kb_sand():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Есть", callback_data="sand_yes"),
         InlineKeyboardButton(text="❌ Нет", callback_data="sand_no")],
    ])

def kb_skip():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip")],
    ])

def kb_confirm():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="send"),
         InlineKeyboardButton(text="🔄 Заново", callback_data="restart")],
    ])

def kb_photos():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="photos_done"),
         InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip")],
    ])

def kb_cancel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


# ============================================================
# ВАЛИДАЦИЯ
# ============================================================

def validate_phone(text: str) -> str | None:
    """Возвращает очищенный телефон или None."""
    clean = re.sub(r"[\s\-\(\)]", "", text)
    if re.match(r"^\+?\d{10,15}$", clean):
        return clean
    return None

def validate_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None

def validate_float(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None

def validate_coordinates(text: str) -> str | None:
    """Парсит координаты из текста. Принимает: '56.31, 43.95' или '56.31 43.95'."""
    clean = text.strip().replace(",", " ").replace(";", " ")
    parts = clean.split()
    if len(parts) >= 2:
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            if 40 < lat < 70 and 30 < lon < 60:
                return f"{lat}, {lon}"
        except ValueError:
            pass
    return None


# ============================================================
# НАВИГАЦИЯ
# ============================================================

def get_state(user_id: int) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {"step": "client_name", "data": {}}
    return user_state[user_id]


async def send_step(chat_id: int, step: str):
    """Отправляет сообщение для текущего шага."""
    prompt = PROMPTS.get(step, "")
    if not prompt:
        return

    if step == "object_type":
        await bot.send_message(chat_id, prompt, reply_markup=kb_object_type())
    elif step == "city_or_oblast":
        await bot.send_message(chat_id, prompt, reply_markup=kb_city())
    elif step == "base_type":
        await bot.send_message(chat_id, prompt, reply_markup=kb_base())
    elif step == "keramzit":
        await bot.send_message(chat_id, prompt, reply_markup=kb_yes_no("ker"))
    elif step == "mesh":
        await bot.send_message(chat_id, prompt, reply_markup=kb_yes_no("mesh"))
    elif step == "sand_removal":
        await bot.send_message(chat_id, prompt, reply_markup=kb_sand())
    elif step == "photos":
        await bot.send_message(chat_id, prompt, reply_markup=kb_photos())
    elif step in ("extra_work", "deadline"):
        await bot.send_message(chat_id, prompt, reply_markup=kb_skip())
    else:
        await bot.send_message(chat_id, prompt, reply_markup=kb_cancel())


def next_step(st: dict) -> str:
    """Определяет следующий шаг FSM."""
    current = st["step"]
    data = st["data"]

    order = [
        "client_name", "client_phone", "object_type", "city_or_oblast",
        "address", "coordinates", "floor", "area", "thickness", "base_type",
        "keramzit",
    ]

    # Квартира: после адреса → подъезд → номер квартиры → координаты
    if current == "address":
        if data.get("object_type") == "квартира":
            return "entrance"
        return "coordinates"
    if current == "entrance":
        return "apartment_num"
    if current == "apartment_num":
        return "coordinates"

    # Керамзит: да → площадь + толщина, нет → пропускаем
    if current == "keramzit":
        if data.get("keramzit") == "yes":
            return "keramzit_area"
        else:
            return "mesh"
    if current == "keramzit_area":
        return "keramzit_thick"
    if current == "keramzit_thick":
        return "mesh"

    # Сетка: да → площади, нет → пропускаем
    if current == "mesh":
        if data.get("mesh") == "yes":
            return "mesh_material"
        else:
            return "sand_removal"
    if current == "mesh_material":
        return "mesh_work"
    if current == "mesh_work":
        return "sand_removal"

    if current == "sand_removal":
        return "extra_work"
    if current == "extra_work":
        return "deadline"
    if current == "deadline":
        return "photos"
    if current == "photos":
        return "confirm"

    # Обычный порядок
    idx = order.index(current) if current in order else -1
    if idx >= 0 and idx < len(order) - 1:
        return order[idx + 1]

    return "confirm"


def format_result(data: dict, global_num: int = None, personal_num: int = None,
                   surveyor_name: str = None) -> str:
    """Формирует готовое сообщение замера для группы."""
    lines = []

    # Нумерация
    if global_num is not None:
        num_line = f"Замер №{global_num}"
        if surveyor_name and personal_num is not None:
            num_line += f" ({surveyor_name} №{personal_num})"
        lines.append(num_line)

    lines.append(f"{data.get('client_name', '')} {data.get('client_phone', '')}")

    obj = data.get("object_type", "")
    loc = data.get("city_or_oblast", "")
    floor = data.get("floor", "")
    lines.append(f"{obj}, {loc}, {floor} этаж")

    addr = data.get("address", "")
    if data.get("object_type") == "квартира" and data.get("entrance") and data.get("apartment_num"):
        addr += f", подъезд {data['entrance']}, кв. {data['apartment_num']}"
    lines.append(addr)
    lines.append(f"Координаты: {data.get('coordinates', '')}")

    lines.append(f"{data.get('area', '')}м2 {data.get('thickness', '')}мм")
    lines.append(f"Основание: {data.get('base_type', '')}")

    if data.get("keramzit") == "yes":
        lines.append(f"Керамзит: {data.get('keramzit_area', '')}м2, слой {data.get('keramzit_thick', '')}мм")

    if data.get("mesh") == "yes":
        lines.append(f"Сетка: материал {data.get('mesh_material', '')}м2, работа {data.get('mesh_work', '')}м2")

    if data.get("sand_removal") == "yes":
        lines.append("Вывоз песка")

    if data.get("extra_work"):
        lines.append(f"Доп: {data['extra_work']}")

    if data.get("deadline"):
        lines.append(f"Сроки: {data['deadline']}")

    return "\n".join(lines)


# ============================================================
# ОБРАБОТЧИКИ
# ============================================================

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    user_state[message.from_user.id] = {"step": "client_name", "data": {}}
    await message.answer(
        "👋 <b>ARTPOL — Форма замера</b>\n\n"
        "Заполни все поля — готовое сообщение уйдёт в группу менеджеров.\n"
        "Для отмены: /cancel\n\n"
        "Поехали! 👇"
    )
    await send_step(message.chat.id, "client_name")


@dp.message(F.text == "/cancel")
async def cmd_cancel(message: Message):
    user_state.pop(message.from_user.id, None)
    await message.answer("❌ Форма отменена. Нажми /start чтобы начать заново.")


# --- Кнопки выбора ---

@dp.callback_query(F.data == "cancel")
async def on_cancel(callback: CallbackQuery):
    user_state.pop(callback.from_user.id, None)
    await callback.message.answer("❌ Форма отменена. Нажми /start чтобы начать заново.")
    await callback.answer()


@dp.callback_query(F.data == "restart")
async def on_restart(callback: CallbackQuery):
    user_state[callback.from_user.id] = {"step": "client_name", "data": {}}
    await send_step(callback.message.chat.id, "client_name")
    await callback.answer()


@dp.callback_query(F.data == "skip")
async def on_skip(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    # Пропускаем текущий шаг
    st["step"] = next_step(st)

    if st["step"] == "confirm":
        await show_confirm(callback.message.chat.id, st["data"])
    else:
        await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data.startswith("obj_"))
async def on_object_type(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    st["data"]["object_type"] = callback.data.replace("obj_", "")
    st["step"] = next_step(st)
    await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data.startswith("loc_"))
async def on_location(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    st["data"]["city_or_oblast"] = callback.data.replace("loc_", "")
    st["step"] = next_step(st)
    await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data.startswith("base_"))
async def on_base(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    st["data"]["base_type"] = callback.data.replace("base_", "")
    st["step"] = next_step(st)
    await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data.startswith("ker_"))
async def on_keramzit(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    st["data"]["keramzit"] = "yes" if callback.data == "ker_yes" else "no"
    st["step"] = next_step(st)
    await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data.startswith("mesh_"))
async def on_mesh(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    if callback.data in ("mesh_yes", "mesh_no"):
        st["data"]["mesh"] = "yes" if callback.data == "mesh_yes" else "no"
        st["step"] = next_step(st)
        await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data.startswith("sand_"))
async def on_sand(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    st["data"]["sand_removal"] = "yes" if callback.data == "sand_yes" else "no"
    st["step"] = next_step(st)
    await send_step(callback.message.chat.id, st["step"])
    await callback.answer()


@dp.callback_query(F.data == "photos_done")
async def on_photos_done(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    st["step"] = "confirm"
    await show_confirm(callback.message.chat.id, st["data"])
    await callback.answer()


@dp.callback_query(F.data == "send")
async def on_send(callback: CallbackQuery):
    st = get_state(callback.from_user.id)
    data = st["data"]

    # Определяем замерщика и присваиваем номера
    user_id = callback.from_user.id
    surveyor_name = SURVEYORS.get(user_id, "Володя")
    global_num, personal_num = get_next_numbers(surveyor_name)

    result = format_result(data, global_num, personal_num, surveyor_name)

    if GROUP_CHAT_ID:
        group_msg = f"📋 <b>Новый замер от {surveyor_name}</b>\n\n{result}"
        try:
            await bot.send_message(GROUP_CHAT_ID, group_msg)

            # Отправляем фото в группу
            photos = data.get("photos", [])
            if photos:
                # Отправляем альбомом (до 10 фото за раз)
                for i in range(0, len(photos), 10):
                    batch = photos[i:i+10]
                    media = [InputMediaPhoto(media=fid) for fid in batch]
                    await bot.send_media_group(GROUP_CHAT_ID, media)

            await callback.message.answer("✅ <b>Замер отправлен в группу!</b>\n\nНажми /start для нового замера.")
        except Exception as e:
            logger.error("Ошибка отправки в группу: %s", e)
            await callback.message.answer(
                f"❌ Не удалось отправить в группу. Скопируй вручную:\n\n<code>{result}</code>",
            )
    else:
        await callback.message.answer(
            f"⚠️ Группа не настроена. Скопируй:\n\n<code>{result}</code>",
        )

    user_state.pop(callback.from_user.id, None)
    await callback.answer()


# --- Приём фото ---

# Таймеры для сбора альбомов {user_id: asyncio.Task}
_photo_timers: dict[int, asyncio.Task] = {}

async def _photo_batch_done(user_id: int, chat_id: int):
    """Вызывается через 1.5 сек после последнего фото — показывает итог."""
    await asyncio.sleep(1.5)
    st = get_state(user_id)
    count = len(st["data"].get("photos", []))
    await bot.send_message(
        chat_id,
        f"✅ Принято фото: {count} шт.\nОтправь ещё или нажми <b>Готово</b>.",
        reply_markup=kb_photos()
    )
    _photo_timers.pop(user_id, None)

@dp.message(F.photo)
async def on_photo(message: Message):
    st = get_state(message.from_user.id)
    if st["step"] != "photos":
        await message.answer("📸 Фото можно отправить на шаге загрузки фото.")
        return

    user_id = message.from_user.id

    # Сохраняем file_id самого большого размера
    if "photos" not in st["data"]:
        st["data"]["photos"] = []
    st["data"]["photos"].append(message.photo[-1].file_id)

    # Сбрасываем таймер — ждём ещё фото из альбома
    if user_id in _photo_timers:
        _photo_timers[user_id].cancel()
    _photo_timers[user_id] = asyncio.create_task(
        _photo_batch_done(user_id, message.chat.id)
    )


# --- Ввод текста ---

@dp.message()
async def on_text(message: Message):
    st = get_state(message.from_user.id)
    step = st["step"]
    text = (message.text or "").strip()

    if not text:
        await message.answer("❌ Введи данные текстом.")
        return

    if step == "photos":
        await message.answer("📸 Отправь фото или нажми кнопку выше.", reply_markup=kb_photos())
        return

    if step == "confirm":
        await message.answer("👆 Нажми кнопку выше: Отправить или Заново.")
        return

    # Валидация по типу шага
    if step == "client_name":
        st["data"]["client_name"] = text

    elif step == "client_phone":
        phone = validate_phone(text)
        if not phone:
            await message.answer("❌ Неверный формат телефона. Введи номер (например: +79030407152):")
            return
        st["data"]["client_phone"] = phone

    elif step == "address":
        st["data"]["address"] = text

    elif step == "entrance":
        val = validate_int(text)
        if val is None or val <= 0:
            await message.answer("❌ Введи номер подъезда (число):")
            return
        st["data"]["entrance"] = val

    elif step == "apartment_num":
        val = validate_int(text)
        if val is None or val <= 0:
            await message.answer("❌ Введи номер квартиры (число):")
            return
        st["data"]["apartment_num"] = val

    elif step == "coordinates":
        coords = validate_coordinates(text)
        if not coords:
            await message.answer("❌ Не распознал координаты. Введи в формате: 56.310043, 43.953282")
            return
        st["data"]["coordinates"] = coords

    elif step == "floor":
        val = validate_int(text)
        if val is None or val < 0 or val > 100:
            await message.answer("❌ Введи этаж (число от 0 до 100):")
            return
        st["data"]["floor"] = val

    elif step == "area":
        val = validate_float(text)
        if val is None or val <= 0 or val > 10000:
            await message.answer("❌ Введи площадь (число, например: 63 или 63.5):")
            return
        st["data"]["area"] = val

    elif step == "thickness":
        val = validate_float(text)
        if val is None or val <= 0 or val > 500:
            await message.answer("❌ Введи средний слой в мм (число, например: 90):")
            return
        st["data"]["thickness"] = val

    elif step == "keramzit_area":
        val = validate_float(text)
        if val is None or val <= 0:
            await message.answer("❌ Введи площадь керамзита (число):")
            return
        st["data"]["keramzit_area"] = val

    elif step == "keramzit_thick":
        val = validate_float(text)
        if val is None or val <= 0:
            await message.answer("❌ Введи толщину керамзита в мм (число):")
            return
        st["data"]["keramzit_thick"] = val

    elif step == "mesh_material":
        val = validate_float(text)
        if val is None or val <= 0:
            await message.answer("❌ Введи площадь материала сетки (число):")
            return
        st["data"]["mesh_material"] = val

    elif step == "mesh_work":
        val = validate_float(text)
        if val is None or val <= 0:
            await message.answer("❌ Введи площадь работы по укладке сетки (число):")
            return
        st["data"]["mesh_work"] = val

    elif step == "extra_work":
        st["data"]["extra_work"] = text

    elif step == "deadline":
        st["data"]["deadline"] = text

    else:
        # Шаг с кнопками — игнорируем текст
        await message.answer("👆 Выбери вариант кнопкой.")
        return

    # Переход к следующему шагу
    st["step"] = next_step(st)

    if st["step"] == "confirm":
        await show_confirm(message.chat.id, st["data"])
    else:
        await send_step(message.chat.id, st["step"])


async def show_confirm(chat_id: int, data: dict):
    """Показывает итог замера для подтверждения."""
    result = format_result(data)
    text = f"📋 <b>Проверь замер:</b>\n\n{result}\n\n✅ Всё верно — отправляем в группу?"
    await bot.send_message(chat_id, text, reply_markup=kb_confirm())


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    await bot.set_my_commands([
        BotCommand(command="start", description="Новый замер"),
        BotCommand(command="cancel", description="Отменить"),
    ])
    logger.info("Бот-замерщик ARTPOL запущен")
    if GROUP_CHAT_ID:
        logger.info("Группа: %s", GROUP_CHAT_ID)
    else:
        logger.warning("GROUP_CHAT_ID не задан! Замеры не будут отправляться в группу.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
