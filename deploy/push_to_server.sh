#!/bin/bash
# Перенос с Mac на сервер того, чего нет в git. Запускать на Mac из папки проекта.
#   deploy/push_to_server.sh gradus@<адрес>            — секреты и шаблоны (.env, ключи Google, шаблон КП); Mac продолжает работать
#   deploy/push_to_server.sh gradus@<адрес> --switch   — переезд: остановить задачи на Mac, перенести их состояние на сервер
#   deploy/push_to_server.sh --back                    — вернуть задачи на Mac (сначала остановить службы на сервере!)
# Клиентские выгрузки (data/b24, data/chats, data/calc, КП в data/kp/out) НЕ переносятся.
set -euo pipefail
cd "$(dirname "$0")/.."

# задачи, которые переезжают; сторож сводки (check-summary) остаётся на Mac — сводку делает задача Claude здесь
LABELS="ru.90gradusov.kp-bot ru.90gradusov.reports-bot ru.90gradusov.manager-digest ru.90gradusov.client-agent ru.90gradusov.inbound-leads ru.90gradusov.check-client-agent ru.90gradusov.check-inbound-leads"
DOMAIN="gui/$(id -u)"
APP=/home/gradus/90gradusov

if [ "${1:-}" = "--back" ]; then
  for l in $LABELS; do
    launchctl enable "$DOMAIN/$l"
    launchctl bootstrap "$DOMAIN" "$HOME/Library/LaunchAgents/$l.plist" 2>/dev/null || true
  done
  launchctl list | grep ru.90gradusov || true
  echo "Задачи снова работают на Mac."
  exit 0
fi

HOST="${1:-}"
[ -n "$HOST" ] || { echo "Укажите сервер: deploy/push_to_server.sh gradus@<адрес> [--switch]"; exit 1; }
MODE="${2:-}"

send() { # файлы/папки относительно корня проекта, если есть
  local items=()
  for p in "$@"; do [ -e "$p" ] && items+=("$p"); done
  [ ${#items[@]} -eq 0 ] || rsync -aR "${items[@]}" "$HOST:$APP/"
  ssh "$HOST" "chmod 600 $APP/.env 2>/dev/null; chmod -R go= $APP/data"   # openrsync на Mac не умеет --chmod
}

echo "== Секреты и шаблоны"
send .env data/kp/google_oauth_client.json data/kp/google_token.json data/kp/google_drive.json \
     data/kp/template/ data/client_agent/ignore_tg.txt

if [ "$MODE" = "--switch" ]; then
  echo "== Останавливаю задачи на Mac (и выключаю автозапуск при входе)"
  for l in $LABELS; do
    launchctl bootout "$DOMAIN/$l" 2>/dev/null || true
    launchctl disable "$DOMAIN/$l"
  done
  sleep 5   # дать агенту доработать текущий проход
  if launchctl list | grep -E "$(echo $LABELS | tr ' ' '|')"; then
    echo "Какая-то задача ещё работает на Mac — состояние не переношу"; exit 1
  fi
  echo "== Состояние задач (что уже обработано — чтобы не было дублей лидов и повторных сообщений)"
  send data/leads/inbound_state.json data/client_agent/state.json data/client_agent/external_sent.json data/kp/bot/state.json data/reports/bot_state.json data/reports/digest_config.json
  echo
  echo "Mac остановлен. Теперь на сервере от root:  bash $APP/deploy/install_services.sh --start"
  echo "Если что-то не так — на сервере  install_services.sh --stop, на Mac  deploy/push_to_server.sh --back"
else
  echo "Готово. Mac продолжает работать. Проверка на сервере:  bash $APP/deploy/check_server.sh"
fi
