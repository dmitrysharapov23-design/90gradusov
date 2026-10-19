"""Разбор открытых лидов менеджера: предложение статуса, следующего шага и черновика сообщения.

Только чтение Б24. Результат — на утверждение владельцу: data/leads/triage_<дата>.md и .csv.
Очевидные случаи решает код (есть сделка, клиент не отвечал, внутренний чат), остальные — Claude API.
Кэш решений: data/leads/triage_cache.json (повторный запуск не тратит вызовы по тем же перепискам).

  .venv/bin/python scripts/lead_triage.py [--days 90] [--limit N] [--workers 6]
"""
import argparse
import csv
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "reports"))

import anthropic  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from data import MANAGER_ID, MSK, env_b24, load_env, messages, work_days_between, work_hours  # noqa: E402

OUT = ROOT / "data" / "leads"
CACHE = OUT / "triage_cache.json"
OPEN = ["NEW", "IN_PROCESS", "UC_VE9PFM"]
INTERNAL = ("расчеты 90", "расчёты 90", "админ 90", "90 градусов")
NO_REPLY_DAYS = 10

STATUSES = {
    "IN_PROCESS": "В работе", "UC_VE9PFM": "Дали цену", "CONVERTED": "Качественный лид",
    "JUNK": "Маленький чек", "1": "Долго", "2": "Сложно", "3": "Не наш формат", "4": "Дорого",
    "5": "нет ответа, игнор", "6": "СПАМ", "7": "Другое", "8": "Дубль / не клиент", "NEW": "Не обработан",
}


class Verdict(BaseModel):
    status: str = Field(description="Код статуса из списка в инструкции")
    reason: str = Field(description="Почему такой статус, 1 короткое предложение с фактами из переписки")
    action: str = Field(description="Следующий шаг для Екатерины: ответить / дожать / создать сделку / закрыть / ничего")
    draft: Optional[str] = Field(default=None, description="Текст сообщения клиенту от Екатерины, если нужно написать; иначе null")
    confidence: str = Field(description="high / medium / low")


SYSTEM = """Ты разбираешь переписку менеджера Екатерины (компания «90 градусов», металлообработка: мебель и изделия из нержавеющей и чёрной стали на заказ, производство в Янино под Санкт-Петербургом) с клиентом в Telegram и предлагаешь, какой статус поставить лиду в CRM и что делать дальше. Сейчас статусы почти не ведутся, твоя задача — навести порядок.

Коды статусов (выбери ровно один):
IN_PROCESS — В работе: диалог идёт, собираем ТЗ, цену ещё не называли.
UC_VE9PFM — Дали цену: менеджер назвал цену или отправил КП, решение клиента ещё не известно (в том числе клиент молчит после КП меньше 10 рабочих дней).
3 — Не наш формат: мы такое не делаем (кухни из ЛДСП, не металл и т. п.).
4 — Дорого: клиент отказался из-за цены.
1 — Долго: клиент отказался из-за срока.
2 — Сложно: отказались мы или клиент из-за сложности.
JUNK — Маленький чек: заказ слишком мелкий для нас.
5 — нет ответа, игнор: клиент перестал отвечать (после КП — 10 и более рабочих дней тишины; до КП — 10 и более рабочих дней без ответа на наш вопрос).
6 — СПАМ.
8 — Дубль / не клиент: поставщики, соискатели, внутренние чаты, рассылки, повторное обращение того же клиента, уже ведущегося в другом лиде, вопросы по уже купленному изделию (это сервис, не новая продажа).
7 — Другое: отказ по другой причине (ушёл к местным, передумал, проект заморожен) — причину назови в reason.
CONVERTED — только если клиент согласился заказывать (договор, оплата), а сделки ещё нет: тогда action «создать сделку».

Действие (action) — коротко: «ответить клиенту», «дожать после КП (1-е касание)», «дожать (2-е касание)», «создать сделку», «закрыть», «ждём клиента», «ничего».

Черновик (draft) — только если action «ответить клиенту» или «дожать». Пишет Екатерина от своего имени, на «вы», коротко, по-человечески, как в её сообщениях в переписке. Правила компании:
- Не называй цен, сроков и скидок, которых не было в переписке. Срок производства: типовое изделие 4–5 недель, проект от 6 недель — всё после согласования финального чертежа. Оплата 70/30. Доставку не организуем: упаковываем и грузим в ТК клиента в Янино. Работаем без НДС, по запросу НДС сверху.
- Дожим — с пользой: вопрос по конструктиву, предложение уточнить детали, напоминание без давления. Не «Вы передумали?».
- Если клиент задал вопрос — ответь на него, если ответ есть в правилах или переписке; иначе напиши, что уточнишь, и задай встречный вопрос.
- Не обещай того, что зависит от производства. Никаких выдуманных фактов.
- Учитывай, сколько времени прошло: если клиент ждал долго — коротко извинись.

Неизвестное не выдумывай. Если переписки почти нет — так и скажи в reason, confidence low."""


def load_cache():
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def transcript(msgs):
    lines = [f"[{m['time']:%d.%m.%Y %H:%M}] {'Екатерина' if m['who'] == 'm' else 'Клиент'}: {m['text']}" for m in msgs]
    text = "\n".join(lines)
    return text if len(text) < 40000 else text[:10000] + "\n…[пропуск]…\n" + text[-30000:]


def rule_verdict(lead, msgs, deals, now):
    """Очевидные случаи без нейросети. → dict или None."""
    title = (lead["TITLE"] or "").lower()
    if any(x in title for x in INTERNAL):
        return {"status": "8", "reason": "внутренний чат через рабочий номер", "action": "закрыть", "draft": None,
                "confidence": "high", "by": "правило"}
    if deals:
        ds = ", ".join(f"сд {x['ID']}" for x in deals)
        return {"status": "CONVERTED", "reason": f"есть сделка: {ds}", "action": "ничего", "draft": None,
                "confidence": "high", "by": "правило"}
    if not msgs:
        return {"status": "5", "reason": "переписки в CRM нет", "action": "закрыть", "draft": None,
                "confidence": "low", "by": "правило"}
    if not any(m["who"] == "c" for m in msgs) and work_days_between(msgs[-1]["time"], now) >= NO_REPLY_DAYS:
        return {"status": "5", "reason": f"клиент ни разу не ответил, последнее наше сообщение {msgs[-1]['time']:%d.%m}",
                "action": "закрыть", "draft": None, "confidence": "high", "by": "правило"}
    return None


def ask_claude(cl, lead, msgs, now):
    last = msgs[-1]
    ctx = (f"Сегодня {now:%d.%m.%Y %H:%M} (пн–пт 10–19 рабочее время). Лид {lead['ID']} «{lead['TITLE']}», "
           f"текущий статус «{STATUSES.get(lead['STATUS_ID'], lead['STATUS_ID'])}», создан {lead['DATE_CREATE'][:10]}. "
           f"Последнее сообщение — {'клиента' if last['who'] == 'c' else 'Екатерины'}, "
           f"{work_days_between(last['time'], now)} раб. дн. назад"
           + (f", клиент ждёт ответа {work_hours(last['time'], now):.1f} раб. ч" if last["who"] == "c" else "") + ".")
    resp = cl.beta.messages.create(
        model="claude-opus-5-5", max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"{ctx}\n\nПереписка:\n{transcript(msgs)}"}],
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": anthropic.transform_schema(Verdict.model_json_schema())}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    v = Verdict.model_validate_json(text).model_dump()
    if v["status"] not in STATUSES:
        v["status"], v["confidence"] = "7", "low"
    v["by"] = "Claude"
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    env = load_env()
    d = env_b24(env)
    now = datetime.now(MSK)
    since = now - timedelta(days=args.days)
    leads = d.all("crm.lead.list", {"ASSIGNED_BY_ID": MANAGER_ID, "@STATUS_ID": OPEN, ">=DATE_CREATE": since.isoformat()},
                  ["ID", "TITLE", "STATUS_ID", "SOURCE_ID", "DATE_CREATE", "CONTACT_ID"], {"ID": "ASC"})
    if args.limit:
        leads = leads[:args.limit]
    print(f"открытых лидов: {len(leads)}", flush=True)
    ids = [l["ID"] for l in leads]
    head = d.comments("lead", ids)
    tail = d.tails(ids, n=40)
    deals_by_lead = {}
    for x in d.deals({"@LEAD_ID": ids, "CATEGORY_ID": "0"}) if ids else []:
        deals_by_lead.setdefault(str(x["LEAD_ID"]), []).append(x)
    contacts = sorted({l["CONTACT_ID"] for l in leads if l.get("CONTACT_ID")})
    deals_by_contact = {}
    for x in d.deals({"@CONTACT_ID": contacts, "CATEGORY_ID": "0"}) if contacts else []:
        deals_by_contact.setdefault(str(x["CONTACT_ID"]), []).append(x)

    cache = load_cache()
    cl = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    rows, todo = {}, []
    for l in leads:
        com = {c["ID"]: c for c in head.get(l["ID"], []) + tail.get(l["ID"], [])}
        msgs = messages(sorted(com.values(), key=lambda c: int(c["ID"])))
        deals = deals_by_lead.get(l["ID"], []) + deals_by_contact.get(str(l.get("CONTACT_ID") or ""), [])
        deals = list({x["ID"]: x for x in deals}.values())
        v = rule_verdict(l, msgs, deals, now)
        key = hashlib.sha1((l["ID"] + (str(msgs[-1]["id"]) if msgs else "")).encode()).hexdigest()
        if v:
            rows[l["ID"]] = (l, msgs, v)
        elif key in cache:
            rows[l["ID"]] = (l, msgs, cache[key])
        else:
            todo.append((l, msgs, key))
    print(f"по правилам/из кэша: {len(rows)}, к Claude: {len(todo)}", flush=True)

    def work(item):
        l, msgs, key = item
        try:
            return l, msgs, key, ask_claude(cl, l, msgs, now)
        except Exception as e:
            return l, msgs, key, {"status": l["STATUS_ID"], "reason": f"ошибка разбора: {str(e)[:120]}",
                                  "action": "проверить вручную", "draft": None, "confidence": "low", "by": "ошибка"}

    done = 0
    with ThreadPoolExecutor(args.workers) as ex:
        for l, msgs, key, v in ex.map(work, todo):
            rows[l["ID"]] = (l, msgs, v)
            if v["by"] == "Claude":
                cache[key] = v
            done += 1
            if done % 10 == 0:
                OUT.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  {done}/{len(todo)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    stamp = f"{now:%Y-%m-%d}"
    csv_path, md_path = OUT / f"triage_{stamp}.csv", OUT / f"triage_{stamp}.md"
    order = sorted(rows.values(), key=lambda r: (r[2]["action"] in ("ничего", "закрыть"), r[0]["ID"]))
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Лид", "Название", "Сейчас", "Предлагаю", "Код", "Почему", "Действие", "Черновик", "Уверенность", "Кто решил", "Утвердить (да/нет/код)"])
        for l, msgs, v in order:
            w.writerow([l["ID"], l["TITLE"], STATUSES.get(l["STATUS_ID"]), STATUSES.get(v["status"]), v["status"],
                        v["reason"], v["action"], v.get("draft") or "", v["confidence"], v["by"], ""])
    from collections import Counter
    cnt = Counter(STATUSES.get(v["status"]) for _, _, v in rows.values())
    act = Counter(v["action"] for _, _, v in rows.values())
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Разбор открытых лидов Екатерины — {now:%d.%m.%Y %H:%M}\n\nЛидов: {len(rows)}. Б24 не менялся.\n\n")
        f.write("Предлагаемые статусы: " + ", ".join(f"{k} {v}" for k, v in cnt.most_common()) + "\n\n")
        f.write("Действия: " + ", ".join(f"{k} {v}" for k, v in act.most_common()) + "\n\n")
        for l, msgs, v in order:
            if v.get("draft"):
                f.write(f"## Лид {l['ID']} — {l['TITLE']}\n{STATUSES.get(v['status'])} · {v['action']} · {v['reason']}\n\n> "
                        + v["draft"].replace("\n", "\n> ") + "\n\n")
    print(f"готово: {csv_path}\n{md_path}")
    print("Статусы:", dict(cnt.most_common()))
    print("Действия:", dict(act.most_common()))


if __name__ == "__main__":
    main()
