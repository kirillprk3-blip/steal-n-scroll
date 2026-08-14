FROM python:3.12-slim

# Создаём непривилегированного пользователя
RUN groupadd -r hoopbot && useradd -r -g hoopbot -d /app -s /sbin/nologin hoopbot

WORKDIR /app

# Сначала зависимости — кешируются слоем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения (без .env — .dockerignore)
COPY --chown=hoopbot:hoopbot . .

# Директория для state (data/ пересоздаётся при старте)
RUN mkdir -p data && chown hoopbot:hoopbot data

ENV PYTHONUNBUFFERED=1

USER hoopbot
CMD ["python", "bot.py"]
