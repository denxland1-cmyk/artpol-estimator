"""
Интеграция с Кронос 2.0 (ГЕНЕЗИС) — система записи в amoCRM.
Создание записей (замеров) через публичное API.

⚠️ KRONOS_API_KEY и KRONOS_FILIAL_ID — ТОЛЬКО в Railway Variables!
"""

import os
import logging
import aiohttp

logger = logging.getLogger(__name__)

# ---------- Конфигурация ----------

KRONOS_BASE_URL = "https://genezis-platform-api.gnzs.ru/api/v1"
KRONOS_API_KEY = os.getenv("KRONOS_API_KEY", "")
KRONOS_FILIAL_ID = os.getenv("KRONOS_FILIAL_ID", "1654")

# Услуга «Замер» (60 мин)
SERVICE_ZAMER_ID = 10258

# Замерщики (resourceTypeId: 1817)
SURVEYORS = {
    "Дмитрий Рябов": 5964,
    "Кирилл Шкарин": 14328,
    "Владимир Чернов": 26071,
}

# Для удобного поиска по частичному имени
SURVEYOR_ALIASES = {
    "рябов": 5964,
    "дмитрий": 5964,
    "шкарин": 14328,
    "кирилл": 14328,
    "чернов": 26071,
    "владимир": 26071,
}


def _headers():
    return {
        "Content-Type": "application/json",
        "X-API-KEY": KRONOS_API_KEY,
        "x-filial-id": KRONOS_FILIAL_ID,
    }


def find_surveyor_id(name: str) -> int | None:
    """Найти ID замерщика по имени/фамилии."""
    if not name:
        return None
    low = name.lower().strip()
    # Точное совпадение
    for full_name, sid in SURVEYORS.items():
        if low in full_name.lower():
            return sid
    # По алиасам
    for alias, sid in SURVEYOR_ALIASES.items():
        if alias in low:
            return sid
    return None


async def create_event(
    date: str,
    time_from: str,
    time_to: str | None = None,
    surveyor_id: int | None = None,
    contact_name: str = "",
    contact_phone: str = "",
    address: str = "",
    status_id: int = 3,
) -> dict | None:
    """
    Создать запись (замер) в Кроносе.

    Args:
        date: дата замера "2026-03-25"
        time_from: время начала "14:00"
        time_to: время окончания "15:00" (если None — +1 час)
        surveyor_id: ID замерщика из SURVEYORS
        contact_name: имя клиента
        contact_phone: телефон клиента
        address: адрес объекта (будет в названии записи)
        status_id: статус записи (3 по умолчанию)

    Returns:
        dict с данными созданной записи или None при ошибке
    """
    if not KRONOS_API_KEY:
        logger.error("KRONOS_API_KEY не задан!")
        return None

    # Время окончания = +1 час если не указано
    if not time_to:
        hour = int(time_from.split(":")[0])
        minute = time_from.split(":")[1] if ":" in time_from else "00"
        time_to = f"{hour + 1:02d}:{minute}"

    body = {
        "dateFrom": date,
        "dateTo": date,
        "timeFrom": time_from,
        "timeTo": time_to,
        "statusId": status_id,
        "order": {
            "products": [
                {
                    "id": SERVICE_ZAMER_ID,
                    "count": 1,
                    "price": 0,
                }
            ]
        },
        "contact": {
            "name": contact_name or "Заказчик",
            "phone": contact_phone or "",
        },
    }

    # Замерщик
    if surveyor_id:
        body["resources"] = [{"id": surveyor_id}]

    logger.info("Кронос: создаём запись %s %s-%s, замерщик=%s", date, time_from, time_to, surveyor_id)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{KRONOS_BASE_URL}/event",
                json=body,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    logger.info("Кронос: запись создана, id=%s", data.get("id"))
                    return data
                else:
                    text = await resp.text()
                    logger.error("Кронос: ошибка %s — %s", resp.status, text)
                    return None
    except Exception as e:
        logger.error("Кронос: исключение — %s", e)
        return None


async def bind_lead(event_id: int, lead_id: int) -> bool:
    """
    Привязать сделку AMO к записи Кроноса.

    Args:
        event_id: ID записи в Кроносе
        lead_id: ID сделки в AMO CRM

    Returns:
        True если успешно
    """
    if not KRONOS_API_KEY:
        logger.error("KRONOS_API_KEY не задан!")
        return False

    body = {
        "leadId": lead_id,
        "eventId": event_id,
    }

    logger.info("Кронос: привязка lead=%s к event=%s", lead_id, event_id)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{KRONOS_BASE_URL}/event/bind-lead",
                json=body,
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (200, 201):
                    logger.info("Кронос: сделка привязана")
                    return True
                else:
                    text = await resp.text()
                    logger.error("Кронос: ошибка привязки %s — %s", resp.status, text)
                    return False
    except Exception as e:
        logger.error("Кронос: исключение привязки — %s", e)
        return False


async def get_resources() -> list:
    """Получить список ресурсов (для отладки)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KRONOS_BASE_URL}/resources",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    logger.error("Кронос resources: %s — %s", resp.status, text)
                    return []
    except Exception as e:
        logger.error("Кронос resources: %s", e)
        return []
