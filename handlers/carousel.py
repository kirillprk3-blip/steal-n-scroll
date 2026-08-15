"""Обработчики: справка, очередь ссылок и batch-обработка каруселей.

Поток: бот отвечает на всё — справка, команды, статус очереди.
Ссылки на TikTok копятся в очереди; /run N берёт до N (макс 10) и обрабатывает:
парсинг -> параллельный Vision (в памяти) -> публикация каждого слайда отдельным
сообщением с подписью под фото -> дублирование в канал-архив.

Удаление из очереди — строго после завершения отправки или фиксации ошибки.
"""

import asyncio
import logging
import os

import aiohttp
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from config import config
from services.ai_vision import VisionError, analyze_slide
from services.dedup import mark_processed
from services.inpainter import clean_image_text_async
from services.queue import clear as queue_clear
from services.queue import count as queue_count
from services.queue import peek as queue_peek
from services.queue import remove as queue_remove
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


def _escape_md(text: str) -> str:
    """Экранирует спецсимволы Markdown (_ * ` [) обратным слешем."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def _caption(meta: dict, idx: int) -> str:
    translation = _escape_md(meta.get("translation", "")).strip()
    design = _escape_md(meta.get("design", "")).strip()

    parts = [f"**[Слайд №{idx}]**\n{translation}"]
    if design:
        parts.append(f"**💡 Подсказки по композиции:**\n{design}")

    return "\n\n".join(parts)[: config.CAPTION_LIMIT]


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


async def _send_single_photo(
    message: Message,
    data: dict,
    slide_num: int,
    target: str = "chat",
) -> bool:
    """Отправляет одно фото с подписью. target='chat'|'channel'.

    Возвращает True при успехе, False при ошибке.
    """
    ext = "png" if data["mime"] == "image/png" else "jpg"
    photo = BufferedInputFile(data["bytes"], filename=f"slide_{slide_num}.{ext}")
    caption = _caption(data["meta"], slide_num)

    try:
        if target == "channel" and config.TARGET_CHANNEL_ID:
            await message.bot.send_photo(
                chat_id=config.TARGET_CHANNEL_ID,
                photo=photo,
                caption=caption[: config.CAPTION_LIMIT],
                parse_mode="Markdown",
            )
        else:
            await message.answer_photo(
                photo=photo,
                caption=caption[: config.CAPTION_LIMIT],
                parse_mode="Markdown",
            )
        return True
    except TelegramBadRequest as exc:
        dest = "канал" if target == "channel" else "чат"
        log.error("Ошибка отправки фото #%d в %s: %s", slide_num, dest, exc)
        if target == "chat":
            await message.answer(f"⚠️ Ошибка отправки слайда #{slide_num}: {exc}")
        return False


async def _send_promo(message: Message) -> bool:
    """Отправляет промо-слайд отдельным фото (без caption)."""
    if not os.path.exists(config.PROMO_IMAGE_PATH):
        return False
    try:
        with open(config.PROMO_IMAGE_PATH, "rb") as fh:
            data = fh.read()
        photo = BufferedInputFile(data, filename="promo.jpg")
        await message.answer_photo(
            photo=photo,
            caption=config.PROMO_CAPTION,
            parse_mode="Markdown",
        )
        if config.TARGET_CHANNEL_ID:
            await message.bot.send_photo(
                chat_id=config.TARGET_CHANNEL_ID,
                photo=photo,
                caption=config.PROMO_CAPTION,
                parse_mode="Markdown",
            )
        return True
    except Exception as exc:
        log.warning("Не удалось отправить промо: %s", exc)
        return False


async def _process_single(
    session: aiohttp.ClientSession,
    url: str,
    vision_sem: asyncio.Semaphore,
    download_sem: asyncio.Semaphore,
    inpaint_sem: asyncio.Semaphore,
) -> tuple[bool, list, str]:
    """Обрабатывает один TikTok. Возвращает (ok, slides, comment).

    slides — список словарей с ключами bytes(cleaned)/mime/meta.
    """
    try:
        slide_urls = await get_tiktok_slides(url)
    except (VideoOnlyError, TikTokScraperError) as exc:
        return False, [], str(exc)

    async def process_one(img_url: str, idx: int):
        try:
            async with download_sem:
                data = await _download(session, img_url)
            meta = await analyze_slide(session, data, _mime(img_url), idx, vision_sem)
            # Inpainting: удаляем текст, оставляем очищенное фото
            log.info("Слайд %d: запуск инпейнтинга (%d байт)", idx, len(data))
            async with inpaint_sem:
                clean_bytes = await clean_image_text_async(data)
            if len(clean_bytes) != len(data):
                log.info("Слайд %d: инпейнтинг применился (%d → %d байт)",
                          idx, len(data), len(clean_bytes))
            else:
                log.warning("Слайд %d: инпейнтинг НЕ применился (размер не изменился: %d байт)",
                            idx, len(data))
            return {"bytes": clean_bytes, "mime": _mime(img_url), "meta": meta}
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

    failed = len(results) - len(ok)
    return True, ok, f"{len(ok)} слайдов" + (f", не удалось {failed}" if failed else "")


async def _post(message: Message, slides: list) -> tuple[int, int]:
    """Отправляет слайды по одному в чат и канал.

    Returns:
        (chat_ok, channel_ok) — количество успешно отправленных фото.
    """
    chat_ok = 0
    for idx, slide in enumerate(slides, start=1):
        if await _send_single_photo(message, slide, idx, target="chat"):
            chat_ok += 1

    channel_ok = 0
    if config.TARGET_CHANNEL_ID:
        for idx, slide in enumerate(slides, start=1):
            if await _send_single_photo(message, slide, idx, target="channel"):
                channel_ok += 1

    # Промо — отдельным фото в конце
    await _send_promo(message)

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
    report = await get_daily_report()
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
    if await is_budget_exceeded(config.DAILY_BUDGET_CENTS):
        await message.answer(
            f"⚠️ Дневной бюджет ${config.DAILY_BUDGET_CENTS / 100:.2f} исчерпан.\n"
            f"{await get_daily_report()}\n"
            "Лимит сбросится завтра. /budget для деталей."
        )
        return

    status = await message.answer(f"🔄 Обрабатываю до {n} роликов из очереди…")
    urls = await queue_peek(n)
    vision_sem = asyncio.Semaphore(config.MAX_PARALLEL)
    download_sem = asyncio.Semaphore(config.MAX_DOWNLOADS)
    inpaint_sem = asyncio.Semaphore(1)  # inpainting: последовательно, один за другим (CPU-bound)

    done_ok = 0
    done_fail = 0

    try:
        async with aiohttp.ClientSession() as session:
            for i, url in enumerate(urls, start=1):
                ok, slides, comment = await _process_single(
                    session, url, vision_sem, download_sem, inpaint_sem
                )
                if ok:
                    await _post(message, slides)
                    await queue_remove(url)
                    await mark_processed(url)
                    done_ok += 1
                else:
                    await queue_remove(url)  # удаляем и при ошибке
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
            "Необработанные ссылки НЕ удалены из очереди — напиши /run снова."
        )

    await _safe_edit(
        status,
        f"✅ Готово: {done_ok} роликов обработано, {done_fail} с ошибкой.",
    )


@router.message(F.text.contains("tiktok.com"))
async def enqueue_tiktok(message: Message):
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