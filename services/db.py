"""Database layer — async SQLite for queue, dedup, and spending.

Использует aiosqlite для асинхронного доступа к SQLite без блокировки
event loop. WAL-режим для конкурентных reads без блокировок.

Таблицы:
- queue:          очередь TikTok-ссылок на обработку
- processed:      уже обработанные ссылки (dedup)
- spending:       дневной расход OpenRouter
"""

import os
import json
import logging

import aiosqlite

from config import config

log = logging.getLogger("hoopbot.db")

_DB_PATH = os.path.join("data", "hoopbot.db")
_DB_DIR = os.path.dirname(_DB_PATH)


async def get_conn() -> aiosqlite.Connection:
    """Возвращает новое подключение к БД (WAL, Row-фабрика)."""
    os.makedirs(_DB_DIR, exist_ok=True)
    db = await aiosqlite.connect(_DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db() -> None:
    """Создаёт таблицы при первом запуске."""
    db = await get_conn()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS processed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_processed_url ON processed(url);
            CREATE INDEX IF NOT EXISTS idx_queue_url ON queue(url);
        """)
        await db.commit()
    finally:
        await db.close()


async def migrate_from_json() -> None:
    """Переносит данные из старых JSON-файлов в SQLite (однократно)."""
    # Проверяем, есть ли уже данные в SQLite
    db = await get_conn()
    try:
        row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM processed")
        if row and row[0][0] > 0:
            return  # уже мигрировано

        # queue.json -> queue table
        queue_path = os.path.join("data", "queue.json")
        migrated = 0
        if os.path.exists(queue_path):
            try:
                with open(queue_path, "r", encoding="utf-8") as fh:
                    urls = json.load(fh)
                for url in urls:
                    try:
                        await db.execute(
                            "INSERT OR IGNORE INTO queue (url) VALUES (?)",
                            (url.strip(),),
                        )
                        migrated += 1
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("Ошибка миграции queue.json: %s", exc)

        # processed.json -> processed table
        processed_path = os.path.join("data", "processed.json")
        migrated_proc = 0
        if os.path.exists(processed_path):
            try:
                with open(processed_path, "r", encoding="utf-8") as fh:
                    urls = json.load(fh)
                for url in urls:
                    try:
                        await db.execute(
                            "INSERT OR IGNORE INTO processed (url) VALUES (?)",
                            (url.strip(),),
                        )
                        migrated_proc += 1
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("Ошибка миграции processed.json: %s", exc)

        await db.commit()
        if migrated > 0 or migrated_proc > 0:
            log.info(
                "Мигрировано %d очередей + %d обработанных в SQLite",
                migrated,
                migrated_proc,
            )
    finally:
        await db.close()