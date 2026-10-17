#!/bin/bash
# Сторож агента клиентов: если в рабочее время (пн–пт 10–19 МСК) data/client_agent/heartbeat
# старше 15 минут (или его нет) — шлёт владельцу предупреждение в Telegram, не чаще раза в час.
# Запускается launchd каждые 5 минут (см. scripts/launchd/). Claude не нужен.
# Для проверки: CLIENT_AGENT_DIR=<папка> — другая папка с heartbeat; WATCHDOG_DRY_RUN=1 — не слать, а вывести текст.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${CLIENT_AGENT_DIR:-$ROOT/data/client_agent}"
HB="$DIR/heartbeat"
STAMP="$DIR/watchdog_alert"   # время последнего предупреждения (по mtime)
MAX_AGE_MIN=15

[ "$(TZ=Europe/Moscow date +%u)" -le 5 ] || exit 0
HOUR="$(TZ=Europe/Moscow date +%H)"
[ "$HOUR" -ge 10 ] && [ "$HOUR" -lt 19 ] || exit 0

if [ -f "$HB" ]; then
  # heartbeat — ISO со смещением, например 2026-10-16T17:30:26.771745+03:00
  AGE_MIN="$("$ROOT/.venv/bin/python" -c 'import sys,datetime as d
t=d.datetime.fromisoformat(open(sys.argv[1]).read().strip())
print(int((d.datetime.now(d.timezone.utc)-t).total_seconds()//60))' "$HB")"
  WHAT="не работал $AGE_MIN мин (последний проход: $(cut -c1-19 "$HB"))"
else
  AGE_MIN=999999
  WHAT="ни разу не отметился (нет файла heartbeat)"
fi
[ "$AGE_MIN" -gt "$MAX_AGE_MIN" ] || exit 0

# не чаще раза в час
if [ -f "$STAMP" ] && find "$STAMP" -mmin -60 | grep -q .; then
  exit 0
fi

if command -v systemctl >/dev/null; then   # сервер (deploy/)
  WHERE="сервер $(hostname)"; RESTART="sudo systemctl start 90g-client-agent.service; статус — systemctl status 90g-client-agent.timer"
else
  WHERE="Mac включён и не спит"; RESTART="launchctl kickstart -k gui/\$(id -u)/ru.90gradusov.client-agent"
fi
MSG="$DIR/watchdog_msg.txt"
cat > "$MSG" <<TXT
⚠️ Агент клиентов $WHAT.
Проверьте: $WHERE; лог — data/client_agent/launchd.log и agent.log.
Перезапуск: $RESTART
TXT

if [ "${WATCHDOG_DRY_RUN:-0}" = "1" ]; then
  cat "$MSG"
else
  "$ROOT/scripts/tg_send.sh" "$MSG"
fi
touch "$STAMP"
