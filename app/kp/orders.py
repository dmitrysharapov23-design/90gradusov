"""Заказы бота @Kp90gradusovbot: данные копятся по заказу, КП и договор собираются из них.

Заказ — это то, что Екатерина ведёт по одному клиенту: задание и позиции (КП), картинки, реквизиты
и условия (договор), готовые документы. Всё присланное, пока заказ открыт, попадает в него;
Claude только определяет, что это за файл и о чём текст (route), а данные разбирают parse / parse_contract.
Механика — docs/14_КП_правила.md, раздел «Бот».
"""
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Literal

import anthropic
from PIL import Image
from pydantic import BaseModel

import parse

MODEL = "claude-opus-5-5"
ORDER_IDLE_H = 12   # заказ без сообщений дольше — следующее сообщение без #номера открывает новый заказ

ROUTER = """Ты помогаешь менеджеру «90 градусов» (мебель и конструкции из металла) вести заказ в Telegram-боте.
Бот по заказу собирает КП (коммерческое предложение) и договор. Тебе дают: что уже есть в заказе, новый текст менеджера и
список новых файлов (имя и превью). Определи, что это, — ничего не выдумывай.

files — по одному на каждый новый файл (index как в списке):
- kp — готовое коммерческое предложение (презентация/PDF с позициями и ценами);
- task — задание/ТЗ/расчёт с позициями и ценами (не оформленное КП);
- requisites — карточка предприятия, реквизиты, паспорт, данные заказчика;
- drawing — чертёж, визуализация, фото изделия или объекта, референс;
- contract — договор (прошлый или подписанный);
- invoice — счёт;
- other — что-то иное.
note — 3-8 слов, что в файле («КП, 3 позиции на 124 000», «карточка ООО «Ромашка»», «чертёж стеллажа»).

Флаги по тексту и файлам вместе:
- kp_update — есть данные для КП или его правка: позиции, цены, количество, размеры, материалы, срок, доставка/монтаж для КП, картинки к позициям;
- contract_update — есть данные для договора: реквизиты заказчика, подписант, порядок оплаты, доставка/монтаж и адрес, срок чертежей, правка договора, готовое КП как основа договора;
- build_kp — просят собрать/обновить КП; build_contract — просят собрать/обновить договор («договор», «сделай договор», «собери»);
- invoice — просят выставить счёт;
- deal_id — номер сделки после «#», если есть; client_hint — имя клиента/компании/проекта, если видно (иначе пусто).
reply — коротко ответь только если это вопрос к боту или непонятно, что делать (иначе пусто)."""


class FileKind(BaseModel):
    index: int
    kind: Literal["kp", "task", "requisites", "drawing", "contract", "invoice", "other"]
    note: str


class Route(BaseModel):
    files: List[FileKind]
    kp_update: bool
    contract_update: bool
    build_kp: bool
    build_contract: bool
    invoice: bool
    deal_id: str
    client_hint: str
    reply: str


def _thumb(path: Path, side=900):
    im = Image.open(path).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    import base64
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def pdf_text(path: Path, limit=6000):
    import fitz
    return "\n".join(p.get_text() for p in fitz.open(path))[:limit]


def preview(path: Path, i: int, page1=None, words=""):
    """Превью файла для классификации: картинка, первая страница PDF + текст, текст Word."""
    head = {"type": "text", "text": f"Файл {i}: «{path.name}»"}
    suf = path.suffix.lower()
    if suf in (".jpg", ".jpeg", ".png"):
        return [head, _thumb(path)]
    if suf == ".pdf":
        out = [head]
        if page1:
            out.append(_thumb(page1))
        out.append({"type": "text", "text": "Текст PDF (начало):\n" + pdf_text(path, 1500)})
        return out
    return [head, {"type": "text", "text": "Текст Word (начало):\n" + words[:1500]}]


def route(client: anthropic.Anthropic, order_text: str, text: str, files: list) -> Route:
    """files: [(Path, превью-блоки)]."""
    content = [{"type": "text", "text": f"Что уже есть в заказе:\n{order_text}\n\nНовый текст менеджера:\n{text or '(без текста)'}"}]
    for _, blocks in files:
        content += blocks
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=4000,
        system=[{"type": "text", "text": ROUTER, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": anthropic.transform_schema(Route.model_json_schema())}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    out = "".join(b.text for b in resp.content if b.type == "text")
    if resp.stop_reason != "end_turn" or not out:
        raise RuntimeError(f"Claude не разобрал сообщение: stop_reason={resp.stop_reason}")
    return Route.model_validate_json(out)


# --- заказ ---

def new_order(chat, deal_id="", client=""):
    now = datetime.now().isoformat(timespec="seconds")
    return {"id": deal_id or "z" + uuid.uuid4().hex[:6], "chat": chat, "deal_id": deal_id, "client": client,
            "created": now, "updated": now, "kp": None, "kp_images": [], "kp_updated": "",
            "contract": None, "contract_docs": [], "contract_updated": "", "questions": [],
            "docs": {}, "files": []}


def title(o):
    who = o.get("client") or "клиент не указан"
    return f"Заказ #{o['deal_id']} · {who}" if o.get("deal_id") else f"Заказ без сделки · {who}"


def kp_items_total(kp):
    return sum(sum(p["price"] for p in it.get("parts", [])) if it.get("parts") else (it.get("price") or 0)
               for it in kp.get("items", []))


def contract_stale(o):
    return bool(o.get("contract") and o.get("kp") and o.get("kp_updated", "") > o.get("contract_updated", ""))


def order_text(o):
    """Коротко, что есть в заказе — для Claude (route) и людей."""
    lines = [title(o)]
    kp = o.get("kp")
    if kp and kp.get("items"):
        lines.append("КП: " + "; ".join(f"{it['name']} - {it.get('price') or ''}" for it in kp["items"][:10])
                     + f"; срок {kp.get('production_term') or '?'}")
    c = o.get("contract")
    if c and c.get("customer", {}).get("full_name"):
        lines.append(f"Заказчик: {c['customer']['full_name']}")
    if o.get("files"):
        lines.append("Файлы: " + "; ".join(f["note"] for f in o["files"][-8:]))
    return "\n".join(lines)


def card(o, contract_missing, contract_card_ready):
    """Карточка заказа -> (текст, кнопки). contract_missing/ready — из bot (по модели Contract)."""
    lines = [f"📁 {title(o)}", ""]
    kp, doc = o.get("kp"), o.get("docs", {})
    if doc.get("kp"):
        d = doc["kp"]
        stale = o.get("kp_updated", "") > d["at"]
        lines.append(f"{'⚠️' if stale else '✅'} КП: v{d['version']} от {d['at'][8:10]}.{d['at'][5:7]}, "
                     f"{parse_money(d['total'])}" + (" — данные изменились, обновите" if stale else ""))
    elif kp and kp.get("items"):
        miss = parse.missing(parse.KP.model_validate(kp))
        lines.append("❗ КП: не хватает — " + "; ".join(miss) if miss else "▫️ КП: данные есть, можно собрать")
    else:
        lines.append("▫️ КП: пришлите задание — позиции с ценами и срок")
    c = o.get("contract")
    cust = (c or {}).get("customer", {})
    if cust.get("full_name"):
        ids = cust.get("inn") and f", ИНН {cust['inn']}" or ""
        lines.append(f"✅ Заказчик: {cust.get('short_name') or cust['full_name']}{ids}")
    else:
        lines.append("▫️ Реквизиты заказчика: пришлите карточку (PDF, Word, фото) или текстом")
    miss = contract_missing(c) if c else ["реквизиты и данные заказа"]
    if doc.get("contract"):
        d = doc["contract"]
        stale = contract_stale(o) or o.get("contract_updated", "") > d["at"]
        lines.append(f"{'⚠️' if stale else '✅'} Договор № {d['number']}" + (" — данные изменились, обновите" if stale else ""))
    elif c and not miss:
        lines.append("▫️ Договор: можно собрать")
    elif c:
        lines.append("❗ Договор: не хватает — " + "; ".join(miss))
    else:
        lines.append("▫️ Договор: нужны КП и реквизиты")
    lines.append("▫️ Счёт: PDF из 1С сюда — поставлю подпись и печать")
    if o.get("questions"):
        lines += ["", "Проверьте:"] + [f"– {q}" for q in o["questions"][:3]]
    buttons = []
    if kp and kp.get("items") and not parse.missing(parse.KP.model_validate(kp)):
        buttons.append(("Обновить КП" if doc.get("kp") else "Собрать КП", f"kp:{o['id']}"))
    if c and not miss:
        buttons.append(("Обновить договор" if doc.get("contract") else "Собрать договор", f"contract:{o['id']}"))
    buttons += [("Заказы", "orders:"), ("Новый заказ", "new:")]
    return "\n".join(lines).replace(" ", " "), buttons


def parse_money(n):
    return f"{n:,}".replace(",", " ") + " руб"
