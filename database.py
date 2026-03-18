"""
ARTPOL Агент-Сметчик — модуль базы данных
PostgreSQL на Railway
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta

import asyncpg

logger = logging.getLogger(__name__)

# ============================================================
# ⚠️ ВСЕ КЛЮЧИ И ТОКЕНЫ — ТОЛЬКО ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ!
# НИКОГДА не вставляй значения прямо в код!
# ============================================================

DATABASE_URL = os.environ["DATABASE_URL"]

# Московское время (UTC+3)
MSK = timezone(timedelta(hours=3))

# Пул соединений
pool: asyncpg.Pool = None


async def init_db():
    """Создаёт пул соединений и таблицу замеров."""
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id SERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                manager_tg_id BIGINT NOT NULL,
                manager_name TEXT,
                client_name TEXT,
                client_phone TEXT,
                object_type TEXT,
                location_type TEXT,
                address TEXT,
                area_m2 REAL,
                thickness_mm_avg REAL,
                warm_floor BOOLEAN,
                deadline TEXT,
                coordinates_lat REAL,
                coordinates_lon REAL,
                distance_km REAL,
                special_conditions TEXT[],
                zones JSONB,
                raw_text TEXT,
                status TEXT DEFAULT 'parsed'
            )
        """)

    logger.info("База данных инициализирована")


async def save_measurement(
    manager_tg_id: int,
    manager_name: str,
    raw_text: str,
    parsed: dict,
) -> dict:
    """
    Сохраняет замер в БД.
    Возвращает {"id": число, "created_at": datetime}
    """
    coords = parsed.get("coordinates") or {}

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO measurements (
                manager_tg_id, manager_name, client_name, client_phone,
                object_type, location_type, address,
                area_m2, thickness_mm_avg, warm_floor, deadline,
                coordinates_lat, coordinates_lon, distance_km,
                special_conditions, zones, raw_text
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9, $10, $11,
                $12, $13, $14,
                $15, $16, $17
            )
            RETURNING id, created_at
            """,
            manager_tg_id,
            manager_name,
            parsed.get("client_name"),
            parsed.get("client_phone"),
            parsed.get("object_type"),
            parsed.get("location_type"),
            parsed.get("address"),
            parsed.get("area_m2"),
            parsed.get("thickness_mm_avg"),
            parsed.get("warm_floor"),
            parsed.get("deadline"),
            coords.get("lat"),
            coords.get("lon"),
            parsed.get("distance", {}).get("distance_km") if isinstance(parsed.get("distance"), dict) else None,
            parsed.get("special_conditions", []),
            json.dumps(parsed.get("zones", []), ensure_ascii=False),
            raw_text,
        )

    created_msk = row["created_at"].astimezone(MSK)
    logger.info("Замер #%d сохранён", row["id"])

    return {
        "id": row["id"],
        "created_at": created_msk,
    }


async def get_measurement(measurement_id: int) -> dict | None:
    """Получает замер по ID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM measurements WHERE id = $1", measurement_id
        )
    return dict(row) if row else None


async def update_measurement_status(measurement_id: int, status: str):
    """Обновляет статус замера (parsed → confirmed → calculated → sent)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE measurements SET status = $1 WHERE id = $2",
            status, measurement_id,
        )


async def close_db():
    """Закрывает пул соединений."""
    global pool
    if pool:
        await pool.close()
        logger.info("Пул соединений закрыт")
