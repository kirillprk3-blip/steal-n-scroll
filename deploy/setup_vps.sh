#!/usr/bin/env bash
# Одношаговый установщик бота на Ubuntu VPS (22.04/24.04).
# Запускать от root:  bash /opt/hooplabs_bot/deploy/setup_vps.sh
set -euo pipefail

APP_DIR="/opt/hooplabs_bot"
SERVICE="hoopbot"

if [ ! -f "$APP_DIR/requirements.txt" ]; then
  echo "НЕ НАЙДЕН проект в $APP_DIR."
  echo "Скопируй папку с Windows на сервер:"
  echo '  scp -r C:\Users\weloyy\hooplabs_tiktok_bot root@SERVER_IP:/opt/'
  exit 1
fi

echo "==> Обновление системы и установка Python"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip

echo "==> Виртуальное окружение и зависимости"
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f "$APP_DIR/.env" ]; then
  echo "! Нет файла .env. Скопируй пример и заполни:"
  echo "  nano $APP_DIR/.env"
  echo "  (нужны BOT_TOKEN, OPENROUTER_API_KEY, TARGET_CHANNEL_ID)"
  exit 1
fi
chmod 600 "$APP_DIR/.env"

echo "==> Установка systemd-сервиса"
cp "$APP_DIR/deploy/hoopbot.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

echo "==> Статус (q — выход)"
systemctl --no-pager status "$SERVICE" --lines=15
echo "Логи: journalctl -u $SERVICE -f"
echo "Готово ✅"
