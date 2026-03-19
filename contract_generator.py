"""
ARTPOL — Генератор договора подряда
Берёт шаблон ШАБЛОН_ДОГОВОРА_НОВЫИ_.docx, подставляет данные клиента и сметы.
"""

import os
import re
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "ШАБЛОН_ДОГОВОРА_НОВЫИ_.docx"
SCRIPTS_DIR = "/mnt/skills/public/docx/scripts"
MSK = timezone(timedelta(hours=3))

# Месяцы на русском (родительный падеж)
MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _num_to_words(n: int) -> str:
    """Простое число → прописью (до миллиона). Для договора."""
    if n == 0:
        return "ноль"

    ones = ["", "одна", "две", "три", "четыре", "пять", "шесть",
            "семь", "восемь", "девять", "десять", "одиннадцать",
            "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
            "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
    tens = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят",
            "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
    hundreds = ["", "сто", "двести", "триста", "четыреста", "пятьсот",
                "шестьсот", "семьсот", "восемьсот", "девятьсот"]

    parts = []

    if n >= 1000:
        t = n // 1000
        n = n % 1000
        if t >= 100:
            parts.append(hundreds[t // 100])
            t = t % 100
        if t >= 20:
            parts.append(tens[t // 10])
            t = t % 10
        if t > 0:
            # тысячи — женский род
            if t == 1:
                parts.append("одна")
            elif t == 2:
                parts.append("две")
            else:
                parts.append(ones[t])
        # тысяча/тысячи/тысяч
        last_t = (n // 1000 if n >= 1000 else t) if parts else t
        total_t = int("".join(p for p in str(n // 1000) if p.isdigit()) or t)
        # Упрощённо
        parts.append("тысяч" if t in [0, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19]
                      else "тысяча" if t == 1
                      else "тысячи")

    if n >= 100:
        parts.append(hundreds[n // 100])
        n = n % 100
    if n >= 20:
        parts.append(tens[n // 10])
        n = n % 10
    if n > 0:
        parts.append(ones[n])

    return " ".join(p for p in parts if p)


def _xml_escape(text: str) -> str:
    """Экранирует спецсимволы для XML."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))


def generate_contract(
    parsed: dict,
    estimate: dict,
    client_data: dict,
    grade: str = "М150",
    include_sand_removal: bool = False,
    output_path: str = None,
) -> str:
    """
    Генерирует договор подряда .docx.

    parsed: данные замера
    estimate: результат calculate_estimate()
    client_data: {
        "full_name": "Котелков Виктор Михайлович",
        "passport_series": "2221",
        "passport_number": "295591",
        "passport_issued_by": "ГУ МВД России по Нижегородской области",
        "passport_date": "02.06.2021",
        "registration_address": "г. Нижний Новгород, ул. Политбойцов, д.19, кв. 19",
        "contract_number": "48",
        "contract_date": "04.03.2026",  # или auto
        "work_start_date": "05.03.2026",
        "work_end_date": "05.03.2026",
        "payment_date": "05.03.2026",
    }
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Шаблон не найден: {TEMPLATE_PATH}")

    # --- Подготовка данных ---
    area = parsed.get("area_m2", 0)
    thickness = parsed.get("thickness_mm_avg", 0)
    address = parsed.get("address", "___")

    keramzit_data = parsed.get("keramzit") or {}
    ker_area = keramzit_data.get("area_m2", 0)
    ker_thick = keramzit_data.get("thickness_mm", 0)

    total = estimate["grand_total"]
    if include_sand_removal:
        total += 5000

    total_words = _num_to_words(total)

    full_name = client_data.get("full_name", "___")
    passport = f"{client_data.get('passport_series', '____')} {client_data.get('passport_number', '______')}"
    issued_by = client_data.get("passport_issued_by", "___")
    passport_date = client_data.get("passport_date", "___")
    reg_address = client_data.get("registration_address", "___")

    contract_num = client_data.get("contract_number", "___")
    contract_date = client_data.get("contract_date", "")
    if not contract_date:
        now = datetime.now(MSK)
        contract_date = f"{now.day:02d}.{now.month:02d}.{now.year}"

    # Парсим дату договора для шапки
    try:
        cd_parts = contract_date.split(".")
        cd_day = cd_parts[0]
        cd_month = MONTHS_RU[int(cd_parts[1])]
        cd_year = cd_parts[2]
        date_header = f"«{cd_day}» {cd_month} {cd_year} г."
    except Exception:
        date_header = contract_date

    work_start = client_data.get("work_start_date", "___")
    work_end = client_data.get("work_end_date", "___")
    payment_date = client_data.get("payment_date", "___")

    # Фамилия и инициалы
    name_parts = full_name.split()
    if len(name_parts) >= 3:
        short_name = f"{name_parts[0]} {name_parts[1][0]}.{name_parts[2][0]}."
    elif len(name_parts) == 2:
        short_name = f"{name_parts[0]} {name_parts[1][0]}."
    else:
        short_name = full_name

    # --- Копируем и распаковываем шаблон ---
    work_dir = Path(f"/tmp/contract_{contract_num}_{os.getpid()}")
    if work_dir.exists():
        shutil.rmtree(work_dir)

    unpacked = work_dir / "unpacked"

    subprocess.run(
        ["python", f"{SCRIPTS_DIR}/office/unpack.py", str(TEMPLATE_PATH), str(unpacked)],
        check=True, capture_output=True,
    )

    # --- Редактируем XML ---
    doc_xml = unpacked / "word" / "document.xml"
    xml = doc_xml.read_text(encoding="utf-8")

    # Экранируем данные для XML
    e = _xml_escape

    # 1. Номер договора
    xml = xml.replace("№ 1/26ФЛ", f"№ {e(contract_num)}/26ФЛ")

    # 2. Дата в шапке
    xml = xml.replace(
        "«  » января 2026 г.",
        e(date_header),
    )

    # 3. ФИО клиента — между "и" и ","
    xml = xml.replace(
        '<w:t xml:space="preserve"> с одной стороны, и </w:t>',
        f'<w:t xml:space="preserve"> с одной стороны, и {e(full_name)}</w:t>',
    )
    # Убираем лишнюю запятую после ФИО (была пустая)
    xml = xml.replace(
        f'{e(full_name)}</w:t>\n      </w:r>\n      \n      \n      \n      \n      <w:r>\n        <w:rPr>\n          <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n          <w:b/>\n          <w:szCs w:val="22"/>\n        </w:rPr>\n        <w:t xml:space="preserve">, </w:t>',
        f'{e(full_name)},</w:t>\n      </w:r>\n      \n      \n      \n      \n      <w:r>\n        <w:rPr>\n          <w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>\n          <w:b/>\n          <w:szCs w:val="22"/>\n        </w:rPr>\n        <w:t xml:space="preserve"> </w:t>',
    )

    # 4. Адрес объекта (п.1.1)
    xml = xml.replace(
        "г. Нижний Новгород, ул. Норильская, д. 16, кв. 5",
        e(address),
    )

    # 5. Площадь (п.1.2.1) — "м2" → "XX м2"
    xml = xml.replace(
        '<w:u w:val="single"/>\n        </w:rPr>\n        <w:t>м2</w:t>',
        f'<w:u w:val="single"/>\n        </w:rPr>\n        <w:t>{area} м2</w:t>',
    )

    # 6. Толщина (п.1.2.1) — " мм" → "XX мм"
    xml = xml.replace(
        '<w:u w:val="single"/>\n        </w:rPr>\n        <w:t xml:space="preserve"> мм</w:t>',
        f'<w:u w:val="single"/>\n        </w:rPr>\n        <w:t>{thickness} мм</w:t>',
        1,  # только первое вхождение
    )

    # 6. Сумма (п.2.1)
    xml = xml.replace(
        "(тысяч) рублей 00 копеек.",
        f"{total:,} ({e(total_words)}) рублей 00 копеек.".replace(",", " "),
    )

    # 7. Толщина в п.2.5
    xml = xml.replace(
        "средней толщине стяжки  мм",
        f"средней толщине стяжки {thickness} мм",
    )

    # 8. Дата начала работ (п.3.1)
    xml = xml.replace(
        ' .01.2026г. ',
        f' {e(work_start)} ',
    )

    # 9. Дата завершения работ (п.3.2)
    xml = xml.replace(
        '       .01.2026г',
        f' {e(work_end)}',
    )

    # 10. Реквизиты заказчика (секция 9 + Приложение)
    xml = xml.replace("ФИО: </w:t>", f"ФИО: {e(full_name)}</w:t>")
    xml = xml.replace("Паспорт: </w:t>", f"Паспорт: {e(passport)}</w:t>")
    xml = xml.replace("Выдан: </w:t>", f"Выдан: {e(issued_by)}</w:t>")
    xml = xml.replace("Дата выдачи: </w:t>", f"Дата выдачи: {e(passport_date)}</w:t>")

    # 11. Подпись (Фамилия И.О.)
    xml = xml.replace(
        "( )",
        f"({e(short_name)})",
    )

    # 12. Номер и дата в Приложении 1
    xml = xml.replace(
        "№1/26 от 13.01.2026 г.",
        f"№{e(contract_num)}/26 от {e(contract_date)} г.",
    )

    # 13. График финансирования
    xml = re.sub(
        r'16\.01\.2025г Расчет\s+рублей\.',
        f"{e(payment_date)} Расчет {total:,} рублей.".replace(",", " "),
        xml,
    )

    # Сохраняем XML
    doc_xml.write_text(xml, encoding="utf-8")

    # --- Запаковываем ---
    if output_path is None:
        output_path = f"/tmp/Договор_{contract_num}_{full_name.split()[0] if full_name != '___' else 'клиент'}.docx"

    subprocess.run(
        ["python", f"{SCRIPTS_DIR}/office/pack.py", str(unpacked), output_path,
         "--original", str(TEMPLATE_PATH), "--validate", "false"],
        check=True, capture_output=True,
    )

    # Чистим
    shutil.rmtree(work_dir)

    logger.info("Договор сохранён: %s", output_path)
    return output_path


# ---------- Быстрый тест ----------

if __name__ == "__main__":
    parsed = {
        "area_m2": 36.9,
        "thickness_mm_avg": 86,
        "address": "г. Нижний Новгород, ул. Политбойцов, д.19, кв. 19",
    }

    # Мок estimate
    estimate = {"grand_total": 75000}

    client_data = {
        "full_name": "Котелков Виктор Михайлович",
        "passport_series": "2221",
        "passport_number": "295591",
        "passport_issued_by": "ГУ МВД России по Нижегородской области",
        "passport_date": "02.06.2021",
        "registration_address": "г. Нижний Новгород, ул. Политбойцов, д.19, кв. 19",
        "contract_number": "48",
        "contract_date": "04.03.2026",
        "work_start_date": "05.03.2026г.",
        "work_end_date": "05.03.2026г.",
        "payment_date": "05.03.2026г",
    }

    path = generate_contract(
        parsed=parsed,
        estimate=estimate,
        client_data=client_data,
        output_path="/home/claude/test_contract.docx",
    )
    print(f"Договор: {path}")
