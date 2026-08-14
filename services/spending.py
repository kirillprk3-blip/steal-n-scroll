"""Spending tracker: дневной лимит бюджета OpenRouter.

Хранит посуточный usage в data/spending.json.
При достижении DAILY_BUDGET_CENTS — блокирует обработку новых роликов.
"""

import json
import os
from datetime import date
from typing import Dict

_DATA_DIR = "data"
_PATH = os.path.join(_DATA_DIR, "spending.json")

# Цены моделей: input/output за 1M токенов в USD
# Источник: openrouter.ai/models на момент разработки
_MODEL_PRICES: Dict[str, tuple[float, float]] = {
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


def _default_price(model: str) -> tuple[float, float]:
    """Fallback цена для неизвестной модели — средняя."""
    return (1.0, 3.0)


def _load() -> dict:
    if not os.path.exists(_PATH):
        return {}
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(data: dict) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _today_key() -> str:
    return date.today().isoformat()  # "2026-08-14"


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


def track_usage(prompt_tokens: int, completion_tokens: int, model: str) -> dict:
    """Записывает usage в дневной лог.

    Returns:
        Словарь с {prompt_tokens, completion_tokens, cost_usd} за этот запрос
    """
    cost = calculate_cost(prompt_tokens, completion_tokens, model)
    data = _load()
    key = _today_key()
    day = data.get(key, {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "requests": 0})
    day["prompt_tokens"] += prompt_tokens
    day["completion_tokens"] += completion_tokens
    day["cost_usd"] = round(day["cost_usd"] + cost, 6)
    day["requests"] += 1
    data[key] = day
    _save(data)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(cost, 6),
    }


def get_daily_spent_cents() -> float:
    """Возвращает, сколько $ЦЕНТОВ потрачено сегодня."""
    data = _load()
    day = data.get(_today_key(), {})
    return round(day.get("cost_usd", 0.0) * 100, 4)


def is_budget_exceeded(budget_cents: float) -> bool:
    """Проверяет, превышен ли дневной лимит."""
    if budget_cents <= 0:
        return False  # 0 = без лимита
    return get_daily_spent_cents() >= budget_cents


def get_daily_report() -> str:
    """Краткий отчёт о расходах за сегодня."""
    data = _load()
    day = data.get(_today_key(), {})
    if not day:
        return "Сегодня расходов не было."
    return (
        f"💰 Сегодня: {day.get('requests', 0)} запросов, "
        f"{day.get('prompt_tokens', 0):,} in / {day.get('completion_tokens', 0):,} out токенов, "
        f"${day.get('cost_usd', 0):.4f}"
    )