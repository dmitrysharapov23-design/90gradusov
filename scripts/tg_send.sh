#!/bin/bash
# Отправка сводки владельцу в Telegram.
# Использование: scripts/tg_send.sh <файл_с_текстом> [файл_вложение]
# Токен и chat_id берутся из .env: TG_BOT_TOKEN, TG_CHAT_ID. В репозиторий не попадают.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; . "$ROOT/.env"; set +a
API="${API:-https://api.telegram.org/bot${TG_BOT_TOKEN}}"
API="https://api.telegram.org/bot${TG_BOT_TOKEN}"
# Telegram режет сообщения длиннее 4096 символов — отправляем первые 4000
TEXT="$(node -e 'process.stdout.write(require("fs").readFileSync(process.argv[1],"utf8").slice(0,4000))' "$1")"
curl -sf -X POST "$API/sendMessage" --data-urlencode "chat_id=${TG_CHAT_ID}" --data-urlencode "text=${TEXT}" -o /dev/null
if [ "${2:-}" != "" ]; then
  curl -sf -X POST "$API/sendDocument" -F "chat_id=${TG_CHAT_ID}" -F "document=@$2" -o /dev/null
fi
echo "отправлено"
