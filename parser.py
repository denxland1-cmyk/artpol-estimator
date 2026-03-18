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
YANDEX_ROUTES_API_KEY = os.environ["YANDEX_ROUTES_API_KEY"]

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
  "zones": [{"name": "строка", "area_m2": число, "thickness_mm": число}, ...],
  "object_type": "квартира" | "дом" | "коммерция" | null,
  "location_type": "город" | "за городом" | null,
  "warm_floor": true | false | null,
  "deadline": "строка" или null,
  "address": "строка" или null,
  "coordinates": {"lat": число, "lon": число} или null,
  "special_conditions": ["строка", ...] или []
}

Правила:
- zones: массив зон объекта. Каждая зона — это часть объекта со своей площадью и толщиной слоя.
  Если в тексте одна зона (например "78м², слой 50мм") — верни одну зону: [{"name": "объект", "area_m2": 78, "thickness_mm": 50}].
  Если несколько зон с разной толщиной (например "1 этаж 111.7м2 на 95мм, 2 этаж 92.1м2 на 85мм") — верни каждую отдельно.
  Если площадь указана без толщины — поставь thickness_mm: null. И наоборот.
  Если указана только общая площадь без разбивки — одна зона с name: "объект".
  Толщину в см переводи в мм (6см = 60мм).
- object_type: определи по контексту (квартира, частный дом, коммерческое помещение)
- location_type: "город" если ЖК, улица в Нижнем Новгороде; "за городом" если деревня, посёлок, коттеджный посёлок
- warm_floor: true если упоминается тёплый пол, иначе false; null если неясно
- deadline: сроки как строка (например "к 20 марта", "срочно", "на следующей неделе")
- address: адрес, название ЖК, населённый пункт — как есть из текста
- coordinates: широта и долгота если указаны в тексте (любой формат)
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


# ---------- Яндекс Routes API ----------


async def get_distance_km(lat: float, lon: float) -> dict:
    """
    Считает расстояние по дороге от базы до объекта.
    Использует Yandex Routes API (driving).
    Возвращает {"distance_km": число, "duration_min": число} или {"error": ...}
    """
    url = "https://routes.api.yandex.net/v2/route"

    headers = {
        "Authorization": f"Bearer {YANDEX_ROUTES_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "source": {"point": {"latitude": BASE_LAT, "longitude": BASE_LON}},
        "destination": {"point": {"latitude": lat, "longitude": lon}},
        "routing_mode": "DRIVING",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Извлекаем дистанцию и время
        route = data.get("route", {})
        distance_m = route.get("distanceMeters", 0)
        duration_s = route.get("durationSeconds", 0)

        result = {
            "distance_km": round(distance_m / 1000, 1),
            "duration_min": round(duration_s / 60),
        }
        logger.info("Расстояние: %s км, время: %s мин", result["distance_km"], result["duration_min"])
        return result

    except Exception as e:
        logger.error("Ошибка Yandex Routes API: %s", e)
        return {"error": "routes_failed", "detail": str(e)}


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
