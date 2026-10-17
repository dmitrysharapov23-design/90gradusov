#!/bin/bash
# Проверка сервера перед запуском служб. От gradus:  bash ~/90gradusov/deploy/check_server.sh
# Ничего не отправляет и не меняет; значения секретов не выводит — только есть ключ или нет.
set -uo pipefail
cd "$(dirname "$0")/.."
ok()   { echo "  ok    $1"; }
bad()  { echo "  НЕТ   $1"; FAIL=1; }
FAIL=0

echo "== Система"
[ "$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value)" = "Europe/Moscow" ] && ok "часовой пояс МСК" || bad "часовой пояс не МСК (timedatectl set-timezone Europe/Moscow)"
command -v node >/dev/null && ok "node (нужен scripts/tg_send.sh)" || bad "node"
command -v claude >/dev/null && ok "Claude Code $(claude --version 2>/dev/null | head -1)" || bad "Claude Code"

echo "== Python"
.venv/bin/python -c "import pptx, PIL, fitz, anthropic, pydantic, googleapiclient, google_auth_oauthlib" 2>/dev/null \
  && ok "зависимости ботов" || bad "зависимости (bash deploy/install_app.sh)"
.venv/bin/python -m py_compile app/kp/bot.py app/client_agent/agent.py scripts/inbound_leads.py && ok "код компилируется" || bad "ошибка в коде"

echo "== Файлы не из git"
[ -f .env ] && ok ".env" || bad ".env (deploy/push_to_server.sh на Mac)"
[ "$(stat -c %a .env 2>/dev/null)" = "600" ] && ok ".env закрыт (600)" || bad "права .env не 600"
for f in data/kp/template/master.pptx data/kp/google_token.json data/kp/google_oauth_client.json data/kp/google_drive.json; do
  [ -f "$f" ] && ok "$f" || bad "$f"
done

echo "== Ключи в .env (только наличие)"
for k in ANTHROPIC_API_KEY KP_TG_BOT_TOKEN KP_TG_ALLOWED_IDS B24_AGENT_WEBHOOK B24_LEADS_WEBHOOK WAZZUP_API_KEY WAZZUP_CHANNEL_ID TG_BOT_TOKEN TG_CHAT_ID; do
  grep -qE "^$k=.+" .env 2>/dev/null && ok "$k" || bad "$k"
done

echo "== Доступ к сервисам (без авторизации, только сеть)"
for u in https://api.anthropic.com https://api.telegram.org https://api.wazzup24.com https://www.googleapis.com https://github.com; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$u")
  [ "$code" != "000" ] && ok "$u (HTTP $code)" || bad "$u недоступен"
done
B24=$(grep -E '^B24_AGENT_WEBHOOK=' .env 2>/dev/null | cut -d= -f2- | sed -E 's#(https?://[^/]+).*#\1#')
if [ -n "$B24" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$B24")
  [ "$code" != "000" ] && ok "Битрикс24 (HTTP $code)" || bad "Битрикс24 недоступен с сервера"
fi
# Anthropic отвечает 403 на запросы из неподдерживаемых стран — проверяем ключом, но не печатаем его
KEY=$(grep -E '^ANTHROPIC_API_KEY=' .env 2>/dev/null | cut -d= -f2- | tr -d '"'"'")
if [ -n "$KEY" ]; then
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.anthropic.com/v1/models \
    -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01")
  [ "$code" = "200" ] && ok "Claude API с этого сервера работает" || bad "Claude API ответил HTTP $code (403 — страна сервера не поддерживается)"
fi

echo "== Задачи на Mac"
echo "  перед  install_services.sh --start  на Mac должен быть выполнен  deploy/push_to_server.sh <сервер> --switch"
echo
[ "$FAIL" = 0 ] && echo "Всё готово к запуску служб." || echo "Есть замечания (НЕТ) — исправить до запуска."
exit $FAIL
