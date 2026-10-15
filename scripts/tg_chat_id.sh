#!/bin/bash
# Узнать chat_id владельца: сначала напишите боту /start, потом запустите этот скрипт.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
set -a; . "$ROOT/.env"; set +a
curl -s "https://api.telegram.org/bot${TG_BOT_TOKEN}/getUpdates" | node -e '
let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{const r=JSON.parse(d);
if(!r.ok){console.log("Ошибка: проверьте TG_BOT_TOKEN");return}
const seen=new Set();for(const u of r.result){const c=(u.message||{}).chat;if(c&&!seen.has(c.id)){seen.add(c.id);console.log("chat_id="+c.id+"  @"+(c.username||"")+"  "+(c.first_name||""))}}
if(!seen.size)console.log("Сообщений нет: напишите боту /start и запустите снова")})'
