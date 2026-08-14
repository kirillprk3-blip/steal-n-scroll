"""Парсер TikTok-каруселей через embed-эндпоинт.

Не требует Playwright, X-Bogus или сторонних API.
Использует https://www.tiktok.com/embed/v2/{id} — endpoint,
который TikTok отдаёт серверам без JS.

Логика:
1. Извлекаем item_id из URL (/photo/{id})
2. Делаем GET на embed/v2/{id}
3. Извлекаем все URL изображений photomode из HTML
4. Дедуплицируем (каждый слайд есть на p16 и p19 CDN)
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
    r'https?://[^"\'\\\s]*?photomode[^"\'\\\s]*?(?:\.jpg|\.jpeg|\.png|\.webp)'
)


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
    """Убирает дубликаты одного слайда на разных CDN (p16/p19)."""
    seen_hashes = set()
    result = []
    for url in urls:
        # Берём последний path-сегмент до ~tplv как ключ дедупа
        m = re.search(r'/([^/]+?)~tplv-photomode', url)
        key = m.group(1) if m else url
        if key not in seen_hashes:
            seen_hashes.add(key)
            result.append(url)
    return result


async def get_tiktok_slides(tiktok_url: str) -> list[str]:
    """Возвращает список прямых ссылок на слайды карусели."""
    item_id = _extract_item_id(tiktok_url)
    embed_url = f"https://www.tiktok.com/embed/v2/{item_id}"

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT)

    last_err: Optional[Exception] = None

    for attempt in range(config.TIKWM_MAX_RETRIES):
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(embed_url) as resp:
                    if resp.status != 200:
                        raise TikTokScraperError(
                            f"Embed endpoint вернул HTTP {resp.status}."
                        )
                    raw = await resp.text()

            # Извлекаем URL изображений
            urls = _IMAGE_RE.findall(raw)
            if not urls:
                # Если нет photomode — возможно это видео, а не карусель
                raise VideoOnlyError(
                    "Это не карусель (Photo Mode). "
                    "Бот принимает только TikTok-карусели."
                )

            unique = _dedup(urls)
            log.info("TikTok: %d images from %s", len(unique), tiktok_url)
            return unique

        except TikTokScraperError:
            raise
        except VideoOnlyError:
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
        f"Не удалось загрузить данные TikTok после {config.TIKWM_MAX_RETRIES} попыток."
    )


