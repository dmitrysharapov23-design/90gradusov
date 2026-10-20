#!/bin/bash
# Установка проекта на сервер. Запускать от пользователя gradus (не root):
#   bash install_app.sh git@github.com:<аккаунт>/90gradusov.git
# Повторный запуск безопасен: обновит код (git pull) и зависимости.
set -euo pipefail

REPO="${1:-}"
APP="$HOME/90gradusov"

[ "$(id -u)" != 0 ] || { echo "Запускать от gradus, не от root"; exit 1; }

if [ -d "$APP/.git" ]; then
  git -C "$APP" pull --ff-only
else
  [ -n "$REPO" ] || { echo "Укажите адрес репозитория: bash install_app.sh git@github.com:<аккаунт>/90gradusov.git"; exit 1; }
  git clone "$REPO" "$APP"
fi
cd "$APP"
git config user.name  >/dev/null || git config user.name  "90gradusov VPS"
git config user.email >/dev/null || git config user.email "vps@90gradusov.local"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r app/kp/requirements.txt -r app/client_agent/requirements.txt

# папки под рабочие файлы ботов (data/ не в git; закрыта от других пользователей)
mkdir -p data/kp/bot data/kp/out data/kp/template data/client_agent data/leads data/chats data/reports
chmod 700 data
[ -f .env ] && chmod 600 .env

echo
echo "Код и зависимости установлены в $APP."
[ -f .env ] || echo "Нет .env — перенесите с Mac: deploy/push_to_server.sh (запускается на Mac)."
echo "Проверка без запуска служб: bash deploy/check_server.sh"
