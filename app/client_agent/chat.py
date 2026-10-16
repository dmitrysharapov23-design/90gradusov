"""Разбор переписки Wazzup из таймлайна лида и расчёт рабочего времени.

Wazzup пишет каждое сообщение комментарием вида
  [img]https://static.wazzup24.com/images/bitrix/telegram.png[/img]&nbsp;  Автор:\nтекст
Сообщения компании подписаны «Телефон:», клиента — его именем в Telegram.
"""
import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
WORK_DAYS, WORK_START, WORK_END = range(5), 10, 19  # пн–пт 10–19 (документ 12)

HEADER = re.compile(r"^\[img\]https://static\.wazzup24\.com/images/bitrix/(\w+)\.png\[/img\](?:&nbsp;|\s)*(.*?):\n", re.S)
COMPANY = "Телефон"
URL = re.compile(r"\[url=[^\]]*\]\s*(.*?)\[/url\]", re.S)
EMOJI = re.compile(r":((?:[0-9a-f]{2}){2,8}):")


def _emoji(m):
    try:
        return bytes.fromhex(m.group(1)).decode("utf-8")
    except ValueError:
        return m.group(0)


def clean(text: str) -> str:
    text = URL.sub(lambda m: f"[{m.group(1).strip() or 'файл'}]", text)
    text = EMOJI.sub(_emoji, text)
    return text.replace("&nbsp;", " ").replace("&quot;", '"').strip()


def parse(comments: list) -> list:
    """Сообщения Wazzup по порядку: {id, time, who: 'c'|'m', author, channel, text}. Прочие комментарии пропускаются."""
    msgs = []
    for c in comments:
        m = HEADER.match(c.get("COMMENT") or "")
        if not m:
            continue
        channel, author = m.group(1), m.group(2).strip()
        msgs.append({
            "id": int(c["ID"]),
            "time": datetime.fromisoformat(c["CREATED"]),
            "who": "m" if author == COMPANY else "c",
            "author": author,
            "channel": channel,
            "text": clean(c["COMMENT"][m.end():]),
        })
    msgs.sort(key=lambda x: x["id"])
    return msgs


def is_work_time(t: datetime) -> bool:
    t = t.astimezone(MSK)
    return t.weekday() in WORK_DAYS and WORK_START <= t.hour < WORK_END


def work_hours(a: datetime, b: datetime) -> float:
    """Рабочие часы между a и b (праздники не учитываются)."""
    a, b = a.astimezone(MSK), b.astimezone(MSK)
    total, t = 0.0, a
    while t < b:
        nxt = min(b, (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
        if is_work_time(t):
            total += (nxt - t).total_seconds()
        t = nxt
    return total / 3600


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
