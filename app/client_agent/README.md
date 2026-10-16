# Агент по работе с клиентами

Механика и решения — `docs/13_агент_клиентов.md`. Сейчас только режим «тень»: агент читает Б24 и пишет, что ответил бы клиенту. Клиентам ничего не отправляет, в Б24 ничего не пишет.

## Файлы
- `agent.py` — один проход: новые лиды Wazzup → переписка → решение → черновик.
- `chat.py` — разбор сообщений Wazzup из таймлайна лида, рабочее время (пн–пт 10–19 МСК).
- `llm.py` — инструкция ассистенту и вызов Claude (`claude-opus-5-5`, структурированный ответ; при отказе модели запрос автоматически переходит на запасную модель — `fallbacks: "default"`).
- `b24.py` — чтение Б24 через входящий вебхук.
- `test_bridge.py` — тесты стыка со скриптом заявок на подделанных данных: `.venv/bin/python -m unittest app/client_agent/test_bridge.py -v`.

## Ключи в `.env` (кладёт владелец, в чат не присылать)
- `B24_AGENT_WEBHOOK` — адрес входящего вебхука Б24 (Разработчикам → Другое → Входящий вебхук; права: CRM; для будущей передачи Екатерине — ещё «Задачи»).
- `ANTHROPIC_API_KEY` — console.anthropic.com → API keys.
- `TG_BOT_TOKEN`, `TG_CHAT_ID` — уже есть (сводка); туда приходят черновики.
- Необязательно: `CLIENT_AGENT_TG_NOTIFY=0` — не слать черновики в Telegram, только в файл.

## Локальные данные (`data/client_agent/`, не в git)
- `shadow_<дата>.md` — черновики: переписка, что ответил бы ассистент, ТЗ, чего не хватает.
- `agent.log` — что агент решил по каждому лиду; `state.json` — состояние диалогов; `heartbeat` — время последнего прохода.
- `external_sent.json` — реестр приветствий, отправленных скриптом заявок (`scripts/inbound_leads.py --send`): агент считает их своими и сразу ведёт такие лиды (docs/12, «Стык с агентом клиентов»).
- `ignore_tg.txt` — свои аккаунты (бот, сотрудники, партнёры): Telegram ID или @ник, по одному в строке, после `#` — комментарий. Действует на лиды, которые агент ещё не начал обрабатывать.

## Запуск
Проверка без ключей (тестовые данные, без Claude):
```bash
.venv/bin/python app/client_agent/agent.py --offline data/client_agent/test/fixture.json --fake-llm --now 2026-10-16T16:31:00+03:00 --state data/client_agent/test/state.json
```

Один боевой проход (после ключей):
```bash
.venv/bin/python app/client_agent/agent.py
```

Включить каждые 2 минуты:
```bash
cp scripts/launchd/ru.90gradusov.client-agent.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/ru.90gradusov.client-agent.plist
```

Выключить:
```bash
launchctl unload ~/Library/LaunchAgents/ru.90gradusov.client-agent.plist
```

Сторож (`scripts/check_client_agent.sh`, каждые 5 минут): если в пн–пт 10–19 МСК `heartbeat` старше 15 минут — предупреждение в Telegram, не чаще раза в час. Проверка без отправки: `CLIENT_AGENT_DIR=<папка с heartbeat> WATCHDOG_DRY_RUN=1 bash scripts/check_client_agent.sh`. Включить:
```bash
cp scripts/launchd/ru.90gradusov.check-client-agent.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/ru.90gradusov.check-client-agent.plist
```

## Ещё не сделано
- Боевой режим: отправка через API Wazzup, комментарий «ТЗ» и задача Екатерине в Б24 — после проверки API Wazzup и разбора черновиков.
- Заявки из Instagram и с сайта: стык сделан, автоотправка приветствий выключена (решение владельца). НЕ ПРОВЕРЕНО, куда Wazzup кладёт ответ клиента — обработаны оба варианта.
