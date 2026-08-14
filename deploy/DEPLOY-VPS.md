# Деплой на VPS — пошагово (для новичка)

Цель: бот работает 24/7 на арендованном сервере (VPS) за ~200–400 ₽/мес.
На этом же сервере можно крутить ещё несколько лёгких сервисов.

## Шаг 1. Купи VPS
- Провайдер на выбор: **FirstVDS (~219–300 ₽)**, **AdminVPS (~299 ₽)**,
  **Beget**, **VDSina**.
- Конфигурация: **1 vCPU, 1 ГБ RAM, 10–20 ГБ SSD, IPv4, Ubuntu 22.04/24.04**.
  (Ориентируйся на KVM, образ Ubuntu, наличие IPv4.)
- После покупки тебе дадут: **IP-адрес сервера** и **пароль root** (в панели/на почте).

## Шаг 2. Подключись к серверу с Windows
- Проще всего через **PuTTY** (скачай putty.exe).
- Запусти PuTTY → в поле Host Name впиши IP сервера → Open.
- Логин: `root`, пароль: тот что дали (при вводе пароль не видно — это нормально).
- Альтернатива без PuTTY: в PowerShell набери `ssh root@IP` и введи пароль.

## Шаг 3. Загрузи проект на сервер
Закрой PuTTY (сессию можно оставить, это из другой программы).

**Способ A — WinSCP (перетащить мышкой, проще):**
1. Скачай и открой **WinSCP**.
2. Заполни: Host=IP, User=root, Password=пароль.
3. Слева (Windows) зайди в `C:\Users\weloyy\hooplabs_tiktok_bot`.
4. Справа (сервер) войди в папку `/opt`.
5. Перетащи всю папку `hooplabs_tiktok_bot` в `/opt`.

**Способ B — scp из PowerShell:**
```powershell
scp -r C:\Users\weloyy\hooplabs_tiktok_bot root@IP:/opt/
```
Введи пароль. Папка появится как `/opt/hooplabs_tiktok_bot`.

## Шаг 4. Один раз запусти установщик
Снова открой PuTTY/SSH и выполни:
```bash
bash /opt/hooplabs_tiktok_bot/deploy/setup_vps.sh
```
Скрипт сам: обновит систему, поставит Python, создаст venv, установит
зависимости, проверит `.env` (идет вместе с проектом — там уже твои токены),
установит systemd-сервис и запустит бота.

## Шаг 5. Проверь, что бот работает
```bash
systemctl status hoopbot        # должно быть active (running)
journalctl -u hoopbot -f        # логи в реальном времени (Ctrl+C — выход)
```
Открой Telegram, напиши боту @stealnscroll_bot — он должен ответить.

## Как добавить ещё сервисы на этот же VPS
1. Создай папку: `mkdir -p /opt/myservice`
2. Закинь код (WinSCP/scp).
3. Сделай свой systemd-юнит по аналогии с `deploy/hoopbot.service`
   (поменяй WorkingDirectory и ExecStart).
4. `systemctl daemon-reload && systemctl enable --now myservice`

Память контролируй: `free -h` (при 1 ГБ держи суммарно ~5–6 лёгких процессов).

## Полезные команды
| Команда | Назначение |
|---|---|
| `systemctl restart hoopbot` | Перезапустить бота |
| `systemctl status hoopbot` | Статус |
| `journalctl -u hoopbot -f` | Логи |
| `free -h` | Сколько памяти свободно |
```
