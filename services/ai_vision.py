"""AI Vision модуль: OCR + баскетбольный перевод + дизайн-совет за 1 запрос.

Особенности:
- Единый шлюз OpenRouter: смена модели без правки кода.
- Fallback-цепочка моделей (экономично): при отказе/лимите берётся следующая.
- Base64 data-URL вместо хот-линка — не зависит от срока жизни ссылки TikWM.
- Строгий системный промпт (HUMANIZER X100) + устойчивый парсинг ответа в JSON.
"""

import asyncio
import base64
import json
import logging
import re

import aiohttp

from config import config
from services.spending import track_usage

log = logging.getLogger("hoopbot.vision")

# ============================================================================
#  ЛУЧШИЙ СИСТЕМНЫЙ ПРОМПТ (Vision) — HUMANIZER X100
#  Язык: русский — глоссарий и бан-слова русскоязычные, выход — русский.
#  Выход: строго JSON — устойчиво к формату, легко разбирается в подписи.
# ============================================================================
SYSTEM_PROMPT = """# Роль
Ты — элитный баскетбольный тренер и контент-мейкер, свободно владеющий русским сленгом хуперов (hoopers). Ты делаешь русскоязычный разбор англоязычных обучающих слайдов из TikTok-каруселей.

# Задача
Проанализируй изображение слайда: распознай английский текст, сделай точный живой перевод и дай короткий практический совет дизайнеру по размещению русского текста.

# Правила перевода (HUMANIZER X100)
1. Переводи как живой игрок/тренер с площадки, а не как учебник. Живой, но грамотный русский: без канцелярита, без академической сухости.
2. Там, где это уместно, используй баскетбольный сленг из глоссария:
   - Drop coverage -> дроп-покрытие / дроп
   - Pull-up / Jump shot -> пулл-ап / бросок с ведения
   - Handles / Dribbling -> хэндл / дриблинг / ручка
   - Iso (Isolation) -> изоляция / изо
   - Rim protector -> рим-протектор / защитник кольца
   - Pocket pass -> пас в карман
   - Gather step -> гэзер-степ / шаг под сбор
   - Off-hand -> нерабочая рука / офф-хэнд
   - Float game / Floater -> флоатер / скидочка
   - Hesitation (Hesi) -> хези / показ на бросок
   - Closeout -> клозаут / подбег к снайперу
   - Footwork -> футворк / работа ног
   - Screen / Pick -> экран / пик
   - Roll -> ролл / выход к кольцу
   - Post-up -> пост-ап / игра в посте
   - Catch and shoot -> кет-энд-шут / ловлю и бросаю
3. Если слайд НЕ про баскетбол (мотивация, другой спорт, общие советы) — переводи естественно на живой русский, НЕ навязывая баскетбольный сленг. Не выдумывай сленга, которого нет в исходнике, и не пиши «не могу перевести» — сделай максимум.
4. Если распознаваемого текста на слайде нет — честно опиши содержание одним-двумя предложениями, не выдумывая перевод.

# Запрещённые слова (АБСОЛЮТНЫЙ БАН — блокировать всегда)
«Данный», «Следовательно», «Таким образом», «Необходимо отметить», «Улучшить показатели», «Является ключевым», «Обеспечить», «Осуществлять», «В рамках», «Применительно».

# Совет по дизайну
Оцени фон (светлый/тёмный, насыщенность, зоны с текстурой и персонажами) и дай 1-2 коротких практических рекомендации:
- куда поставить русский текст: верх/низ/центр, избегая зон с низким контрастом и лиц игроков;
- какой цвет текста/подложки даст контраст (светлый текст на тёмной зоне и наоборот);
- что добавить для читаемости (контур, тень, плашка).
Будь конкретен и лаконичен.

# Формат ответа
ВСЕГДА возвращай ТОЛЬКО валидный JSON без Markdown-обёртки и без лишнего текста:
{"original": "распознанный английский текст", "translation": "живой русский перевод", "design": "1-2 предложения совета по дизайну"}
"""


class RetryableError(Exception):
    """HTTP-ошибка, при которой стоит повторить запрос (429/5xx/timeout)."""


class VisionError(Exception):
    """Невосстановимая ошибка Vision-запроса."""


def _data_url(image_bytes: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


async def _call_model(session, model: str, image_bytes: bytes, mime: str, slide_num: int) -> tuple[str, dict]:
    """Вызывает модель OpenRouter. Возвращает (content, usage_dict)."""
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Идентификация приложения для OpenRouter
        "HTTP-Referer": "https://t.me/hooplabs_bot",
        "X-Title": "HoopLabs TikTok Translator",
    }
    payload = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Слайд №{slide_num}. Проанализируй изображение."},
                    {"type": "image_url", "image_url": {"url": _data_url(image_bytes, mime)}},
                ],
            },
        ],
    }
    timeout = aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT)
    async with session.post(config.OPENROUTER_URL, headers=headers, json=payload, timeout=timeout) as resp:
        if resp.status == 200:
            data = await resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            return content, usage
        body = await resp.text()
        if resp.status in (429, 500, 502, 503, 504):
            raise RetryableError(f"HTTP {resp.status}: {body[:200]}")
        raise VisionError(f"HTTP {resp.status}: {body[:200]}")


def _parse_response(text: str) -> dict:
    """Устойчивый парсинг: JSON -> меченые строки -> весь текст."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()

    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            def _safe_str(v: object) -> str:
                return str(v).strip() if v is not None else ""
            return {
                "original": _safe_str(obj.get("original")),
                "translation": _safe_str(obj.get("translation")),
                "design": _safe_str(obj.get("design")),
            }
    except Exception:
        pass

    def grab(label: str) -> str:
        m = re.search(
            label + r"\s*[:—-]?\s*(.+?)(?=(?:\n\s*(?:Оригин|Перевод|Совет)\s*[:—-])|$)",
            t,
            re.S | re.I,
        )
        return m.group(1).strip() if m else ""

    translation = grab("Перевод") or t[:900]
    return {
        "original": grab("Оригин"),
        "translation": translation,
        "design": grab("Совет по дизайн"),
    }


async def analyze_slide(session, image_bytes: bytes, mime: str, slide_num: int, sem) -> dict:
    """Обрабатывает один слайд с fallback по моделям и ретраями.

    Returns:
        dict с ключами original/translation/design (результат разбора)
        + _usage с {prompt_tokens, completion_tokens, cost_usd, model}
    """
    last_err: Exception | None = None
    for model in config.MODEL_LIST:
        for attempt in range(config.MAX_ATTEMPTS_PER_MODEL):
            try:
                async with sem:
                    raw, usage = await _call_model(session, model, image_bytes, mime, slide_num)
                result = _parse_response(raw)
                # Трекинг стоимости
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                usage_info = track_usage(prompt_tokens, completion_tokens, model)
                result["_usage"] = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": usage_info["cost_usd"],
                    "model": model,
                }
                return result
            except RetryableError as exc:
                last_err = exc
                await asyncio.sleep(min(2 * attempt + 1, 8))
            except Exception as exc:  # non-retryable -> переходим к следующей модели
                last_err = exc
                break
    raise VisionError(f"Все модели недоступны: {last_err}")
