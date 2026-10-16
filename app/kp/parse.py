"""Задание Екатерины (свободный текст + картинки) -> поля КП для make_kp.build.

Один вызов Claude API со структурированным ответом. Ключ — ANTHROPIC_API_KEY в .env.
Цены и срок Claude только переносит из текста; если их нет — не придумывает, а задаёт вопрос.
"""
import base64
import io
import json
from pathlib import Path
from typing import List, Optional

import anthropic
from PIL import Image
from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"
IMG_MAX = 1568  # длинная сторона картинки для Claude, px

SYSTEM = """Ты готовишь данные для коммерческого предложения (КП) компании «90 градусов» (мебель и конструкции из металла). Менеджер Екатерина присылает задание свободным текстом и картинки проекта. Твоя задача — разложить задание по полям КП. Саму презентацию собирает программа.

Источник данных — только текущее сообщение Екатерины (и, при правке, прошлый вариант КП с её исправлением). Ничего не бери «из опыта» и не придумывай: ни размеров, ни материалов, ни цен, ни сроков.

Цены:
- Переноси суммы ровно как написаны, в целых рублях: «111 000», «111000», «111 тыс», «111к» -> 111000. Не пересчитывай, не округляй, не складывай за Екатерину.
- Цена позиции — за всю позицию (за всё количество). Если написано «4 шт по 1 000» — цена позиции 4000, а в questions добавь «Проверьте: 4 шт × 1 000 = 4 000 руб за позицию».
- Если у позиции несколько сумм (например, «стеновая панель 84 000, модуль 277 000») — это parts; price тогда null.
- Нет цены у позиции — price null, parts пустой, и вопрос в questions.
- Доставка и монтаж одной суммой — delivery_combined_price; раздельно — delivery_price и installation_price. Не упомянуты — null.
- Срок производства — число рабочих дней. Нет срока — null и вопрос.

Позиции:
- name — короткое название изделия с большой буквы, как в КП: «Стеллаж», «Рабочая зона №1», «Барная стойка».
- quantity — число; не указано — 1.
- dimensions — габарит как написан («1695×350×3000 мм», знак ×, «мм»); нет — null. dimensions_label — «Габарит» или «Габарит по фронту», если так в задании.
- material, finish — только если есть в задании; иначе null. С маленькой буквы, полными словами, как в КП: «нержавеющая сталь», «сатинированная обработка нержавеющей стали», «порошковая покраска RAL 9005». Марку стали (AISI) — только если она названа.
- features — технические характеристики: 2–6 коротких пунктов деловым языком («выполняется из…», «предусмотрена…»), только по тому, что есть в задании. Если в задании есть отдельные детали конструкции — оформи их пунктами. Последним пунктом добавляй «Финальный конструктив согласовывается перед запуском в производство.», если позиция изготавливается по проекту.
- Никогда не пиши в КП себестоимость, наценку, коэффициенты, подрядчиков, дизайнерский процент. Если они есть в задании — не переноси, а цену бери итоговую.
- Только короткий дефис «-», без длинного тире.

Картинки: перед текстом пронумерованы как «Картинка 0», «Картинка 1»… image_indices — номера картинок, относящихся к позиции, в порядке показа (сначала чертёж, потом вид/визуализация). Одна картинка может относиться к нескольким позициям. Картинку, которая не относится ни к одной позиции, не используй.

deal_id — номер сделки после «#», если есть. project_name — название проекта/объекта, если есть.

questions — коротко, что Екатерине нужно уточнить или проверить (нет цены, нет срока, непонятно, к какой позиции картинка, сомнение в сумме). Не спрашивай то, что может решить производство (толщина, каркас). Пусто, если всё ясно."""


class Part(BaseModel):
    label: str
    price: int


class Item(BaseModel):
    name: str
    quantity: int = 1
    unit: Optional[str] = Field(None, description="единица, по умолчанию «шт.»")
    dimensions: Optional[str] = None
    dimensions_label: Optional[str] = None
    material: Optional[str] = None
    finish: Optional[str] = None
    finish_label: Optional[str] = None
    features: List[str] = []
    price: Optional[int] = None
    parts: List[Part] = []
    image_indices: List[int] = []


class KP(BaseModel):
    deal_id: Optional[str] = None
    project_name: Optional[str] = None
    items: List[Item]
    delivery_combined_price: Optional[int] = None
    delivery_label: Optional[str] = Field(None, description="подпись доставки одной суммой, если в задании своя")
    delivery_price: Optional[int] = None
    installation_price: Optional[int] = None
    production_term: Optional[int] = None
    questions: List[str] = []


def image_block(path: Path) -> dict:
    im = Image.open(path).convert("RGB")
    im.thumbnail((IMG_MAX, IMG_MAX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def parse(client: anthropic.Anthropic, text: str, images: List[Path], previous: Optional[KP] = None) -> KP:
    content = []
    for i, p in enumerate(images):
        content += [{"type": "text", "text": f"Картинка {i}:"}, image_block(p)]
    if previous is None:
        content.append({"type": "text", "text": f"Задание Екатерины:\n{text}"})
    else:
        content.append({"type": "text", "text":
                        "Прошлый вариант КП (JSON):\n" + previous.model_dump_json(indent=1)
                        + f"\n\nИсправление Екатерины — примени его, остальное оставь как было:\n{text}"})
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(KP.model_json_schema())},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    out = "".join(b.text for b in resp.content if b.type == "text")
    if resp.stop_reason != "end_turn" or not out:
        raise RuntimeError(f"Claude не разобрал задание: stop_reason={resp.stop_reason}")
    return KP.model_validate_json(out)


def to_make_kp(kp: KP, images: List[Path], client_name: Optional[str], kp_date: str) -> dict:
    """Поля parse -> вход make_kp.build. Позиции без цены сюда не попадают (сначала вопрос)."""
    items = []
    for it in kp.items:
        d = {"name": it.name, "quantity": it.quantity, "features": it.features,
             "images": [str(images[i]) for i in it.image_indices if 0 <= i < len(images)]}
        for k in ("unit", "dimensions", "dimensions_label", "material", "finish", "finish_label"):
            if getattr(it, k):
                d[k] = getattr(it, k)
        if it.parts:
            d["parts"] = [p.model_dump() for p in it.parts]
        else:
            d["price"] = it.price
        items.append(d)
    lg = {}
    if kp.delivery_combined_price is not None:
        lg = {"combined_price": kp.delivery_combined_price}
        if kp.delivery_label:
            lg["label"] = kp.delivery_label
    else:
        for k in ("delivery_price", "installation_price"):
            if getattr(kp, k) is not None:
                lg[k] = getattr(kp, k)
    return {
        "project": {"deal_id": kp.deal_id, "client": client_name or kp.project_name, "name": kp.project_name, "kp_date": kp_date},
        "items": items,
        "logistics": lg,
        "conditions": {"production_term": kp.production_term},
    }


def missing(kp: KP) -> List[str]:
    """Чего не хватает для сборки — без этого КП не собираем."""
    out = []
    if not kp.items:
        out.append("нет ни одной позиции")
    for it in kp.items:
        if it.price is None and not it.parts:
            out.append(f"нет цены: {it.name}")
    if not kp.production_term:
        out.append("нет срока производства (рабочих дней)")
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client_agent"))
    from agent import load_env
    env = load_env()
    text = sys.argv[1]
    imgs = [Path(p) for p in sys.argv[2:]]
    kp = parse(anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"]), text, imgs)
    print(kp.model_dump_json(indent=1))
    print("не хватает:", missing(kp))
    print(json.dumps(to_make_kp(kp, imgs, None, "2026-10-16"), ensure_ascii=False, indent=1))
