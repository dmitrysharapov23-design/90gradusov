"""Бот сводки: отчёты по запросу владельца.

Тот же бот, что присылает ежедневную сводку (TG_BOT_TOKEN). Отвечает только в чат TG_CHAT_ID.
Ключи в .env: TG_BOT_TOKEN, TG_CHAT_ID, B24_AGENT_WEBHOOK, ANTHROPIC_API_KEY (для «клиент»).
Б24 — только чтение. Запуск: launchd ru.90gradusov.reports-bot.
"""
import json
import re
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import reports
from data import MSK, ROOT, env_b24, load_env

DATA = ROOT / "data" / "reports"
STATE = DATA / "bot_state.json"

HELP = """Отчёты по запросу. Пишите словами или жмите кнопки.

отчёт [период] — лиды по источникам и статусам, скорость ответа, КП, оплаты и отгрузки, сравнение с прошлым периодом
лиды [период] — только лиды: сколько, откуда, статусы (быстро)
висят — кто ждёт ответа и кому пора дожимать после КП
воронка — лиды и сделки по стадиям, суммы, зависшие
клиент 1251 / клиент сд761 / клиент Виталий — саммари истории и договорённостей (около 2 минут)

Периоды: сегодня, вчера, неделя, прошлая неделя, месяц, прошлый месяц, сентябрь, 30 дней, квартал, год, 01.09-15.10, 01.09.2026-30.09.2026. Без периода — текущий месяц."""

KEYBOARD = {"keyboard": [["Отчёт за месяц", "Отчёт за неделю"], ["Висят", "Воронка"],
                         ["Лиды за прошлый месяц", "Помощь"]], "resize_keyboard": True, "is_persistent": True}

COMMANDS = [("report", "Отчёт за период: /report месяц"), ("leads", "Лиды за период: /leads неделя"),
            ("hanging", "Кто ждёт ответа и дожима"), ("funnel", "Воронка и зависшие сделки"),
            ("client", "Саммари клиента: /client 1251"), ("help", "Справка")]

MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]


def log(msg):
    DATA.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(DATA / "bot.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- периоды ----------

def day(d):
    return datetime(d.year, d.month, d.day, tzinfo=MSK)


def month_start(y, m):
    return datetime(y + (m - 1) // 12, (m - 1) % 12 + 1, 1, tzinfo=MSK)


def parse_period(text: str, now: datetime = None):
    """→ (начало, конец-не-включительно) или None. По умолчанию — текущий месяц."""
    now = now or datetime.now(MSK)
    today = day(now)
    t = text.lower().replace("ё", "е").strip()
    if not t or t in ("месяц", "этот месяц", "текущий месяц", "за месяц"):
        return month_start(now.year, now.month), today + timedelta(days=1)
    if "прошл" in t and "месяц" in t:
        return month_start(now.year, now.month - 1), month_start(now.year, now.month)
    if "прошл" in t and "недел" in t:
        mon = today - timedelta(days=today.weekday())
        return mon - timedelta(days=7), mon
    if "недел" in t:
        return today - timedelta(days=today.weekday()), today + timedelta(days=1)
    if t == "сегодня":
        return today, today + timedelta(days=1)
    if t == "вчера":
        return today - timedelta(days=1), today
    if "квартал" in t:
        q0 = (now.month - 1) // 3 * 3 + 1
        if "прошл" in t:
            return month_start(now.year, q0 - 3), month_start(now.year, q0)
        return month_start(now.year, q0), today + timedelta(days=1)
    if t in ("год", "этот год", "с начала года"):
        return datetime(now.year, 1, 1, tzinfo=MSK), today + timedelta(days=1)
    m = re.fullmatch(r"(?:за\s+)?(\d{1,3})\s*(дн|дней|день|дня)", t)
    if m:
        return today - timedelta(days=int(m.group(1)) - 1), today + timedelta(days=1)
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*[-–—]\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", t)
    if m:
        def mk(dd, mm, yy):
            y = int(yy) + (2000 if yy and len(yy) == 2 else 0) if yy else now.year
            return datetime(y, int(mm), int(dd), tzinfo=MSK)
        a = mk(m.group(1), m.group(2), m.group(3) or m.group(6))
        b = mk(m.group(4), m.group(5), m.group(6))
        return (a, b + timedelta(days=1)) if a <= b else None
    m = re.fullmatch(r"([а-я]+)(?:\s+(\d{4}))?", t)
    if m:
        word = m.group(1)
        for i, stem in enumerate(MONTHS):
            if word.startswith(stem) and (stem != "ма" or word in ("май", "мая", "мае")):
                y = int(m.group(2)) if m.group(2) else (now.year if i + 1 <= now.month else now.year - 1)
                return month_start(y, i + 1), month_start(y, i + 2)
    return None


# ---------- Telegram ----------

class TG:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, params=None, timeout=40):
        req = urllib.request.Request(self.base + method, data=json.dumps(params or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Telegram {method}: HTTP {e.code} {e.read().decode(errors='ignore')[:200]}")
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res.get('description')}")
        return res["result"]

    def send(self, chat, text):
        """Длинный текст — частями по строкам, до 3900 знаков."""
        chunk = ""
        for line in text.split("\n"):
            if len(chunk) + len(line) + 1 > 3900:
                self.call("sendMessage", {"chat_id": chat, "text": chunk, "reply_markup": KEYBOARD})
                chunk = ""
            chunk += line + "\n"
        if chunk.strip():
            self.call("sendMessage", {"chat_id": chat, "text": chunk, "reply_markup": KEYBOARD})


# ---------- команды ----------

def route(text: str):
    """→ (команда, аргумент) или None."""
    t = text.strip()
    t = re.sub(r"^/(\w+)(@\w+)?", lambda m: {"report": "отчёт", "leads": "лиды", "hanging": "висят", "funnel": "воронка",
                                            "client": "клиент", "help": "помощь", "start": "помощь"}.get(m.group(1), m.group(1)), t)
    low = t.lower().replace("ё", "е")
    for word, cmd in (("отчет", "period"), ("лиды", "leads"), ("висят", "hanging"), ("воронка", "funnel"),
                      ("клиент", "client"), ("помощь", "help")):
        if low.startswith(word):
            arg = t[len(word):].strip()
            arg = re.sub(r"^за\s+", "", arg, flags=re.I)
            return cmd, arg
    return None


def handle(tg, chat, text, env, d):
    r = route(text)
    if not r:
        tg.send(chat, "Не понял. Напишите «помощь» — пришлю список отчётов.")
        return
    cmd, arg = r
    log(f"команда {cmd} [{arg}]")
    if cmd == "help":
        tg.send(chat, HELP)
    elif cmd in ("period", "leads"):
        p = parse_period(arg)
        if not p:
            tg.send(chat, f"Не понял период «{arg}». Примеры: месяц, прошлая неделя, сентябрь, 01.09-15.10.")
            return
        if cmd == "period":
            tg.send(chat, "Считаю, 1–2 минуты…")
        tg.send(chat, reports.period(d, *p, leads_only=(cmd == "leads")))
    elif cmd == "hanging":
        tg.send(chat, "Проверяю переписку…")
        tg.send(chat, reports.hanging(d))
    elif cmd == "funnel":
        tg.send(chat, reports.funnel(d))
    elif cmd == "client":
        if not arg:
            tg.send(chat, "Кого? Например: клиент 1251, клиент сд761, клиент Виталий.")
            return
        tg.send(chat, "Собираю историю и пишу саммари, около 2 минут…")
        tg.send(chat, reports.client(d, arg, reports.claude_llm(env)))


def main():
    env = load_env()
    tg = TG(env["TG_BOT_TOKEN"])
    owner = str(env["TG_CHAT_ID"])
    d = env_b24(env)
    st = json.loads(STATE.read_text()) if STATE.exists() else {"offset": 0}
    try:
        tg.call("setMyCommands", {"commands": [{"command": c, "description": t} for c, t in COMMANDS]})
    except Exception as e:
        log(f"setMyCommands: {e}")
    log("бот отчётов запущен")
    while True:
        try:
            ups = tg.call("getUpdates", {"offset": st["offset"], "timeout": 25, "allowed_updates": ["message"]}, timeout=40)
        except Exception as e:
            log(f"getUpdates: {e}")
            time.sleep(10)
            continue
        for u in ups:
            st["offset"] = u["update_id"] + 1
            DATA.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(st))
            msg = u.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text") or ""
            if chat != owner:
                log(f"чужой чат {chat}: пропущено")
                continue
            if not text:
                continue
            try:
                handle(tg, chat, text, env, d)
            except Exception as e:
                log(f"ошибка: {e}\n{traceback.format_exc()}")
                try:
                    tg.send(chat, f"Не получилось: {str(e)[:300]}")
                except Exception:
                    pass


if __name__ == "__main__":
    main()
