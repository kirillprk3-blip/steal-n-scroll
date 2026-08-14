"""Живой тест: валидация ключа + реальные токены Vision-слайда.

Снимает usage с OpenRouter и считает фактическую стоимость 1 слайда.
"""

import asyncio
import json
import os

import aiohttp
from dotenv import load_dotenv

load_dotenv()

KEY = os.getenv("OPENROUTER_API_KEY")
URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

MODEL = "google/gemini-2.5-flash"
IN_PRICE = 0.30 / 1_000_000   # $ за токен ввода
OUT_PRICE = 2.50 / 1_000_000  # $ за токен вывода


async def _call(payload: dict) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.post(URL, headers=HEADERS, json=payload) as r:
            txt = await r.text()
            if r.status != 200:
                return {"error": r.status, "body": txt[:400]}
            return await r.json()


async def main():
    # 1) text-only: валидация ключа + базовый usage
    print("=== 1. TEXT-ONLY (валидация ключа) ===")
    p1 = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Ответь ровно: OK"}],
        "max_tokens": 5,
    }
    d1 = await _call(p1)
    if "error" in d1:
        print("KEY FAIL:", d1)
        return
    print("usage:", d1.get("usage"))
    print("content:", d1["choices"][0]["message"]["content"])

    # 2) vision: тот же системный промпт + реальная картинка
    print("\n=== 2. VISION (реальный слайд) ===")
    img_path = os.path.join(os.path.dirname(__file__), "assets", "test_slide.jpg")
    if not os.path.exists(img_path):
        print("нет тестовой картинки, пропускаю vision-тест")
        return
    with open(img_path, "rb") as fh:
        b64 = __import__("base64").b64encode(fh.read()).decode()
    sys_prompt = open("services/ai_vision.py", encoding="utf-8").read()
    import re
    m = re.search(r'SYSTEM_PROMPT = """.*?"""', sys_prompt, re.S)
    system = m.group(0).split('"""', 2)[1] if m else ""

    p2 = {
        "model": MODEL,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Слайд №1. Проанализируй изображение."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            },
        ],
        "max_tokens": 700,
    }
    d2 = await _call(p2)
    if "error" in d2:
        print("VISION FAIL:", d2)
        return
    usage = d2.get("usage", {})
    pin = usage.get("prompt_tokens", 0)
    pout = usage.get("completion_tokens", 0)
    cost = pin * IN_PRICE + pout * OUT_PRICE
    print("usage:", usage)
    print("content:", d2["choices"][0]["message"]["content"][:300])
    print(f"\n== СТОИМОСТЬ 1 СЛАЙДА = {cost*100:.5f} $центов ({cost:.6f} $) =~ {cost*95:.3f} руб")
    print(f"== 48 слайдов/день = {cost*48*30:.3f}$/мес =~ {cost*48*30*95:.0f} руб/мес")


asyncio.run(main())
