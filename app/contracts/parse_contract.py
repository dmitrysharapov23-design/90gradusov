"""Данные договора из сообщения Екатерины (текст + фото/PDF карточки реквизитов) и, если есть, из КП -> JSON для make_contract.

Один вызов Claude API со структурированным ответом. Суммы и номер договора считает make_contract, не Claude.
"""
import base64
import json
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kp"))
from parse import image_block  # noqa: E402

MODEL = "claude-opus-5-5"

SYSTEM = """Ты готовишь данные для договора на изготовление изделий ООО «90 Градусов» (мебель и конструкции из металла). Документ собирает программа по утверждённой рыбе; ты только раскладываешь данные текущего заказа по полям.

Источники — только: сообщение Екатерины, приложенные файлы (PDF и картинки: КП, карточка предприятия, паспорт, прошлый договор, Word-файлы — текстом в сообщении) и данные КП, если они даны.
- Если приложено КП (PDF) — позиции, количество, цены, характеристики, доставку и срок бери из него.
- Если приложен прошлый договор, а Екатерина просит другие реквизиты — бери реквизиты только из карточки/сообщения, из прошлого договора — только заказ. Ничего не выдумывай и не ищи в интернете. Чего нет — оставь пустую строку (числа — 0) и добавь вопрос в questions.

Заказчик:
- type: «ООО» (любое юрлицо), «ИП» или «физлицо».
- ООО: full_name — полное наименование («Общество с ограниченной ответственностью «Ромашка»»), short_name — «ООО «Ромашка»». representative — подписант: position («Генеральный директор»), name (ФИО полностью, именительный падеж), position_genitive и name_genitive — те же слова в родительном падеже для фразы «в лице Генерального директора Иванова Петра Сергеевича», basis_genitive — «Устава» или «Доверенности № … от …», gender — «м»/«ж». name_parts — фамилия, имя, отчество подписанта по отдельности, ровно как написаны.
- ИП: full_name — только ФИО без «ИП». name_parts — ФИО по отдельности. ОГРНИП, ИНН.
- физлицо: full_name — ФИО полностью; gender; name_parts; паспорт (серия, номер, кем выдан, дата выдачи, код подразделения) и адрес регистрации — только если даны.
- Реквизиты (ИНН, КПП, ОГРН, адрес, банк, р/с, к/с, БИК, телефон, почта) переписывай посимвольно. Не путай реквизиты разных лиц.
- Нет отчества — middle пусто и вопрос «как формировать номер договора без отчества».

Позиции (items): только изделия, без доставки и монтажа.
- Название — дословно (из КП, если дано, или из сообщения), с большой буквы. quantity — число, unit — «шт.», «компл.» и т. п.
- Цена: unit_price — за единицу, total — за всю позицию; заполни то, что дано. В КП price — цена за всю позицию (total).

tech — одна общая таблица характеристик на все изделия, компактно, через точку с запятой, без повторов:
- materials — материалы и толщины («Нержавеющая сталь толщиной 0,8 мм и 2 мм; профильная труба 40×40 мм; ЛДСП»);
- dimensions — размеры по изделиям («Портал - 4051 × 6540 мм; полки - 3000 × 200 мм») или «согласно проектной документации»;
- finish — финиш и покрытие; construction — конструктив по изделиям; exclusions — что не входит в стоимость (если сказано);
- approval — только если Екатерина дала свою формулировку согласования.
Только то, что есть в КП и сообщении. Не придумывай марку стали, радиусы, крепёж, фурнитуру, нагрузку.

services — доставка и монтаж: included — входят ли в стоимость (по умолчанию false); installation — есть ли монтаж (false — только доставка); label — «Доставка и монтаж» или «Доставка»; price — цена (из КП: delivery_combined_price или сумма доставки и монтажа); scope — состав работ, если дан («монтаж портала - 1 шт.; …»); address — адрес объекта; term — только если назван срок.

act_title — короткое название комплекта для акта: «Комплект изделий для винного бара», «Комплект изделий для Bel Corpo» (по проекту/объекту из КП или сообщения); не знаешь — «Комплект изделий».

Оплата: payment — пусто, если ничего не сказано (будет 70/30 по умолчанию). Если дан иной порядок — этапы: percent и text — продолжение фразы «N% от общей стоимости Заказа …», например «оплачивается Заказчиком при подписании Договора», «оплачиваются Заказчиком после завершения монтажа и подписания акта приема-передачи». Сумма процентов — 100.

Сроки: production_days — рабочих дней производства (из КП или сообщения); drawings_days — срок конструкторских чертежей строкой («6-8», «7-10»), если назван.

Прочее: date — дата договора ГГГГ-ММ-ДД, только если названа (иначе пусто — будет сегодня); warranty_months — если названа не 12; contact_person, contact_phone, contact_email, special_conditions — только если даны. Для физлица birth_date — дата рождения, если дана. deal_id — номер сделки после «#».

questions — не больше 3 самых важных вопросов, без которых договор будет неверным (нет реквизитов, подписанта, цен, срока; явное противоречие в суммах). Не спрашивай номер сделки, дату договора, адрес объекта, если доставка не входит, и основание полномочий директора ООО (по умолчанию «Устава» — ставь без вопроса). При правке: вопрос, на который уже ответили, не повторяй. Пусто, если всё ясно."""


class NameParts(BaseModel):
    last: str = ""
    first: str = ""
    middle: str = ""


class Representative(BaseModel):
    """Только для ООО; для ИП и физлица — пустые строки."""
    position: str = ""
    name: str = ""
    position_genitive: str = ""
    name_genitive: str = ""
    gender: Literal["м", "ж", ""] = ""
    basis_genitive: str = ""
    name_parts: NameParts = NameParts()


class Passport(BaseModel):
    series: str = ""
    number: str = ""
    issued_by: str = ""
    issue_date: str = ""
    department_code: str = ""


class Bank(BaseModel):
    account: str = ""
    name: str = ""
    correspondent_account: str = ""
    bik: str = ""


class Customer(BaseModel):
    type: Literal["ООО", "ИП", "физлицо"]
    full_name: str = ""
    short_name: str = ""
    inn: str = ""
    kpp: str = ""
    ogrn: str = ""
    ogrnip: str = ""
    address: str = ""
    passport: Passport = Passport()
    bank: Bank = Bank()
    representative: Representative = Representative()
    name_parts: NameParts = NameParts()
    gender: Literal["м", "ж", ""] = ""
    birth_date: str = ""
    phone: str = ""
    email: str = ""


class Item(BaseModel):
    name: str
    quantity: int
    unit: str = ""
    unit_price: int = 0
    total: int = 0


class Tech(BaseModel):
    materials: str = ""
    dimensions: str = ""
    finish: str = ""
    construction: str = ""
    exclusions: str = ""
    approval: str = ""


class Services(BaseModel):
    included: bool = False
    installation: bool = True
    label: str = ""
    price: int = 0
    scope: str = ""
    address: str = ""
    term: str = ""


class PayStage(BaseModel):
    percent: int
    text: str


class Schedule(BaseModel):
    production_days: int = 0
    drawings_days: str = ""


class Contract(BaseModel):
    deal_id: str = ""
    date: str = ""
    customer: Customer
    items: List[Item]
    tech: Tech = Tech()
    services: Services = Services()
    payment: List[PayStage] = []
    schedule: Schedule = Schedule()
    act_title: str = ""
    warranty_months: int = 0
    contact_person: str = ""
    contact_phone: str = ""
    contact_email: str = ""
    special_conditions: str = ""
    questions: List[str] = []


SCHEMA_TEXT = json.dumps(Contract.model_json_schema(), ensure_ascii=False)


def _json(text):
    """JSON-объект из ответа (без обрамления ```json)."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("в ответе нет JSON")
    return text[a:b + 1]


def pdf_block(path: Path) -> dict:
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                           "data": base64.standard_b64encode(path.read_bytes()).decode()},
            "title": path.name}


def parse(client: anthropic.Anthropic, text: str, images: List[Path], kp: Optional[dict] = None,
          previous: Optional[Contract] = None, docs: Optional[List[Path]] = None) -> Contract:
    """Строгий структурированный ответ для этой схемы не проходит по размеру грамматики (ошибка 400),
    поэтому JSON — текстом по схеме из system, с проверкой pydantic и одним повтором при ошибке."""
    content = [pdf_block(d) for d in docs or []]  # PDF целиком: КП, карточка предприятия, прошлый договор
    for i, p in enumerate(images):
        content += [{"type": "text", "text": f"Картинка {i}:"}, image_block(p)]
    if kp:
        content.append({"type": "text", "text": "Данные КП по этому заказу (JSON; price — цена за всю позицию):\n"
                        + json.dumps(kp, ensure_ascii=False, indent=1)})
    if previous is None:
        content.append({"type": "text", "text": f"Сообщение Екатерины:\n{text}"})
    else:
        content.append({"type": "text", "text": "Прошлый вариант договора (JSON):\n" + previous.model_dump_json(indent=1)
                        + f"\n\nИсправление Екатерины — примени его, остальное оставь как было:\n{text}"})
    system = [{"type": "text", "cache_control": {"type": "ephemeral"},
               "text": SYSTEM + "\n\nОтвет — только один JSON-объект по этой JSON Schema, без пояснений. "
                                "Пустые поля — пустая строка, числа — 0.\n" + SCHEMA_TEXT}]
    messages = [{"role": "user", "content": content}]
    for attempt in range(2):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system, messages=messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        out = "".join(b.text for b in resp.content if b.type == "text")
        if resp.stop_reason != "end_turn" or not out:
            raise RuntimeError(f"Claude не разобрал данные договора: stop_reason={resp.stop_reason}")
        try:
            return Contract.model_validate_json(_json(out))
        except ValueError as e:
            if attempt:
                raise RuntimeError(f"ответ Claude не по схеме: {str(e)[:300]}")
            messages += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": f"JSON не прошёл проверку схемы: {str(e)[:1500]}\nПришли исправленный JSON целиком."}]



def _prune(x):
    """Убрать пустые строки, нули и пустые объекты — make_contract считает их отсутствующими."""
    if isinstance(x, dict):
        out = {k: _prune(v) for k, v in x.items()}
        return {k: v for k, v in out.items() if v not in ("", 0, None, {}, [])}
    if isinstance(x, list):
        return [_prune(v) for v in x]
    return x


def to_make_contract(c: Contract) -> dict:
    """Contract -> вход make_contract.build (оплата по умолчанию 70/30, представитель — только у ООО)."""
    d = _prune(c.model_dump(exclude={"questions", "deal_id", "services"}))
    cust = d["customer"]
    if cust["type"] != "ООО":
        cust.pop("representative", None)
    sv = c.services
    d["services"] = {"included": sv.included, "installation": sv.installation,
                     **{k: v for k, v in sv.model_dump().items() if k not in ("included", "installation") and v}}
    if not sv.included:
        d["services"].pop("price", None)
    d.setdefault("items", [])
    d.setdefault("schedule", {})
    return d
