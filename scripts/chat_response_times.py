"""Время ответа менеджера в переписке: от первого неотвеченного сообщения клиента до ответа.

Вход: JSON {lead_id: [[iso_time, "c"|"m"], ...]} (c — клиент, m — менеджер).
Считает календарные часы и рабочие часы (пн–пт 10:00–19:00 — ДОПУЩЕНИЕ, график не известен).
"""
import json
import sys
from datetime import datetime, timedelta

WORK_START, WORK_END = 10, 19  # ДОПУЩЕНИЕ


def work_hours(a: datetime, b: datetime) -> float:
    total, t = 0.0, a
    while t < b:
        nxt = min(b, (t + timedelta(hours=1)).replace(minute=0, second=0))
        if t.weekday() < 5 and WORK_START <= t.hour < WORK_END:
            total += (nxt - t).total_seconds()
        t = nxt
    return total / 3600


def waits(msgs, now):
    res, pending = [], None
    for ts, who in msgs:
        t = datetime.fromisoformat(ts)
        if who == "c" and pending is None:
            pending = t
        elif who == "m" and pending is not None:
            res.append((pending, t, False))
            pending = None
    if pending is not None:
        res.append((pending, now, True))
    return res


def main(path, now_iso):
    data = json.load(open(path, encoding="utf-8"))
    now = datetime.fromisoformat(now_iso)
    for lead, msgs in data.items():
        w = waits(msgs, now)
        if not w:
            continue
        cal = [(b - a).total_seconds() / 3600 for a, b, _ in w]
        wrk = [work_hours(a, b) for a, b, _ in w]
        first = w[0]
        print(f"лид {lead}: ожиданий {len(w)}; первое {cal[0]:.1f} ч (раб. {wrk[0]:.1f})"
              f"{' — БЕЗ ОТВЕТА' if first[2] else ''}; макс {max(cal):.1f} ч (раб. {max(wrk):.1f});"
              f" без ответа сейчас: {'да, с ' + w[-1][0].strftime('%d.%m %H:%M') if w[-1][2] else 'нет'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "2026-10-14T18:00:00")
