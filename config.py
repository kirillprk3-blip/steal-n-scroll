"""Конфигурация приложения — единый источник настроек.

Особенности:
- Загрузка из .env + пулл валидированных значений.
- TARGET_CHANNEL_ID строго int (Telegram API), пустая строка → 0 → канал отключён.
- Fallback-цепочка моделей: порядок в MODEL_LIST = порядок приоритета.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _split_models(raw: str) -> list[str]:
    """'a,b, c' -> ['a', 'b', 'c'] (порядок = порядок fallback)."""
    return [m.strip() for m in raw.split(",") if m.strip()]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _parse_channel_id(raw: Optional[str]) -> int:
    """Парсит ID канала. Пустая строка = канал отключён (0)."""
    if not raw or not raw.strip():
        return 0
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return 0


def _validate_int_id(raw: Optional[str]) -> int:
    """Валидирует числовой ID из строки. Возвращает 0 при ошибке."""
    if not raw or not raw.strip():
        return 0
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return 0


@dataclass
class Config:
    # Credentials
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    # ID канала-архива (int). 0 = отключён. Telegram требует int для supergroups.
    TARGET_CHANNEL_ID: int = field(
        default_factory=lambda: _parse_channel_id(os.getenv("TARGET_CHANNEL_ID"))
    )

    # OpenRouter
    OPENROUTER_URL: str = os.getenv(
        "OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions"
    )
    # Порядок = порядок fallback: первая рабочая модель побеждает.
    MODEL_LIST: list = field(
        default_factory=lambda: _split_models(
            os.getenv(
                "MODEL_LIST",
                "google/gemini-2.5-flash,qwen/qwen-2.5-vl-72b-instruct",
            )
        )
    )

    # Промо-слайд
    PROMO_IMAGE_PATH: str = os.getenv("PROMO_IMAGE_PATH", "assets/promo_final.jpg")
    PROMO_CAPTION: str = os.getenv(
        "PROMO_CAPTION", "🏁 HoopLabs — больше баскетбольных дрилов!"
    )

    # Бюджет OpenRouter ($0.50/день = 50 центов)
    DAILY_BUDGET_CENTS: int = _env_int("DAILY_BUDGET_CENTS", 50)

    # Производительность / устойчивость
    BATCH_SIZE: int = 10  # лимит Telegram на один альбом
    MAX_PARALLEL: int = _env_int("MAX_PARALLEL", 4)  # одновременных Vision-запросов
    MAX_DOWNLOADS: int = _env_int("MAX_DOWNLOADS", 4)  # одновременных download изображений
    MAX_ATTEMPTS_PER_MODEL: int = _env_int("MAX_ATTEMPTS_PER_MODEL", 2)
    REQUEST_TIMEOUT: float = _env_float("REQUEST_TIMEOUT", 120.0)
    CAPTION_LIMIT: int = _env_int("CAPTION_LIMIT", 950)  # запас под лимит 1024
    TIKWM_TIMEOUT: float = _env_float("TIKWM_TIMEOUT", 30.0)
    TIKWM_MAX_RETRIES: int = _env_int("TIKWM_MAX_RETRIES", 3)


config = Config()
