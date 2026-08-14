"""Обработчики: справка, очередь ссылок и batch-обработка каруселей.

Поток: бот отвечает на всё — справка, команды, статус очереди.
Ссылки на TikTok копятся в очереди; /run N берёт до N (макс 10) и обрабатывает:
парсинг -> параллельный Vision (в памяти) -> чанкинг по 10 -> промо в финале
-> публикация в чат и дублирование в канал-архив.
"""

import asyncio
import logging
import os

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from config import config
from services.ai_vision import VisionError, analyze_slide
from services.dedup import mark_processed
from services.queue import clear as queue_clear
from services.queue import count as queue_count
from services.queue import take as queue_take
from services.spending import get_daily_report, is_budget_exceeded
from services.tiktok_scraper import TikTokScraperError, VideoOnlyError, get_tiktok_slides

log = logging.getLogger("hoopbot.handlers")
router = Router()

HELP = (
    "🎾 Steal & Scroll — русификатор TikTok-каруселей.\n\n"
    "Как работает:\n"
    "1. Скидывай сюда ссылки на TikTok-карусели (Photo Mode) — "
    "они накапливаются в очереди.\n"
    "2. Команда /run N обработает до N роликов (макс 10) и запостит "
    "результат в чат и канал-архив.\n\n"
    "Команды:\n"
    "/run [N] — обработать до N из очереди (макс 10)\n"
    "/count — сколько роликов в очереди\n"
    "/budget — статус дневного бюджета OpenRouter\n"
    "/clear — очистить очередь\n"
    "/help — эта справка"
)

# User-Agent для HTTP-запросов к CDN-изображениям
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _mime(url: str) -> str:
    low = url.lower()
    if ".png" in low:
        return "image/png"
    if ".webp" in low:
        return "image/webp"
    return "image/jpeg"


def _caption(meta: dict, idx: int) -> str:
    parts = [f"[Слайд №{idx}] {meta['translation']}".strip()]
    if meta.get("design"):
        parts.append("💡 " + meta["design"])
    return "\n\n".join(parts)[: config.CAPTION_LIMIT]


def _promo_media() -> InputMediaPhoto | None:
    if not os.path.exists(config.PROMO_IMAGE_PATH):
        return None
    with open(config.PROMO_IMAGE_PATH, "rb") as fh:
        data = fh.read()
    return InputMediaPhoto(
        media=BufferedInputFile(data, filename="promo.jpg"),
        caption=config.PROMO_CAPTION,
    )


async def _safe_edit(
    message: Message, text: str, log_on_fail: bool = True
) -> bool:
    """Безопасно редактирует сообщение. Не крашит handler при ошибке.

    Возвращает True если успешно, False при любой ошибке.
    """
    try:
        await message.edit_text(text)
        return True
    except TelegramBadRequest as exc:
        if log_on_fail:
            log.warning("Не удалось отредактировать статус (%s): %s", type(exc).__name__, exc)
        return False


async def _download(session: aiohttp.ClientSession, url: str) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT)
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        return await resp.read()


async def _process_single(
    session: aiohttp.ClientSession,
    url: str,
    vision_sem: asyncio.Semaphore,
    download_sem: asyncio.Semaphore,
) -> tuple[bool, list, str]:
    """Обрабатывает один TikTok. Возвращает (ok, chunks_of_media, comment)."""
    try:
        slide_urls = await get_tiktok_slides(url)
    except (VideoOnlyError, TikTokScraperError) as exc:
        return False, [], str(exc)

    async def process_one(img_url: str, idx: int):
        try:
            async with download_sem:
                data = await _download(session, img_url)
            meta = await analyze_slide(session, data, _mime(img_url), idx, vision_sem)
            return {"bytes": data, "mime": _mime(img_url), "meta": meta}
        except (VisionError, TikTokScraperError) as exc:
            log.warning("Слайд %d упал: %s", idx, exc)
            return None
        except Exception:
            log.exception("Неожиданная ошибка слайда %d", idx)
            return None

    results = await asyncio.gather(
        *[process_one(u, i) for i, u in enumerate(slide_urls, start=1)]
    )
    ok = [r for r in results if r]
    if not ok:
        return False, [], "не удалось обработать ни один слайд карусели"

    media = []
    for idx, item in enumerate(ok, start=1):
        ext = "png" if item["mime"] == "image/png" else "jpg"
        photo = InputMediaPhoto(
            media=BufferedInputFile(item["bytes"], filename=f"slide_{idx}.{ext}"),
            caption=_caption(item["meta"], idx),
        )
        media.append(photo)

    promo = _promo_media()
    if promo:
        media.append(promo)

    chunks = [media[i : i + config.BATCH_SIZE] for i in range(0, len(media), config.BATCH_SIZE)]
    failed = len(results) - len(ok)
    return True, chunks, f"{len(ok)} слайдов" + (f", не удалось {failed}" if failed else "")


async def _send_chunk(
    message: Message, chunk: list, target: str = "chat"
) -> bool:
    """Отправляет один чанк медиа-группы. target='chat'|'channel'.

    Возвращает True при успехе, False при ошибке (ошибка логируется, в чат
    не пишется, если это канал — чтобы не путать пользователя).
    """
    try:
        if target == "channel" and config.TARGET_CHANNEL_ID:
            await message.bot.send_media_group(
                chat_id=config.TARGET_CHANNEL_ID, media=chunk
            )
        else:
            await message.answer_media_group(media=chunk)
        return True
    except TelegramBadRequest as exc:
        dest = "канал" if target == "channel" else "чат"
        log.error("Ошибка отправки в %s: %s", dest, exc)
        if target == "chat":
            await message.answer(f"⚠️ Telegram отклонил отправку: {exc}")
        return False


async def _post(message: Message, chunks: list) -> tuple[int, int]:
    """Публикует чанки в чат и канал. Возвращает (chat_ok, channel_ok)."""
    chat_ok = 0
    for chunk in chunks:
        if await _send_chunk(message, chunk, target="chat"):
            chat_ok += 1

    channel_ok = 0
    if config.TARGET_CHANNEL_ID:
        for chunk in chunks:
            if await _send_chunk(message, chunk, target="channel"):
                channel_ok += 1

    return chat_ok, channel_ok


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(HELP)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP)


@router.message(Command("count"))
async def cmd_count(message: Message):
    n = await queue_count()
    await message.answer(f"В очереди: {n} роликов.")


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    n = await queue_clear()
    await message.answer(f"Очередь очищена (убрано {n}).")


@router.message(Command("budget"))
async def cmd_budget(message: Message):
    report = get_daily_report()
    budget_info = f"Дневной лимит: {config.DAILY_BUDGET_CENTS}¢"
    await message.answer(f"{report}\n{budget_info}")


@router.message(Command("run"))
async def cmd_run(message: Message):
    n = 10
    args = message.text.split()
    if len(args) >= 2:
        try:
            n = int(args[1])
        except ValueError:
            pass
    n = max(1, min(n, 10))

    if await queue_count() == 0:
        await message.answer("Очередь пуста. Скидывай ссылки на карусели — копятся тут.")
        return

    # Проверка дневного бюджета OpenRouter
    if is_budget_exceeded(config.DAILY_BUDGET_CENTS):
        await message.answer(
            f"⚠️ Дневной бюджет ${config.DAILY_BUDGET_CENTS / 100:.2f} исчерпан.\n"
            f"{get_daily_report()}\n"
            "Лимит сбросится завтра. /budget для деталей."
        )
        return

    status = await message.answer(f"🔄 Обрабатываю до {n} роликов из очереди…")
    urls = await queue_take(n)
    vision_sem = asyncio.Semaphore(config.MAX_PARALLEL)
    download_sem = asyncio.Semaphore(config.MAX_DOWNLOADS)

    done_ok = 0
    done_fail = 0

    try:
        async with aiohttp.ClientSession() as session:
            for i, url in enumerate(urls, start=1):
                ok, chunks, comment = await _process_single(
                    session, url, vision_sem, download_sem
                )
                if ok:
                    await _post(message, chunks)
                    await mark_processed(url)
                    done_ok += 1
                else:
                    done_fail += 1
                    await message.answer(f"❌ {url}\n{comment}")
                await _safe_edit(
                    status,
                    f"🔄 {i}/{len(urls)}… (ок: {done_ok}, ошибок: {done_fail})",
                )
    except Exception:
        log.exception("Критическая ошибка в /run: обработано %d, не удалось %d", done_ok, done_fail)
        await message.answer(
            f"⚠️ Бот упал с ошибкой. Обработано: {done_ok}, с ошибкой: {done_fail}.\n"
            "Необработанные ссылки не потеряны — напиши /run снова, проблема устранена."
        )

    await _safe_edit(
        status,
        f"✅ Готово: {done_ok} роликов обработано, {done_fail} с ошибкой.",
    )


@router.message(F.text.contains("tiktok.com"))
async def enqueue_tiktok(message: Message):
    from services.queue import add as queue_add

    from services.queue import add as queue_add

    added, note = await queue_add(message.text.strip())
    if added:
        await message.answer(f"✅ Добавлено в очередь ({note}).\nДля обработки: /run")
    else:
        n = await queue_count()
        await message.answer(f"⏭ Не добавлено — {note}. В очереди: {n}.")


# Ловит прочие текстовые сообщения (чтобы бот всегда отвечал в ТГ).
@router.message(F.text)
async def catch_all_text(message: Message):
    await message.answer(HELP)
