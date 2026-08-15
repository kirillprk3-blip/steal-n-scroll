"""Spending tracker: дневной лимит бюджета OpenRouter.

Хранит посуточный usage в таблице spending SQLite.
При достижении DAILY_BUDGET_CENTS — блокирует обработку новых роликов.

Таймзона: Europe/Moscow (UTC+3) для корректного сброса лимита в полночь по Москве.
"""

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from services.db import get_conn

# Цены моделей: input/output за 1M токенов в USD
# Источник: openrouter.ai/models на момент разработки
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "google/gemini-2.5-flash": (0.30, 2.50),
    "google/gemini-2.5-pro": (2.50, 10.00),
    "qwen/qwen-2.5-vl-72b-instruct": (0.30, 0.60),
    "qwen/qwen-2-vl-72b-instruct": (0.30, 0.60),
    "qwen/qwen-2.5-vl-7b-instruct": (0.10, 0.20),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "anthropic/claude-3-haiku": (0.25, 1.25),
}

_TIMEZONE = ZoneInfo("Europe/Moscow")


def _default_price(model: str) -> tuple[float, float]:
    """Fallback цена для неизвестной модели — средняя."""
    return (1.0, 3.0)


def _today_key() -> str:
    """Возвращает сегодняшнюю дату в Europe/Moscow."""
    return datetime.now(_TIMEZONE).strftime("%Y-%m-%d")


async def _fetch_day(date_key: Optional[str] = None) -> dict:
    """Достаёт запись дня из БД. Возвращает dict с ключами или пустой."""
    key = date_key or _today_key()
    db = await get_conn()
    row = await db.execute_fetchall(
        "SELECT prompt_tokens, completion_tokens, cost_usd, requests "
        "FROM spending WHERE date = ?",
        (key,),
    )
    if row:
        return {
            "prompt_tokens": row[0][0],
            "completion_tokens": row[0][1],
            "cost_usd": row[0][2],
            "requests": row[0][3],
        }
    return {}


def calculate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    """Считает стоимость запроса в долларах США по ценам OpenRouter.

    Args:
        prompt_tokens: токенов в запросе (input)
        completion_tokens: токенов в ответе (output)
        model: имя модели (e.g. "google/gemini-2.5-flash")

    Returns:
        Стоимость в USD
    """
    in_price, out_price = _MODEL_PRICES.get(model, _default_price(model))
    cost = (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000
    return cost


async def track_usage(prompt_tokens: int, completion_tokens: int, model: str) -> dict:
    """Записывает usage в дневной лог (транзакция SQLite).

    Returns:
        Словарь с {prompt_tokens, completion_tokens, cost_usd} за этот запрос
    """
    cost = calculate_cost(prompt_tokens, completion_tokens, model)
    key = _today_key()

    db = await get_conn()
    await db.execute(
        """INSERT INTO spending (date, prompt_tokens, completion_tokens, cost_usd, requests)
           VALUES (?, ?, ?, ?, 1)
           ON CONFLICT(date) DO UPDATE SET
               prompt_tokens = prompt_tokens + excluded.prompt_tokens,
               completion_tokens = completion_tokens + excluded.completion_tokens,
               cost_usd = cost_usd + excluded.cost_usd,
               requests = requests + 1""",
        (key, prompt_tokens, completion_tokens, round(cost, 6)),
    )
    await db.commit()

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(cost, 6),
    }


async def get_daily_spent_cents() -> float:
    """Возвращает, сколько $ЦЕНТОВ потрачено сегодня."""
    day = await _fetch_day()
    return round(day.get("cost_usd", 0.0) * 100, 4)


async def is_budget_exceeded(budget_cents: float) -> bool:
    """Проверяет, превышен ли дневной лимит."""
    if budget_cents <= 0:
        return False  # 0 = без лимита
    return await get_daily_spent_cents() >= budget_cents


async def get_daily_report() -> str:
    """Краткий отчёт о расходах за сегодня."""
    day = await _fetch_day()
    if not day:
        return "Сегодня расходов не было."
    return (
        f"💰 Сегодня: {day.get('requests', 0)} запросов, "
        f"{day.get('prompt_tokens', 0):,} in / {day.get('completion_tokens', 0):,} out токенов, "
        f"${day.get('cost_usd', 0):.4f}"
    )