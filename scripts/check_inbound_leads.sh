#!/bin/bash
# Сторож скрипта входящих заявок (scripts/inbound_leads.py): в рабочее время (пн–пт 10–19 МСК)
# предупреждает владельца в Telegram, если data/leads/inbound_heartbeat старше 25 минут (или его нет)
# либо в data/leads/inbound.log появились новые строки «ОШИБКА». Не чаще раза в час.
# По образцу scripts/check_client_agent.sh. Запуск — launchd каждые 5 минут. WATCHDOG_DRY_RUN=1 — вывести, не слать.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$ROOT/data/leads"
HB="$DIR/inbound_heartbeat"
LOG="$DIR/inbound.log"
STAMP="$DIR/watchdog_alert"
ERRSEEN="$DIR/watchdog_errors_seen"   # сколько строк ОШИБКА уже сообщили
MAX_AGE_MIN=25

[ "$(TZ=Europe/Moscow date +%u)" -le 5 ] || exit 0
HOUR="$(TZ=Europe/Moscow date +%H)"
[ "$HOUR" -ge 10 ] && [ "$HOUR" -lt 19 ] || exit 0
# первые полчаса рабочего дня не проверяем отметку: скрипт ещё мог не пройти ни разу
MIN_OF_DAY=$(( 10#$HOUR * 60 + 10#$(TZ=Europe/Moscow date +%M) ))

PROBLEMS=""
if [ "$MIN_OF_DAY" -ge 630 ]; then
  if [ -f "$HB" ]; then
    AGE_MIN="$(/usr/bin/python3 -c 'import sys,datetime as d
t=d.datetime.fromisoformat(open(sys.argv[1]).read().strip())
print(int((d.datetime.now(d.timezone.utc)-t).total_seconds()//60))' "$HB")"
    [ "$AGE_MIN" -gt "$MAX_AGE_MIN" ] && PROBLEMS="не работал $AGE_MIN мин (последний проход: $(cut -c1-16 "$HB"))"
  else
    PROBLEMS="ни разу не отметился (нет файла heartbeat)"
  fi
fi

ERRS=$( [ -f "$LOG" ] && grep -c "ОШИБКА" "$LOG" || true ); ERRS=${ERRS:-0}
SEEN=$( [ -f "$ERRSEEN" ] && cat "$ERRSEEN" || echo 0 )
if [ "$ERRS" -gt "$SEEN" ]; then
  LAST=$(grep "ОШИБКА" "$LOG" | tail -1 | cut -c1-200)
  PROBLEMS="${PROBLEMS:+$PROBLEMS; }новых ошибок: $((ERRS - SEEN)), последняя: $LAST"
fi
[ -n "$PROBLEMS" ] || exit 0

if [ -f "$STAMP" ] && find "$STAMP" -mmin -60 | grep -q .; then
  exit 0
fi

MSG="$DIR/watchdog_msg.txt"
cat > "$MSG" <<TXT
⚠️ Автозаявки (Instagram/сайт → лиды): $PROBLEMS.
Проверьте: Mac включён и не спит; журнал — data/leads/inbound.log и inbound_launchd.log.
Перезапуск: launchctl kickstart -k gui/\$(id -u)/ru.90gradusov.inbound-leads
TXT

if [ "${WATCHDOG_DRY_RUN:-0}" = "1" ]; then
  cat "$MSG"
else
  "$ROOT/scripts/tg_send.sh" "$MSG"
  touch "$STAMP"
  echo "$ERRS" > "$ERRSEEN"
fi
