# Root-level Dockerfile для Render (ожидает Dockerfile в корне)
FROM python:3.12-slim

RUN groupadd -r hoopbot && useradd -r -g hoopbot -d /app -s /sbin/nologin hoopbot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=hoopbot:hoopbot . .

# Модель LaMa (208MB) скачивается лениво при первом инпейнтинге (inpainter.py:_ensure_model()).
# Не вшиваем в образ — на free tier Render билд падает по таймауту.

RUN mkdir -p data && chown hoopbot:hoopbot data

ENV PYTHONUNBUFFERED=1

USER hoopbot
CMD ["python", "bot.py"]