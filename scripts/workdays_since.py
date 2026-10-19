"""Какой сегодня рабочий день после события (КП, цена, последнее сообщение) — для дожима на 2-й, 5-й, 10-й день.

Использование: python3 scripts/workdays_since.py <сегодня ГГГГ-ММ-ДД> <метка>=<ГГГГ-ММ-ДД> ...
Пример:       python3 scripts/workdays_since.py 2026-10-19 "1263 КП"=2026-10-14 1255=2026-10-14
Рабочие дни — пн–пт, праздники не учитываются. День события не считается: следующий рабочий день — 1-й.
"""
import sys
from datetime import date, timedelta


def workdays_between(start: date, today: date) -> int:
    n, d = 0, start
    while d < today:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def main(args):
    today = date.fromisoformat(args[0])
    for item in args[1:]:
        label, _, day = item.rpartition("=")
        print(f"{label}: {workdays_between(date.fromisoformat(day), today)}-й раб. день")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1:])
