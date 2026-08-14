"""Парсер TikTok-каруселей через бесплатный TikWM API.

Устойчивость: таймауты, ретраи на сетевые сбои, явная диагностика
"видео вместо карусели" и ошибок самого API.
"""

import asyncio
import json
import re

import aiohttp

from config import config

_TIKTOK_URL_RE = re.compile(r"(tiktok\.com|t\.co|musical\.ly)", re.IGNORECASE)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class TikWMError(Exception):
    """Любая невосстановимая ошибка парсинга, показывается пользователю."""


class VideoOnlyError(TikWMError):
    """Ссылка ведёт на видео, а не на карусель (фото-режим)."""

    def __init__(self, video_url: str = ""):
        self.video_url = video_url
        super().__init__(
            "Это видео, а не карусель. Бот принимает TikTok-карусели (Photo Mode)."
        )


def _is_transient(exc: Exception) -> bool:
    """Проверяет, стоит ли повторить запрос при этой ошибке."""
    if isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
        return True
    # JSONDecodeError — API вернул не-JSON (HTML, Cloudflare), стоит повторить
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return True
    return False


class _RetryableHttpError(Exception):
    """HTTP-статус, который можно ретраить (403, 429, 5xx)."""


def is_tiktok_url(text: str) -> bool:
    return bool(_TIKTOK_URL_RE.search(text))


async def get_tiktok_slides(tiktok_url: str) -> list[str]:
    """Возвращает прямые ссылки на .jpg слайды. Бросает TikWMError/VideoOnlyError."""
    if not is_tiktok_url(tiktok_url):
        raise TikWMError("Ссылка не распознана как TikTok.")

    api_url = "https://www.tikwm.com/api/"
    params = {"url": tiktok_url, "hd": 1}
    timeout = aiohttp.ClientTimeout(total=config.TIKWM_TIMEOUT)
    headers = {"User-Agent": _USER_AGENT}

    last_err: Exception | None = None
    data: dict | None = None

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for attempt in range(config.TIKWM_MAX_RETRIES):
            try:
                async with session.get(api_url, params=params) as resp:
                    if resp.status != 200:
                        # 403 (TikTok блокирует TikWM) / 429 (rate limit) / 5xx — ретраим
                        if resp.status in (403, 429) or resp.status >= 500:
                            raise _RetryableHttpError(f"HTTP {resp.status}")
                        raise TikWMError(f"Сервер TikWM вернул HTTP {resp.status}.")
                    raw = await resp.text()
                    data = json.loads(raw)
                break
            except TikWMError:
                raise  # не ретраим, если сервер явно вернул невместную ошибку
            except _RetryableHttpError as exc:
                last_err = exc
                if attempt == config.TIKWM_MAX_RETRIES - 1:
                    raise TikWMError(
                        f"Сервер TikWM недоступен после {config.TIKWM_MAX_RETRIES} попыток "
                        f"(последний ответ: HTTP {exc}). Попробуй позже."
                    ) from exc
                await asyncio.sleep(min(2 * attempt + 1, 6))
            except Exception as exc:
                last_err = exc
                if not _is_transient(exc) or attempt == config.TIKWM_MAX_RETRIES - 1:
                    raise TikWMError(
                        f"Не удалось распознать ответ TikWM (попытка {attempt + 1}): {exc}"
                    ) from exc
                await asyncio.sleep(min(2 * attempt + 1, 6))

    if data is None:
        raise TikWMError("Не удалось получить ответ от TikWM после всех попыток.")

    if data.get("code") != 0:
        raise TikWMError(f"Ошибка парсинга TikWM: {data.get('msg')}")

    d = data.get("data") or {}
    images = d.get("images") or []
    if images:
        return images

    video = d.get("play") or d.get("video") or ""
    if video:
        raise VideoOnlyError(video_url=video)

    raise TikWMError("Не удалось извлечь изображения из ссылки.")
