#!/bin/bash
# Сторож сводки: если сегодня (пн–пт) не появился data/chats/tg_*.txt — шлёт владельцу предупреждение.
# Запускается launchd в 10:30 (см. scripts/launchd/). Claude не нужен.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(date +%u)" -le 5 ] || exit 0
TODAY="$(date +%Y-%m-%d)"
if find "$ROOT/data/chats" -name 'tg_*.txt' -newermt "$TODAY 00:00" | grep -q .; then
  exit 0
fi
MSG="$ROOT/data/chats/alert_$TODAY.txt"
cat > "$MSG" <<TXT
⚠️ Сводка по чатам за $TODAY не сформирована к $(date +%H:%M).
Проверьте: Mac включён и не спит, приложение Claude открыто.
Запустить вручную: Claude → Scheduled → «Сводка по чатам менеджера» → Run now.
TXT
"$ROOT/scripts/tg_send.sh" "$MSG"
