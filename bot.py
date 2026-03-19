"""
ARTPOL Агент-Сметчик — Telegram-бот
Парсер + PostgreSQL + Калькулятор сметы

Менеджер отправляет текст → AI парсит → подтверждает → калькулятор считает смету.
"""

import os
import re
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode

from parser import process_measurement, get_distance_km
from database import init_db, save_measurement, update_measurement_status, close_db
from calculator import calculate_estimate, format_estimate, MATERIALS_BASE_LAT, MATERIALS_BASE_LON

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

# user_id -> {"parsed": dict, "db_id": int, "created_at": datetime, "grade": str, "estimate": dict}
user_measurements = {}


# ---------- Вспомогательные ----------

def extract_floor(parsed: dict) -> int:
    """Извлекает этаж: сначала из поля floor, потом из текста."""
    # Поле floor от парсера (приоритет)
    if parsed.get("floor") and isinstance(parsed["floor"], (int, float)):
        return int(parsed["floor"])
    # Fallback: ищем в особых условиях
    for cond in parsed.get("special_conditions", []):
        m = re.search(r"(\d+)\s*этаж", cond, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 1  # по умолчанию 1 этаж


async def get_materials_distance(lat: float, lon: float) -> float:
    """Расстояние от базы материалов (Окская Гавань) до объекта."""
    # Временно подменяем координаты базы для OSRM запроса
    import httpx
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{MATERIALS_BASE_LON},{MATERIALS_BASE_LAT};{lon},{lat}"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url, params={"overview": "false"})
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") == "Ok":
            return round(data["routes"][0]["distance"] / 1000, 1)
    except Exception as e:
        logger.error("Ошибка OSRM (материалы): %s", e)
    return 0


# ---------- Форматирование ----------

def format_parsed_result(data: dict, db_id: int = None, created_at=None) -> str:
    """Форматирует распознанные данные для менеджера."""
    lines = []

    if db_id and created_at:
        date_str = created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"📋 <b>Замер #{db_id}</b> от {date_str}")
    else:
        lines.append("📋 <b>Распознанные данные замера:</b>")
    lines.append("")

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
    if data.get("floor"):
        lines.append(f"🏢 Этаж: {data['floor']}")
    if data.get("distance") and isinstance(data["distance"], dict) and not data["distance"].get("error"):
        d = data["distance"]
        lines.append(f"🚛 От базы: {d['distance_km']} км (~{d['duration_min']} мин)")
    if data.get("address"):
        lines.append(f"🏘 Адрес: {data['address']}")
    if data.get("coordinates"):
        c = data["coordinates"]
        lines.append(f"🗺 Координаты: {c['lat']}, {c['lon']}")
    if data.get("warm_floor") is True:
        lines.append("🔥 Тёплый пол: да")
    elif data.get("warm_floor") is False:
        lines.append("❄️ Тёплый пол: нет")
    if data.get("keramzit"):
        k = data["keramzit"]
        lines.append(f"🟤 Керамзит: {k.get('area_m2', '?')} м², слой {k.get('thickness_mm', '?')} мм")
    if data.get("deadline"):
        lines.append(f"⏰ Сроки: {data['deadline']}")
    if data.get("special_conditions"):
        lines.append(f"⚠️ Особые условия: {', '.join(data['special_conditions'])}")

    if data.get("missing_fields"):
        lines.append("")
        lines.append("❓ <b>Не хватает данных:</b>")
        for f in data["missing_fields"]:
            lines.append(f"  • {f}")

    return "\n".join(lines)


def get_result_keyboard(has_missing: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_missing:
        buttons.append([
            InlineKeyboardButton(text="📝 Дополнить данные", callback_data="fill_missing")
        ])
    buttons.append([
        InlineKeyboardButton(text="✅ Всё верно → Смета", callback_data="confirm"),
        InlineKeyboardButton(text="🔄 Ввести заново", callback_data="retry"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_estimate_keyboard(current_grade: str) -> InlineKeyboardMarkup:
    """Кнопки после расчёта сметы — переключение М150/М200."""
    if current_grade == "М150":
        grade_btn = InlineKeyboardButton(text="🔴 Пересчитать М200", callback_data="grade_m200")
    else:
        grade_btn = InlineKeyboardButton(text="🟢 Пересчитать М150", callback_data="grade_m150")

    return InlineKeyboardMarkup(inline_keyboard=[
        [grade_btn],
        [InlineKeyboardButton(text="🔄 Новый замер", callback_data="retry")],
    ])


# ---------- Хендлеры ----------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👷 <b>ARTPOL Агент-Сметчик</b>\n\n"
        "Отправь мне текст замера — я распознаю параметры и посчитаю смету.\n\n"
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

        manager_name = message.from_user.full_name or "unknown"
        db_result = await save_measurement(
            manager_tg_id=user_id,
            manager_name=manager_name,
            raw_text=message.text,
            parsed=result,
        )

        user_measurements[user_id] = {
            "parsed": result,
            "db_id": db_result["id"],
            "created_at": db_result["created_at"],
            "grade": "М150",
        }

        text = format_parsed_result(result, db_id=db_result["id"], created_at=db_result["created_at"])
        has_missing = bool(result.get("missing_fields"))
        keyboard = get_result_keyboard(has_missing)

        await processing_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    except Exception as e:
        logger.error("Ошибка обработки: %s", e, exc_info=True)
        await processing_msg.edit_text("❌ Что-то пошло не так. Попробуй ещё раз.")


@dp.callback_query(F.data == "confirm")
async def on_confirm(callback: CallbackQuery):
    """Менеджер подтвердил → считаем смету."""
    user_id = callback.from_user.id
    entry = user_measurements.get(user_id)

    if not entry:
        await callback.answer("Данные не найдены. Отправь замер заново.")
        return

    await update_measurement_status(entry["db_id"], "confirmed")

    parsed = entry["parsed"]
    grade = entry.get("grade", "М150")

    area = parsed.get("area_m2") or 0
    thickness = parsed.get("thickness_mm_avg") or 0
    is_city = parsed.get("location_type") != "за городом"
    floor = extract_floor(parsed)

    # Расстояния для области
    dist_equipment = 0
    dist_materials = 0

    if not is_city:
        coords = parsed.get("coordinates")
        if coords and coords.get("lat") and coords.get("lon"):
            # Расстояние от базы оборудования (Интернациональная) — уже есть
            dist_info = parsed.get("distance", {})
            if isinstance(dist_info, dict) and dist_info.get("distance_km"):
                dist_equipment = dist_info["distance_km"]

            # Расстояние от базы материалов (Окская Гавань)
            dist_materials = await get_materials_distance(coords["lat"], coords["lon"])

    # Керамзит
    keramzit_data = parsed.get("keramzit") or {}
    ker_area = keramzit_data.get("area_m2", 0) or 0
    ker_thick = keramzit_data.get("thickness_mm", 0) or 0

    # Считаем смету
    estimate = calculate_estimate(
        area_m2=area,
        thickness_mm=thickness,
        is_city=is_city,
        grade=grade,
        floor=floor,
        distance_materials_km=dist_materials,
        distance_equipment_km=dist_equipment,
        keramzit_area_m2=ker_area,
        keramzit_thickness_mm=ker_thick,
    )

    entry["estimate"] = estimate
    entry["dist_materials"] = dist_materials
    entry["dist_equipment"] = dist_equipment
    entry["floor"] = floor
    entry["keramzit_area"] = ker_area
    entry["keramzit_thick"] = ker_thick

    # Формируем сообщение
    header = format_parsed_result(parsed, db_id=entry["db_id"], created_at=entry["created_at"])
    estimate_text = format_estimate(estimate)

    await callback.message.edit_text(
        header + "\n\n" + estimate_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_estimate_keyboard(grade),
    )
    await callback.answer("Смета рассчитана!")


@dp.callback_query(F.data.in_({"grade_m150", "grade_m200"}))
async def on_change_grade(callback: CallbackQuery):
    """Переключение М150/М200 — пересчёт сметы."""
    user_id = callback.from_user.id
    entry = user_measurements.get(user_id)

    if not entry:
        await callback.answer("Данные не найдены. Отправь замер заново.")
        return

    new_grade = "М200" if callback.data == "grade_m200" else "М150"
    entry["grade"] = new_grade

    parsed = entry["parsed"]
    area = parsed.get("area_m2") or 0
    thickness = parsed.get("thickness_mm_avg") or 0
    is_city = parsed.get("location_type") != "за городом"

    estimate = calculate_estimate(
        area_m2=area,
        thickness_mm=thickness,
        is_city=is_city,
        grade=new_grade,
        floor=entry.get("floor", 1),
        distance_materials_km=entry.get("dist_materials", 0),
        distance_equipment_km=entry.get("dist_equipment", 0),
        keramzit_area_m2=entry.get("keramzit_area", 0),
        keramzit_thickness_mm=entry.get("keramzit_thick", 0),
    )

    entry["estimate"] = estimate

    header = format_parsed_result(parsed, db_id=entry["db_id"], created_at=entry["created_at"])
    estimate_text = format_estimate(estimate)

    await callback.message.edit_text(
        header + "\n\n" + estimate_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_estimate_keyboard(new_grade),
    )
    await callback.answer(f"Пересчитано на {new_grade}")


@dp.callback_query(F.data == "retry")
async def on_retry(callback: CallbackQuery):
    user_id = callback.from_user.id
    user_measurements.pop(user_id, None)
    await callback.message.edit_text("🔄 Отправь текст замера заново.")
    await callback.answer()


@dp.callback_query(F.data == "fill_missing")
async def on_fill_missing(callback: CallbackQuery):
    user_id = callback.from_user.id
    entry = user_measurements.get(user_id)

    if not entry or not entry["parsed"].get("missing_fields"):
        await callback.answer("Нет недостающих полей.")
        return

    missing = entry["parsed"]["missing_fields"]
    await callback.message.answer(
        "📝 <b>Допиши недостающие данные одним сообщением:</b>\n"
        + "\n".join(f"  • {f}" for f in missing),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ---------- Запуск ----------

async def main():
    await init_db()
    logger.info("Бот ARTPOL Агент-Сметчик запущен")
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
