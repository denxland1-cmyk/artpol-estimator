"""
ARTPOL — Генератор договора подряда
Использует python-docx напрямую. Без внешних скриптов.
Открывает шаблон .docx, подставляет данные, сохраняет.
"""

import os
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from copy import deepcopy

from docx import Document

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "ШАБЛОН_ДОГОВОРА_НОВЫИ_.docx"
MSK = timezone(timedelta(hours=3))

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _num_to_words(n: int) -> str:
    """Число → прописью (до миллиона)."""
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
    orig = n

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
            if t == 1:
                parts.append("одна")
            elif t == 2:
                parts.append("две")
            else:
                parts.append(ones[t])

        # тысяча/тысячи/тысяч
        tt = (orig // 1000) % 100
        last_digit = (orig // 1000) % 10
        if 11 <= tt <= 19:
            parts.append("тысяч")
        elif last_digit == 1:
            parts.append("тысяча")
        elif 2 <= last_digit <= 4:
            parts.append("тысячи")
        else:
            parts.append("тысяч")

    if n >= 100:
        parts.append(hundreds[n // 100])
        n = n % 100
    if n >= 20:
        parts.append(tens[n // 10])
        n = n % 10
    if n > 0:
        parts.append(ones[n])

    return " ".join(p for p in parts if p)


def _replace_in_paragraph(paragraph, old_text, new_text):
    """
    Заменяет текст в параграфе, даже если он разбит на несколько runs.
    """
    # Сначала пробуем простую замену в каждом run
    for run in paragraph.runs:
        if old_text in run.text:
            run.text = run.text.replace(old_text, new_text)
            return True

    # Если не нашли — текст может быть разбит по runs
    full_text = paragraph.text
    if old_text not in full_text:
        return False

    # Собираем все runs с текстом
    runs_with_text = [(i, run) for i, run in enumerate(paragraph.runs) if run.text]
    if not runs_with_text:
        return False

    # Склеиваем текст, находим позицию, распределяем по runs
    concat = ""
    run_boundaries = []  # (start_pos, end_pos, run_index)
    for i, run in runs_with_text:
        start = len(concat)
        concat += run.text
        run_boundaries.append((start, len(concat), i))

    find_pos = concat.find(old_text)
    if find_pos == -1:
        return False

    find_end = find_pos + len(old_text)

    # Определяем какие runs затронуты
    new_concat = concat[:find_pos] + new_text + concat[find_end:]

    # Простой подход: записываем весь текст в первый run, остальные очищаем
    first_run_idx = runs_with_text[0][0]
    paragraph.runs[first_run_idx].text = new_concat
    for i, run in runs_with_text[1:]:
        run.text = ""

    return True


def _replace_in_doc(doc, old_text, new_text):
    """Заменяет текст во всём документе (параграфы + таблицы)."""
    for para in doc.paragraphs:
        _replace_in_paragraph(para, old_text, new_text)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    _replace_in_paragraph(para, old_text, new_text)


def generate_contract(
    parsed: dict,
    estimate: dict,
    client_data: dict,
    grade: str = "М150",
    include_sand_removal: bool = False,
    output_path: str = None,
) -> str:
    """
    Генерирует договор подряда .docx из шаблона.
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Шаблон не найден: {TEMPLATE_PATH}")

    doc = Document(str(TEMPLATE_PATH))

    # --- Данные ---
    area = parsed.get("area_m2", 0)
    thickness = parsed.get("thickness_mm_avg", 0)
    address = parsed.get("address", "___")

    total = estimate["grand_total"]
    if include_sand_removal:
        total += 5000

    total_words = _num_to_words(total)
    total_formatted = f"{total:,}".replace(",", " ")

    full_name = client_data.get("full_name", "___")
    passport_series = client_data.get("passport_series", "____")
    passport_number = client_data.get("passport_number", "______")
    passport = f"{passport_series} {passport_number}"
    issued_by = client_data.get("passport_issued_by", "___")
    passport_date = client_data.get("passport_date", "___")
    reg_address = client_data.get("registration_address", "___")

    contract_num = client_data.get("contract_number", "___")
    contract_date = client_data.get("contract_date", "")
    if not contract_date:
        now = datetime.now(MSK)
        contract_date = f"{now.day:02d}.{now.month:02d}.{now.year}"

    # Дата для шапки
    try:
        cd_parts = contract_date.split(".")
        date_header = f"«{cd_parts[0]}» {MONTHS_RU[int(cd_parts[1])]} {cd_parts[2]} г."
    except Exception:
        date_header = contract_date

    work_start = client_data.get("work_start_date", "___")
    work_end = client_data.get("work_end_date", "___")
    payment_date = client_data.get("payment_date", "___")

    # Фамилия И.О.
    name_parts = full_name.split()
    if len(name_parts) >= 3:
        short_name = f"{name_parts[0]} {name_parts[1][0]}.{name_parts[2][0]}."
    elif len(name_parts) == 2:
        short_name = f"{name_parts[0]} {name_parts[1][0]}."
    else:
        short_name = full_name

    # --- Замены в документе ---

    # Номер договора
    _replace_in_doc(doc, "№ 1/26ФЛ", f"№ {contract_num}/26ФЛ")
    _replace_in_doc(doc, "№1/26", f"№{contract_num}/26")

    # Дата в шапке
    _replace_in_doc(doc, "«  » января 2026 г.", date_header)

    # ФИО клиента (в шапке после "и")
    _replace_in_doc(doc, ", именуемая в дальнейшем", f" {full_name}, именуемая в дальнейшем")

    # Адрес объекта
    _replace_in_doc(doc, "г. Нижний Новгород, ул. Норильская, д. 16, кв. 5", address)

    # Площадь п.1.2.1 — ищем underlined "м2"
    _replace_in_doc(doc, "составляет м2", f"составляет {area} м2")

    # Толщина п.1.2.1
    _replace_in_doc(doc, "составляет  мм", f"составляет {thickness} мм")
    _replace_in_doc(doc, "составляет мм", f"составляет {thickness} мм")

    # Сумма п.2.1
    _replace_in_doc(doc, "(тысяч) рублей 00 копеек",
                    f"{total_formatted} ({total_words}) рублей 00 копеек")

    # Толщина п.2.5
    _replace_in_doc(doc, "средней толщине стяжки  мм", f"средней толщине стяжки {thickness} мм")
    _replace_in_doc(doc, "средней толщине стяжки мм", f"средней толщине стяжки {thickness} мм")

    # Дата начала работ п.3.1
    _replace_in_doc(doc, ".01.2026г.", f"{work_start}")
    # Дата завершения п.3.2
    _replace_in_doc(doc, ".01.2026г", f"{work_end}")

    # Дата в приложении
    _replace_in_doc(doc, "от 13.01.2026 г.", f"от {contract_date} г.")

    # График финансирования
    _replace_in_doc(doc, "16.01.2025г Расчет              рублей.",
                    f"{payment_date} Расчет {total_formatted} рублей.")
    _replace_in_doc(doc, "16.01.2025г Расчет", f"{payment_date} Расчет {total_formatted}")

    # Реквизиты заказчика (в таблицах)
    _replace_in_doc(doc, "ФИО: ", f"ФИО: {full_name}")
    _replace_in_doc(doc, "Паспорт: ", f"Паспорт: {passport}")
    _replace_in_doc(doc, "Выдан: ", f"Выдан: {issued_by}")
    _replace_in_doc(doc, "Дата выдачи: ", f"Дата выдачи: {passport_date}")

    # Подпись
    _replace_in_doc(doc, "( )", f"({short_name})")

    # --- Сохранение ---
    if output_path is None:
        name = full_name.split()[0] if full_name != "___" else "клиент"
        output_path = f"/tmp/Договор_{contract_num}_{name}.docx"

    doc.save(output_path)
    logger.info("Договор сохранён: %s", output_path)
    return output_path


# ---------- Тест ----------

if __name__ == "__main__":
    parsed = {
        "area_m2": 36.9,
        "thickness_mm_avg": 86,
        "address": "г. Нижний Новгород, ул. Политбойцов, д.19, кв. 19",
    }

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
        output_path="/home/claude/test_contract2.docx",
    )
    print(f"Договор: {path}")
