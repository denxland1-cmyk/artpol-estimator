"""
ARTPOL Агент-Сметчик — Telegram-бот
Парсер + Калькулятор + КП генератор

Менеджер: текст замера → подтверждение → смета → настройки → КП в .docx
"""

import os
import re
import logging

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode

from parser import process_measurement, get_distance_km, parse_passport_photo, parse_passport_text
from database import init_db, save_measurement, update_measurement_status, close_db
from calculator import calculate_estimate, format_estimate, MATERIALS_BASE_LAT, MATERIALS_BASE_LON
from kp_generator import generate_kp
from contract_generator import generate_contract

# ============================================================
# ⚠️ ВСЕ КЛЮЧИ И ТОКЕНЫ — ТОЛЬКО ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ!
# НИКОГДА не вставляй значения прямо в код!
# ============================================================

BOT_TOKEN = os.environ["ESTIMATOR_BOT_TOKEN"]

# Whitelist менеджеров — ID через запятую в Railway Variables
# Если не задано — бот открыт для всех (для тестирования)
_allowed_raw = os.environ.get("ALLOWED_USERS", "")
ALLOWED_USERS = set()
if _allowed_raw.strip():
    ALLOWED_USERS = {int(x.strip()) for x in _allowed_raw.split(",") if x.strip().isdigit()}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Состояние менеджера
user_state = {}


# ========== Проверка доступа ==========

def is_allowed(user_id: int) -> bool:
    """Проверяет доступ. Если ALLOWED_USERS пуст — доступ для всех."""
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


# ========== Вспомогательные ==========

def extract_floor(parsed: dict) -> int:
    if parsed.get("floor") and isinstance(parsed["floor"], (int, float)):
        return int(parsed["floor"])
    for cond in parsed.get("special_conditions", []):
        m = re.search(r"(\d+)\s*этаж", cond, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 1


async def get_materials_distance(lat: float, lon: float) -> float:
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


# ========== Форматирование ==========

def format_parsed_result(data: dict, db_id: int = None, created_at=None) -> str:
    lines = []
    if db_id and created_at:
        date_str = created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(f"📋 <b>Замер #{db_id}</b> от {date_str}")
    else:
        lines.append("📋 <b>Распознанные данные замера:</b>")
    lines.append("")

    if data.get("client_name") or data.get("client_phone"):
        parts = []
        if data.get("client_name"):
            parts.append(data["client_name"])
        if data.get("client_phone"):
            parts.append(data["client_phone"])
        lines.append(f"👤 Клиент: {' | '.join(parts)}")

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
    if data.get("keramzit"):
        k = data["keramzit"]
        lines.append(f"🟤 Керамзит: {k.get('area_m2', '?')} м², слой {k.get('thickness_mm', '?')} мм")
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


def format_full_estimate(st: dict) -> str:
    """Смета + вывоз песка."""
    est = st["estimate"]
    sand_removal = st.get("sand_removal", False)

    lines = [format_estimate(est)]

    if sand_removal:
        total_with_sand = est["grand_total"] + 5000
        lines.append(f"\n🚛 + Вывоз песка: 5,000₽")
        lines.append(f"💰 <b>ИТОГО с вывозом: {total_with_sand:,}₽</b>")

    return "\n".join(lines)


# ========== Клавиатуры ==========

def get_parse_keyboard(has_missing: bool) -> InlineKeyboardMarkup:
    buttons = []
    if has_missing:
        buttons.append([InlineKeyboardButton(text="📝 Дополнить данные", callback_data="fill_missing")])
    buttons.append([
        InlineKeyboardButton(text="✅ Всё верно → Смета", callback_data="confirm"),
        InlineKeyboardButton(text="🔄 Ввести заново", callback_data="retry"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_estimate_keyboard(st: dict) -> InlineKeyboardMarkup:
    grade = st.get("grade", "М150")
    modifier = st.get("modifier", 0)
    payment = st.get("payment", "")
    sand = st.get("sand_removal", False)

    rows = []

    # М150 / М200
    m150 = "🟢 М150 ✓" if grade == "М150" else "М150"
    m200 = "🔴 М200 ✓" if grade == "М200" else "М200"
    rows.append([
        InlineKeyboardButton(text=m150, callback_data="grade_m150"),
        InlineKeyboardButton(text=m200, callback_data="grade_m200"),
    ])

    # Скидка
    discounts = [(-1, "-1%"), (-3, "-3%"), (-5, "-5%")]
    disc_btns = []
    for val, label in discounts:
        txt = f"🔴 {label} ✓" if modifier == val else label
        disc_btns.append(InlineKeyboardButton(text=txt, callback_data=f"mod_{val}"))
    disc_btns.append(InlineKeyboardButton(
        text="🔴 Своя ✓" if (modifier < 0 and modifier not in [-1, -3, -5]) else "Своя",
        callback_data="mod_custom_disc"
    ))
    rows.append(disc_btns)

    # Наценка
    markups = [(1, "+1%"), (3, "+3%"), (5, "+5%")]
    mark_btns = []
    for val, label in markups:
        txt = f"🟢 {label} ✓" if modifier == val else label
        mark_btns.append(InlineKeyboardButton(text=txt, callback_data=f"mod_{val}"))
    mark_btns.append(InlineKeyboardButton(
        text="🟢 Своя ✓" if (modifier > 0 and modifier not in [1, 3, 5]) else "Своя",
        callback_data="mod_custom_mark"
    ))
    rows.append(mark_btns)

    # Сброс если есть модификатор
    if modifier != 0:
        rows.append([InlineKeyboardButton(text="↩️ Сбросить скидку/наценку", callback_data="mod_0")])

    # Нал / Безнал
    cash_txt = "🟢 Нал ✓" if payment == "наличными" else "Нал"
    bank_txt = "🟢 Безнал ✓" if payment == "безналичный расчет" else "Безнал"
    rows.append([
        InlineKeyboardButton(text=cash_txt, callback_data="pay_cash"),
        InlineKeyboardButton(text=bank_txt, callback_data="pay_bank"),
    ])

    # Вывоз песка
    sand_txt = "🟢 Вывоз песка: ДА (+5,000₽) ✓" if sand else "Вывоз песка: НЕТ"
    rows.append([InlineKeyboardButton(text=sand_txt, callback_data="sand_toggle")])

    # Сформировать КП — доступна только если выбран способ оплаты
    if payment:
        rows.append([InlineKeyboardButton(text="📄 Сформировать КП", callback_data="generate_kp")])
        rows.append([InlineKeyboardButton(text="📋 Сформировать договор", callback_data="start_contract")])

    # Новый замер
    rows.append([InlineKeyboardButton(text="🔄 Новый замер", callback_data="retry")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def recalc_and_show(callback: CallbackQuery, st: dict):
    """Пересчитывает смету и обновляет сообщение."""
    parsed = st["parsed"]
    area = parsed.get("area_m2") or 0
    thickness = parsed.get("thickness_mm_avg") or 0
    is_city = parsed.get("location_type") != "за городом"

    estimate = calculate_estimate(
        area_m2=area,
        thickness_mm=thickness,
        is_city=is_city,
        grade=st.get("grade", "М150"),
        floor=st.get("floor", 1),
        distance_materials_km=st.get("dist_materials", 0),
        distance_equipment_km=st.get("dist_equipment", 0),
        keramzit_area_m2=st.get("keramzit_area", 0),
        keramzit_thickness_mm=st.get("keramzit_thick", 0),
        price_modifier=st.get("modifier", 0),
    )
    st["estimate"] = estimate

    header = format_parsed_result(parsed, db_id=st["db_id"], created_at=st["created_at"])
    estimate_text = format_full_estimate(st)
    keyboard = get_estimate_keyboard(st)

    await callback.message.edit_text(
        header + "\n\n" + estimate_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ========== Хендлеры ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Доступ ограничен. Обратитесь к руководителю.")
        logger.warning("Отказ: user_id=%s (%s)", message.from_user.id, message.from_user.full_name)
        return
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


@dp.message(F.photo)
async def handle_photo(message: Message):
    """Обработка фото — для распознавания паспорта."""
    user_id = message.from_user.id

    if not is_allowed(user_id):
        await message.answer("⛔ Доступ ограничен.")
        return

    st = user_state.get(user_id)

    # Фото в контексте договора?
    if st and st.get("contract_step", -1) >= 0:
        handled = await handle_contract_input(message, st)
        if handled:
            return

    await message.answer("📸 Фото получено, но сейчас я жду текст замера. Отправь текст.")


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    user_id = message.from_user.id

    if not is_allowed(user_id):
        await message.answer("⛔ Доступ ограничен.")
        return

    st = user_state.get(user_id)

    # Ждём ввод пользовательского % скидки/наценки?
    if st and st.get("awaiting_custom_modifier"):
        direction = st.pop("awaiting_custom_modifier")  # "disc" или "mark"
        try:
            val = float(message.text.replace(",", ".").replace("%", "").strip())
            val = abs(val)
            if direction == "disc":
                st["modifier"] = -val
            else:
                st["modifier"] = val

            # Пересчитываем — нужно отправить новое сообщение
            parsed = st["parsed"]
            area = parsed.get("area_m2") or 0
            thickness = parsed.get("thickness_mm_avg") or 0
            is_city = parsed.get("location_type") != "за городом"

            estimate = calculate_estimate(
                area_m2=area, thickness_mm=thickness, is_city=is_city,
                grade=st.get("grade", "М150"), floor=st.get("floor", 1),
                distance_materials_km=st.get("dist_materials", 0),
                distance_equipment_km=st.get("dist_equipment", 0),
                keramzit_area_m2=st.get("keramzit_area", 0),
                keramzit_thickness_mm=st.get("keramzit_thick", 0),
                price_modifier=st.get("modifier", 0),
            )
            st["estimate"] = estimate

            header = format_parsed_result(parsed, db_id=st["db_id"], created_at=st["created_at"])
            estimate_text = format_full_estimate(st)
            keyboard = get_estimate_keyboard(st)

            await message.answer(
                header + "\n\n" + estimate_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
        except ValueError:
            await message.answer("❌ Введи число (например: 7 или 12.5)")
            st["awaiting_custom_modifier"] = direction
            return

    # Ждём ввод данных для договора?
    if st and st.get("contract_step", -1) >= 0:
        handled = await handle_contract_input(message, st)
        if handled:
            return

    # Обычный замер
    processing_msg = await message.answer("⏳ Распознаю данные замера...")

    try:
        result = await process_measurement(message.text)

        if result.get("error"):
            await processing_msg.edit_text(
                f"❌ Ошибка распознавания: {result.get('detail', '?')}\n\nПопробуй ещё раз.")
            return

        manager_name = message.from_user.full_name or "unknown"
        db_result = await save_measurement(
            manager_tg_id=user_id, manager_name=manager_name,
            raw_text=message.text, parsed=result,
        )

        user_state[user_id] = {
            "parsed": result,
            "db_id": db_result["id"],
            "created_at": db_result["created_at"],
            "grade": "М150",
            "modifier": 0,
            "payment": "",
            "sand_removal": False,
            "estimate": None,
        }

        text = format_parsed_result(result, db_id=db_result["id"], created_at=db_result["created_at"])
        has_missing = bool(result.get("missing_fields"))
        keyboard = get_parse_keyboard(has_missing)

        await processing_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    except Exception as e:
        logger.error("Ошибка: %s", e, exc_info=True)
        await processing_msg.edit_text("❌ Что-то пошло не так. Попробуй ещё раз.")


@dp.callback_query(F.data == "confirm")
async def on_confirm(callback: CallbackQuery):
    user_id = callback.from_user.id
    st = user_state.get(user_id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return

    await update_measurement_status(st["db_id"], "confirmed")

    parsed = st["parsed"]
    is_city = parsed.get("location_type") != "за городом"
    st["floor"] = extract_floor(parsed)

    # Расстояния
    st["dist_equipment"] = 0
    st["dist_materials"] = 0
    if not is_city:
        coords = parsed.get("coordinates")
        if coords and coords.get("lat") and coords.get("lon"):
            dist_info = parsed.get("distance", {})
            if isinstance(dist_info, dict) and dist_info.get("distance_km"):
                st["dist_equipment"] = dist_info["distance_km"]
            st["dist_materials"] = await get_materials_distance(coords["lat"], coords["lon"])

    # Керамзит
    keramzit_data = parsed.get("keramzit") or {}
    st["keramzit_area"] = keramzit_data.get("area_m2", 0) or 0
    st["keramzit_thick"] = keramzit_data.get("thickness_mm", 0) or 0

    await recalc_and_show(callback, st)
    await callback.answer("Смета рассчитана!")


# --- Марка ---
@dp.callback_query(F.data.in_({"grade_m150", "grade_m200"}))
async def on_grade(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return
    st["grade"] = "М200" if callback.data == "grade_m200" else "М150"
    await recalc_and_show(callback, st)
    await callback.answer(f"Марка: {st['grade']}")


# --- Скидка / Наценка ---
@dp.callback_query(F.data.startswith("mod_"))
async def on_modifier(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return

    data = callback.data

    if data == "mod_custom_disc":
        st["awaiting_custom_modifier"] = "disc"
        await callback.message.answer("📝 Введи % скидки (только число, например: 7):")
        await callback.answer()
        return

    if data == "mod_custom_mark":
        st["awaiting_custom_modifier"] = "mark"
        await callback.message.answer("📝 Введи % наценки (только число, например: 12):")
        await callback.answer()
        return

    # mod_-5, mod_-3, mod_-1, mod_0, mod_1, mod_3, mod_5
    val_str = data.replace("mod_", "")
    try:
        val = float(val_str)
    except ValueError:
        val = 0

    # Тогл: если нажали ту же кнопку — сбрасываем
    if st.get("modifier") == val and val != 0:
        st["modifier"] = 0
    else:
        st["modifier"] = val

    await recalc_and_show(callback, st)
    if st["modifier"] == 0:
        await callback.answer("Сброшено")
    elif st["modifier"] < 0:
        await callback.answer(f"Скидка {st['modifier']}%")
    else:
        await callback.answer(f"Наценка +{st['modifier']}%")


# --- Оплата ---
@dp.callback_query(F.data.in_({"pay_cash", "pay_bank"}))
async def on_payment(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return
    st["payment"] = "наличными" if callback.data == "pay_cash" else "безналичный расчет"

    # Обновляем клавиатуру без пересчёта сметы
    parsed = st["parsed"]
    header = format_parsed_result(parsed, db_id=st["db_id"], created_at=st["created_at"])
    estimate_text = format_full_estimate(st)
    keyboard = get_estimate_keyboard(st)

    await callback.message.edit_text(
        header + "\n\n" + estimate_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    await callback.answer(f"Оплата: {st['payment']}")


# --- Вывоз песка ---
@dp.callback_query(F.data == "sand_toggle")
async def on_sand(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return
    st["sand_removal"] = not st.get("sand_removal", False)

    parsed = st["parsed"]
    header = format_parsed_result(parsed, db_id=st["db_id"], created_at=st["created_at"])
    estimate_text = format_full_estimate(st)
    keyboard = get_estimate_keyboard(st)

    await callback.message.edit_text(
        header + "\n\n" + estimate_text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    status = "ДА" if st["sand_removal"] else "НЕТ"
    await callback.answer(f"Вывоз песка: {status}")


# --- Генерация КП ---
@dp.callback_query(F.data == "generate_kp")
async def on_generate_kp(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st or not st.get("estimate"):
        await callback.answer("Сначала рассчитай смету.")
        return

    if not st.get("payment"):
        await callback.answer("Выбери способ оплаты: Нал или Безнал")
        return

    await callback.answer("⏳ Генерирую КП...")

    parsed = st["parsed"]
    estimate = st["estimate"]

    try:
        from datetime import datetime, timezone, timedelta
        msk = timezone(timedelta(hours=3))
        ts = datetime.now(msk).strftime("%H%M%S")

        client_name = parsed.get("client_name", "клиент")
        area = parsed.get("area_m2", 0)
        thickness = parsed.get("thickness_mm_avg", 0)

        total = estimate["grand_total"]
        if st.get("sand_removal"):
            total += 5000

        # Населённый пункт из location_type
        loc = parsed.get("location_type", "город")

        fname = f"{client_name}_{loc}_{int(area)}-{int(thickness)}_{total}руб"
        # Убираем спецсимволы из имени файла
        fname_clean = "".join(c for c in fname if c.isalnum() or c in "._-")

        output_path = f"/tmp/KP_{fname_clean}_{ts}.docx"

        generate_kp(
            parsed=parsed,
            estimate=estimate,
            grade=st.get("grade", "М150"),
            payment_type=st["payment"],
            include_sand_removal=st.get("sand_removal", False),
            output_path=output_path,
        )

        # Отправляем файл
        doc_file = FSInputFile(output_path, filename=f"КП_{fname}.docx")
        await callback.message.answer_document(
            doc_file,
            caption=f"📄 КП для {client_name}, {area} м², {st.get('grade', 'М150')}"
        )

    except Exception as e:
        logger.error("Ошибка генерации КП: %s", e, exc_info=True)
        await callback.message.answer("❌ Ошибка генерации КП. Попробуй ещё раз.")


# --- Сброс ---
@dp.callback_query(F.data == "retry")
async def on_retry(callback: CallbackQuery):
    user_state.pop(callback.from_user.id, None)
    await callback.message.edit_text("🔄 Отправь текст замера заново.")
    await callback.answer()


# --- Договор: FSM ---

CONTRACT_STEPS = [
    ("passport_photo", "📸 Скинь <b>фото паспорта</b> или <b>напиши все данные текстом</b>\n(ФИО, серия/номер, кем выдан, дата выдачи, адрес прописки):"),
    ("reg_address", "🏘 Скинь <b>фото прописки</b> или введи <b>адрес регистрации</b> текстом:"),
    ("contract_number", "📄 Введи <b>номер договора</b> (только число, например: 48):"),
    ("work_start", "🏗 Введи <b>дату начала работ</b> (ДД.ММ.ГГГГ):"),
    ("payment_date", "💰 Введи <b>дату оплаты</b> (ДД.ММ.ГГГГ):"),
]


@dp.callback_query(F.data == "start_contract")
async def on_start_contract(callback: CallbackQuery):
    """Начинает сбор данных для договора."""
    st = user_state.get(callback.from_user.id)
    if not st or not st.get("estimate"):
        await callback.answer("Сначала рассчитай смету.")
        return

    st["contract_step"] = 0
    st["contract_data"] = {}

    _, prompt = CONTRACT_STEPS[0]
    await callback.message.answer(prompt, parse_mode=ParseMode.HTML)
    await callback.answer("Заполняем договор")


async def handle_contract_input(message: Message, st: dict):
    """Обрабатывает ввод данных для договора (текст или фото)."""
    step_idx = st.get("contract_step", -1)
    if step_idx < 0 or step_idx >= len(CONTRACT_STEPS):
        return False

    step_key, _ = CONTRACT_STEPS[step_idx]

    # --- Шаг: фото паспорта ИЛИ текст ---
    if step_key == "passport_photo":
        result = None

        if message.photo:
            # Фото паспорта
            processing = await message.answer("⏳ Распознаю паспорт...")
            try:
                photo = message.photo[-1]
                file = await bot.get_file(photo.file_id)
                photo_bytes = await bot.download_file(file.file_path)
                data = photo_bytes.read()
                result = await parse_passport_photo(data)
            except Exception as e:
                logger.error("Ошибка распознавания паспорта: %s", e, exc_info=True)
                await processing.edit_text("❌ Ошибка. Попробуй другое фото или введи текстом.")
                return True

        elif message.text and message.text.strip():
            # Текст с паспортными данными
            processing = await message.answer("⏳ Распознаю данные...")
            try:
                result = await parse_passport_text(message.text.strip())
            except Exception as e:
                logger.error("Ошибка парсинга паспорта из текста: %s", e, exc_info=True)
                await processing.edit_text("❌ Не удалось распознать. Попробуй ещё раз.")
                return True
        else:
            await message.answer("📸 Скинь фото паспорта или напиши данные текстом.")
            return True

        if not result or result.get("error"):
            await processing.edit_text("❌ Не удалось распознать. Попробуй другое фото или текстом.")
            return True

        st["contract_data"]["full_name"] = result.get("full_name", "")
        series = result.get("passport_series", "")
        number = result.get("passport_number", "")
        st["contract_data"]["passport"] = f"{series} {number}"
        st["contract_data"]["passport_issued"] = result.get("passport_issued_by", "")
        st["contract_data"]["passport_date"] = result.get("passport_date", "")

        if result.get("registration_address"):
            st["contract_data"]["reg_address"] = result["registration_address"]

        summary = (
            "✅ <b>Распознано:</b>\n"
            f"👤 {st['contract_data']['full_name']}\n"
            f"🪪 {st['contract_data']['passport']}\n"
            f"📝 {st['contract_data']['passport_issued']}\n"
            f"📅 {st['contract_data']['passport_date']}"
        )
        if result.get("registration_address"):
            summary += f"\n🏘 {result['registration_address']}"

        await processing.edit_text(summary, parse_mode=ParseMode.HTML)

        if result.get("registration_address"):
            st["contract_step"] = 2  # contract_number
        else:
            st["contract_step"] = 1  # reg_address

        _, prompt = CONTRACT_STEPS[st["contract_step"]]
        await message.answer(prompt, parse_mode=ParseMode.HTML)
        return True

    # --- Шаг: адрес регистрации (фото прописки или текст) ---
    if step_key == "reg_address":
        if message.photo:
            processing = await message.answer("⏳ Распознаю прописку...")
            try:
                photo = message.photo[-1]
                file = await bot.get_file(photo.file_id)
                photo_bytes = await bot.download_file(file.file_path)
                data = photo_bytes.read()

                result = await parse_passport_photo(data)
                addr = result.get("registration_address")
                if addr:
                    st["contract_data"]["reg_address"] = addr
                    await processing.edit_text(f"✅ Прописка: {addr}")
                else:
                    await processing.edit_text("❌ Не удалось распознать адрес. Введи текстом:")
                    return True
            except Exception as e:
                logger.error("Ошибка распознавания прописки: %s", e, exc_info=True)
                await processing.edit_text("❌ Ошибка. Введи адрес текстом:")
                return True
        else:
            text = message.text.strip() if message.text else ""
            if not text:
                await message.answer("❌ Введи адрес регистрации или скинь фото прописки.")
                return True
            st["contract_data"]["reg_address"] = text

        # Переходим к номеру договора
        st["contract_step"] = 2
        _, prompt = CONTRACT_STEPS[st["contract_step"]]
        await message.answer(prompt, parse_mode=ParseMode.HTML)
        return True

    # --- Текстовые шаги ---
    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("❌ Введи данные.")
        return True

    st["contract_data"][step_key] = text

    # Следующий шаг
    next_idx = step_idx + 1
    if next_idx < len(CONTRACT_STEPS):
        st["contract_step"] = next_idx
        _, prompt = CONTRACT_STEPS[next_idx]
        await message.answer(prompt, parse_mode=ParseMode.HTML)
        return True

    # Все данные собраны → подтверждение
    st["contract_step"] = -1
    cd = st["contract_data"]

    summary = (
        "📋 <b>Данные для договора:</b>\n\n"
        f"👤 ФИО: {cd.get('full_name', '—')}\n"
        f"🪪 Паспорт: {cd.get('passport', '—')}\n"
        f"📝 Выдан: {cd.get('passport_issued', '—')}\n"
        f"📅 Дата выдачи: {cd.get('passport_date', '—')}\n"
        f"🏘 Регистрация: {cd.get('reg_address', '—')}\n"
        f"📄 Договор №: {cd.get('contract_number', '—')}\n"
        f"🏗 Начало работ: {cd.get('work_start', '—')}\n"
        f"💰 Дата оплаты: {cd.get('payment_date', '—')}\n"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Всё верно → Договор", callback_data="confirm_contract"),
            InlineKeyboardButton(text="🔄 Заново", callback_data="restart_contract"),
        ]
    ])

    await message.answer(summary, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    return True


@dp.callback_query(F.data == "confirm_contract")
async def on_confirm_contract(callback: CallbackQuery):
    """Генерирует договор."""
    st = user_state.get(callback.from_user.id)
    if not st or not st.get("contract_data"):
        await callback.answer("Данные не найдены. Начни заново.")
        return

    await callback.answer("⏳ Генерирую договор...")

    parsed = st["parsed"]
    estimate = st["estimate"]
    cd = st["contract_data"]

    # Разбираем паспорт
    passport_parts = cd["passport"].split()
    series = passport_parts[0] if len(passport_parts) >= 1 else "____"
    number = passport_parts[1] if len(passport_parts) >= 2 else "______"

    client_data = {
        "full_name": cd["full_name"],
        "passport_series": series,
        "passport_number": number,
        "passport_issued_by": cd["passport_issued"],
        "passport_date": cd["passport_date"],
        "registration_address": cd["reg_address"],
        "contract_number": cd["contract_number"],
        "work_start_date": cd["work_start"] + "г.",
        "work_end_date": cd["work_start"] + "г.",
        "payment_date": cd["payment_date"] + "г",
    }

    try:
        from datetime import datetime, timezone, timedelta
        msk = timezone(timedelta(hours=3))
        ts = datetime.now(msk).strftime("%H%M%S")

        name_short = cd["full_name"].split()[0] if cd["full_name"] else "клиент"
        output_path = f"/tmp/Contract_{cd['contract_number']}_{name_short}_{ts}.docx"

        generate_contract(
            parsed=parsed,
            estimate=estimate,
            client_data=client_data,
            grade=st.get("grade", "М150"),
            include_sand_removal=st.get("sand_removal", False),
            output_path=output_path,
        )

        doc_file = FSInputFile(
            output_path,
            filename=f"Договор_{cd['contract_number']}_{name_short}.docx"
        )
        await callback.message.answer_document(
            doc_file,
            caption=f"📋 Договор №{cd['contract_number']} — {cd['full_name']}"
        )

    except Exception as e:
        logger.error("Ошибка генерации договора: %s", e, exc_info=True)
        await callback.message.answer("❌ Ошибка генерации договора. Попробуй ещё раз.")


@dp.callback_query(F.data == "restart_contract")
async def on_restart_contract(callback: CallbackQuery):
    """Начинает сбор данных заново."""
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return

    st["contract_step"] = 0
    st["contract_data"] = {}

    _, prompt = CONTRACT_STEPS[0]
    await callback.message.answer(prompt, parse_mode=ParseMode.HTML)
    await callback.answer()


@dp.callback_query(F.data == "fill_missing")
async def on_fill_missing(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st or not st["parsed"].get("missing_fields"):
        await callback.answer("Нет недостающих полей.")
        return
    missing = st["parsed"]["missing_fields"]
    await callback.message.answer(
        "📝 <b>Допиши недостающие данные одним сообщением:</b>\n"
        + "\n".join(f"  • {f}" for f in missing),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ========== Запуск ==========

async def main():
    await init_db()
    logger.info("Бот ARTPOL Агент-Сметчик запущен")
    if ALLOWED_USERS:
        logger.info("Доступ ограничен: %d менеджеров", len(ALLOWED_USERS))
    else:
        logger.info("⚠️ ALLOWED_USERS не задан — бот открыт для всех!")
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
