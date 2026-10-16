"""Разбор журнала пилота учёта (таблица «Пилот_учёта_Силов_717», лист «Журнал»).

Вход: выгрузка таблицы в xlsx (Google Drive → скачать .xlsx), лежит в data/pilot/.
Выход: сводка часов по позициям, операциям, работникам; проблемы по кодам; ошибки заполнения.
Деньги не считаются — только часы и штуки.

Запуск: python3 scripts/pilot_journal.py data/pilot/<файл>.xlsx
Без сторонних библиотек: xlsx читается через zipfile + xml.
"""
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
COLS = ["Дата", "Позиция", "Операция", "Работник", "Начало", "Конец", "Часы",
        "Сделано", "Брак", "Код", "Комментарий", "Исправлено", "Внёс"]
MAX_DAY_H = 11  # больше — подозрительно (смена 8 ч + переработка)


def read_xlsx(path):
    """Возвращает {имя листа: [строки]}; значения — строки или числа."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    sheets = {}
    for s in wb.find("m:sheets", NS):
        rid = s.get(f"{{{NS['r']}}}id")
        p = target[rid].lstrip("/")
        p = p if p.startswith("xl/") else "xl/" + p
        rows = []
        for row in ET.fromstring(z.read(p)).iter(f"{{{NS['m']}}}row"):
            vals = {}
            for c in row.findall("m:c", NS):
                col = re.match(r"[A-Z]+", c.get("r")).group()
                idx = 0
                for ch in col:
                    idx = idx * 26 + ord(ch) - 64
                v = c.find("m:v", NS)
                if c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
                elif v is None:
                    val = None
                elif c.get("t") == "s":
                    val = shared[int(v.text)]
                elif c.get("t") in ("str", "b"):
                    val = v.text
                else:
                    val = float(v.text)
                vals[idx - 1] = val
            n = max(vals) + 1 if vals else 0
            rows.append([vals.get(i) for i in range(n)])
        sheets[s.get("name")] = rows
    return sheets


def to_date(v):
    if isinstance(v, float):
        return date(1899, 12, 30) + timedelta(days=int(v))
    if isinstance(v, str) and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", v.strip()):
        d, m, y = map(int, v.strip().split("."))
        return date(y, m, d)
    return None


def to_hours(v):
    """Время суток в часах: 0.375 (доля суток) или '09:00'."""
    if isinstance(v, float):
        return v * 24 if v < 1 else None
    if isinstance(v, str) and re.fullmatch(r"\d{1,2}:\d{2}", v.strip()):
        h, m = map(int, v.strip().split(":"))
        return h + m / 60
    return None


def num(v):
    if isinstance(v, float):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return float(v.replace(",", ".").replace(" ", ""))
        except ValueError:
            return None
    return None


def main(path):
    sh = read_xlsx(path)
    ref = sh.get("Справочники", [])
    ops = {r[0] for r in ref[1:] if r and r[0]}
    workers = {r[2] for r in ref[1:] if len(r) > 2 and r[2]}
    positions = {r[3] for r in ref[1:] if len(r) > 3 and r[3]}
    codes = {str(r[1]).split()[0] for r in ref[1:] if len(r) > 1 and r[1]}

    rows, errors = [], []
    for i, r in enumerate(sh["Журнал"][1:], start=2):
        r = (r + [None] * len(COLS))[:len(COLS)]
        if not any(x not in (None, "") for x in r):
            continue
        e = dict(zip(COLS, r))
        e["row"] = i
        e["d"], e["s"], e["f"] = to_date(e["Дата"]), to_hours(e["Начало"]), to_hours(e["Конец"])
        e["h"] = num(e["Часы"])
        if e["h"] is None and e["s"] is not None and e["f"] is not None:
            e["h"] = e["f"] - e["s"]
        e["done"], e["bad"] = num(e["Сделано"]) or 0, num(e["Брак"]) or 0
        code = str(int(num(e["Код"]))) if num(e["Код"]) is not None else (str(e["Код"]).split()[0] if e["Код"] else "")
        e["code"] = code
        err = []
        if "пример" in str(e["Комментарий"] or "").lower():
            err.append("строка-пример — удалить")
        for k in ("Дата", "Позиция", "Операция", "Работник", "Начало", "Конец"):
            if e[k] in (None, ""):
                err.append(f"пусто: {k}")
        if e["Дата"] and not e["d"]:
            err.append("дата не распознана")
        if e["s"] is not None and e["f"] is not None and e["f"] <= e["s"]:
            err.append("конец раньше начала")
        if e["s"] is not None and e["f"] is not None and e["h"] is not None and abs((e["f"] - e["s"]) - e["h"]) > 0.05:
            err.append(f"часы {e['h']:.2f} ≠ конец−начало {e['f'] - e['s']:.2f}")
        if e["Операция"] and ops and e["Операция"] not in ops:
            err.append(f"операция не из справочника: {e['Операция']}")
        if e["Работник"] and workers and e["Работник"] not in workers:
            err.append(f"работник не из справочника: {e['Работник']}")
        if e["Позиция"] and positions and e["Позиция"] not in positions:
            err.append(f"позиция не из справочника: {e['Позиция']}")
        if e["bad"] > 0 and not code:
            err.append("брак без кода проблемы")
        if code and codes and code not in codes:
            err.append(f"неизвестный код проблемы: {code}")
        if code and not e["Комментарий"]:
            err.append("код проблемы без комментария")
        if err:
            errors.append((i, err))
        rows.append(e)

    # нахлёсты: один работник, один день, пересекающиеся интервалы
    by_wd = defaultdict(list)
    for e in rows:
        if e["d"] and e["s"] is not None and e["f"] is not None and e["Работник"]:
            by_wd[(e["Работник"], e["d"])].append(e)
    for (w, d), es in by_wd.items():
        es.sort(key=lambda x: x["s"])
        for a, b in zip(es, es[1:]):
            if b["s"] < a["f"]:
                errors.append((b["row"], [f"нахлёст со строкой {a['row']} ({w}, {d:%d.%m})"]))
        tot = sum(x["h"] or 0 for x in es)
        if tot > MAX_DAY_H:
            errors.append((es[0]["row"], [f"{w} {d:%d.%m}: {tot:.1f} ч за день"]))

    def agg(key):
        out = defaultdict(lambda: [0.0, 0.0, 0.0])
        for e in rows:
            k = e[key] or "—"
            out[k][0] += e["h"] or 0
            out[k][1] += e["done"]
            out[k][2] += e["bad"]
        return sorted(out.items(), key=lambda kv: -kv[1][0])

    real = [e for e in rows if "пример" not in str(e["Комментарий"] or "").lower()]
    days = sorted({e["d"] for e in real if e["d"]})
    print(f"Файл: {path}")
    print(f"Строк журнала: {len(rows)} (без примеров: {len(real)}); дней с записями: {len(days)}"
          + (f", {days[0]:%d.%m}–{days[-1]:%d.%m}" if days else ""))
    print(f"Всего часов: {sum(e['h'] or 0 for e in rows):.1f}")
    for title, key in (("Позиция", "Позиция"), ("Операция", "Операция"), ("Работник", "Работник")):
        print(f"\n{title:<22} {'ч':>6} {'шт':>5} {'брак':>5}")
        for k, (h, dn, bd) in agg(key):
            print(f"{str(k):<22} {h:6.1f} {dn:5.0f} {bd:5.0f}")
    probs = defaultdict(list)
    for e in rows:
        if e["code"]:
            probs[e["code"]].append(e)
    print("\nПроблемы по кодам:" + (" нет" if not probs else ""))
    for c, es in sorted(probs.items()):
        print(f"  код {c}: {len(es)} записей, {sum(x['h'] or 0 for x in es):.1f} ч — "
              + "; ".join(f"стр.{x['row']} {x['Позиция']} {x['Комментарий'] or ''}".strip() for x in es[:5]))
    print("\nОшибки заполнения:" + (" нет" if not errors else f" {len(errors)}"))
    for row, err in sorted(errors):
        print(f"  стр. {row}: " + "; ".join(err))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
