# HoopLabs — TikTok Carousel Translator Bot

Парсер и AI-переводчик баскетбольных TikTok-каруселей (Photo Mode).
Слайды обрабатываются одним Vision-запросом через OpenRouter: OCR + живой
баскетбольный перевод (сленг хуперов) + совет по дизайну текста. Готовый
альбом уходит пользователю и дублируется в закрытый канал-архив.

## Стек
Python 3.11+, Aiogram 3, OpenRouter. Модели (порядок fallback, экономично + эффективно):
`google/gemini-2.5-flash` → `qwen/qwen-2.5-vl-72b-instruct`. Смена без правки кода.

## Локальный запуск (Windows)
```powershell
cd C:\Users\weloyy\hooplabs_tiktok_bot
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # заполните BOT_TOKEN и OPENROUTER_API_KEY
python bot.py
```

## Деплой на VPS (24/7, systemd)
```bash
# на сервере:
scp -r hooplabs_tiktok_bot root@VPS:/opt/hooplabs_bot
cd /opt/hooplabs_bot
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # пропишите ключи
cp deploy/hoopbot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable hoopbot
systemctl start hoopbot
systemctl status hoopbot
```

## Деплой через Docker
```bash
cd deploy && docker compose up -d --build
```

## Управление
```bash
systemctl restart hoopbot    # перезапуск
journalctl -u hoopbot -f     # логи
```

## Важно
- Бот принимает **карусели** (Photo Mode). Видео отклоняется с явным сообщением.
- Промо-слайд добавляется в **финальную** партию (при >10 слайдов карусель режется по партиям).
- Картинки обрабатываются в памяти (io.BytesIO), файлы на диск не копятся.
- Все изображения шлются из памяти через base64 — не зависят от срока жизни ссылки TikWM.
