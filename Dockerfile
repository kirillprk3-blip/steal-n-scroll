# Root-level Dockerfile для Render (ожидает Dockerfile в корне)
FROM python:3.12-slim

# Системные зависимости для Playwright + Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r hoopbot && useradd -r -g hoopbot -d /app -s /sbin/nologin hoopbot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем Chromium в общедоступную директорию (не в ~root)
ENV PLAYWRIGHT_BROWSERS_PATH=/app/playwright-browsers
RUN python3 -m playwright install chromium && \
    chmod -R 755 /app/playwright-browsers

COPY --chown=hoopbot:hoopbot . .

RUN mkdir -p data && chown hoopbot:hoopbot data

ENV PYTHONUNBUFFERED=1

USER hoopbot
CMD ["python", "bot.py"]