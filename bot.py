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
from amo_crm import format_pipelines, format_custom_fields, fill_amo_lead, upload_file_to_lead, get_lead_by_id
from kronos import create_event, bind_lead, SURVEYORS, find_surveyor_id

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
    if data.get("sand_transport"):
        label = "КАМАЗЫ" if data["sand_transport"] == "камаз" else "ГАЗОНЫ"
        lines.append(f"🚛 Песок: только {label}")
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
        rows.append([InlineKeyboardButton(text="📊 Заполнить АМО", callback_data="fill_amo")])
        rows.append([InlineKeyboardButton(text="📅 Записать в Кронос", callback_data="start_kronos")])

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
        sand_transport=st.get("sand_transport"),
    )
    st["estimate"] = estimate

    header = format_parsed_result(parsed, db_id=st["db_id"], created_at=st["created_at"])
    estimate_text = format_full_estimate(st)
    keyboard = get_estimate_keyboard(st)

    try:
        await callback.message.edit_text(
            header + "\n\n" + estimate_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except Exception:
        pass  # message not modified — OK


# ========== Хендлеры ==========

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("⛔ Доступ ограничен. Обратитесь к руководителю.")
        logger.warning("Отказ: user_id=%s (%s)", message.from_user.id, message.from_user.full_name)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Договор из данных АМО", callback_data="contract_from_amo")]
    ])
    await message.answer(
        "👷 <b>ARTPOL Агент-Сметчик</b>\n\n"
        "Отправь мне текст замера — я распознаю параметры и посчитаю смету.\n\n"
        "Можно:\n"
        "• Написать своими словами\n"
        "• Переслать сообщение замерщика\n\n"
        "Пример: <i>Алексей +79001234567, квартира 78м², "
        "ЖК Анкудиновский, слой 50мм, тёплый пол</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


@dp.message(F.text == "/amo")
async def cmd_amo(message: Message):
    """Показывает воронки и кастомные поля AMO CRM."""
    if not is_allowed(message.from_user.id):
        return
    msg = await message.answer("⏳ Загружаю данные из AMO CRM...")
    pipelines = await format_pipelines()
    fields = await format_custom_fields()
    await msg.edit_text(pipelines + "\n\n" + fields, parse_mode=ParseMode.HTML)


@dp.message(F.text == "/contract")
async def cmd_contract_from_amo(message: Message):
    """Договор из сделки АМО."""
    if not is_allowed(message.from_user.id):
        return
    user_id = message.from_user.id
    st = user_state.get(user_id) or {}
    st["awaiting_amo_lead_id"] = True
    user_state[user_id] = st
    await message.answer(
        "📋 <b>Договор из сделки АМО</b>\n\n"
        "Введи <b>номер сделки</b> (например: 29421713):",
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == "contract_from_amo")
async def on_contract_from_amo(callback: CallbackQuery):
    """Кнопка: Договор из данных АМО."""
    user_id = callback.from_user.id
    if not is_allowed(user_id):
        await callback.answer("⛔ Доступ ограничен.")
        return
    st = user_state.get(user_id) or {}
    st["awaiting_amo_lead_id"] = True
    user_state[user_id] = st
    await callback.message.answer(
        "📋 <b>Договор из сделки АМО</b>\n\n"
        "Введи <b>номер сделки</b> (например: 29421713):",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


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
                sand_transport=st.get("sand_transport"),
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

    # Ждём номер сделки АМО для договора?
    if st and st.get("awaiting_amo_lead_id"):
        st.pop("awaiting_amo_lead_id")
        text = message.text.strip().replace("#", "")

        if not text.isdigit():
            await message.answer("❌ Введи номер сделки (только цифры).")
            st["awaiting_amo_lead_id"] = True
            return

        lead_id = int(text)
        processing = await message.answer(f"⏳ Загружаю сделку #{lead_id} из АМО...")

        lead = await get_lead_by_id(lead_id)
        if not lead:
            await processing.edit_text(f"❌ Сделка #{lead_id} не найдена в АМО.")
            return

        # Извлекаем данные
        lead_name = lead.get("name", "")
        price = lead.get("price", 0)
        area = lead.get("area")
        thickness_raw = lead.get("thickness", "")
        address = lead.get("address", "")
        floor_val = lead.get("floor")
        object_type = lead.get("object_type", "квартира")
        client_name = lead.get("contact_name") or lead_name.split("+")[0].strip() or lead_name
        phone = lead.get("phone", "")

        # Парсим толщину из строки "80.0 мм"
        import re
        thickness = 0
        if thickness_raw:
            m = re.search(r"(\d+\.?\d*)", str(thickness_raw))
            if m:
                thickness = float(m.group(1))

        # Формируем parsed для договора
        parsed = {
            "client_name": client_name,
            "client_phone": phone,
            "area_m2": float(area) if area else 0,
            "thickness_mm_avg": thickness,
            "address": address,
            "object_type": object_type,
            "location_type": "город",
            "floor": int(float(floor_val)) if floor_val else 1,
        }

        # Формируем estimate (берём бюджет из сделки)
        estimate = {
            "grand_total": price,
            "sand": {"total": 0, "sand_tons": 0, "sand_cost": 0, "delivery": 0, "extra": 0, "transport": "из АМО", "volume_m3": 0},
            "cement": {"total": 0, "bags": 0, "cement_cost": 0, "delivery": 0, "grade": "М150"},
            "fiber": {"cost": 0, "kg": 0},
            "film": {"cost": 0, "m2": 0},
            "izoflex": {"cost": 0, "meters": 0},
            "equipment_delivery": {"cost": 0, "detail": "из АМО"},
            "work": {"cost": 0, "rate": "из АМО", "floor_label": ""},
            "keramzit": None,
            "materials_total": 0,
            "price_modifier": 0,
        }

        # Сохраняем в user_state
        user_state[user_id] = {
            "parsed": parsed,
            "db_id": 0,
            "created_at": "",
            "grade": "М150",
            "modifier": 0,
            "payment": "cash",
            "sand_removal": False,
            "estimate": estimate,
            "dist_materials": 0,
            "dist_equipment": 0,
            "floor": int(float(floor_val)) if floor_val else 1,
            "keramzit_area": 0,
            "keramzit_thick": 0,
            "sand_transport": None,
            "amo_lead_id": lead_id,
            "from_amo_lead": True,
        }
        st = user_state[user_id]

        # Показываем данные
        summary = (
            f"✅ <b>Сделка #{lead_id}</b>\n\n"
            f"👤 {client_name}"
        )
        if phone:
            summary += f" | {phone}"
        summary += f"\n📍 {address}" if address else ""
        summary += f"\n📐 Площадь: {area} м²" if area else ""
        summary += f"\n📏 Толщина: {thickness_raw}" if thickness_raw else ""
        summary += f"\n💰 Бюджет: {price:,}₽" if price else ""

        await processing.edit_text(summary, parse_mode=ParseMode.HTML)

        # Запускаем FSM договора
        st["contract_step"] = 0
        st["contract_data"] = {}
        _, prompt = CONTRACT_STEPS[0]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_contract")]
        ])
        await message.answer(prompt, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # Ждём дату/время для Кроноса?
    if st and st.get("kronos_step") == "datetime":
        handled = await handle_kronos_datetime(message, st)
        if handled:
            return

    # Ждём ввод данных для договора?
    if st and st.get("contract_step", -1) >= 0:
        # Проверяем — может это замер, а не данные для договора?
        text = message.text or ""
        looks_like_measurement = (
            len(text) > 60
            and any(x in text.lower() for x in ["м2", "мм", "м²", "квартир", "этаж", "площад", "слой", "стяжк"])
        )

        if looks_like_measurement:
            # Это замер, а не паспортные данные — сбрасываем FSM
            st["contract_step"] = -1
            st.pop("contract_data", None)
            await message.answer("📝 Похоже на замер — отменяю создание договора.")
            # Не return — пусть пойдёт дальше как обычный замер
        else:
            handled = await handle_contract_input(message, st)
            if handled:
                return

    # Ждём дополнение данных замера?
    if st and st.get("awaiting_supplement"):
        st.pop("awaiting_supplement")
        processing_msg = await message.answer("⏳ Дополняю данные замера...")

        try:
            old = st["parsed"]
            text = message.text.strip()

            # Если текст короткий (до 40 символов) и нет цифр длиннее 4 знаков —
            # скорее всего это просто имя клиента
            import re
            has_measurement_data = bool(re.search(r'\d{5,}|м2|м²|мм|этаж|площад', text.lower()))
            if len(text) <= 40 and not has_measurement_data:
                # Это имя клиента
                old["client_name"] = text
                logger.info("Дополнение: имя клиента = %s", text)
            else:
                # Парсим как полноценные данные замера
                supplement = await process_measurement(text)
                if supplement.get("error"):
                    await processing_msg.edit_text("❌ Не удалось распознать. Попробуй ещё раз.")
                    st["awaiting_supplement"] = True
                    return

                # Объединяем: новые данные перезаписывают только пустые поля
                for key, val in supplement.items():
                    if key in ("missing_fields", "error", "raw_response"):
                        continue
                    if val is None or val == "" or val == [] or val == {}:
                        continue
                    old_val = old.get(key)
                    if old_val is None or old_val == "" or old_val == [] or old_val == {}:
                        old[key] = val

            # Пересчитываем missing_fields
            required = []
            if not old.get("client_name") and not old.get("client_phone"):
                required.append("имя или телефон клиента")
            if not old.get("area_m2"):
                required.append("площадь (м²)")
            if not old.get("thickness_mm_avg") and not old.get("zones"):
                required.append("толщина слоя (мм)")
            if not old.get("object_type"):
                required.append("тип объекта")
            if not old.get("location_type"):
                required.append("город или за городом")
            old["missing_fields"] = required if required else []

            text = format_parsed_result(old, db_id=st["db_id"], created_at=st["created_at"])
            has_missing = bool(old.get("missing_fields"))
            keyboard = get_parse_keyboard(has_missing)

            await processing_msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception as e:
            logger.error("Ошибка дополнения: %s", e, exc_info=True)
            await processing_msg.edit_text("❌ Ошибка. Попробуй ещё раз.")
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
    st["floor"] = extract_floor(parsed)

    # Расстояния — считаем ВСЕГДА если есть координаты
    st["dist_equipment"] = 0
    st["dist_materials"] = 0
    coords = parsed.get("coordinates")
    if coords and coords.get("lat") and coords.get("lon"):
        dist_info = parsed.get("distance", {})
        if isinstance(dist_info, dict) and dist_info.get("distance_km"):
            st["dist_equipment"] = dist_info["distance_km"]
        st["dist_materials"] = await get_materials_distance(coords["lat"], coords["lon"])

    # Город = расстояние ≤ 20 км от базы
    # Всё что дальше 20 км — область (по километражу)
    max_dist = max(st["dist_equipment"], st["dist_materials"])
    if max_dist > 0 and max_dist <= 20:
        parsed["location_type"] = "город"
        logger.info("Расстояние %.1f км ≤ 20 км → считаем как город", max_dist)
    elif max_dist > 20:
        parsed["location_type"] = "за городом"
        logger.info("Расстояние %.1f км > 20 км → считаем как область", max_dist)
    # max_dist == 0 — нет координат, верим парсеру

    is_city = parsed.get("location_type") != "за городом"

    # Если город — обнуляем расстояния (фикс доставка)
    if is_city:
        st["dist_equipment"] = 0
        st["dist_materials"] = 0

    # Керамзит
    keramzit_data = parsed.get("keramzit") or {}
    st["keramzit_area"] = keramzit_data.get("area_m2", 0) or 0
    st["keramzit_thick"] = keramzit_data.get("thickness_mm", 0) or 0

    # Спецтранспорт для песка
    st["sand_transport"] = parsed.get("sand_transport")

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

    try:
        await callback.message.edit_text(
            header + "\n\n" + estimate_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except Exception:
        pass
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

    try:
        await callback.message.edit_text(
            header + "\n\n" + estimate_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    except Exception:
        pass
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
        st["kp_path"] = output_path  # сохраняем для АМО
        doc_file = FSInputFile(output_path, filename=f"КП_{fname}.docx")
        await callback.message.answer_document(
            doc_file,
            caption=f"📄 КП для {client_name}, {area} м², {st.get('grade', 'М150')}"
        )

    except Exception as e:
        logger.error("Ошибка генерации КП: %s", e, exc_info=True)
        await callback.message.answer("❌ Ошибка генерации КП. Попробуй ещё раз.")


# --- Заполнить АМО ---
@dp.callback_query(F.data == "fill_amo")
async def on_fill_amo(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st or not st.get("estimate"):
        await callback.answer("Сначала рассчитай смету.")
        return

    parsed = st["parsed"]
    phone = parsed.get("client_phone", "")
    if not phone:
        await callback.answer("❌ Нет телефона клиента в замере!")
        await callback.message.answer("❌ Телефон клиента не найден в замере. АМО не может найти сделку без телефона.")
        return

    await callback.answer("⏳ Ищу сделку в АМО...")
    processing = await callback.message.answer("🔍 Ищу сделку в АМО по номеру " + phone + "...")

    estimate = st["estimate"]
    total = estimate["grand_total"]
    if st.get("sand_removal"):
        total += 5000

    # Дата и время первого замера
    created = st.get("created_at")
    if created:
        measurement_dt = created.strftime("%d.%m.%Y %H:%M")
        measurement_ts = int(created.timestamp())
    else:
        measurement_dt = "—"
        measurement_ts = None

    # Собираем текст замера
    raw_text = ""
    if st.get("db_id"):
        # Форматируем из parsed
        lines = []
        if parsed.get("client_name"):
            lines.append(f"Клиент: {parsed['client_name']}")
        if phone:
            lines.append(f"Тел: {phone}")
        if parsed.get("object_type"):
            lines.append(f"Тип: {parsed['object_type']}")
        if parsed.get("area_m2"):
            lines.append(f"Площадь: {parsed['area_m2']} м²")
        if parsed.get("thickness_mm_avg"):
            lines.append(f"Толщина: {parsed['thickness_mm_avg']} мм")
        if parsed.get("location_type"):
            lines.append(f"Локация: {parsed['location_type']}")
        if parsed.get("floor"):
            lines.append(f"Этаж: {parsed['floor']}")
        if parsed.get("address"):
            lines.append(f"Адрес: {parsed['address']}")
        if parsed.get("keramzit"):
            k = parsed["keramzit"]
            lines.append(f"Керамзит: {k.get('area_m2', 0)} м², {k.get('thickness_mm', 0)} мм")
        if parsed.get("special_conditions"):
            lines.append(f"Особые условия: {', '.join(parsed['special_conditions'])}")
        lines.append(f"\nБюджет: {total:,}₽ ({st.get('grade', 'М150')})")
        raw_text = "\n".join(lines)

    result = await fill_amo_lead(
        phone=phone,
        price=total,
        raw_text=raw_text,
        area=parsed.get("area_m2", 0),
        thickness=parsed.get("thickness_mm_avg", 0),
        floor=st.get("floor", 1),
        address=parsed.get("address", ""),
        object_type=parsed.get("object_type", ""),
        measurement_datetime=measurement_dt,
        measurement_timestamp=measurement_ts,
        client_name=parsed.get("client_name", ""),
    )

    if result.get("success"):
        lead_id = result["lead_id"]
        st["amo_lead_id"] = lead_id  # Для привязки к Кроносу
        files_sent = []

        # Прикрепляем КП если есть
        kp_path = st.get("kp_path")
        if kp_path:
            import os
            if os.path.exists(kp_path):
                kp_name = os.path.basename(kp_path)
                await upload_file_to_lead(lead_id, kp_path, kp_name)
                files_sent.append("📄 КП")

        # Прикрепляем договор если есть
        contract_path = st.get("contract_path")
        if contract_path:
            import os
            if os.path.exists(contract_path):
                contract_name = os.path.basename(contract_path)
                await upload_file_to_lead(lead_id, contract_path, contract_name)
                files_sent.append("📋 Договор")

        files_info = ""
        if files_sent:
            files_info = f"\n📎 Прикреплено: {', '.join(files_sent)}"

        action = "✨ Создана новая" if result.get("created_new") else "🔄 Обновлена"
        await processing.edit_text(
            f"✅ <b>АМО {action.split()[1]}!</b>\n\n"
            f"📊 Сделка: {result['lead_name']}\n"
            f"💰 Бюджет: {total:,}₽\n"
            f"📍 Статус: → Сделано предложение{files_info}",
            parse_mode=ParseMode.HTML,
        )
    elif result.get("error") == "not_found":
        await processing.edit_text(
            f"❌ Сделка с телефоном {phone} не найдена и не удалось создать новую."
        )
    else:
        await processing.edit_text(
            f"❌ Ошибка обновления АМО: {result.get('detail', 'неизвестно')}"
        )


# --- Сброс ---
@dp.callback_query(F.data == "retry")
async def on_retry(callback: CallbackQuery):
    user_state.pop(callback.from_user.id, None)
    try:
        await callback.message.edit_text("🔄 Отправь текст замера заново.")
    except Exception:
        await callback.message.answer("🔄 Отправь текст замера заново.")
    try:
        await callback.answer()
    except Exception:
        pass


# --- Кронос: запись замера ---

def _parse_measurement_date(raw: str) -> tuple[str, str, str, int, int, int] | None:
    """Парсит дату из формата 'ДД.ММ.ГГ' или 'ДД.ММ.ГГГГ' → (date_str, time не трогаем, day, month, year)."""
    import re
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?", raw.strip())
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year_str = m.group(3)
    if year_str:
        year = int(year_str)
        if year < 100:
            year += 2000
    else:
        year = 2026
    date_str = f"{year}-{month:02d}-{day:02d}"
    return date_str, day, month, year


async def _do_kronos_create(message_or_callback, st, date_str, day, month, year, time_from, time_to, surveyor_id, surveyor_name):
    """Общая функция создания записи в Кроносе."""
    parsed = st.get("parsed", {})
    address = parsed.get("address", "Адрес не указан")
    phone = parsed.get("client_phone", "")
    client_name = parsed.get("client_name", "Клиент")

    # Определяем куда писать
    if hasattr(message_or_callback, 'message'):
        send = message_or_callback.message.answer
    else:
        send = message_or_callback.answer

    processing = await send("⏳ Создаю запись в Кроносе...")

    result = await create_event(
        date=date_str,
        time_from=time_from,
        time_to=time_to,
        surveyor_id=surveyor_id,
        contact_name=client_name,
        contact_phone=phone,
        address=address,
    )

    if not result:
        await processing.edit_text("❌ Ошибка создания записи в Кроносе. Попробуй ещё раз.")
        st.pop("kronos_step", None)
        return

    event_id = result.get("id")
    msg = (
        f"✅ <b>Запись в Кроносе создана!</b>\n\n"
        f"📅 {day:02d}.{month:02d}.{year} {time_from}–{time_to}\n"
        f"👷 Замерщик: {surveyor_name}\n"
        f"📍 {address}"
    )

    # Привязка к сделке AMO если есть lead_id
    lead_id = st.get("amo_lead_id")
    if lead_id and event_id:
        bound = await bind_lead(event_id, lead_id)
        if bound:
            msg += f"\n🔗 Привязана к сделке AMO #{lead_id}"
        else:
            msg += f"\n⚠️ Не удалось привязать к сделке AMO"

    await processing.edit_text(msg, parse_mode=ParseMode.HTML)

    # Очищаем состояние Кроноса
    st.pop("kronos_step", None)
    st.pop("kronos_surveyor_id", None)
    st.pop("kronos_surveyor_name", None)


@dp.callback_query(F.data == "start_kronos")
async def on_start_kronos(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return

    parsed = st.get("parsed", {})

    # Проверяем: есть ли дата/время и замерщик в данных замера?
    raw_date = parsed.get("measurement_date", "")
    raw_time = parsed.get("measurement_time", "")
    raw_surveyor = parsed.get("surveyor_name", "")

    # Ищем замерщика
    surveyor_id = find_surveyor_id(raw_surveyor) if raw_surveyor else None
    surveyor_name = next((n for n, sid in SURVEYORS.items() if sid == surveyor_id), None) if surveyor_id else None

    # Парсим дату
    date_info = _parse_measurement_date(raw_date) if raw_date else None

    # Парсим время
    time_from = raw_time.strip() if raw_time else None
    if time_from:
        # Вычисляем time_to (+1 час)
        try:
            h = int(time_from.split(":")[0])
            m = time_from.split(":")[1] if ":" in time_from else "00"
            time_to = f"{h + 1:02d}:{m}"
        except (ValueError, IndexError):
            time_from = None
            time_to = None
    else:
        time_to = None

    # ВСЁ ЕСТЬ → сразу создаём
    if date_info and time_from and surveyor_id and surveyor_name:
        date_str, day, month, year = date_info
        await callback.answer("Создаю запись...")
        await _do_kronos_create(callback, st, date_str, day, month, year, time_from, time_to, surveyor_id, surveyor_name)
        return

    # Замерщик есть, даты нет → сохраняем замерщика, спрашиваем дату
    if surveyor_id and surveyor_name and not (date_info and time_from):
        st["kronos_surveyor_id"] = surveyor_id
        st["kronos_surveyor_name"] = surveyor_name
        st["kronos_step"] = "datetime"
        await callback.message.answer(
            f"✅ Замерщик: <b>{surveyor_name}</b>\n\n"
            "📅 Введи <b>дату и время замера</b>:\n"
            "Формат: <code>25.03.2026 14:00</code>",
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    # Дата есть, замерщика нет → сохраняем дату, спрашиваем замерщика
    if date_info and time_from:
        st["kronos_date_info"] = date_info
        st["kronos_time_from"] = time_from
        st["kronos_time_to"] = time_to

    # Показываем выбор замерщика
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"kronos_surveyor_{sid}")]
        for name, sid in SURVEYORS.items()
    ] + [
        [InlineKeyboardButton(text="❌ Отмена", callback_data="kronos_cancel")]
    ])
    await callback.message.answer(
        "📅 <b>Запись в Кронос</b>\n\nВыбери замерщика:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("kronos_surveyor_"))
async def on_kronos_surveyor(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return

    surveyor_id = int(callback.data.replace("kronos_surveyor_", ""))
    st["kronos_surveyor_id"] = surveyor_id
    surveyor_name = next((n for n, sid in SURVEYORS.items() if sid == surveyor_id), "?")
    st["kronos_surveyor_name"] = surveyor_name

    # Если дата уже сохранена → сразу создаём
    date_info = st.get("kronos_date_info")
    time_from = st.get("kronos_time_from")
    time_to = st.get("kronos_time_to")
    if date_info and time_from:
        date_str, day, month, year = date_info
        await callback.answer("Создаю запись...")
        await _do_kronos_create(callback, st, date_str, day, month, year, time_from, time_to, surveyor_id, surveyor_name)
        return

    # Иначе спрашиваем дату
    st["kronos_step"] = "datetime"
    await callback.message.edit_text(
        f"✅ Замерщик: <b>{surveyor_name}</b>\n\n"
        "📅 Введи <b>дату и время замера</b>:\n"
        "Формат: <code>25.03.2026 14:00</code>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@dp.callback_query(F.data == "kronos_cancel")
async def on_kronos_cancel(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if st:
        st.pop("kronos_step", None)
        st.pop("kronos_surveyor_id", None)
        st.pop("kronos_surveyor_name", None)
        st.pop("kronos_date_info", None)
        st.pop("kronos_time_from", None)
        st.pop("kronos_time_to", None)
    await callback.message.edit_text("❌ Запись в Кронос отменена.")
    await callback.answer()


async def handle_kronos_datetime(message, st):
    """Обработка ввода даты/времени для Кроноса."""
    import re

    text = message.text.strip()
    # Парсим "25.03.2026 14:00" или "25.03 14:00" или "25.03.26 14:00"
    m = re.match(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s+(\d{1,2}):(\d{2})", text)
    if not m:
        await message.answer(
            "❌ Не могу разобрать дату.\n"
            "Формат: <code>25.03.2026 14:00</code>",
            parse_mode=ParseMode.HTML,
        )
        return True

    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else 2026
    if year < 100:
        year += 2000
    hour, minute = int(m.group(4)), int(m.group(5))

    date_str = f"{year}-{month:02d}-{day:02d}"
    time_from = f"{hour:02d}:{minute:02d}"
    time_to = f"{hour + 1:02d}:{minute:02d}"

    surveyor_id = st.get("kronos_surveyor_id")
    surveyor_name = st.get("kronos_surveyor_name", "?")

    await _do_kronos_create(message, st, date_str, day, month, year, time_from, time_to, surveyor_id, surveyor_name)
    return True


# --- Договор: FSM ---

CONTRACT_STEPS = [
    ("passport_photo", "📸 Скинь <b>фото паспорта</b> или <b>напиши все данные текстом</b>\n(ФИО, серия/номер, кем выдан, дата выдачи, адрес прописки):"),
    ("reg_address", "🏘 Скинь <b>фото прописки</b> или введи <b>адрес регистрации</b> текстом:"),
    ("contract_number", "📄 Введи <b>номер договора</b> (только число, например: 48):"),
    ("work_start", "🏗 Введи <b>дату начала работ</b> (ДД.ММ.ГГГГ):"),
    ("work_end", "🏗 Введи <b>дату окончания работ</b> (ДД.ММ.ГГГГ):"),
    ("payment_terms", "💰 Введи <b>условия оплаты</b> (свободный текст, например: «Аванс 26.03.2026 - 50000 руб. Окончательный расчет 30.03.2026 - 60000 руб.» или «Рассрочка: 1 апреля 30000, 1 мая 30000»):"),
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
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_contract")]
    ])
    await callback.message.answer(prompt, parse_mode=ParseMode.HTML, reply_markup=cancel_kb)
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
            # Буферизируем фото — может прийти media group (2 фото)
            if "photo_buffer" not in st:
                st["photo_buffer"] = []

            photo = message.photo[-1]
            file = await bot.get_file(photo.file_id)
            photo_bytes = await bot.download_file(file.file_path)
            data = photo_bytes.read()
            st["photo_buffer"].append(data)

            # Если это второе+ фото — просто добавляем и выходим (первое обработает)
            if len(st["photo_buffer"]) > 1:
                return True

            # Первое фото — ждём 2.5 сек на случай media group
            import asyncio
            await asyncio.sleep(2.5)

            # Обрабатываем все фото из буфера
            photos = st.pop("photo_buffer", [])
            processing = await message.answer(f"⏳ Распознаю паспорт ({len(photos)} фото)...")

            passport_result = None
            registration = None

            for photo_data in photos:
                try:
                    r = await parse_passport_photo(photo_data)
                    if r.get("error"):
                        continue

                    fn = r.get("full_name") or ""
                    ps = r.get("passport_series") or ""

                    if fn and ps:
                        # Это паспорт
                        passport_result = r
                    if r.get("registration_address"):
                        registration = r["registration_address"]
                except Exception as e:
                    logger.error("Ошибка распознавания фото: %s", e)

            if not passport_result:
                await processing.edit_text(
                    "❌ Не удалось распознать паспорт.\n"
                    "Скинь фото чётче или введи данные текстом."
                )
                return True

            st["contract_data"]["full_name"] = passport_result.get("full_name", "")
            series = passport_result.get("passport_series", "")
            number = passport_result.get("passport_number", "")
            st["contract_data"]["passport"] = f"{series} {number}"
            st["contract_data"]["passport_issued"] = passport_result.get("passport_issued_by", "")
            st["contract_data"]["passport_date"] = passport_result.get("passport_date", "")

            # Прописка — из паспорта или из отдельного фото
            if not registration and passport_result.get("registration_address"):
                registration = passport_result["registration_address"]
            if registration:
                st["contract_data"]["reg_address"] = registration

            summary = (
                "✅ <b>Распознано:</b>\n"
                f"👤 {st['contract_data']['full_name']}\n"
                f"🪪 {st['contract_data']['passport']}\n"
                f"📝 {st['contract_data']['passport_issued']}\n"
                f"📅 {st['contract_data']['passport_date']}"
            )
            if registration:
                summary += f"\n🏘 {registration}"

            await processing.edit_text(summary, parse_mode=ParseMode.HTML)

            if registration:
                st["contract_step"] = 2  # contract_number
                _, prompt = CONTRACT_STEPS[st["contract_step"]]
                await message.answer(prompt, parse_mode=ParseMode.HTML)
            else:
                st["contract_step"] = 1
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Без прописки", callback_data="skip_registration")],
                    [InlineKeyboardButton(text="📸 Добавить прописку", callback_data="add_registration")],
                ])
                await message.answer("Прописка не найдена.", reply_markup=kb)

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

            if not result or result.get("error"):
                await processing.edit_text("❌ Не удалось распознать. Попробуй ещё раз.")
                return True

            fn = result.get("full_name") or ""
            ps = result.get("passport_series") or ""
            pn = result.get("passport_number") or ""
            pi = result.get("passport_issued_by") or ""

            if not fn or not ps or not pn or not pi:
                missing = []
                if not fn: missing.append("ФИО")
                if not ps or not pn: missing.append("серия/номер паспорта")
                if not pi: missing.append("кем выдан")
                await processing.edit_text(
                    "⚠️ Не хватает данных: " + ", ".join(missing) + "\n\n"
                    "Введи все данные:\nФИО, серия номер, кем выдан, дата выдачи, адрес прописки"
                )
                return True

            st["contract_data"]["full_name"] = fn
            st["contract_data"]["passport"] = f"{ps} {pn}"
            st["contract_data"]["passport_issued"] = pi
            st["contract_data"]["passport_date"] = result.get("passport_date", "")

            reg = result.get("registration_address")
            if reg:
                st["contract_data"]["reg_address"] = reg

            summary = (
                "✅ <b>Распознано:</b>\n"
                f"👤 {fn}\n🪪 {ps} {pn}\n📝 {pi}\n📅 {result.get('passport_date', '')}"
            )
            if reg:
                summary += f"\n🏘 {reg}"
            await processing.edit_text(summary, parse_mode=ParseMode.HTML)

            if reg:
                st["contract_step"] = 2
                _, prompt = CONTRACT_STEPS[st["contract_step"]]
                await message.answer(prompt, parse_mode=ParseMode.HTML)
            else:
                st["contract_step"] = 1
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Без прописки", callback_data="skip_registration")],
                    [InlineKeyboardButton(text="📸 Добавить прописку", callback_data="add_registration")],
                ])
                await message.answer("Прописка не найдена.", reply_markup=kb)
            return True

        else:
            await message.answer("📸 Скинь фото паспорта или напиши данные текстом.")
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
        if message.photo:
            await message.answer("📸 Сейчас жду текст, а не фото. Введи данные текстом.")
        else:
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
        f"🏗 Окончание работ: {cd.get('work_end', '—')}\n"
        f"💰 Условия оплаты: {cd.get('payment_terms', '—')}\n"
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
        "work_end_date": cd["work_end"] + "г.",
        "payment_terms": cd["payment_terms"],
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

        st["contract_path"] = output_path  # сохраняем для АМО
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


@dp.callback_query(F.data == "cancel_contract")
async def on_cancel_contract(callback: CallbackQuery):
    """Отменяет создание договора."""
    st = user_state.get(callback.from_user.id)
    if st:
        st["contract_step"] = -1
        st.pop("contract_data", None)
    await callback.message.answer("❌ Создание договора отменено.")
    await callback.answer()


@dp.callback_query(F.data == "skip_registration")
async def on_skip_registration(callback: CallbackQuery):
    """Пропускает прописку — переходим к номеру договора."""
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return
    st["contract_data"]["reg_address"] = "—"
    st["contract_step"] = 2  # contract_number
    _, prompt = CONTRACT_STEPS[st["contract_step"]]
    await callback.message.answer(prompt, parse_mode=ParseMode.HTML)
    await callback.answer()


@dp.callback_query(F.data == "add_registration")
async def on_add_registration(callback: CallbackQuery):
    """Просит фото/текст прописки."""
    st = user_state.get(callback.from_user.id)
    if not st:
        await callback.answer("Отправь замер заново.")
        return
    st["contract_step"] = 1  # reg_address
    _, prompt = CONTRACT_STEPS[st["contract_step"]]
    await callback.message.answer(prompt, parse_mode=ParseMode.HTML)
    await callback.answer()


@dp.callback_query(F.data == "fill_missing")
async def on_fill_missing(callback: CallbackQuery):
    st = user_state.get(callback.from_user.id)
    if not st or not st["parsed"].get("missing_fields"):
        await callback.answer("Нет недостающих полей.")
        return
    missing = st["parsed"]["missing_fields"]
    st["awaiting_supplement"] = True  # ждём дополнение
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
