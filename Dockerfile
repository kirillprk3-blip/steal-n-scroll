# Root-level Dockerfile для Render (ожидает Dockerfile в корне)
FROM python:3.12-slim

RUN groupadd -r hoopbot && useradd -r -g hoopbot -d /app -s /sbin/nologin hoopbot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Предзагрузка LaMa ONNX модели (208MB) — вшивается в образ, не скачивается при каждом рестарте
RUN python3 -c "import urllib.request, os; \
    d = 'data/models'; os.makedirs(d, exist_ok=True); \
    p = os.path.join(d, 'big-lama.onnx'); \
    print('Downloading LaMa ONNX model...'); \
    urllib.request.urlretrieve('https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx', p); \
    print(f'LaMa model: {os.path.getsize(p)} bytes')"

COPY --chown=hoopbot:hoopbot . .

RUN mkdir -p data && chown hoopbot:hoopbot data

ENV PYTHONUNBUFFERED=1

USER hoopbot
CMD ["python", "bot.py"]