"""Аудит лидов Wazzup: сколько из них лишние и почему.

Вход: JSON из data/leads/ (разметка лидов + связанные лиды и сделки, выгрузка Б24 только чтением).
Считает доли категорий, отдельно проверяет совпадения телефонов среди связанных лидов
(дубли заявок с сайта) и выводит список лишних лидов со ссылками на исходные записи.
"""
import json
import re
import sys
from collections import Counter, defaultdict

EXTRA = {"not_client", "partner", "open_deal", "other_channel", "returning"}


def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    return "7" + d[1:] if len(d) == 11 and d[0] in "78" else d


def main(path):
    data = json.load(open(path, encoding="utf-8"))
    cats, leads = data["categories"], data["leads"]
    total = len(leads)
    cnt = Counter(l["cat"] for l in leads)

    print(f"Лидов Wazzup: {total}\n")
    print(f"{'Категория':<52}{'шт':>4}{'%':>6}")
    for key in cats:
        if cnt[key]:
            mark = "  лишний" if key in EXTRA else ""
            print(f"{cats[key]:<52}{cnt[key]:>4}{cnt[key] / total * 100:>6.0f}{mark}")
    extra = sum(cnt[k] for k in EXTRA)
    print(f"\nЛишних (не новый запрос): {extra} из {total} — {extra / total * 100:.0f}%")
    print(f"Новых запросов: {cnt['new']}, неясных: {cnt['unclear']}")

    started_by_manager = sum(1 for l in leads if l["first"] == "m")
    print(f"Чат начала менеджер (лид создан её исходящим сообщением): {started_by_manager} из {total}")
    extra_by_manager = sum(1 for l in leads if l["first"] == "m" and l["cat"] in EXTRA)
    print(f"  из них лишних: {extra_by_manager}; лишних, начатых клиентом: {extra - extra_by_manager}")

    # Дубли по телефону среди всех известных лидов
    by_phone = defaultdict(list)
    for l in leads + data.get("related_leads", []):
        if l.get("phone"):
            by_phone[norm_phone(l["phone"])].append(l["id"])
    dups = {p: ids for p, ids in by_phone.items() if len(ids) > 1}
    print(f"\nОдин телефон — несколько лидов: {len(dups)}")
    for p, ids in dups.items():
        print(f"  …{p[-4:]}: лиды {', '.join(map(str, sorted(ids)))}")

    print("\nЛишние лиды:")
    for key in cats:
        if key not in EXTRA:
            continue
        for l in leads:
            if l["cat"] == key:
                ref = f" → {', '.join(map(str, l['ref']))}" if l.get("ref") else ""
                print(f"  [{key}] {l['id']} {l['title']}{ref}: {l['note']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/leads/wazzup_2026-09-29_10-14.json")
