"""Парсер TikTok-каруселей через embed-эндпоинт.

Не требует Playwright, X-Bogus или сторонних API.
Использует https://www.tiktok.com/embed/v2/{id} — endpoint,
который TikTok отдаёт серверам без JS.

Логика:
1. Резолв коротких ссылок (vt.tiktok.com, vm.tiktok.com) через HEAD-редирект
2. Извлекаем item_id из URL (/photo/{id})
3. Делаем GET на embed/v2/{id}
4. Извлекаем все URL изображений photomode из HTML
5. Дедуплицируем (каждый слайд есть на p16 и p19 CDN)
6. Если embed endpoint не сработал — fallback на TikWM
"""

import asyncio
import logging
import re
from typing import Optional

import aiohttp

from config import config

log = logging.getLogger("tiktok_scraper")


class TikTokScraperError(Exception):
    """Ошибка парсинга TikTok."""


class VideoOnlyError(TikTokScraperError):
    """Ссылка ведёт на видео, а не на карусель."""


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_PHOTO_ID_RE = re.compile(r'/photo/(\d+)')
_IMAGE_RE = re.compile(
    r'https?://[^"\'\\\s<>]*?photomode[^"\'\\\s<>]*?'
    r'\.(?:jpe?g|png|webp)(?:\?[^"\'\\\s<>]*)?'
)
_SHORTLINK_DOMAINS = ("vt.tiktok.com", "vm.tiktok.com")


def _extract_item_id(tiktok_url: str) -> str:
    """Извлекает ID поста из TikTok URL."""
    m = _PHOTO_ID_RE.search(tiktok_url)
    if not m:
        raise TikTokScraperError(
            "Не удалось распознать ID поста TikTok. "
            "Ссылка должна содержать /photo/{id}"
        )
    return m.group(1)


def _dedup(urls: list[str]) -> list[str]:
    """Убирает дубликаты, предпочитает signed URLs (с x-expires=)."""
    by_key: dict[str, list[str]] = {}
    for url in urls:
        m = re.search(r'/([^/]+?)~tplv-photomode', url)
        key = m.group(1) if m else url
        by_key.setdefault(key, []).append(url)

    result = []
    for key, versions in by_key.items():
        # Предпочитаем URL с x-expires= (подписанный)
        signed = [v for v in versions if 'x-expires=' in v]
        result.append(signed[0] if signed else versions[0])
    return result


async def resolve_shortlink(url: str, session: aiohttp.ClientSession) -> str:
    """Резолвит короткую ссылку TikTok через HEAD-редирект.

    Args:
        url: короткая ссылка (vt.tiktok.com/..., vm.tiktok.com/...)
        session: aiohttp-сессия для запроса

    Returns:
        Полный URL после редиректов.
    """
    try:
        async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resolved = str(resp.url)
            log.info("Shortlink resolved: %s -> %s", url, resolved)
            return resolved
    except Exception as exc:
        log.warning("Shortlink resolution failed for %s: %s", url, exc)
        # Если HEAD не сработал, пробуем GET
        try:
            async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resolved = str(resp.url)
                log.info("Shortlink resolved via GET: %s -> %s", url, resolved)
                return resolved
        except Exception as exc2:
            log.warning("Shortlink GET also failed for %s: %s", url, exc2)
            return url  # возвращаем как есть, _extract_item_id выдаст ошибку


async def _try_embed(tiktok_url: str) -> list[str]:
    """Пытается получить слайды через embed/v2/{id}.

    Возвращает список URL изображений или бросает TikTokScraperError/VideoOnlyError.
    """
    item_id = _extract_item_id(tiktok_url)
    embed_url = f"https://www.tiktok.com/embed/v2/{item_id}"

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT)

    last_err: Optional[Exception] = None

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for attempt in range(config.TIKWM_MAX_RETRIES):
            try:
                async with session.get(embed_url) as resp:
                    if resp.status != 200:
                        raise TikTokScraperError(
                            f"Embed endpoint вернул HTTP {resp.status}."
                        )
                    raw = await resp.text()

                urls = _IMAGE_RE.findall(raw)
                if not urls:
                    raise VideoOnlyError(
                        "Это не карусель (Photo Mode). "
                        "Бот принимает только TikTok-карусели."
                    )

                unique = _dedup(urls)
                log.info("TikTok embed: %d images from %s", len(unique), tiktok_url)
                return unique

            except (VideoOnlyError, TikTokScraperError):
                raise
            except Exception as exc:
                last_err = exc
                if attempt == config.TIKWM_MAX_RETRIES - 1:
                    log.warning(
                        "Failed to fetch embed after %d attempts: %s",
                        config.TIKWM_MAX_RETRIES, exc,
                    )
                await asyncio.sleep(min(2 * attempt + 1, 6))

    raise TikTokScraperError(
        f"Embed endpoint недоступен после {config.TIKWM_MAX_RETRIES} попыток."
    )


async def get_tiktok_slides(tiktok_url: str) -> list[str]:
    """Возвращает список прямых ссылок на слайды карусели.

    Автоматический fallback: если embed endpoint не сработал (TikTokScraperError,
    но не VideoOnlyError), пробует TikWM API.
    """
    resolved = tiktok_url

    # Определяем, нужно ли резолвить короткую ссылку
    clean = tiktok_url.strip().lower()
    if any(domain in clean for domain in _SHORTLINK_DOMAINS):
        headers = {"User-Agent": _USER_AGENT}
        timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            resolved = await resolve_shortlink(tiktok_url, session)

    try:
        return await _try_embed(resolved)
    except VideoOnlyError:
        raise
    except TikTokScraperError:
        pass  # fallback to TikWM

    # Fallback: TikWM API
    from services.tikwm import get_tiktok_slides as tikwm_get, TikWMError

    log.info("TikTok scraper fallback -> TikWM for %s", tiktok_url)
    try:
        slides = await tikwm_get(tiktok_url)
        if slides:
            log.info("TikWM fallback: %d images from %s", len(slides), tiktok_url)
            return slides
    except TikWMError as exc:
        log.warning("TikWM fallback also failed for %s: %s", tiktok_url, exc)

    raise TikTokScraperError(
        "Не удалось получить слайды: embed endpoint и TikWM недоступны."
    )