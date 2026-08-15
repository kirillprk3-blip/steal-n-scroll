"""Database layer — async SQLite with global connection pool.

Использует единое aiosqlite-подключение на весь lifecycle бота.
WAL-режим для конкурентных reads без блокировок.

Lifecycle:
  init_db() — на старте, открывает подключение + создаёт таблицы
  close_db() — на shutdown, закрывает подключение
  get_conn() — везде в сервисах, возвращает глобальное подключение

Таблицы:
  - queue:     очередь TikTok-ссылок на обработку
  - processed: уже обработанные ссылки (dedup)
  - spending:  дневной расход OpenRouter
"""

import os
import json
import logging
from typing import Optional

import aiosqlite

log = logging.getLogger("hoopbot.db")

_DB_PATH = os.path.join("data", "hoopbot.db")
_DB_DIR = os.path.dirname(_DB_PATH)

_db: Optional[aiosqlite.Connection] = None


async def get_conn() -> aiosqlite.Connection:
    """Возвращает глобальное подключение к БД.

    Raises RuntimeError, если init_db() не был вызван.
    """
    if _db is None:
        raise RuntimeError("DB not initialised — call init_db() first.")
    return _db


async def init_db() -> None:
    """Открывает единое подключение + создаёт таблицы при первом запуске."""
    global _db
    os.makedirs(_DB_DIR, exist_ok=True)
    _db = await aiosqlite.connect(_DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.executescript("""
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
        CREATE TABLE IF NOT EXISTS spending (
            date TEXT PRIMARY KEY,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            requests INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_processed_url ON processed(url);
        CREATE INDEX IF NOT EXISTS idx_queue_url ON queue(url);
    """)
    await _db.commit()
    log.info("Database initialised: %s", _DB_PATH)


async def close_db() -> None:
    """Закрывает глобальное подключение."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        log.info("Database connection closed.")


async def migrate_from_json() -> None:
    """Переносит данные из старых JSON-файлов в SQLite (однократно)."""
    db = await get_conn()

    # Проверяем, есть ли уже данные
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