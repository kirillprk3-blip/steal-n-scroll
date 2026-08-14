"""Парсер TikTok-каруселей через headless Chromium (Playwright).

Прямой парсинг TikTok вместо использования сторонних API (TikWM и др.),
которые возвращают 403 на Render.

Логика:
1. Запускает headless Chromium
2. Загружает страницу TikTok photo mode
3. Извлекает URL изображений из DOM
4. Возвращает уникальные слайды
5. Fallback: если Playwright не установлен — пробует TikWM
"""

import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright

from config import config

log = logging.getLogger("tiktok_scraper")

_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-setuid-sandbox",
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class TikTokScraperError(Exception):
    """Ошибка парсинга TikTok через Playwright."""


class VideoOnlyError(TikTokScraperError):
    """Ссылка ведёт на видео, а не на карусель."""


async def get_tiktok_slides(tiktok_url: str) -> list[str]:
    """Возвращает список прямых ссылок на JPG слайды.

    Использует headless Chromium для обхода блокировок TikTok.
    Если Playwright недоступен — пробует TikWM как fallback.
    """
    try:
        return await _scrape_with_playwright(tiktok_url)
    except TikTokScraperError:
        raise
    except Exception as exc:
        log.warning("Playwright scraper failed: %s", exc)
        # Fallback: попробовать TikWM
        from services.tikwm import get_tiktok_slides as tikwm_fallback
        return await tikwm_fallback(tiktok_url)


async def _scrape_with_playwright(tiktok_url: str) -> list[str]:
    """Загружает страницу TikTok через headless Chromium и извлекает слайды."""
    log.info("Opening TikTok page: %s", tiktok_url)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=_BROWSER_ARGS,
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=_USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
            )

            page = await context.new_page()
            # Скрываем автоматизацию
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            try:
                await page.goto(tiktok_url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_selector("img", timeout=15000)
                await page.wait_for_timeout(3000)

                # Debug: страница и изображения
                page_title = await page.title()
                page_url = page.url
                log.info("Page title: %s", page_title)
                log.info("Page URL: %s", page_url)

                total_imgs = await page.evaluate("document.querySelectorAll('img').length")
                log.info("Total img tags: %d", total_imgs)

                # Извлекаем все <img> с TikTok CDN
                result = await page.evaluate("""
                    () => {
                        const imgs = document.querySelectorAll('img');
                        const debug = [];
                        const urls = [];
                        const seen = new Set();

                        for (const img of imgs) {
                            const src = img.src || '';
                            const alt = img.alt || '';
                            const cls = img.className || '';
                            debug.push({src: src.substring(0, 150), alt: alt.substring(0, 50), cls: cls.substring(0, 40)});

                            if (src.includes('tiktokcdn') &&
                                src.includes('photomode') &&
                                !seen.has(src)) {
                                seen.add(src);
                                urls.push(src);
                            }
                        }

                        return {debug: debug.slice(0, 15), urls: urls};
                    }
                """)

                total = len(result.get("urls", []))
                log.info("Found %d photomode images", total)

                # Логируем несколько первых src для отладки
                if result.get("debug"):
                    for d in result["debug"][:8]:
                        log.info("  img src=%s cls=%s", d["src"][:100], d["cls"])

                urls = result.get("urls", [])

                if not urls:
                    # Если картинок нет — попробуем другой путь:
                    # некоторые страницы используют video poster вместо img
                    log.info("No photomode images. Checking video poster...")
                    poster = await page.evaluate("""
                        () => {
                            const videos = document.querySelectorAll('video');
                            return Array.from(videos).map(v => v.poster).filter(Boolean);
                        }
                    """)
                    if poster:
                        log.info("Found video posters: %s", poster)
                    raise TikTokScraperError(
                        "Не удалось найти изображения на странице TikTok. "
                        "Возможно, ссылка ведёт на видео, а не на карусель."
                    )

                return urls

            finally:
                await browser.close()

    except TikTokScraperError:
        raise
    except Exception as exc:
        raise TikTokScraperError(
            f"Ошибка загрузки TikTok через Playwright: {exc}"
        ) from exc