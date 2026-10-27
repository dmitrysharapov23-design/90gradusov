# Договоры

Правила — `docs/17_договоры_правила.md`. В боте — команда «договор» (`app/kp/bot.py`).

## Файлы
- `make_contract.py` — JSON заказа → DOCX по стандарту пакета: номер, суммы, оплата, спецификация, акт, правила эксплуатации.
- `parse_contract.py` — сообщение + фото/PDF реквизитов + КП → JSON (Claude `claude-opus-5-5`; JSON текстом с проверкой pydantic — строгий структурированный ответ для этой схемы не проходит по размеру грамматики).
- `contract_text.py` — юридический текст рыбы (выгружен из `data/contracts/package_2026-10-20/…/01_Рыба_договора.docx`).
- `example.json` — тестовый пример (данные вымышлены).

## Запуск без бота
```bash
.venv/bin/python app/contracts/make_contract.py app/contracts/example.json --out data/contracts/test
```

## Поля JSON
- `date` (ГГГГ-ММ-ДД; нет — сегодня).
- `customer`: `type` (ООО | ИП | физлицо), `full_name`, `short_name`, `inn`, `kpp`, `ogrn`, `ogrnip`, `address`, `bank{account,name,correspondent_account,bik}`, `phone`, `email`.
  - ООО: `representative{position, name, position_genitive, name_genitive, gender, basis_genitive, name_parts{last,first,middle}}`.
  - ИП и физлицо: `name_parts`; физлицо: `gender`, `birth_date`, `passport{series,number,issued_by,issue_date,department_code}`.
- `items[]`: `name`, `quantity`, `unit`, `unit_price` и/или `total` — только изделия.
- `tech`: `materials`, `dimensions`, `finish`, `construction`, `exclusions`, `approval` — одна общая таблица характеристик.
- `services`: `included`, `installation`, `label`, `price`, `scope`, `address`, `term` — доставка и монтаж.
- `payment[]`: `percent`, `text` — продолжение фразы «N% от общей стоимости Заказа …» (нет — 70/30).
- `schedule`: `production_days`, `drawings_days` («6-8»).
- `act_title`, `warranty_months`, `contact_person`, `contact_phone`, `contact_email`, `special_conditions`.

Результат — `data/contracts/out/` (не в git).
