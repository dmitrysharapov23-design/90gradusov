"""Обработка пометок Екатерины в Google Документе «Задачи по лидам — 19.10.2026».

Пометка в заголовке лида (разделы 1 и 3):
  «пишем»       — отправить клиенту заготовленное сообщение из документа (через Wazzup, Telegram);
  «НЕ пишем!»   — не отправлять, закрыть лид статусом провала, причина — текст после пометки (комментарий в лид);
  «в работе» / «обработан» — ничего не делать.

Вход — текст документа (выгрузка коннектора Drive) в data/leads/katya_review/.
По умолчанию — только план: что отправить, что закрыть, и была ли переписка после 19.10 (тогда заготовка
могла устареть — такие лиды не отправляются без --include-stale).
  python3 scripts/lead_tasks_apply.py data/leads/katya_review/doc_2026-10-27.txt [--apply] [--include-stale]
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inbound_leads import B24, load_env, wazzup, wazzup_channel  # noqa: E402

DOC_DATE = "2026-10-19"
CLOSE_STATUS = "7"          # «Другое» — причина в комментарии
TG_ID = "UF_CRM_TELEGRAMID_WZ"
LOG = ROOT / "data" / "leads" / "katya_review" / "applied.log"


def unescape(s):
    return re.sub(r"\\([!#&*_\[\]()~.-])", r"\1", s)


def parse(text):
    """[{lead, name, mark, reason, draft}] по заголовкам «### **☐ Лид N — Имя ПОМЕТКА**»."""
    rows, cur, sec = [], None, None
    for line in text.splitlines():
        m = re.match(r"^## \*\*(\d)\.", line)
        if m:
            sec, cur = m.group(1), None
            continue
        m = re.match(r"^### \*\*.\s*Лид (\d+) — (.*?)\*\*(.*)$", line)
        if m:
            head = unescape((m.group(2) + " " + m.group(3)).strip())
            mm = re.search(r"\s(НЕ пишем!?|пишем|в работе|обработан)\s*(.*)$", " " + head, re.I)
            mark = mm.group(1).lower().rstrip("!") if mm else ""
            cur = {"lead": m.group(1), "section": sec, "name": head[:mm.start() - 1].strip() if mm else head,
                   "mark": mark, "reason": (mm.group(2).strip() if mm else ""), "draft": []}
            rows.append(cur)
            continue
        if cur is None or line.startswith("#"):
            continue
        s = line.strip()
        if not s or s.startswith("[Открыть в Битриксе]") or (s.startswith("*") and s.endswith("*")):
            continue
        cur["draft"].append(unescape(s))
    for r in rows:
        r["draft"] = "\n".join(r["draft"]).strip()
    return rows


def activity_since(b24, lead_id, since):
    """Сообщения Wazzup в таймлайне лида после даты документа: (кто последний, сколько)."""
    items = b24.call("crm.timeline.comment.list", {"filter": {"ENTITY_TYPE": "lead", "ENTITY_ID": int(lead_id)},
                                                    "order": {"CREATED": "DESC"}}) or []
    # свежие первыми (страница — 50 записей): этого хватает, чтобы увидеть переписку после даты документа
    new = [c for c in items if c.get("CREATED", "") >= since and "wazzup24.com/images" in (c.get("COMMENT") or "")]
    last = ""
    if new:
        last = "менеджер" if re.search(r"&nbsp;\s*Телефон:", new[0]["COMMENT"]) else "клиент"
    return len(new), last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--apply", action="store_true", help="отправить «пишем» и закрыть «НЕ пишем»")
    ap.add_argument("--include-stale", action="store_true", help="отправлять и там, где была переписка после 19.10")
    args = ap.parse_args()

    rows = parse(Path(args.doc).read_text(encoding="utf-8"))
    env = load_env()
    b24 = B24(env["B24_LEADS_WEBHOOK"])
    leads = {l["ID"]: l for l in b24.call("crm.lead.list", {"filter": {"ID": [r["lead"] for r in rows]},
                                                            "select": ["ID", "TITLE", "STATUS_ID", TG_ID, "PHONE"]}) or []}
    channel = wazzup_channel(env) if args.apply else None
    plan = {"send": [], "stale": [], "no_target": [], "close": [], "skip": []}
    for r in rows:
        lead = leads.get(r["lead"], {})
        if r["mark"] == "пишем":
            n, last = activity_since(b24, r["lead"], DOC_DATE)
            tg = str(lead.get(TG_ID) or "")
            phone = next((re.sub(r"\D", "", p.get("VALUE", "")) for p in lead.get("PHONE") or []), "")
            item = {**r, "tg": tg, "phone": phone, "activity": n, "last": last, "status": lead.get("STATUS_ID")}
            if not r["draft"]:
                plan["no_target"].append({**item, "why": "нет текста заготовки"})
            elif not (tg or phone):
                plan["no_target"].append({**item, "why": "нет Telegram ID и телефона"})
            elif n and not args.include_stale:
                plan["stale"].append(item)
            else:
                plan["send"].append(item)
        elif r["mark"] == "не пишем":
            plan["close"].append(r)
        else:
            plan["skip"].append(r)

    for key, title in (("send", "Отправить заготовку"), ("stale", "Была переписка после 19.10 — не отправляем"),
                       ("no_target", "Нельзя отправить"), ("close", "Закрыть (НЕ пишем)"), ("skip", "Ничего не делать")):
        print(f"\n== {title}: {len(plan[key])}")
        for i in plan[key]:
            extra = (f" · сообщений после 19.10: {i['activity']}, последнее — {i['last']}" if i.get("activity") else "")
            print(f"  {i['lead']} {i['name'][:40]} [{i['mark'] or '—'}]{(' — ' + i['reason']) if i.get('reason') else ''}"
                  f"{(' — ' + i['why']) if i.get('why') else ''}{extra}")
    out = ROOT / "data" / "leads" / "katya_review" / "plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.apply:
        print(f"\nТолько план ({out.relative_to(ROOT)}). Для выполнения — --apply.")
        return
    with LOG.open("a", encoding="utf-8") as log:
        for i in plan["send"]:
            body = {"channelId": channel, "chatType": "telegram", "text": i["draft"]}
            body.update({"chatId": i["tg"]} if i["tg"] else {"phone": i["phone"]})
            try:
                wazzup(env, "message", body)
                log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {i['lead']} отправлено\n")
                print(f"  {i['lead']}: отправлено")
            except Exception as e:
                log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {i['lead']} ОШИБКА отправки {e}\n")
                print(f"  {i['lead']}: ОШИБКА {e}")
            time.sleep(3)  # не частить с первыми сообщениями с рабочего аккаунта
        for i in plan["close"]:
            b24.call("crm.lead.update", {"id": int(i["lead"]), "fields": {"STATUS_ID": CLOSE_STATUS}})
            b24.call("crm.timeline.comment.add", {"fields": {"ENTITY_ID": int(i["lead"]), "ENTITY_TYPE": "lead",
                      "COMMENT": f"Закрыт по разбору «Задачи по лидам — 19.10» (Екатерина): {i['reason'] or 'не пишем'}"}})
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {i['lead']} закрыт (статус {CLOSE_STATUS}): {i['reason']}\n")
            print(f"  {i['lead']}: закрыт — {i['reason']}")


if __name__ == "__main__":
    main()
