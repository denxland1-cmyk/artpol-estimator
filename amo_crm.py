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

# Воронка "Полусухая стяжка"
PIPELINE_ID = 1055275
STATUS_MEASUREMENT_DONE = 70381582   # Замер состоялся
STATUS_OFFER_MADE = 18964384         # Сделано предложение

# Кастомные поля сделок
FIELD_AREA = 657699           # Площадь, м2 (numeric)
FIELD_FLOOR = 657701          # Этаж (numeric)
FIELD_ADDRESS = 657535        # Адрес (streetaddress)
FIELD_MEASUREMENT_DT = 657533 # Дата и время замера (date_time)
FIELD_INFO = 657579           # Общая информация (textarea)
FIELD_OBJECT_TYPE = 658967    # Тип объекта (text)
FIELD_THICKNESS = 658969      # Толщина стяжки (text)

# Группа СМЕТА — кастомные поля для сметных данных
FIELD_SMETA_THICKNESS = 663353    # Толщина (мм) — numeric
FIELD_SMETA_MATERIAL = 663355     # Материал — numeric
FIELD_SMETA_DELIVERY_MAT = 663357 # Доставка материала — numeric
# 663359 — УДАЛЕНО (старое "Работы"), заменено на 663449 "Работы стяжка"
FIELD_SMETA_EXTRAS = 663361       # Допы в смете — numeric
FIELD_SMETA_DELIVERY_EQ = 663363  # Доставка оборудования — numeric
FIELD_SMETA_SAND = 663365         # Песок (т) — numeric
FIELD_SMETA_CEMENT = 663367       # Цемент (мешки) — numeric
FIELD_SMETA_FIBER = 663369        # Фибра (кг) — numeric
# 663371 — УДАЛЕНО (старое "Плёнка м2"), заменено на 663395 + 663397
# 663373 — УДАЛЕНО (старое "Тип плёнки")
FIELD_SMETA_KERAMZIT = 663375     # Керамзит (мешки) — numeric
FIELD_SMETA_MESH = 663377         # Сетка (м2) — numeric
FIELD_SMETA_IZOFLEX = 663379      # Izoflex (п.м.) — numeric
FIELD_SMETA_PAYMENT = 663383      # Тип оплаты — select

# Поля СМЕТЫ (актуальные)
FIELD_SMETA_FILM_TECH = 663395    # Плёнка техническая (м2) — numeric
FIELD_SMETA_FILM_ARM = 663397     # Плёнка армированная (м2) — numeric
FIELD_SMETA_WORK_SCREED = 663449  # Работы стяжка — numeric
FIELD_SMETA_WORK_KERAMZIT = 663453  # Работы керамзит — numeric
FIELD_SMETA_SAND_REMOVAL = 663455 # Вывоз песка — select (Да/Нет)
FIELD_SMETA_NOTES = 663457        # Примечания по объекту — text
FIELD_SMETA_WORK_MESH = 663469    # Работы сетка — numeric
FIELD_SMETA_WORK_EPPS = 663471    # Работы ЭППС — numeric
FIELD_SMETA_WORK_SAND_BASE = 663473  # Работы песчаное основание — numeric
FIELD_SMETA_AREA_KERAMZIT = 663483   # Площадь керамзит (м2) — numeric
FIELD_SMETA_AREA_MESH = 663485       # Площадь сетка (м2) — numeric
FIELD_SMETA_AREA_EPPS = 663487       # Площадь ЭППС (м2) — numeric
FIELD_SMETA_AREA_SAND_BASE = 663489  # Площадь песчаное основание (м2) — text

# Факт-поля (заполняются после выполнения работ, НЕ при создании сметы)
FIELD_SMETA_FACT_DEL_MAT = 663459   # Факт доставка материала — numeric
FIELD_SMETA_FACT_MATERIAL = 663461  # Факт материал — numeric
FIELD_SMETA_FACT_WORK = 663465      # Факт работы — numeric
FIELD_SMETA_FACT_DEL_EQ = 663467    # Факт доставка оборудования — numeric

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


# ========== Получение сделки по ID ==========

async def get_lead_by_id(lead_id: int) -> dict | None:
    """
    Получает сделку по ID с контактами.
    Возвращает dict с полями сделки или None.
    """
    data = await _amo_get(f"/leads/{lead_id}", params={"with": "contacts"})
    if data.get("error"):
        logger.error("AMO: ошибка получения сделки #%s: %s", lead_id, data)
        return None

    if not data.get("id"):
        return None

    # Извлекаем кастомные поля
    custom = {}
    for cf in data.get("custom_fields_values") or []:
        fid = cf.get("field_id")
        vals = cf.get("values", [])
        val = vals[0].get("value") if vals else None
        custom[fid] = val

    result = {
        "id": data["id"],
        "name": data.get("name", ""),
        "price": data.get("price", 0),
        "area": custom.get(FIELD_AREA),
        "floor": custom.get(FIELD_FLOOR),
        "address": custom.get(FIELD_ADDRESS),
        "thickness": custom.get(FIELD_THICKNESS),
        "object_type": custom.get(FIELD_OBJECT_TYPE),
        "info": custom.get(FIELD_INFO),
    }

    # Получаем контакт (телефон)
    contacts = data.get("_embedded", {}).get("contacts", [])
    if contacts:
        contact_id = contacts[0].get("id")
        if contact_id:
            contact_data = await _amo_get(f"/contacts/{contact_id}")
            if not contact_data.get("error"):
                result["contact_name"] = contact_data.get("name", "")
                for cf in contact_data.get("custom_fields_values") or []:
                    code = cf.get("field_code", "")
                    if code == "PHONE":
                        vals = cf.get("values", [])
                        result["phone"] = vals[0].get("value", "") if vals else ""
                        break

    logger.info("AMO: получена сделка #%s '%s', бюджет=%s", result["id"], result["name"], result["price"])
    return result


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

    # Если не нашли точное совпадение — НЕ возвращаем чужую сделку
    logger.info("AMO: телефон %s не найден в названиях сделок, вернули None", clean_phone)
    return None


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


async def upload_file_to_lead(lead_id: int, file_path: str, filename: str) -> dict:
    """Загружает файл в Yandex S3 и прикрепляет ссылку к сделке."""
    import os
    if not os.path.exists(file_path):
        logger.error("AMO upload: файл не найден %s", file_path)
        return {"error": "file_not_found"}

    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            "s3",
            endpoint_url="https://storage.yandexcloud.net",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            config=Config(signature_version="s3v4"),
            region_name="ru-central1",
        )

        bucket = os.environ.get("S3_BUCKET", "artpol-docs")
        s3_key = f"docs/{filename}"

        s3.upload_file(
            file_path, bucket, s3_key,
            ExtraArgs={
                "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "ACL": "public-read",
            },
        )

        public_url = f"https://{bucket}.storage.yandexcloud.net/{s3_key}"
        logger.info("S3: файл загружен → %s", public_url)

        # Прикрепляем ссылку как примечание к сделке
        note_data = [{
            "entity_id": lead_id,
            "note_type": "common",
            "params": {
                "text": f"📎 {filename}\n{public_url}",
            },
        }]
        await _amo_post(f"/leads/{lead_id}/notes", note_data)

        return {"success": True, "url": public_url}

    except Exception as e:
        logger.error("S3/AMO upload error: %s", e, exc_info=True)
        return {"error": str(e)}


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


async def create_lead(name: str, price: int, status_id: int, custom_fields: list = None, phone: str = None, client_name: str = None) -> dict:
    """Создаёт новую сделку + контакт."""
    lead_data = [{
        "name": name,
        "price": price,
        "status_id": status_id,
        "pipeline_id": PIPELINE_ID,
    }]
    if custom_fields:
        lead_data[0]["custom_fields_values"] = custom_fields

    # Если есть телефон — создаём через complex (сделка + контакт)
    if phone:
        contact_name = client_name or name
        complex_data = [{
            "name": name,
            "price": price,
            "status_id": status_id,
            "pipeline_id": PIPELINE_ID,
            "custom_fields_values": custom_fields or [],
            "_embedded": {
                "contacts": [{
                    "first_name": contact_name,
                    "custom_fields_values": [{
                        "field_code": "PHONE",
                        "values": [{"value": phone}],
                    }],
                }],
            },
        }]
        result = await _amo_post("/leads/complex", complex_data)
        # /leads/complex может вернуть list (ID сделок) или dict с ошибкой
        if isinstance(result, list):
            # Успех — список ID
            if result:
                lead_id = result[0].get("id") if isinstance(result[0], dict) else result[0]
                return {"success": True, "lead_id": lead_id}
            return {"error": "create_failed", "detail": "Пустой ответ от AMO"}
        if not result.get("error"):
            # complex возвращает dict с _embedded
            lead_ids = result.get("_embedded", {}).get("leads", [])
            if lead_ids:
                lead_id = lead_ids[0].get("id") if isinstance(lead_ids[0], dict) else lead_ids[0]
                return {"success": True, "lead_id": lead_id}
        return result

    return await _amo_post("/leads", lead_data)


# ========== Построение полей СМЕТЫ ==========

def build_smeta_fields(
    estimate: dict,
    parsed: dict,
    payment: str = "",
    sand_removal: bool = False,
) -> list:
    """
    Формирует кастомные поля группы СМЕТА из данных сметы.
    Возвращает список для custom_fields_values.
    """
    if not estimate:
        return []

    s = estimate.get("sand", {})
    c = estimate.get("cement", {})
    f = estimate.get("fiber", {})
    fl = estimate.get("film", {})
    iz = estimate.get("izoflex", {})
    eq = estimate.get("equipment_delivery", {})
    w = estimate.get("work", {})
    k = estimate.get("keramzit")

    thickness = parsed.get("thickness_mm_avg", 0) or 0
    keramzit_data = parsed.get("keramzit") or {}

    # Материал = все материалы БЕЗ доставки материала и доставки оборудования
    material = (
        s.get("total", 0)           # Песок (цена + доставка в одной строке)
        + c.get("cement_cost", 0)    # Цемент
        + f.get("cost", 0)           # Фибра
        + fl.get("cost", 0)          # Плёнка техническая
        + iz.get("cost", 0)          # Izoflex
    )
    if k:
        material += k.get("keramzit_cost", 0)          # Керамзит
        material += k.get("reinforced_film_cost", 0)    # Арм. плёнка
        material += k.get("mesh_cost", 0)               # Сетка

    # Доставка материала
    delivery_material = c.get("delivery", 0)

    # Работы стяжка
    work_screed = w.get("cost", 0)

    # Работы керамзит
    work_keramzit = k.get("keramzit_work_cost", 0) if k else 0

    # Допы в смете = сумма всех доп. работ
    extras = work_keramzit
    if sand_removal:
        extras += 5000

    # Доставка оборудования
    delivery_eq = eq.get("cost", 0)

    # === Основные поля ===
    fields = [
        {"field_id": FIELD_SMETA_THICKNESS, "values": [{"value": thickness}]},
        {"field_id": FIELD_SMETA_MATERIAL, "values": [{"value": material}]},
        {"field_id": FIELD_SMETA_DELIVERY_MAT, "values": [{"value": delivery_material}]},
        {"field_id": FIELD_SMETA_EXTRAS, "values": [{"value": extras}]},
        {"field_id": FIELD_SMETA_DELIVERY_EQ, "values": [{"value": delivery_eq}]},
        {"field_id": FIELD_SMETA_SAND, "values": [{"value": s.get("sand_tons", 0)}]},
        {"field_id": FIELD_SMETA_CEMENT, "values": [{"value": c.get("bags", 0)}]},
        {"field_id": FIELD_SMETA_FIBER, "values": [{"value": f.get("kg", 0)}]},
        {"field_id": FIELD_SMETA_IZOFLEX, "values": [{"value": iz.get("meters", 0)}]},
    ]

    # === Плёнка: техническая + армированная ===
    fields.append({"field_id": FIELD_SMETA_FILM_TECH, "values": [{"value": fl.get("m2", 0)}]})
    if k:
        fields.append({"field_id": FIELD_SMETA_FILM_ARM, "values": [{"value": k.get("reinforced_film_m2", 0)}]})
    else:
        fields.append({"field_id": FIELD_SMETA_FILM_ARM, "values": [{"value": 0}]})

    # === Керамзит, сетка ===
    if k:
        fields.append({"field_id": FIELD_SMETA_KERAMZIT, "values": [{"value": k.get("keramzit_bags", 0)}]})
        fields.append({"field_id": FIELD_SMETA_MESH, "values": [{"value": k.get("mesh_m2", 0)}]})
    else:
        fields.append({"field_id": FIELD_SMETA_KERAMZIT, "values": [{"value": 0}]})
        fields.append({"field_id": FIELD_SMETA_MESH, "values": [{"value": 0}]})

    # === Работы (детализация) ===
    fields.append({"field_id": FIELD_SMETA_WORK_SCREED, "values": [{"value": work_screed}]})
    fields.append({"field_id": FIELD_SMETA_WORK_KERAMZIT, "values": [{"value": work_keramzit}]})
    fields.append({"field_id": FIELD_SMETA_WORK_MESH, "values": [{"value": 0}]})        # пока не в калькуляторе
    fields.append({"field_id": FIELD_SMETA_WORK_EPPS, "values": [{"value": 0}]})        # пока не в калькуляторе
    fields.append({"field_id": FIELD_SMETA_WORK_SAND_BASE, "values": [{"value": 0}]})   # пока не в калькуляторе

    # === Площади доп. работ ===
    ker_area = keramzit_data.get("area_m2", 0)
    fields.append({"field_id": FIELD_SMETA_AREA_KERAMZIT, "values": [{"value": ker_area}]})
    # Сетка обычно на той же площади что и керамзит
    fields.append({"field_id": FIELD_SMETA_AREA_MESH, "values": [{"value": k.get("mesh_m2", 0) if k else 0}]})
    fields.append({"field_id": FIELD_SMETA_AREA_EPPS, "values": [{"value": 0}]})
    fields.append({"field_id": FIELD_SMETA_AREA_SAND_BASE, "values": [{"value": "0"}]})

    # === Вывоз песка (select Есть/Нет) ===
    fields.append({"field_id": FIELD_SMETA_SAND_REMOVAL, "values": [{"value": "Есть" if sand_removal else "Нет"}]})

    # === Примечания — "без изменений" по умолчанию ===
    fields.append({"field_id": FIELD_SMETA_NOTES, "values": [{"value": "без изменений"}]})

    # === Тип оплаты ===
    if payment:
        payment_text = "наличный" if payment == "наличными" else "безналичный" if payment == "безналичный расчет" else ""
        if payment_text:
            fields.append({"field_id": FIELD_SMETA_PAYMENT, "values": [{"value": payment_text}]})

    logger.info(
        "СМЕТА: материал=%s, дост_мат=%s, работы=%s (стяжка=%s, керамзит=%s), допы=%s, обор=%s",
        material, delivery_material, work_screed + work_keramzit, work_screed, work_keramzit, extras, delivery_eq,
    )

    return fields


# ========== Основная функция: заполнить AMO ==========

async def fill_amo_lead(
    phone: str,
    price: int,
    raw_text: str,
    area: float,
    thickness: float,
    floor: int,
    address: str,
    object_type: str,
    measurement_datetime: str,
    measurement_timestamp: int = None,
    client_name: str = "",
    estimate: dict = None,
    parsed: dict = None,
    payment: str = "",
    sand_removal: bool = False,
    lead_id: int = None,
) -> dict:
    """
    Находит сделку по телефону и заполняет данные.
    Если lead_id указан — обновляет напрямую, без поиска по телефону.
    Если не найдена — создаёт новую.
    Двигает в "Сделано предложение".
    """
    # 1. Формируем кастомные поля
    custom_fields = [
        {"field_id": FIELD_AREA, "values": [{"value": area}]},
        {"field_id": FIELD_FLOOR, "values": [{"value": floor}]},
        {"field_id": FIELD_ADDRESS, "values": [{"value": address}]},
        {"field_id": FIELD_OBJECT_TYPE, "values": [{"value": object_type or "квартира"}]},
        {"field_id": FIELD_THICKNESS, "values": [{"value": f"{thickness} мм"}]},
        {"field_id": FIELD_INFO, "values": [{"value": raw_text}]},
    ]

    # 1b. Добавляем поля СМЕТЫ если есть данные
    if estimate and parsed:
        smeta_fields = build_smeta_fields(
            estimate=estimate,
            parsed=parsed,
            payment=payment,
            sand_removal=sand_removal,
        )
        custom_fields.extend(smeta_fields)

    if measurement_timestamp:
        custom_fields.append(
            {"field_id": FIELD_MEASUREMENT_DT, "values": [{"value": measurement_timestamp}]}
        )

    # 2. Если указан lead_id — обновляем напрямую
    if lead_id:
        logger.info("AMO: обновляем сделку #%s напрямую (по ID)", lead_id)
        lead_data = await get_lead_by_id(lead_id)
        lead_name = lead_data.get("name", f"#{lead_id}") if lead_data else f"#{lead_id}"

        update_result = await update_lead(
            lead_id=lead_id,
            price=price,
            status_id=STATUS_OFFER_MADE,
            custom_fields=custom_fields,
        )
        if update_result.get("error"):
            return {"error": "update_failed", "detail": str(update_result)}

        # Примечание
        note_text = (
            f"📋 ЗАМЕР от {measurement_datetime}\n"
            f"📐 Площадь: {area} м²\n"
            f"📏 Толщина: {thickness} мм\n"
            f"🏢 Этаж: {floor}\n"
            f"🏘 Адрес: {address}\n"
            f"💰 Бюджет: {price:,}₽\n\n"
            f"--- Текст замера ---\n{raw_text}"
        )
        await add_note_to_lead(lead_id, note_text)

        logger.info("AMO: сделка #%s обновлена → 'Сделано предложение', бюджет %s₽", lead_id, price)
        return {
            "success": True,
            "lead_id": lead_id,
            "lead_name": lead_name,
            "created_new": False,
        }

    # 3. Ищем сделку по телефону
    lead = await find_lead_by_phone(phone)
    created_new = False

    if lead:
        lead_id = lead["id"]
        lead_name = lead.get("name", "")
        logger.info("AMO: нашли сделку #%s '%s'", lead_id, lead_name)

        # 3a. Обновляем существующую
        update_result = await update_lead(
            lead_id=lead_id,
            price=price,
            status_id=STATUS_OFFER_MADE,
            custom_fields=custom_fields,
        )
        if update_result.get("error"):
            return {"error": "update_failed", "detail": str(update_result)}
    else:
        # 3b. Создаём новую сделку
        lead_name = f"{client_name} {phone}" if client_name else phone
        logger.info("AMO: сделка не найдена, создаём новую '%s'", lead_name)

        create_result = await create_lead(
            name=lead_name,
            price=price,
            status_id=STATUS_OFFER_MADE,
            custom_fields=custom_fields,
            phone=phone,
            client_name=client_name,
        )

        if create_result.get("error"):
            return {"error": "create_failed", "detail": str(create_result)}

        lead_id = create_result.get("lead_id")
        if not lead_id:
            # Пробуем извлечь из ответа
            embedded = create_result.get("_embedded", {})
            leads = embedded.get("leads", [])
            lead_id = leads[0]["id"] if leads else None

        if not lead_id:
            return {"error": "create_failed", "detail": "Не удалось получить ID новой сделки"}

        created_new = True

    # 4. Примечание с замером
    note_text = (
        f"📋 ЗАМЕР от {measurement_datetime}\n"
        f"📐 Площадь: {area} м²\n"
        f"📏 Толщина: {thickness} мм\n"
        f"🏢 Этаж: {floor}\n"
        f"🏘 Адрес: {address}\n"
        f"💰 Бюджет: {price:,}₽\n\n"
        f"--- Текст замера ---\n{raw_text}"
    )
    await add_note_to_lead(lead_id, note_text)

    action = "создана" if created_new else "обновлена"
    logger.info("AMO: сделка #%s %s → 'Сделано предложение', бюджет %s₽", lead_id, action, price)

    return {
        "success": True,
        "lead_id": lead_id,
        "lead_name": lead_name,
        "created_new": created_new,
    }
