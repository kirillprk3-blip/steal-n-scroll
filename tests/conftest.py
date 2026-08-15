"""Shared fixtures and test configuration.

Resets module-level state between tests for isolation.
Redirects all file/DB paths to tempdir.
"""

import pytest


@pytest.fixture(autouse=True)
async def isolate_state(tmp_path, monkeypatch):
    """Изолируем все модули, работающие с файлами/БД, в tempdir.

    - Перенаправляем _DB_PATH в tmp/data/hoopbot.db
    - Инициализируем таблицы
    """
    from services import db

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setattr(db, "_DB_PATH", str(data_dir / "hoopbot.db"))

    # Инициализируем БД
    await db.init_db()

    yield

    # Закрываем глобальное подключение, чтобы следующий тест начал с чистого
    await db.close_db()


@pytest.fixture
def sample_json_response() -> dict:
    return {
        "original": "Catch and shoot",
        "translation": "Ловлю и бросаю",
        "design": "Поставь текст внизу",
    }


@pytest.fixture
def sample_html_response() -> str:
    """Имитация HTML-ответа от API (Cloudflare)."""
    return "<html><body>Just a moment...</body></html>"