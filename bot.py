"""
ARTPOL Агент-Сметчик — Telegram-бот
Этап 1: парсер текста замерщика + PostgreSQL

Менеджер отправляет текст → AI парсит → сохраняет в БД → показывает результат с кнопками.
"""

import os
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode

from parser import process_measurement
from database import init_db, save_measurement, update_measurement_status, close_db

# ============================================================
# ⚠️ ВСЕ КЛЮЧИ И ТОКЕНЫ — ТОЛЬКО ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ!
# НИКОГДА не вставляй значения прямо в код!
# Railway Variables / .env + python-dotenv
# ============================================================

BOT_TOKEN = os.environ["ESTIMATOR_BOT_TOKEN"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# user_id -> {"parsed": dict, "db_id": int, "created_at": datetime}
user_measurements = {}


# ---------- Форматирование результата ----------

def format_parsed_result(data: dict, db_id: int = None, created_at=None) -> str:
    """Форматирует распознанные данные для менеджера."""
    lines = []

    # Шапка с номером и датой
    if db_id and created_at:
        date_str = created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"📋 <b>Замер #{db_id}</b> от {date_str}")
    else:
        lines.append("📋 <b>Распознанные данные замера:</b>")
    lines.append("")

    # Клиент
    if data.get("client_name") or data.get("client_phone"):
        client_parts = []
        if data.get("client_name"):
            client_parts.append(data["client_name"])
        if data.get("client_phone"):
            client_parts.append(data["client_phone"])
        lines.append(f"👤 Клиент: {' | '.join(client_parts)}")

    if data.get("object_type"):
        lines.append(f"🏠 Тип: {data['object_type']}")
    if data.get("area_m2"):
        lines.append(f"📐 Площадь: {data['area_m2']} м²")
    if data.get("thickness_mm_avg"):
        zones = data.get("zones", [])
        if len(zones) > 1:
            lines.append(f"📏 Средняя толщина: {data['thickness_mm_avg']} мм")
            for z in zones:
                lines.append(f"    ↳ {z['name']}: {z.get('area_m2', '?')} м² × {z.get('thickness_mm', '?')} мм")
        else:
            lines.append(f"📏 Толщина слоя: {data['thickness_mm_avg']} мм")
    if data.get("location_type"):
        lines.append(f"📍 Локация: {data['location_type']}")
    if data.get("address"):
        lines.append(f"🏘 Адрес: {data['address']}")
    if data.get("coordinates"):
        c = data["coordinates"]
        lines.append(f"🗺 Координаты: {c['lat']}, {c['lon']}")
    if data.get("warm_floor") is True:
        lines.append("🔥 Тёплый пол: да")
    elif data.get("warm_floor") is False:
        lines.append("❄️ Тёплый пол: нет")
    if data.get("deadline"):
        lines.append(f"⏰ Сроки: {data['deadline']}")
    if data.get("special_conditions"):
        lines.append(f"⚠️ Особые условия: {', '.join(data['special_conditions'])}")
    if data.get("distance") and isinstance(data["distance"], dict) and not data["distance"].get("error"):
        d = data["distance"]
        lines.append(f"🚛 От базы: {d['distance_km']} км (~{d['duration_min']} мин)")

    if data.get("missing_fields"):
        lines.append("")
        lines.append("❓ <b>Не хватает данных:</b>")
        for f in data["missing_fields"]:
            lines.append(f"  • {f}")

    return "\n".join(lines)


def get_result_keyboard(has_missing: bool) -> InlineKeyboardMarkup:
    """Inline-кнопки после распознавания."""
    buttons = []

    if has_missing:
        buttons.append([
            InlineKeyboardButton(text="📝 Дополнить данные", callback_data="fill_missing")
        ])

    buttons.append([
        InlineKeyboardButton(text="✅ Всё верно", callback_data="confirm"),
        InlineKeyboardButton(text="🔄 Ввести заново", callback_data="retry"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------- Хендлеры ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👷 <b>ARTPOL Агент-Сметчик</b>\n\n"
        "Отправь мне текст замера — я распознаю параметры.\n\n"
        "Можно:\n"
        "• Написать своими словами\n"
        "• Переслать сообщение замерщика\n\n"
        "Пример: <i>Алексей +79001234567, квартира 78м², "
        "ЖК Анкудиновский, слой 50мм, тёплый пол</i>",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_measurement_text(message: Message):
    """Менеджер отправил текст → парсим через AI → сохраняем в БД."""
    user_id = message.from_user.id

    # Показываем что работаем
    processing_msg = await message.answer("⏳ Распознаю данные замера...")

    try:
        result = await process_measurement(message.text)

        if result.get("error"):
            error_detail = result.get("detail", "неизвестная ошибка")
            await processing_msg.edit_text(
                f"❌ Ошибка распознавания: {error_detail}\n\n"
                "Попробуй отправить текст ещё раз.",
            )
            return

        # Сохраняем в БД
        manager_name = message.from_user.full_name or "unknown"
        db_result = await save_measurement(
            manager_tg_id=user_id,
            manager_name=manager_name,
            raw_text=message.text,
            parsed=result,
        )

        db_id = db_result["id"]
        created_at = db_result["created_at"]

        # Сохраняем в памяти для кнопок
        user_measurements[user_id] = {
            "parsed": result,
            "db_id": db_id,
            "created_at": created_at,
        }

        # Показываем результат с кнопками
        text = format_parsed_result(result, db_id=db_id, created_at=created_at)
        has_missing = bool(result.get("missing_fields"))
        keyboard = get_result_keyboard(has_missing)

        await processing_msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.error("Ошибка обработки: %s", e, exc_info=True)
        await processing_msg.edit_text(
            "❌ Что-то пошло не так. Попробуй ещё раз.",
        )


@dp.callback_query(F.data == "confirm")
async def on_confirm(callback: CallbackQuery):
    """Менеджер подтвердил данные."""
    user_id = callback.from_user.id
    entry = user_measurements.get(user_id)

    if not entry:
        await callback.answer("Данные не найдены. Отправь замер заново.")
        return

    # Обновляем статус в БД
    await update_measurement_status(entry["db_id"], "confirmed")

    # TODO: Здесь будет вызов калькулятора → генерация КП
    await callback.message.edit_text(
        format_parsed_result(entry["parsed"], db_id=entry["db_id"], created_at=entry["created_at"])
        + "\n\n✅ <b>Данные подтверждены!</b>"
        + "\n\n🚧 <i>Следующий шаг: калькулятор → КП (в разработке)</i>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Принято!")


@dp.callback_query(F.data == "retry")
async def on_retry(callback: CallbackQuery):
    """Менеджер хочет ввести заново."""
    user_id = callback.from_user.id
    user_measurements.pop(user_id, None)

    await callback.message.edit_text("🔄 Отправь текст замера заново.")
    await callback.answer()


@dp.callback_query(F.data == "fill_missing")
async def on_fill_missing(callback: CallbackQuery):
    """Менеджер хочет дополнить недостающие данные."""
    user_id = callback.from_user.id
    entry = user_measurements.get(user_id)

    if not entry or not entry["parsed"].get("missing_fields"):
        await callback.answer("Нет недостающих полей.")
        return

    missing = entry["parsed"]["missing_fields"]
    # TODO: пошаговый сбор недостающих полей через FSM
    await callback.message.answer(
        "📝 <b>Допиши недостающие данные одним сообщением:</b>\n"
        + "\n".join(f"  • {f}" for f in missing),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ---------- Запуск ----------

async def main():
    # Инициализируем БД
    await init_db()

    logger.info("Бот ARTPOL Агент-Сметчик запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
