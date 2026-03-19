"""
ARTPOL — AI-парсер текста замерщика
Извлекает параметры замера из свободного текста → JSON
Считает расстояние от базы до объекта (Яндекс Routes API)
"""

import os
import json
import logging
import httpx
from anthropic import Anthropic

logger = logging.getLogger(__name__)

# ============================================================
# ⚠️ ВСЕ КЛЮЧИ И ТОКЕНЫ — ТОЛЬКО ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ!
# НИКОГДА не вставляй значения прямо в код!
# Railway Variables / .env + python-dotenv
# ============================================================

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Координаты базы: Нижний Новгород, Интернациональная, 100
BASE_LAT = 56.310043
BASE_LON = 43.953282

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ---------- Промпт для Claude Haiku ----------

PARSER_SYSTEM_PROMPT = """\
Ты — парсер данных замера для компании по устройству полусухой стяжки пола.
Из текста замерщика извлеки параметры и верни ТОЛЬКО валидный JSON, без пояснений.

Формат ответа:
{
  "client_name": "строка" или null,
  "client_phone": "строка" или null,
  "zones": [{"name": "строка", "area_m2": число, "thickness_mm": число}, ...],
  "object_type": "квартира" | "дом" | "коммерция" | null,
  "location_type": "город" | "за городом" | null,
  "floor": число или null,
  "warm_floor": true | false | null,
  "keramzit": {"area_m2": число, "thickness_mm": число} или null,
  "deadline": "строка" или null,
  "address": "строка" или null,
  "coordinates": {"lat": число, "lon": число} или null,
  "special_conditions": ["строка", ...] или []
}

Правила:
- client_name: имя клиента/заказчика если упомянуто в тексте (например "Алексей", "Иванов Пётр")
- client_phone: номер телефона если указан (сохрани как есть, с +7 или 8)
- zones: массив зон объекта. Каждая зона — это часть объекта со своей площадью и толщиной слоя.
  Если в тексте одна зона (например "78м², слой 50мм") — верни одну зону: [{"name": "объект", "area_m2": 78, "thickness_mm": 50}].
  Частый формат: площадь и толщина на одной строке БЕЗ слова "слой": "40м2 104мм" = площадь 40, толщина 104мм. "17.25м2 на 95мм" = площадь 17.25, толщина 95мм. ВСЕГДА ищи число с "мм" — это толщина слоя!
  Если несколько зон с разной толщиной (например "1 этаж 111.7м2 на 95мм, 2 этаж 92.1м2 на 85мм") — верни каждую отдельно.
  Если площадь указана без толщины — поставь thickness_mm: null. И наоборот.
  Если указана только общая площадь без разбивки — одна зона с name: "объект".
  Толщину в см переводи в мм (6см = 60мм).
  ВАЖНО: санузел (с/у) с указанием "ниже на X мм" — это НЕ отдельная зона! Это модификатор уровня.
  Площадь санузла входит в площадь этажа. НЕ создавай отдельную зону для санузла.
  Вместо этого ОБЯЗАТЕЛЬНО добавь в special_conditions запись БОЛЬШИМИ БУКВАМИ, например:
  "САНУЗЕЛ 1 ЭТАЖ: УРОВЕНЬ СТЯЖКИ -10 ММ ОТ УРОВНЯ ЭТАЖА"
- object_type: определи по контексту (квартира, частный дом, коммерческое помещение)
- location_type: "город" если ЖК, улица в Нижнем Новгороде; "за городом" если деревня, посёлок, коттеджный посёлок
- floor: номер этажа объекта (число). ВАЖНО: в адресах формат ВСЕГДА такой: улица, дом, квартира, этаж. После номера дома ВСЕГДА идёт номер квартиры, а потом этаж. Пример: "Ленина 53к1 63 эт 1" означает дом 53 корпус 1, квартира 63, этаж 1. НЕ ПУТАЙ квартиру с этажом! Этаж — это число после "эт"/"этаж". Если этаж не указан — null.
- warm_floor: true если упоминается тёплый пол, иначе false; null если неясно
- deadline: сроки как строка (например "к 20 марта", "срочно", "на следующей неделе")
- address: полный адрес включая номер квартиры — как есть из текста. Пример: "пр-т Ленина 53к1 кв.63"
- coordinates: широта и долгота если указаны в тексте (любой формат)
- keramzit: керамзитное основание. Если в тексте упоминается керамзит/керамзитное основание с площадью и толщиной слоя — верни {"area_m2": число, "thickness_mm": число}. Площадь керамзита может отличаться от площади стяжки! Толщину в см переводи в мм. Если керамзит не упомянут — null.
- special_conditions: список особых условий (этаж, подъём материалов, демонтаж, вывоз остатков песка и т.д.)

Если параметр не упомянут — ставь null (для массива — пустой []).
Не выдумывай данные. Извлекай только то, что явно есть в тексте.
"""


async def parse_measurement_text(text: str) -> dict:
    """
    Отправляет текст замерщика в Claude Haiku.
    Возвращает словарь с извлечёнными параметрами.
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=PARSER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text.strip()

        # Убираем ```json обёртку если Claude её добавил
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        parsed = json.loads(raw)
        logger.info("Парсинг успешен: %s", json.dumps(parsed, ensure_ascii=False))
        return parsed

    except json.JSONDecodeError as e:
        logger.error("Claude вернул невалидный JSON: %s | raw: %s", e, raw)
        return {"error": "parse_failed", "raw_response": raw}
    except Exception as e:
        logger.error("Ошибка вызова Claude API: %s", e)
        return {"error": "api_failed", "detail": str(e)}


# ---------- OSRM — расстояние по дороге (бесплатно) ----------


async def get_distance_km(lat: float, lon: float) -> dict:
    """
    Считает расстояние по дороге от базы до объекта.
    OSRM (Open Source Routing Machine) — бесплатный, без ключа.
    ВАЖНО: OSRM принимает координаты как lon,lat (не lat,lon!)
    Возвращает {"distance_km": число, "duration_min": число} или {"error": ...}
    """
    url = (
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{BASE_LON},{BASE_LAT};{lon},{lat}"
    )
    params = {"overview": "false"}

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != "Ok":
            return {"error": f"OSRM code: {data.get('code')}"}

        route = data["routes"][0]
        distance_km = round(route["distance"] / 1000, 1)
        duration_min = round(route["duration"] / 60)

        logger.info("OSRM: %.4f,%.4f → %s км (~%s мин)", lat, lon, distance_km, duration_min)
        return {
            "distance_km": distance_km,
            "duration_min": duration_min,
        }

    except Exception as e:
        logger.error("Ошибка OSRM: %s", e)
        return {"error": str(e)}


# ---------- ПАСПОРТ — распознавание фото ----------

PASSPORT_PROMPT = """Ты — парсер паспорта РФ. Из фото паспорта извлеки данные и верни ТОЛЬКО валидный JSON.

Формат ответа:
{
  "full_name": "Фамилия Имя Отчество",
  "passport_series": "ХХХХ",
  "passport_number": "ХХХХХХ",
  "passport_issued_by": "кем выдан",
  "passport_date": "ДД.ММ.ГГГГ",
  "birth_date": "ДД.ММ.ГГГГ",
  "registration_address": "адрес регистрации" или null
}

Правила:
- full_name: Фамилия Имя Отчество — с большой буквы, в именительном падеже
- passport_series: 4 цифры (может быть написано на боковой стороне: "22 17" → "2217")
- passport_number: 6 цифр
- passport_issued_by: полностью как написано (ГУ МВД, УФМС и т.д.)
- passport_date: дата выдачи в формате ДД.ММ.ГГГГ
- registration_address: если есть штамп прописки — извлеки адрес. Если нет — null
- Если фото нечёткое и данные не читаются — верни что смог, остальное null
- Не выдумывай данные. Извлекай только то, что видишь.
"""


import base64


async def parse_passport_photo(photo_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """
    Отправляет фото паспорта в Claude Haiku Vision.
    Возвращает словарь с извлечёнными данными.
    """
    try:
        b64 = base64.b64encode(photo_bytes).decode("utf-8")

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": PASSPORT_PROMPT,
                    },
                ],
            }],
        )

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        parsed = json.loads(raw)
        logger.info("Паспорт (фото) распознан: %s", json.dumps(parsed, ensure_ascii=False))
        return parsed

    except json.JSONDecodeError as e:
        logger.error("Паспорт: невалидный JSON: %s | raw: %s", e, raw)
        return {"error": "parse_failed"}
    except Exception as e:
        logger.error("Паспорт: ошибка API: %s", e)
        return {"error": "api_failed", "detail": str(e)}


async def parse_passport_text(text: str) -> dict:
    """
    Парсит паспортные данные из текста менеджера.
    Например: "Князев Сергей Николаевич 2221 309317 ГУ МВД по Нижегородской области 18.02.2022"
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=PASSPORT_PROMPT,
            messages=[{"role": "user", "content": text}],
        )

        raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]

        parsed = json.loads(raw)
        logger.info("Паспорт (текст) распознан: %s", json.dumps(parsed, ensure_ascii=False))
        return parsed

    except json.JSONDecodeError as e:
        logger.error("Паспорт текст: невалидный JSON: %s | raw: %s", e, raw)
        return {"error": "parse_failed"}
    except Exception as e:
        logger.error("Паспорт текст: ошибка API: %s", e)
        return {"error": "api_failed", "detail": str(e)}


# ---------- Полный пайплайн ----------


async def process_measurement(text: str) -> dict:
    """
    Полный пайплайн:
    1. Парсим текст → JSON с зонами
    2. Считаем общую площадь и средневзвешенную толщину
    3. Если за городом + есть координаты → считаем расстояние
    4. Возвращаем готовый результат
    """
    parsed = await parse_measurement_text(text)

    if "error" in parsed:
        return parsed

    # --- Расчёт площади и средней толщины из зон ---
    zones = parsed.get("zones", [])
    total_area = 0
    total_volume = 0
    has_thickness = False
    missing_zone_thickness = False

    for z in zones:
        area = z.get("area_m2") or 0
        thickness = z.get("thickness_mm")
        total_area += area
        if thickness is not None:
            total_volume += area * thickness
            has_thickness = True
        else:
            missing_zone_thickness = True

    parsed["area_m2"] = round(total_area, 1) if total_area > 0 else None

    if has_thickness and total_area > 0 and not missing_zone_thickness:
        parsed["thickness_mm_avg"] = round(total_volume / total_area, 1)
    else:
        parsed["thickness_mm_avg"] = None

    # --- Определяем недостающие обязательные поля ---
    missing = []
    if not parsed.get("client_name"):
        missing.append("имя клиента")
    if not parsed.get("client_phone"):
        missing.append("телефон клиента")
    if not parsed.get("area_m2"):
        missing.append("площадь (м²)")
    if parsed.get("thickness_mm_avg") is None:
        missing.append("толщина слоя (мм)")
    if parsed.get("object_type") is None:
        missing.append("тип объекта")
    if parsed.get("location_type") is None:
        missing.append("город или за городом")

    parsed["missing_fields"] = missing

    # --- Если за городом — считаем расстояние ---
    if parsed.get("location_type") == "за городом":
        coords = parsed.get("coordinates")
        if coords and coords.get("lat") and coords.get("lon"):
            distance = await get_distance_km(coords["lat"], coords["lon"])
            parsed["distance"] = distance
        else:
            parsed["distance"] = None
            if "координаты объекта" not in missing:
                missing.append("координаты объекта")

    return parsed


# ---------- Быстрый тест ----------

if __name__ == "__main__":
    import asyncio

    test_texts = [
        "Квартира 78м², ЖК Анкудиновский, слой 50мм, тёплый пол в санузле. Нужно к 20 числу.",
        "Дом в Афонино, 120м2, толщина 6см, координаты 56.2200 43.8100. Демонтаж старой стяжки, вывоз песка.",
        "Коммерция 350 кв м, слой 80мм, город, ул. Родионова 200. 3 этаж, подъём краном.",
        "60 метров, 4 сантиметра",
    ]

    async def run_tests():
        for text in test_texts:
            print(f"\n{'='*60}")
            print(f"ВХОД: {text}")
            print(f"{'='*60}")
            result = await process_measurement(text)
            print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(run_tests())
