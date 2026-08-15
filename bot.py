"""HoopLabs TikTok Carousel Translator Bot — entrypoint.

Запуск:  python bot.py
Деплой:  systemd (см. deploy/hoopbot.service) или Docker (см. deploy/).

Особенности:
- Graceful shutdown по SIGINT/SIGTERM
- skip_updates=True при старте (игнорируем старые апдейты после рестарта)
"""

import asyncio
import logging
import os
import signal

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from config import config
from handlers.carousel import router
from services.db import close_db, init_db, migrate_from_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("hoopbot")


def _validate():
    problems = []
    if not config.BOT_TOKEN:
        problems.append("BOT_TOKEN не задан")
    if not config.OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY не задан")
    if problems:
        raise SystemExit("❌ Настройка неполная: " + "; ".join(problems))
    if not config.TARGET_CHANNEL_ID:
        log.warning("TARGET_CHANNEL_ID пуст — архив в канал отключён, будет только чат.")


async def main():
    _validate()

    # Инициализация SQLite + миграция из JSON
    await init_db()
    await migrate_from_json()

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # Lifecycle: SQLite (init_db вызывается явно до polling, close_db — на shutdown)
    dp.shutdown.register(close_db)
    dp.shutdown.register(bot.session.close)

    dp.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="run", description="Обработать до 10 роликов из очереди"),
        BotCommand(command="count", description="Сколько роликов в очереди"),
        BotCommand(command="budget", description="Статус дневного бюджета OpenRouter"),
        BotCommand(command="clear", description="Очистить очередь"),
        BotCommand(command="help", description="Справка"),
    ])

    log.info("Бот запущен. Модели: %s", config.MODEL_LIST)
    log.info(
        "Канал-архив: %s", f"ID={config.TARGET_CHANNEL_ID}" if config.TARGET_CHANNEL_ID else "отключён"
    )

    # HTTP health-check сервер (для Render.com — иначе уснёт через 15 мин)
    port = int(os.getenv("PORT", 10000))
    health_app = web.Application()

    async def health_check(_request):
        return web.Response(text="ok", content_type="text/plain")

    health_app.router.add_get("/", health_check)
    health_runner = web.AppRunner(health_app)
    await health_runner.setup()
    site = web.TCPSite(health_runner, "0.0.0.0", port)
    await site.start()
    log.info("Health-check сервер на порту %d", port)

    # Graceful shutdown: перехватываем сигналы
    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("Получен сигнал остановки, завершаю работу…")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    # Запускаем polling (skip_updates=False — важно! на Render бот может
    # просыпаться после сна, нужно получить сообщения за период бездействия)
    polling_task = asyncio.create_task(
        dp.start_polling(bot, skip_updates=False)
    )

    await stop_event.wait()
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    await health_runner.cleanup()
    log.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Принудительная остановка (Ctrl+C).")
