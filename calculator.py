"""
ARTPOL — Калькулятор сметы
Детерминированный расчёт по формулам отдела продаж.
Никакого AI — только математика.

Две базы для логистики:
- Окская Гавань, 1 (56.236821, 43.916676) — песок, цемент, материалы
- Интернациональная, 100 (56.310043, 43.953282) — оборудование
"""

import math
import logging

logger = logging.getLogger(__name__)

# ============================================================
# КООРДИНАТЫ БАЗ
# ============================================================

# База отгрузки материалов (песок, цемент)
MATERIALS_BASE_LAT = 56.236821
MATERIALS_BASE_LON = 43.916676

# База оборудования
EQUIPMENT_BASE_LAT = 56.310043
EQUIPMENT_BASE_LON = 43.953282

# ============================================================
# ЦЕНЫ (вынесены для удобства обновления)
# ============================================================

SAND_PRICE_PER_TON = 670           # ₽/т
CEMENT_PRICE_PER_BAG = 600         # ₽/мешок
FIBER_PRICE_PER_KG = 300           # ₽/кг
FILM_PRICE_PER_M2 = 10             # ₽/м²
IZOFLEX_PRICE_PER_M = 20           # ₽/пог.м
REINFORCED_FILM_PRICE_PER_M2 = 40  # ₽/м² (армированная плёнка)
METAL_MESH_PRICE_PER_M2 = 120      # ₽/м² (мет. сетка)

EQUIPMENT_DELIVERY_CITY = 12000    # ₽ (фикс по НН)
EQUIPMENT_DELIVERY_OBLAST_PER_KM = 140  # ₽/км
EQUIPMENT_DELIVERY_OBLAST_BASE = 10000  # ₽

SAND_EXTRA = 1000  # +1000₽ к каждой доставке песка


# ============================================================
# ПЕСОК
# ============================================================

def _round_sand_tons(tons: float) -> float:
    """
    Специальное округление тонн песка:
    0.1–0.4 → 0.5, 0.6–0.9 → следующее целое, 0.0/0.5 — без изменений.
    Работает по первому десятичному знаку.
    """
    whole = int(tons)
    frac = round(tons - whole, 2)
    # Определяем первый десятичный знак
    first_dec = int(frac * 10)
    if first_dec == 0 or first_dec == 5:
        return whole + first_dec / 10
    elif 1 <= first_dec <= 4:
        return whole + 0.5
    else:  # 6-9
        return whole + 1.0


def _oblast_sand_delivery(tons: float, distance_km: float) -> tuple[int, str]:
    """Рассчитывает доставку песка в область. Возвращает (стоимость, описание)."""
    near = distance_km <= 30  # 20-30 км
    if tons <= 5:
        rate = 275 if near else 255
    elif tons <= 18:
        rate = 400 if near else 350
    else:  # 19-30
        rate = 500 if near else 400
    cost = round(rate * distance_km)
    label = f"{rate}₽×{distance_km}км"
    return cost, label


def _city_sand_delivery(tons: float) -> tuple[int, str]:
    """Рассчитывает доставку песка в городе. Возвращает (стоимость, транспорт)."""
    if tons <= 5:
        return 4500, "ГАЗОН (до 5т)"
    elif tons <= 18:
        return 7500, "КАМАЗ (6-18т)"
    else:  # 19-30
        return 9000, "МАЗ (19-30т)"


def calc_sand(area_m2: float, thickness_mm: float, is_city: bool, distance_km: float = 0, sand_transport: str = None) -> dict:
    """
    Расчёт песка: количество, стоимость, доставка.
    distance_km — от Окской Гавани до объекта (для области).
    sand_transport — "камаз" или "газон" если указан спецтранспорт.
    Формула: объём × 1.8 × 1.12, спецокругление до 0.5 т.

    Город: до 5т=4500, 6-18т=7500, 19-30т=9000, всегда +1000₽
    Область 20-30км: до 5т=275×км, 6-18т=400×км, 19-30т=500×км, без +1000
    Область 31+км: до 5т=255×км, 6-18т=350×км, 19-30т=400×км, без +1000
    >30т: 2 рейса (МАЗ 30т + остаток)
    """
    volume_m3 = area_m2 * thickness_mm / 1000
    sand_tons_raw = round(volume_m3 * 1.8 * 1.12, 2)
    sand_tons = _round_sand_tons(sand_tons_raw)

    sand_cost = round(sand_tons * SAND_PRICE_PER_TON)

    # --- Спецтранспорт: песок на камазах / газонах ---
    if sand_transport in ("камаз", "газон"):
        max_per_trip = 18 if sand_transport == "камаз" else 5

        import math
        n_trips = math.ceil(sand_tons / max_per_trip)
        tons_per_trip = round(sand_tons / n_trips, 2)

        delivery = 0
        extra = 0
        for _ in range(n_trips):
            if is_city:
                d, _ = _city_sand_delivery(tons_per_trip)
                delivery += d
                extra += 1000
            else:
                d, _ = _oblast_sand_delivery(tons_per_trip, distance_km)
                delivery += d

        label = "КАМАЗ" if sand_transport == "камаз" else "ГАЗОН"
        transport = f"{n_trips}× {label} {tons_per_trip}т"

        return {
            "volume_m3": round(volume_m3, 2),
            "sand_tons": sand_tons,
            "sand_cost": sand_cost,
            "delivery": round(delivery),
            "extra": round(extra),
            "transport": transport,
            "total": round(sand_cost + delivery + extra),
        }

    # --- Обычный расчёт ---
    extra = 0

    if is_city:
        if sand_tons <= 30:
            delivery, transport = _city_sand_delivery(sand_tons)
            extra = 1000
        else:
            # >30т — 2 рейса: МАЗ 30т + остаток
            leftover = sand_tons - 30
            first_delivery, _ = _city_sand_delivery(30)  # 9000
            second_delivery, second_label = _city_sand_delivery(leftover)
            delivery = first_delivery + second_delivery
            extra = 2000  # +1000 за каждый рейс
            transport = f"МАЗ 30т + {second_label} {leftover}т (2 рейса)"
    else:
        if sand_tons <= 30:
            delivery, label = _oblast_sand_delivery(sand_tons, distance_km)
            transport = f"Область ({label})"
        else:
            # >30т — 2 рейса
            leftover = sand_tons - 30
            first_delivery, first_label = _oblast_sand_delivery(30, distance_km)
            second_delivery, second_label = _oblast_sand_delivery(leftover, distance_km)
            delivery = first_delivery + second_delivery
            transport = f"Область 30т+{leftover}т (2 рейса)"

    total = sand_cost + delivery + extra

    return {
        "volume_m3": round(volume_m3, 2),
        "sand_tons": sand_tons,
        "sand_cost": sand_cost,
        "delivery": round(delivery),
        "extra": extra,
        "transport": transport,
        "total": round(total),
    }


# ============================================================
# ЦЕМЕНТ
# ============================================================

def _oblast_cement_manipulator_rate(bags: int, distance_km: float) -> int:
    """Тариф манипулятора цемент в области: ₽/км по мешкам и расстоянию."""
    if bags <= 100:
        if distance_km <= 34:
            return 410
        elif distance_km <= 49:
            return 360
        elif distance_km <= 79:
            return 270
        else:
            return 230
    elif bags <= 200:
        if distance_km <= 34:
            return 510
        elif distance_km <= 49:
            return 410
        elif distance_km <= 79:
            return 330
        else:
            return 260
    else:  # 201-240
        if distance_km <= 34:
            return 650
        elif distance_km <= 49:
            return 470
        elif distance_km <= 79:
            return 390
        else:
            return 290


def _oblast_cement_delivery(bags: int, distance_km: float) -> int:
    """Рассчитывает доставку цемента в область для заданного кол-ва мешков."""
    if bags <= 35:
        return round(100 * distance_km + 2000)
    elif bags <= 60:
        return round(100 * distance_km * 2 + 1000)
    elif bags <= 240:
        rate = _oblast_cement_manipulator_rate(bags, distance_km)
        return round(rate * distance_km + 1000)
    else:
        # 241+: манипулятор на 240 + остаток подходящей машиной
        manip_rate = _oblast_cement_manipulator_rate(240, distance_km)
        manip_delivery = round(manip_rate * distance_km + 1000)
        leftover = bags - 240
        extra_delivery = _oblast_cement_delivery(leftover, distance_km)
        return manip_delivery + extra_delivery


def _city_cement_delivery(bags: int) -> int:
    """Рассчитывает доставку цемента в городе для заданного кол-ва мешков."""
    if bags <= 35:
        return 3500
    elif bags <= 60:
        return 7000
    elif bags <= 105:
        return 10000  # манипулятор 8000 + 2000
    elif bags <= 206:
        return 12000  # манипулятор 10000 + 2000
    elif bags <= 240:
        return 15000  # манипулятор 13000 + 2000
    else:
        # 241+: манипулятор на 240 + остаток
        manip = 15000
        leftover = bags - 240
        extra = _city_cement_delivery(leftover)
        return manip + extra


def calc_cement(area_m2: float, thickness_mm: float, grade: str, is_city: bool, distance_km: float = 0) -> dict:
    """
    Расчёт цемента: количество мешков, стоимость, доставка.
    grade: "М150" или "М200"
    distance_km — от Окской Гавани до объекта (для области).

    Город: до 35=3500, 36-60=7000, 61-105=10000, 106-206=12000, 207-240=15000, 241+=2 рейса
    Область газель: до 35=100×км+2000, 36-60=100×км×2+1000
    Область манипулятор (61-240): тариф×км+1000 (тариф зависит от мешков и расстояния)
    241+: манипулятор 240 + остаток подходящей машиной
    """
    volume = area_m2 * thickness_mm
    multiplier = 5 if grade == "М150" else 6
    bags = math.ceil(volume / 1000 * multiplier)

    cement_cost = bags * CEMENT_PRICE_PER_BAG

    if is_city:
        delivery = _city_cement_delivery(bags)
    else:
        delivery = _oblast_cement_delivery(bags, distance_km)

    total = cement_cost + delivery

    return {
        "grade": grade,
        "bags": bags,
        "cement_cost": round(cement_cost),
        "delivery": round(delivery),
        "total": round(total),
    }


# ============================================================
# ФИБРА
# ============================================================

def calc_fiber(area_m2: float, thickness_mm: float) -> dict:
    """Расчёт фибры."""
    kg = area_m2 * thickness_mm / 1000 * 0.8
    cost = kg * FIBER_PRICE_PER_KG

    return {
        "kg": round(kg, 2),
        "cost": round(cost),
    }


# ============================================================
# ПЛЁНКА ТЕХНИЧЕСКАЯ
# ============================================================

def calc_film(area_m2: float) -> dict:
    """Расчёт технической плёнки."""
    m2 = area_m2 * 2 * 1.2
    cost = m2 * FILM_PRICE_PER_M2

    return {
        "m2": round(m2, 1),
        "cost": round(cost),
    }


# ============================================================
# IZOFLEX (подложка/демпферная лента)
# ============================================================

def calc_izoflex(area_m2: float) -> dict:
    """Расчёт подложки Izoflex."""
    meters = area_m2 * 0.7
    cost = meters * IZOFLEX_PRICE_PER_M

    return {
        "meters": round(meters, 1),
        "cost": round(cost),
    }


# ============================================================
# ДОСТАВКА ОБОРУДОВАНИЯ
# ============================================================

def calc_equipment_delivery(is_city: bool, distance_km: float = 0) -> dict:
    """
    Доставка оборудования.
    distance_km — от Интернациональной, 100 до объекта.
    """
    if is_city:
        cost = EQUIPMENT_DELIVERY_CITY
        detail = "НН фикс"
    else:
        cost = EQUIPMENT_DELIVERY_OBLAST_PER_KM * distance_km + EQUIPMENT_DELIVERY_OBLAST_BASE
        detail = f"140₽×{distance_km}км + 10000"

    return {
        "cost": round(cost),
        "detail": detail,
    }


# ============================================================
# СТОИМОСТЬ РАБОТ
# ============================================================

# Тарифы: {диапазон_этажей: {диапазон_площади: цена}}
WORK_TARIFFS = {
    "low": {  # до 10 этажа
        "fixed": {
            (10, 29.99): 35000,
            (30, 59.99): 37000,
            (60, 79.99): 38000,
            (80, 99.99): 39000,
        },
        "per_m2": {
            (100, 159.99): 430,
            (160, 299.99): 450,
            (300, 99999): 470,
        },
    },
    "mid": {  # 10-15 этаж
        "fixed": {
            (10, 29.99): 38000,
            (30, 59.99): 40000,
            (60, 79.99): 42000,
            (80, 99.99): 45000,
        },
        "per_m2": {
            (100, 159.99): 460,
            (160, 299.99): 480,
            (300, 99999): 500,
        },
    },
}
# Выше 15 этажа = тариф "mid" × 2


def calc_work(area_m2: float, floor: int = 1) -> dict:
    """
    Расчёт стоимости работ.
    floor — этаж объекта (влияет на тариф).
    """
    # Определяем тарифную группу
    if floor <= 9:
        tariff_key = "low"
        floor_label = "до 10 этажа"
        multiplier = 1
    elif floor <= 15:
        tariff_key = "mid"
        floor_label = "10-15 этаж"
        multiplier = 1
    else:
        tariff_key = "mid"
        floor_label = f"выше 15 этажа (×2)"
        multiplier = 2

    tariff = WORK_TARIFFS[tariff_key]

    # Ищем в фиксированных тарифах
    for (low, high), price in tariff["fixed"].items():
        if low <= area_m2 <= high:
            cost = price * multiplier
            return {
                "cost": round(cost),
                "rate": f"фикс {price}₽" + (f" ×2" if multiplier > 1 else ""),
                "floor_label": floor_label,
            }

    # Ищем в тарифах за м²
    for (low, high), rate in tariff["per_m2"].items():
        if low <= area_m2 <= high:
            base_cost = area_m2 * rate
            cost = base_cost * multiplier
            return {
                "cost": round(cost),
                "rate": f"{rate}₽/м²" + (f" ×2" if multiplier > 1 else ""),
                "floor_label": floor_label,
            }

    # Площадь меньше 10м² — минимальный тариф
    if area_m2 < 10:
        cost = tariff["fixed"][(10, 29)] * multiplier
        return {
            "cost": round(cost),
            "rate": "минимальный тариф",
            "floor_label": floor_label,
        }

    return {
        "cost": 0,
        "rate": "⚠️ Не удалось определить тариф",
        "floor_label": floor_label,
    }


# ============================================================
# КЕРАМЗИТНОЕ ОСНОВАНИЕ (опция)
# ============================================================

def calc_keramzit(area_keramzit_m2: float, thickness_keramzit_mm: float, area_stjazhka_m2: float) -> dict:
    """
    Расчёт керамзитного основания.
    area_keramzit_m2 — площадь под керамзит
    thickness_keramzit_mm — слой керамзита
    area_stjazhka_m2 — общая площадь стяжки (для расчёта техн. плёнки)
    """
    # Армированная плёнка
    reinforced_film_m2 = math.ceil((area_keramzit_m2 * 1.2) + 2)
    if reinforced_film_m2 % 2 != 0:
        reinforced_film_m2 += 1  # всегда чётное

    reinforced_film_cost = reinforced_film_m2 * REINFORCED_FILM_PRICE_PER_M2

    # Мет. сетка (площадь = площадь арм. плёнки)
    mesh_m2 = reinforced_film_m2
    mesh_cost = mesh_m2 * METAL_MESH_PRICE_PER_M2

    # Керамзит (мешки)
    keramzit_bags = math.ceil(area_keramzit_m2 * thickness_keramzit_mm / 0.055 / 1000)

    # Техническая плёнка (общая минус армированная)
    tech_film_m2 = max(0, area_stjazhka_m2 * 2 * 1.2 - reinforced_film_m2)

    return {
        "reinforced_film_m2": reinforced_film_m2,
        "reinforced_film_cost": round(reinforced_film_cost),
        "mesh_m2": mesh_m2,
        "mesh_cost": round(mesh_cost),
        "keramzit_bags": keramzit_bags,
        "tech_film_m2": round(tech_film_m2, 1),
    }


# ============================================================
# ПОЛНЫЙ РАСЧЁТ СМЕТЫ
# ============================================================

def calculate_estimate(
    area_m2: float,
    thickness_mm: float,
    is_city: bool,
    grade: str = "М150",
    floor: int = 1,
    distance_materials_km: float = 0,
    distance_equipment_km: float = 0,
    keramzit_area_m2: float = 0,
    keramzit_thickness_mm: float = 0,
    price_modifier: float = 0,
    sand_transport: str = None,
    payment_type: str = "",
) -> dict:
    """
    Полный расчёт сметы.

    Параметры:
    - area_m2: площадь (м²)
    - thickness_mm: средняя толщина слоя (мм)
    - is_city: город (True) или область (False)
    - grade: марка прочности "М150" или "М200"
    - floor: этаж (для расчёта работ)
    - distance_materials_km: км от Окской Гавани до объекта (песок, цемент)
    - distance_equipment_km: км от Интернациональной до объекта (оборудование)
    - keramzit_area_m2: площадь керамзитного основания (0 = без керамзита)
    - keramzit_thickness_mm: толщина слоя керамзита в мм
    - price_modifier: скидка/наценка в % (например -5 = скидка 5%, +3 = наценка 3%)
    - sand_transport: "камаз" / "газон" / None — спецтранспорт для песка
    - payment_type: "" / "наличными" / "безналичный расчет"
    """
    # Коэффициент цены: -5% → 0.95, +3% → 1.03
    k = 1 + price_modifier / 100 if price_modifier != 0 else 1

    sand = calc_sand(area_m2, thickness_mm, is_city, distance_materials_km, sand_transport)
    cement = calc_cement(area_m2, thickness_mm, grade, is_city, distance_materials_km)
    fiber = calc_fiber(area_m2, thickness_mm)
    izoflex = calc_izoflex(area_m2)
    equipment = calc_equipment_delivery(is_city, distance_equipment_km)
    work = calc_work(area_m2, floor)

    # Применяем коэффициент ко всем ценам
    if k != 1:
        sand["sand_cost"] = round(sand["sand_cost"] * k)
        sand["delivery"] = round(sand["delivery"] * k)
        sand["extra"] = round(sand["extra"] * k)
        sand["total"] = sand["sand_cost"] + sand["delivery"] + sand["extra"]

        cement["cement_cost"] = round(cement["cement_cost"] * k)
        cement["delivery"] = round(cement["delivery"] * k)
        cement["total"] = cement["cement_cost"] + cement["delivery"]

        fiber["cost"] = round(fiber["cost"] * k)
        izoflex["cost"] = round(izoflex["cost"] * k)
        equipment["cost"] = round(equipment["cost"] * k)
        work["cost"] = round(work["cost"] * k)

        # Обновляем rate — чтобы в КП показывало новую цену
        if "фикс" in work.get("rate", ""):
            work["rate"] = f"фикс {work['cost']}₽"
        else:
            # Для тарифов за м² пересчитываем ставку
            new_rate = round(work["cost"] / area_m2) if area_m2 > 0 else 0
            work["rate"] = f"{new_rate}₽/м²"

    # Керамзит
    has_keramzit = keramzit_area_m2 > 0 and keramzit_thickness_mm > 0
    keramzit = None

    if has_keramzit:
        keramzit = calc_keramzit(keramzit_area_m2, keramzit_thickness_mm, area_m2)

        # Техническая плёнка уменьшается на армированную
        film = calc_film(area_m2)
        film["m2"] = round(max(0, film["m2"] - keramzit["reinforced_film_m2"]), 1)
        film["cost"] = round(film["m2"] * FILM_PRICE_PER_M2)

        # Доставка материалов при керамзите — всё едёт одним рейсом
        # Город: обычная доставка цемента + 3500₽
        # Область: (100 × км) × 2
        if is_city:
            cement["delivery"] = cement["delivery"] + 3500
            cement["total"] = cement["cement_cost"] + cement["delivery"]
        else:
            cement["delivery"] = round((100 * distance_materials_km) * 2)
            cement["total"] = cement["cement_cost"] + cement["delivery"]

        keramzit["keramzit_cost"] = keramzit["keramzit_bags"] * 340
        keramzit["keramzit_work_cost"] = round(keramzit_area_m2 * 220)
        keramzit["keramzit_work_rate"] = 220

        # Применяем коэффициент к керамзиту
        if k != 1:
            keramzit["reinforced_film_cost"] = round(keramzit["reinforced_film_cost"] * k)
            keramzit["mesh_cost"] = round(keramzit["mesh_cost"] * k)
            keramzit["keramzit_cost"] = round(keramzit["keramzit_cost"] * k)
            keramzit["keramzit_work_cost"] = round(keramzit["keramzit_work_cost"] * k)
            keramzit["keramzit_work_rate"] = round(220 * k)
    else:
        film = calc_film(area_m2)

    # Применяем коэффициент к плёнке
    if k != 1:
        film["cost"] = round(film["cost"] * k)

    # ============================================================
    # БЕЗНАЛИЧНЫЙ РАСЧЁТ — наценки после всех расчётов
    # ============================================================
    is_beznal = payment_type == "безналичный расчет"

    if is_beznal:
        # ×1.1 — материалы (песок с доставкой, цемент, фибра, плёнка, izoflex)
        sand["sand_cost"] = round(sand["sand_cost"] * 1.1)
        sand["delivery"] = round(sand["delivery"] * 1.1)
        sand["extra"] = round(sand["extra"] * 1.1)
        sand["total"] = sand["sand_cost"] + sand["delivery"] + sand["extra"]

        cement["cement_cost"] = round(cement["cement_cost"] * 1.1)

        fiber["cost"] = round(fiber["cost"] * 1.1)
        film["cost"] = round(film["cost"] * 1.1)
        izoflex["cost"] = round(izoflex["cost"] * 1.1)

        if has_keramzit and keramzit:
            keramzit["keramzit_cost"] = round(keramzit["keramzit_cost"] * 1.1)
            keramzit["reinforced_film_cost"] = round(keramzit["reinforced_film_cost"] * 1.1)
            keramzit["mesh_cost"] = round(keramzit["mesh_cost"] * 1.1)

        # ×1.5 — доставки и работы
        cement["delivery"] = round(cement["delivery"] * 1.5)
        cement["total"] = cement["cement_cost"] + cement["delivery"]

        equipment["cost"] = round(equipment["cost"] * 1.5)

        work["cost"] = round(work["cost"] * 1.5)
        # Обновляем rate для отображения
        if "фикс" in work.get("rate", ""):
            work["rate"] = f"фикс {work['cost']}₽"
        else:
            new_rate = round(work["cost"] / area_m2) if area_m2 > 0 else 0
            work["rate"] = f"{new_rate}₽/м²"

        if has_keramzit and keramzit:
            keramzit["keramzit_work_cost"] = round(keramzit["keramzit_work_cost"] * 1.5)
            keramzit["keramzit_work_rate"] = round(keramzit["keramzit_work_rate"] * 1.5)

    # Итого материалы
    materials_total = (
        sand["total"]
        + cement["total"]
        + fiber["cost"]
        + film["cost"]
        + izoflex["cost"]
    )

    if has_keramzit:
        materials_total += (
            keramzit["reinforced_film_cost"]
            + keramzit["mesh_cost"]
            + keramzit["keramzit_cost"]
        )

    # Итого всё
    grand_total = materials_total + equipment["cost"] + work["cost"]
    if has_keramzit:
        grand_total += keramzit["keramzit_work_cost"]

    result = {
        "sand": sand,
        "cement": cement,
        "fiber": fiber,
        "film": film,
        "izoflex": izoflex,
        "equipment_delivery": equipment,
        "work": work,
        "keramzit": keramzit,
        "price_modifier": price_modifier,
        "payment_type": payment_type,
        "materials_total": round(materials_total),
        "grand_total": round(grand_total),
    }

    logger.info(
        "Смета: %s м², %s мм, %s, %s, этаж %d%s → итого %s₽",
        area_m2, thickness_mm,
        "город" if is_city else f"область ({distance_materials_km}км)",
        grade, floor,
        f", керамзит {keramzit_area_m2}м²×{keramzit_thickness_mm}мм" if has_keramzit else "",
        f"{grand_total:,.0f}",
    )

    return result


# ============================================================
# ФОРМАТИРОВАНИЕ ДЛЯ МЕНЕДЖЕРА
# ============================================================

def format_estimate(est: dict) -> str:
    """Форматирует смету для отображения в Telegram. Порядок как в КП."""
    s = est["sand"]
    c = est["cement"]
    f = est["fiber"]
    fl = est["film"]
    iz = est["izoflex"]
    eq = est["equipment_delivery"]
    w = est["work"]
    k = est.get("keramzit")

    lines = [
        "💰 <b>СМЕТА:</b>",
        "",
        "📦 <b>Материалы:</b>",
        f"🪨 Песок: {s['sand_tons']}т ({s['transport']})",
        f"    Песок: {s['sand_cost']:,}₽ + доставка: {s['delivery']:,}₽ + {s['extra']:,}₽",
        f"    Итого: <b>{s['total']:,}₽</b>",
        f"🧱 Цемент {c['grade']}: {c['bags']} мешков = {c['cement_cost']:,}₽",
        f"🧵 Фибра: {f['kg']}кг = {f['cost']:,}₽",
        f"📄 Плёнка техн.: {fl['m2']}м² = {fl['cost']:,}₽",
        f"🔇 Izoflex: {iz['meters']}м = {iz['cost']:,}₽",
    ]

    if k:
        lines.append(f"🟤 Керамзит: {k['keramzit_bags']} мешков × 340₽ = {k['keramzit_cost']:,}₽")
        lines.append(f"    Арм. плёнка: {k['reinforced_film_m2']}м² = {k['reinforced_film_cost']:,}₽")
        lines.append(f"    Мет. сетка: {k['mesh_m2']}м² = {k['mesh_cost']:,}₽")

    lines.append(f"🚛 Доставка материалов: {c['delivery']:,}₽")
    lines.append(f"🚛 Доставка оборуд.: {eq['cost']:,}₽ ({eq['detail']})")

    lines.append("")
    lines.append(f"📦 Материалы итого: <b>{est['materials_total']:,}₽</b>")

    # Работы
    lines.append("")
    lines.append("🏗 <b>Работы:</b>")
    if k:
        lines.append(f"    Керамзитное основание: {k['keramzit_work_rate']}₽/м² = {k['keramzit_work_cost']:,}₽")
    lines.append(f"    Стяжка ({w['floor_label']}): {w['rate']} = <b>{w['cost']:,}₽</b>")

    lines.append("")
    lines.append(f"═══════════════════")

    mod = est.get("price_modifier", 0)
    if mod < 0:
        lines.append(f"💰 <b>ИТОГО (скидка {mod}%): {est['grand_total']:,}₽</b>")
    elif mod > 0:
        lines.append(f"💰 <b>ИТОГО (наценка +{mod}%): {est['grand_total']:,}₽</b>")
    else:
        lines.append(f"💰 <b>ИТОГО: {est['grand_total']:,}₽</b>")

    if est.get("payment_type") == "безналичный расчет":
        lines.append("")
        lines.append("📋 <i>В стоимость включен НДС 22%</i>")

    return "\n".join(lines)


# ============================================================
# БЫСТРЫЙ ТЕСТ
# ============================================================

if __name__ == "__main__":
    # Тест 1: квартира в городе, 78м², 50мм, М150, 7 этаж
    print("=" * 50)
    print("ТЕСТ 1: Квартира НН, 78м², 50мм, М150, 7 этаж")
    print("=" * 50)
    est = calculate_estimate(
        area_m2=78, thickness_mm=50, is_city=True,
        grade="М150", floor=7,
    )
    print(format_estimate(est).replace("<b>", "").replace("</b>", ""))

    print()

    # Тест 2: дом за городом, 203.8м², 90.5мм, М150, 1 этаж, 25км
    print("=" * 50)
    print("ТЕСТ 2: Дом область, 203.8м², 90.5мм, М150, 1 этаж, 25км")
    print("=" * 50)
    est2 = calculate_estimate(
        area_m2=203.8, thickness_mm=90.5, is_city=False,
        grade="М150", floor=1,
        distance_materials_km=25, distance_equipment_km=25,
    )
    print(format_estimate(est2).replace("<b>", "").replace("</b>", ""))

    print()

    # Тест 3: тот же дом, но М200
    print("=" * 50)
    print("ТЕСТ 3: Тот же дом, но М200")
    print("=" * 50)
    est3 = calculate_estimate(
        area_m2=203.8, thickness_mm=90.5, is_city=False,
        grade="М200", floor=1,
        distance_materials_km=25, distance_equipment_km=25,
    )
    print(format_estimate(est3).replace("<b>", "").replace("</b>", ""))

    print()

    # Тест 4: с керамзитом
    print("=" * 50)
    print("ТЕСТ 4: Дом с керамзитом, 120м², стяжка 60мм, керамзит 80м²×40мм, город")
    print("=" * 50)
    est4 = calculate_estimate(
        area_m2=120, thickness_mm=60, is_city=True,
        grade="М150", floor=1,
        keramzit_area_m2=80, keramzit_thickness_mm=40,
    )
    print(format_estimate(est4).replace("<b>", "").replace("</b>", ""))
