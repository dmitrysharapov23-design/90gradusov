#!/bin/bash
# Установка служб systemd (бот КП, агент клиентов, входящие заявки, два сторожа). От root:
#   bash /home/gradus/90gradusov/deploy/install_services.sh          — установить, НЕ запускать
#   bash /home/gradus/90gradusov/deploy/install_services.sh --start  — установить и запустить
# Запускать (--start) только после того, как на Mac задачи остановлены (deploy/push_to_server.sh):
# два бота на одном токене мешают друг другу, два скрипта заявок создадут дубли лидов.
#   bash .../install_services.sh --stop — остановить и выключить все службы (возврат на Mac)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)/systemd"
APP=/home/gradus/90gradusov
UNITS="90g-kp-bot.service 90g-client-agent.timer 90g-inbound-leads.timer 90g-check-client-agent.timer 90g-check-inbound-leads.timer"

if [ "${1:-}" = "--stop" ]; then
  systemctl disable --now $UNITS
  echo "Службы остановлены и выключены."
  exit 0
fi

[ -f "$APP/.env" ] || { echo "Нет $APP/.env — сначала deploy/push_to_server.sh на Mac"; exit 1; }
install -m 644 "$SRC"/90g-*.service "$SRC"/90g-*.timer /etc/systemd/system/
systemctl daemon-reload

if [ "${1:-}" = "--start" ]; then
  systemctl enable --now $UNITS
  sleep 3
  systemctl --no-pager --lines=0 status 90g-kp-bot.service || true
  systemctl --no-pager list-timers '90g-*'
else
  echo "Службы установлены, но не запущены. Запуск: bash $0 --start"
fi
