"""Очередь TikTok-ссылок для batch-обработки (SQLite).

Ссылки, скинутые боту, накапливаются в таблице queue БД. Команда /run берёт
до N (максимум 10) и обрабатывает. Разрешение дублей — в dedup.
"""

from services.db import get_conn


async def add(url: str) -> tuple[bool, str]:
    """Добавляет ссылку в очередь.

    Returns:
        (добавлено?, пояснение)
    """
    from services.dedup import is_processed

    u = url.strip().lower()

    if await is_processed(u):
        return False, "этот ролик уже обрабатывали ранее"

    db = await get_conn()
    try:
        await db.execute("INSERT INTO queue (url) VALUES (?)", (u,))
        await db.commit()
        count = await _count(db)
        return True, f"в очереди: {count}"
    except Exception:
        count = await _count(db)
        return False, f"уже в очереди. В очереди: {count}"
    # Подключение не закрываем — глобальное


async def count() -> int:
    db = await get_conn()
    return await _count(db)


async def _count(db) -> int:
    row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM queue")
    return row[0][0] if row else 0


async def take(n: int) -> list[str]:
    """Забирает до n ссылок из начала очереди (удаляя их).

    Использует ORDER BY id ASC для FIFO-поведения.
    Операция атомарна — в транзакции.
    """
    db = await get_conn()
    rows = await db.execute_fetchall(
        "SELECT url FROM queue ORDER BY id ASC LIMIT ?", (n,)
    )
    urls = [row[0] for row in rows]
    if urls:
        placeholders = ",".join("?" for _ in urls)
        await db.execute(
            f"DELETE FROM queue WHERE url IN ({placeholders})", urls
        )
        await db.commit()
    return urls


async def clear() -> int:
    db = await get_conn()
    row = await db.execute_fetchall("SELECT COUNT(*) as cnt FROM queue")
    n = row[0][0] if row else 0
    await db.execute("DELETE FROM queue")
    await db.commit()
    return n