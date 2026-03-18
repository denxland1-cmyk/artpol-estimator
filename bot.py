"""
ARTPOL Агент-Сметчик — Telegram-бот
Этап 1: парсер текста замерщика

Менеджер отправляет текст → AI парсит → показывает результат с кнопками.
"""

import os
import json
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode

from parser import process_measurement

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

# Хранилище последнего распознанного замера (в памяти, потом — БД)
# user_id -> parsed_data
user_measurements = {}


# ---------- Форматирование результата ----------

def format_parsed_result(data: dict) -> str:
    """Форматирует распознанные данные для менеджера."""
    lines = ["📋 <b>Распознанные данные замера:</b>", ""]

    if data.get("object_type"):
        lines.append(f"🏠 Тип: {data['object_type']}")
    if data.get("area_m2"):
        lines.append(f"📐 Площадь: {data['area_m2']} м²")
    if data.get("thickness_mm"):
        lines.append(f"📏 Толщина слоя: {data['thickness_mm']} мм")
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
    if data.get("distance") and not data["distance"].get("error"):
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
        "Пример: <i>Квартира 78м², ЖК Анкудиновский, слой 50мм, тёплый пол</i>",
        parse_mode=ParseMode.HTML,
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_measurement_text(message: Message):
    """Менеджер отправил текст → парсим через AI."""
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

        # Сохраняем результат
        user_measurements[user_id] = result

        # Показываем результат с кнопками
        text = format_parsed_result(result)
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
    data = user_measurements.get(user_id)

    if not data:
        await callback.answer("Данные не найдены. Отправь замер заново.")
        return

    # TODO: Здесь будет вызов калькулятора → генерация КП
    await callback.message.edit_text(
        format_parsed_result(data)
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
    data = user_measurements.get(user_id)

    if not data or not data.get("missing_fields"):
        await callback.answer("Нет недостающих полей.")
        return

    missing = data["missing_fields"]
    # TODO: пошаговый сбор недостающих полей через FSM
    await callback.message.answer(
        "📝 <b>Допиши недостающие данные одним сообщением:</b>\n"
        + "\n".join(f"  • {f}" for f in missing),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ---------- Запуск ----------

async def main():
    logger.info("Бот ARTPOL Агент-Сметчик запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
