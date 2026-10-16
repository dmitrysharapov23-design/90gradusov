# КП из фирменного шаблона

Правила и эталон — `docs/14_КП_правила.md`.

## Файлы
- `make_kp.py` — JSON проекта → PPTX в шаблоне «90 градусов»; считает итоги, проверяет запреты.
- `bot.py` — Telegram-бот @Kp90gradusovbot: задание Екатерины → КП (PDF + PPTX), правки ответом на сообщение. Работает постоянно (launchd `ru.90gradusov.kp-bot`).
- `gdrive.py` — загрузка КП на Google Drive как Google Презентации, ссылка и PDF; вход и доступ (`login`, `share`).
- `parse.py` — разбор задания через Claude (`claude-opus-5-5`, структурированный ответ, запасная модель при отказе — `fallbacks: "default"`).
- `example.json` — тестовый пример полей (суммы вымышлены).
- Не в git: шаблон `data/kp/template/master.pptx`, фото итогового слайда `data/kp/template/total_photo.jpeg`; результат — `data/kp/out/`.

## Установка
```bash
.venv/bin/pip install -r app/kp/requirements.txt
```

## Запуск
```bash
.venv/bin/python app/kp/make_kp.py data/kp/in/<проект>.json
```

## Поля JSON
- `project`: `deal_id`, `client`, `name`, `kp_date` (ГГГГ-ММ-ДД; если не указана — сегодня).
- `items[]`:
  - обязательные: `name`, `quantity`, `price` (целые рубли за всю позицию) или `parts` (`[{label, price}]` — части, ИТОГО посчитает код);
  - необязательные: `unit` (по умолчанию «шт.»), `dimensions`, `material`, `finish`, подписи `dimensions_label` / `material_label` / `finish_label`, `features[]`, `images[]` (пути к файлам проекта).
- `logistics` (если есть в ТЗ): `combined_price` + `label` (по умолчанию «Доставка с разгрузкой и монтажом») или `delivery_price` / `installation_price`.
- `totals.notes[]` — свои примечания на «Итоговых условиях» вместо стандартных.
- `conditions.production_term` (обязательно) — срок в рабочих днях.

## Проверка вида
PowerPoint сохраняет PDF только в папку, к которой у него есть доступ, например в проект:
```bash
osascript -e 'tell application "Microsoft PowerPoint"' -e 'open POSIX file "/полный/путь/КП.pptx"' -e 'delay 4' -e 'save active presentation in POSIX file "/полный/путь/КП.pdf" as save as PDF' -e 'close active presentation saving no' -e 'end tell'
```

## Бот
Ключи в `.env` (кладёт владелец, в чат не присылать): `KP_TG_BOT_TOKEN`, `KP_TG_ALLOWED_IDS` (числовые ID или ники через запятую), `ANTHROPIC_API_KEY`; необязательно `B24_AGENT_WEBHOOK` (название сделки), `KP_RENDER_PDF=1` (PDF через PowerPoint, если нет Drive). Google: `data/kp/google_oauth_client.json` + `.venv/bin/python app/kp/gdrive.py login`.

```bash
launchctl kickstart -k gui/$(id -u)/ru.90gradusov.kp-bot
```
Перезапуск после правки кода. Журнал — `data/kp/bot/bot.log`.

Проверка разбора без Telegram (один вызов Claude):
```bash
.venv/bin/python app/kp/parse.py "текст задания" data/kp/test/wide.png
```
