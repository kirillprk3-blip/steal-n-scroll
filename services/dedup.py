"""Дедепликация: не пересылаем/не переплачиваем за одни и те же TikTok-ссылки.

Хранит обработанные ссылки в SQLite (таблица processed) — атомарные
операции, нет race conditions, в отличие от JSON-файлов.
"""

from services.db import get_conn


def normalize(url: str) -> str:
    """Приводит URL к единому формату для сравнения."""
    return " ".join(url.strip().lower().split())


async def is_processed(url: str) -> bool:
    u = normalize(url)
    db = await get_conn()
    try:
        rows = await db.execute_fetchall(
            "SELECT 1 FROM processed WHERE url = ?", (u,)
        )
        return len(rows) > 0
    finally:
        await db.close()


async def mark_processed(url: str) -> None:
    u = normalize(url)
    db = await get_conn()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO processed (url) VALUES (?)", (u,)
        )
        await db.commit()
    finally:
        await db.close()