"""
ARTPOL — Интеграция с AMO CRM
Прямая API-интеграция через long-term токен.

⚠️ ВСЕ КЛЮЧИ — ТОЛЬКО ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ!
Railway Variables: AMO_TOKEN, AMO_DOMAIN
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

# ============================================================
# ⚠️ ТОКЕН И ДОМЕН — ТОЛЬКО ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ!
# ============================================================

AMO_TOKEN = os.environ.get("AMO_TOKEN", "")
AMO_DOMAIN = os.environ.get("AMO_DOMAIN", "artpol.amocrm.ru")
AMO_BASE_URL = f"https://{AMO_DOMAIN}/api/v4"

HEADERS = {
    "Authorization": f"Bearer {AMO_TOKEN}",
    "Content-Type": "application/json",
}


async def _amo_get(path: str, params: dict = None) -> dict:
    """GET запрос к AMO API."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(f"{AMO_BASE_URL}{path}", headers=HEADERS, params=params)
            if resp.status_code == 204:
                return {"_empty": True}
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("AMO GET %s: %s %s", path, e.response.status_code, e.response.text[:300])
        return {"error": str(e), "status": e.response.status_code}
    except Exception as e:
        logger.error("AMO GET %s: %s", path, e)
        return {"error": str(e)}


async def _amo_patch(path: str, data: dict) -> dict:
    """PATCH запрос к AMO API."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.patch(f"{AMO_BASE_URL}{path}", headers=HEADERS, json=data)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("AMO PATCH %s: %s %s", path, e.response.status_code, e.response.text[:300])
        return {"error": str(e), "status": e.response.status_code}
    except Exception as e:
        logger.error("AMO PATCH %s: %s", path, e)
        return {"error": str(e)}


async def _amo_post(path: str, data: list | dict) -> dict:
    """POST запрос к AMO API."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(f"{AMO_BASE_URL}{path}", headers=HEADERS, json=data)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("AMO POST %s: %s %s", path, e.response.status_code, e.response.text[:300])
        return {"error": str(e), "status": e.response.status_code}
    except Exception as e:
        logger.error("AMO POST %s: %s", path, e)
        return {"error": str(e)}


# ========== Воронки и этапы ==========

async def get_pipelines() -> list:
    """Получает все воронки и этапы."""
    data = await _amo_get("/leads/pipelines")
    if data.get("error") or data.get("_empty"):
        return []

    result = []
    for pipeline in data.get("_embedded", {}).get("pipelines", []):
        p = {
            "id": pipeline["id"],
            "name": pipeline["name"],
            "statuses": [],
        }
        for status in pipeline.get("_embedded", {}).get("statuses", []):
            p["statuses"].append({
                "id": status["id"],
                "name": status["name"],
                "sort": status.get("sort", 0),
            })
        p["statuses"].sort(key=lambda x: x["sort"])
        result.append(p)
    return result


async def format_pipelines() -> str:
    """Форматирует воронки для Telegram."""
    pipelines = await get_pipelines()
    if not pipelines:
        return "❌ Не удалось получить воронки. Проверь AMO_TOKEN."

    lines = ["📊 <b>Воронки AMO CRM:</b>\n"]
    for p in pipelines:
        lines.append(f"<b>{p['name']}</b> (ID: {p['id']})")
        for s in p["statuses"]:
            lines.append(f"  • {s['name']} — ID: {s['id']}")
        lines.append("")
    return "\n".join(lines)


# ========== Поиск сделки по телефону ==========

async def find_lead_by_phone(phone: str) -> dict | None:
    """
    Ищет сделку по номеру телефона в названии сделки.
    Возвращает сделку или None.
    """
    # Нормализуем телефон — убираем +, пробелы, скобки, тире
    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # Ищем по названию сделки
    data = await _amo_get("/leads", params={"query": clean_phone, "limit": 5})
    if data.get("error") or data.get("_empty"):
        # Попробуем с +
        data = await _amo_get("/leads", params={"query": phone, "limit": 5})
        if data.get("error") or data.get("_empty"):
            return None

    leads = data.get("_embedded", {}).get("leads", [])
    if not leads:
        return None

    # Ищем сделку где телефон в названии
    for lead in leads:
        name = lead.get("name", "")
        if clean_phone in name.replace("+", "").replace(" ", "").replace("-", ""):
            return lead

    # Если не нашли точное совпадение — возвращаем первую
    return leads[0] if leads else None


# ========== Обновление сделки ==========

async def update_lead(
    lead_id: int,
    price: int = None,
    status_id: int = None,
    custom_fields: list = None,
) -> dict:
    """Обновляет сделку."""
    payload = {}
    if price is not None:
        payload["price"] = price
    if status_id is not None:
        payload["status_id"] = status_id
    if custom_fields:
        payload["custom_fields_values"] = custom_fields

    return await _amo_patch(f"/leads/{lead_id}", payload)


async def add_note_to_lead(lead_id: int, text: str) -> dict:
    """Добавляет примечание к сделке."""
    data = [{
        "entity_id": lead_id,
        "note_type": "common",
        "params": {
            "text": text,
        },
    }]
    return await _amo_post(f"/leads/{lead_id}/notes", data)


# ========== Получение кастомных полей ==========

async def get_lead_custom_fields() -> list:
    """Получает кастомные поля сделок."""
    data = await _amo_get("/leads/custom_fields")
    if data.get("error") or data.get("_empty"):
        return []

    fields = []
    for f in data.get("_embedded", {}).get("custom_fields", []):
        fields.append({
            "id": f["id"],
            "name": f["name"],
            "type": f.get("type", ""),
        })
    return fields


async def format_custom_fields() -> str:
    """Форматирует кастомные поля для Telegram."""
    fields = await get_lead_custom_fields()
    if not fields:
        return "❌ Не удалось получить поля."

    lines = ["📋 <b>Кастомные поля сделок:</b>\n"]
    for f in fields:
        lines.append(f"  • {f['name']} — ID: {f['id']} ({f['type']})")
    return "\n".join(lines)


# ========== Основная функция: заполнить AMO ==========

async def fill_amo_lead(
    phone: str,
    price: int,
    raw_text: str,
    area: float,
    floor: int,
    address: str,
    measurement_date: str,
    target_status_id: int,
    field_ids: dict = None,
) -> dict:
    """
    Находит сделку по телефону и заполняет данные.

    field_ids: {"area": 123, "floor": 456, "address": 789}
    """
    # 1. Ищем сделку
    lead = await find_lead_by_phone(phone)
    if not lead:
        return {"error": "not_found", "detail": f"Сделка с телефоном {phone} не найдена"}

    lead_id = lead["id"]
    lead_name = lead.get("name", "")
    logger.info("AMO: нашли сделку #%s '%s'", lead_id, lead_name)

    # 2. Обновляем бюджет и статус
    update_result = await update_lead(
        lead_id=lead_id,
        price=price,
        status_id=target_status_id,
    )

    if update_result.get("error"):
        return {"error": "update_failed", "detail": str(update_result)}

    # 3. Добавляем примечание с замером
    note_text = (
        f"📋 ЗАМЕР от {measurement_date}\n"
        f"📐 Площадь: {area} м²\n"
        f"🏢 Этаж: {floor}\n"
        f"🏘 Адрес: {address}\n"
        f"💰 Бюджет: {price:,}₽\n\n"
        f"--- Текст замера ---\n{raw_text}"
    )
    await add_note_to_lead(lead_id, note_text)

    logger.info("AMO: сделка #%s обновлена, бюджет %s, статус %s", lead_id, price, target_status_id)

    return {
        "success": True,
        "lead_id": lead_id,
        "lead_name": lead_name,
    }
