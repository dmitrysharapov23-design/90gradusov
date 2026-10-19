"""Фильтр разбора лидов по правилу владельца (19.10.2026): пушим только простое и от 50 000 ₽.

Не пушим: чек меньше 50 000 ₽; обклейка/облицовка (металл на основу ЛДСП/МДФ/фанеры, облицовка стен,
накладки на фасады); сложное в реализации. Для лидов с действием «ответить / дожать / ждём» Claude
читает переписку и оценивает цену, обклейку и сложность. Результат: triage_<дата>_filtered.csv.

  .venv/bin/python scripts/lead_triage_filter.py data/leads/triage_2026-10-19.csv
"""
import csv
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "reports"))
sys.path.insert(0, str(ROOT / "scripts"))

import anthropic  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from data import MSK, env_b24, load_env, messages  # noqa: E402
from lead_triage import transcript  # noqa: E402

MIN_CHECK = 50000
CACHE = ROOT / "data" / "leads" / "filter_cache.json"


class Fit(BaseModel):
    price_rub: Optional[int] = Field(description="Сумма заказа в рублях: цена, которую назвала Екатерина (если вариантов несколько — основной/максимальный), иначе бюджет клиента, иначе сумма из названия лида; null если неизвестно")
    price_source: str = Field(description="откуда сумма: «наша цена», «бюджет клиента», «название лида», «неизвестно»")
    price_estimate: Optional[int] = Field(default=None, description="Если суммы нет — грубая оценка заказа в рублях по описанию изделия, иначе null")
    cladding: bool = Field(description="Обклейка/облицовка: металл (нержавейка) наклеивается или обшивается на основу из ЛДСП, МДФ, фанеры; облицовка стен, колонн, фасадов; накладки на фасады мебели")
    complex: bool = Field(description="Сложно в реализации: гнутые радиусные и объёмные формы, зеркальная полировка больших поверхностей, нестандартные узлы, много разных позиций, монтаж на объекте, неясное ТЗ при сложной геометрии")
    why: str = Field(description="1 короткое предложение: что за изделие и почему такая оценка")


SYSTEM = """Ты помогаешь владельцу металлообработки «90 градусов» решить, каких клиентов менеджеру дожимать. Владелец хочет только простые заказы за хорошую цену: чек от 50 000 ₽, без обклейки (металл на основу ЛДСП/МДФ/фанеры, облицовка стен и фасадов, накладки) и без сложных в реализации изделий.
Прочитай переписку и оцени заказ. Простое: каркасная и листовая мебель и изделия из металла (столы, стеллажи, стойки, опоры, полки, ограждения, кронштейны) прямой геометрии, сварка и покраска или шлифовка, без облицовки основы. Столешница из цельной нержавейки 2–3 мм без подложки — не обклейка; столешница «нержавейка 0,8 мм на ЛДСП/МДФ» — обклейка.
Факты бери только из переписки и названия лида. Не выдумывай."""


def main():
    src = Path(sys.argv[1])
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig"), delimiter=";"))
    pick = [r for r in rows if any(k in r["Действие"].lower() for k in ("ответить", "дожать", "касани", "ждём", "ждем"))]
    env = load_env()
    d = env_b24(env)
    ids = [r["Лид"] for r in pick]
    head, tail = d.comments("lead", ids), d.tails(ids, n=40)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    cl = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])

    def work(r):
        com = {c["ID"]: c for c in head.get(r["Лид"], []) + tail.get(r["Лид"], [])}
        msgs = messages(sorted(com.values(), key=lambda c: int(c["ID"])))
        key = hashlib.sha1((r["Лид"] + (str(msgs[-1]["id"]) if msgs else "")).encode()).hexdigest()
        if key in cache:
            return r, key, cache[key]
        resp = cl.beta.messages.create(
            model="claude-opus-5-5", max_tokens=2000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Лид «{r['Название']}».\n\nПереписка:\n{transcript(msgs) or '(нет)'}"}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": anthropic.transform_schema(Fit.model_json_schema())}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        return r, key, Fit.model_validate_json(text).model_dump()

    res = {}
    with ThreadPoolExecutor(6) as ex:
        for r, key, fit in ex.map(work, pick):
            cache[key] = fit
            res[r["Лид"]] = fit
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    out = src.with_name(src.stem + "_filtered.csv")
    fields = list(rows[0].keys())
    extra = ["Сумма, ₽", "Откуда сумма", "Обклейка", "Сложно", "Оценка заказа", "Пушим"]
    fields = fields[:-1] + extra + fields[-1:]
    stats = {"пушим": 0, "меньше 50к": 0, "обклейка": 0, "сложно": 0}
    for r in rows:
        f = res.get(r["Лид"])
        if not f:
            for k in extra:
                r[k] = ""
            continue
        price = f["price_rub"] or f.get("price_estimate")
        reasons = []
        if price is not None and price < MIN_CHECK:
            reasons.append("меньше 50 000 ₽")
            stats["меньше 50к"] += 1
        if f["cladding"]:
            reasons.append("обклейка")
            stats["обклейка"] += 1
        if f["complex"]:
            reasons.append("сложно")
            stats["сложно"] += 1
        r.update({"Сумма, ₽": f["price_rub"] or "", "Откуда сумма": f["price_source"] if f["price_rub"] else
                  (f"оценка ~{f['price_estimate']}" if f.get("price_estimate") else "неизвестно"),
                  "Обклейка": "да" if f["cladding"] else "", "Сложно": "да" if f["complex"] else "",
                  "Оценка заказа": f["why"], "Пушим": "нет: " + ", ".join(reasons) if reasons else "да"})
        if reasons:
            r["Код"] = "JUNK" if reasons == ["меньше 50 000 ₽"] else "2"
            r["Предлагаю"] = "Маленький чек" if r["Код"] == "JUNK" else "Сложно"
        else:
            stats["пушим"] += 1
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"проверено: {len(pick)}; {stats}\n{out}")


if __name__ == "__main__":
    main()
