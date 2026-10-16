"""Входящие заявки: Instagram (чат Б24 «Заявки из Инсты») и сайт → лид, контакт, приветствие.

Шаблон заявки и текст приветствия утверждены 16.10.2026 (docs/12_входящие_заявки.md).
Секреты — из .env: B24_LEADS_WEBHOOK, WAZZUP_API_KEY.

Режимы:
  (по умолчанию) вхолостую — только чтение Б24: разбор заявок, проверка дублей за 60 дней,
                 черновики приветствий. Ничего не создаёт и не отправляет.
  --create       создать контакт + лид + комментарий (только для заявок без дубля).
Отправка приветствия через Wazzup пока не реализована: ждёт решения владельца об автоотправке
и проверки формата API (как писать первым по нику/телефону — НЕ ПРОВЕРЕНО).

Запуск: python3 scripts/inbound_leads.py [--since 2026-10-01] [--create]
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTA_CHAT = "chat415"          # «Заявки из Инсты»
SITE_SOURCE = "WEB"
DUP_DAYS = 60                   # решение 16.10.2026
WORK_START, WORK_END = 10, 19   # эталон 06, пн–пт

GREETING = (
    "Добрый день{name}! На связи «90 градусов», меня зовут Екатерина.\n\n"
    "{received}\n\n"
    "Чтобы подготовить расчёт, пришлите, пожалуйста, что есть из этого:\n"
    "1. Размеры, чертёж или фото-референс.\n"
    "2. Количество.\n"
    "3. Материал и цвет, если уже выбрали (нержавейка, чёрная сталь в покраске и т. п.).\n"
    "4. Город, нужны ли доставка и монтаж.\n"
    "5. Желаемый срок.\n"
    "6. Для кого заказ: для себя, для компании (нужен ли НДС) или вы дизайнер.\n\n"
    "Если есть дизайн-проект — присылайте целиком, посчитаем по нему."
)
SOURCE_WORDS = {"instagram": "через Instagram", "site": "с сайта"}


# ---------- окружение и Б24 ----------

def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


class B24:
    def __init__(self, webhook):
        self.url = webhook if webhook.endswith("/") else webhook + "/"

    def call(self, method, params=None):
        data = json.dumps(params or {}).encode()
        req = urllib.request.Request(self.url + method + ".json", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read().decode() or "{}")
        if "error" in body:
            raise RuntimeError(f"{method}: {body.get('error')} {body.get('error_description', '')}")
        return body.get("result")


# ---------- разбор ----------

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10 and d[0] == "9":
        return "+7" + d
    return ""


def norm_user(s):
    s = (s or "").strip()
    m = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,32})", s)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", s):
        return "@" + s
    return ""


def channel_pref(text):
    t = text.lower()
    if re.search(r"ватсап|whatsapp|вацап", t):
        return "WhatsApp"
    if re.search(r"звон|позвон", t):
        return "Звонок"
    return "Telegram"


def parse_template(text):
    """Новый шаблон: строки «Ключ: значение», первая строка #инста."""
    keys = {"имя": "name", "телефон": "phone", "telegram": "tg", "телеграм": "tg",
            "писать в": "channel", "запрос": "request", "город": "city", "срок": "deadline",
            "instagram": "insta", "инстаграм": "insta"}
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([^:]{2,15}):\s*(.+)$", line)
        if m and m.group(1).strip().lower() in keys:
            out[keys[m.group(1).strip().lower()]] = m.group(2).strip()
    return out


def parse_legacy(text):
    """Старый свободный формат: «контакт Имя» в первой строке, дальше запрос."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if not lines:
        return {}
    first, rest = lines[0], " ".join(lines[1:])
    out = {}
    m = re.search(r"\+?\d[\d\s\-()]{9,}\d", first)
    if m:
        out["phone"] = m.group(0)
        first_wo = first.replace(m.group(0), " ")
    else:
        u = re.match(r"\s*(@?[A-Za-z0-9_.]{4,32})", first)
        if u:
            out["tg"] = u.group(1)
            first_wo = first[u.end():]
        else:
            return {}
    name = re.sub(r"\b(ватсап|whatsapp|тг|телеграм)\b", "", first_wo, flags=re.I).strip(" ,.-")
    if name and len(name) <= 30 and not re.search(r"хоч|нуж|интерес", name, re.I):
        out["name"] = name
        out["request"] = rest
    else:
        out["request"] = (first_wo + " " + rest).strip()
    return out


def from_insta(msg):
    text = msg.get("text") or ""
    if not text.strip():
        return None
    fields = parse_template(text) if "#инста" in text.lower() else parse_legacy(text)
    phone, user = norm_phone(fields.get("phone", "")), norm_user(fields.get("tg", ""))
    if not (phone or user):
        return None
    req = re.sub(r"^\s*(хочет|хотят)\s+", "", fields.get("request", ""), flags=re.I).strip()
    return {
        "source": "instagram", "src_id": f"im{msg['id']}", "date": msg["date"][:16],
        "name": fields.get("name", ""), "phone": phone, "tg": user,
        "channel": fields.get("channel") or channel_pref(text),
        "request": req, "template": "#инста" in text.lower(), "raw": text.strip(),
    }


def from_site(lead):
    mtype = (lead.get("UF_CRM_MESSENGERTYPE") or "").lower()
    mid = lead.get("UF_CRM_MESSENGERID") or ""
    phones = [p.get("VALUE", "") for p in lead.get("PHONE") or []]
    phone = norm_phone(phones[0] if phones else "") or norm_phone(mid)
    user = norm_user(lead.get("UF_CRM_TELEGRAMUSERNAME_WZ") or (mid if mtype == "telegram" else ""))
    req = (lead.get("UF_CRM_RASSKAZHITECH") or lead.get("UF_CRM_PRODUCT") or "").split("\n")[0].strip()
    return {
        "source": "site", "src_id": f"lead{lead['ID']}", "date": lead["DATE_CREATE"][:16],
        "name": lead.get("NAME") or "", "phone": phone, "tg": user,
        "channel": "Telegram" if (user or mtype == "telegram") else ("Звонок" if mtype == "phone" else "Telegram"),
        "request": req, "template": True, "lead_id": lead["ID"],
        "chat_bound": bool(lead.get("UF_CRM_TELEGRAMID_WZ")), "raw": "",
    }


# ---------- дубли ----------

def find_dups(b24, req, since, exclude_lead=None):
    hits = []
    if req["phone"]:
        for et in ("LEAD", "CONTACT"):
            r = b24.call("crm.duplicate.findbycomm", {"entity_type": et, "type": "PHONE", "values": [req["phone"]]})
            for i in (r or {}).get(et, []) if isinstance(r, dict) else []:
                hits.append(f"{et.lower()} {i}")
    if req["tg"]:
        for val in {req["tg"], req["tg"].lower()}:
            for l in b24.call("crm.lead.list", {"filter": {"UF_CRM_TELEGRAMUSERNAME_WZ": val, ">=DATE_CREATE": since},
                                                "select": ["ID"]}) or []:
                hits.append(f"lead {l['ID']}")
            for d in b24.call("crm.deal.list", {"filter": {"UF_CRM_69DD014378F08": val, ">=DATE_CREATE": since},
                                                "select": ["ID"]}) or []:
                hits.append(f"deal {d['ID']}")
    hits = sorted(set(h for h in hits if h != f"lead {exclude_lead}"))
    return hits


# ---------- приветствие ----------

def short_request(text):
    """Запрос для вставки в приветствие: без вводных слов, с маленькой буквы; длинный — не цитируем."""
    t = re.sub(r"[,.;]?\s*(лучше|удобнее)\s+(писать|связаться)\s+в\s+\S+\s*$", "", (text or "").strip(), flags=re.I)
    t = re.sub(r"^(здравствуйте|добрый день|привет)[!,.\s]*", "", t, flags=re.I)
    t = re.sub(r"^(хочу|хотим|хотят|хочет|нужен|нужна|нужно|нужны|интересует|интересуют)\s+", "", t, flags=re.I)
    t = re.sub(r"^(заказать|сделать|просчитать|изготовить|обсудить|узнать)\s+", "", t, flags=re.I)
    t = t.strip(" .,")
    if not t or len(t) > 100:
        return ""
    return t[0].lower() + t[1:]


def greeting(req):
    name = req["name"].split()[0] if req["name"] and not re.search(r"\d", req["name"]) else ""
    src = SOURCE_WORDS[req["source"]]
    what = short_request(req["request"])
    received = f"Получили ваш запрос {src}: {what}." if what else f"Получили ваш запрос {src}."
    return GREETING.format(name=f", {name}" if name else "", received=received)


def send_time(created):
    t = datetime.fromisoformat(created)
    if t.weekday() < 5 and WORK_START <= t.hour < WORK_END:
        return "сразу"
    d = t + timedelta(days=1) if t.hour >= WORK_END or t.weekday() >= 5 else t
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%d.%m") + " 10:00"


# ---------- создание (только с --create) ----------

def create(b24, req):
    contact = {"NAME": req["name"] or req["tg"] or req["phone"], "SOURCE_ID": "OTHER" if req["source"] == "instagram" else SITE_SOURCE}
    if req["phone"]:
        contact["PHONE"] = [{"VALUE": req["phone"], "VALUE_TYPE": "WORK"}]
    cid = b24.call("crm.contact.add", {"fields": contact})
    if req["source"] == "site":
        b24.call("crm.lead.update", {"id": req["lead_id"], "fields": {"CONTACT_ID": cid}})
        lid = req["lead_id"]
    else:
        lead = {"TITLE": f"Instagram: {req['name'] or req['tg'] or req['phone']}", "NAME": req["name"],
                "SOURCE_ID": "OTHER", "SOURCE_DESCRIPTION": "Instagram (чат «Заявки из Инсты»)",
                "CONTACT_ID": cid, "COMMENTS": req["raw"]}
        if req["phone"]:
            lead["PHONE"] = [{"VALUE": req["phone"], "VALUE_TYPE": "WORK"}]
        if req["tg"]:
            lead["UF_CRM_TELEGRAMUSERNAME_WZ"] = req["tg"]
        lid = b24.call("crm.lead.add", {"fields": lead})
    return cid, lid


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=(date.today() - timedelta(days=14)).isoformat())
    ap.add_argument("--create", action="store_true")
    args = ap.parse_args()

    env = load_env()
    b24 = B24(env["B24_LEADS_WEBHOOK"])
    dup_since = (date.today() - timedelta(days=DUP_DAYS)).isoformat()

    msgs = (b24.call("im.dialog.messages.get", {"DIALOG_ID": INSTA_CHAT, "LIMIT": 50}) or {}).get("messages", [])
    reqs = [r for m in msgs if m["date"][:10] >= args.since and (r := from_insta(m))]
    site = b24.call("crm.lead.list", {"filter": {"SOURCE_ID": SITE_SOURCE, ">=DATE_CREATE": args.since},
                                      "select": ["ID", "NAME", "DATE_CREATE", "PHONE", "UF_CRM_MESSENGERTYPE",
                                                 "UF_CRM_MESSENGERID", "UF_CRM_RASSKAZHITECH", "UF_CRM_PRODUCT",
                                                 "UF_CRM_TELEGRAMID_WZ", "UF_CRM_TELEGRAMUSERNAME_WZ", "CONTACT_ID"]}) or []
    reqs += [from_site(l) for l in site if not l.get("CONTACT_ID")]
    reqs.sort(key=lambda r: r["date"])

    out = [f"# Входящие заявки с {args.since} — {'СОЗДАНИЕ' if args.create else 'вхолостую (ничего не создано и не отправлено)'}\n"]
    stats = {"всего": len(reqs), "дубль": 0, "к созданию": 0, "без приветствия": 0}
    for r in reqs:
        dups = find_dups(b24, r, dup_since, r.get("lead_id"))
        can_greet = r["channel"] == "Telegram" and bool(r["tg"] or r["phone"]) and not r.get("chat_bound")
        action = "дубль — не создаём, пометить" if dups else ("создать контакт" + (" + лид" if r["source"] == "instagram" else ""))
        stats["дубль" if dups else "к созданию"] += 1
        if not can_greet:
            stats["без приветствия"] += 1
        out.append(f"## {r['date']} · {'Instagram' if r['source'] == 'instagram' else 'Сайт'} · {r['src_id']}")
        out.append(f"- Имя: {r['name'] or '—'}; телефон: {r['phone'] or '—'}; Telegram: {r['tg'] or '—'}; писать в: {r['channel']}"
                   + ("" if r["template"] else " (старый формат, разобрано эвристикой)"))
        out.append(f"- Запрос: {r['request'] or '—'}")
        out.append(f"- Совпадения за {DUP_DAYS} дней: {', '.join(dups) if dups else 'нет'} → **{action}**")
        if r.get("chat_bound"):
            out.append("- Чат Wazzup уже привязан к этому лиду")
        if can_greet and not dups:
            out.append(f"- Приветствие ({send_time(r['date'])}), черновик:\n")
            out.append("> " + greeting(r).replace("\n", "\n> ") + "\n")
        elif r.get("chat_bound"):
            out.append("- Приветствие не нужно: переписка в Telegram уже идёт\n")
        elif not can_greet:
            out.append(f"- Приветствие не отправляется (канал {r['channel']}) → задача Екатерине\n")
        else:
            out.append("")
        if args.create and not dups:
            cid, lid = create(b24, r)
            out.append(f"- Создано: контакт {cid}, лид {lid}\n")
    out.insert(1, "Итог: " + ", ".join(f"{k} {v}" for k, v in stats.items()) + "\n")

    report = "\n".join(out)
    path = ROOT / "data" / "leads" / f"inbound_{'create' if args.create else 'dry'}_{date.today().isoformat()}.md"
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nОтчёт: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
