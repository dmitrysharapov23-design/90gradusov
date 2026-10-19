"""Google Документ «Задачи по лидам» для Екатерины из разбора лидов (triage_<дата>[_filtered].csv).

Без --file-id создаёт новый документ на Drive владельца, с --file-id заменяет содержимое того же файла (ссылка не меняется).
Доступ к Drive — через app/kp/gdrive.py (OAuth владельца, только файлы этого приложения).

  .venv/bin/python scripts/lead_tasks_doc.py data/leads/triage_2026-10-19_filtered.csv [--file-id ID]
"""
import argparse
import csv
import html
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://90gradusov.bitrix24.ru/crm/lead/details/{}/"
e = html.escape


def bucket(r):
    a = r["Действие"].lower()
    push = r.get("Пушим", "да") or "да"
    if "создать сделку" in a:
        return "deal"
    if any(k in a for k in ("ответить", "дожать", "касани", "ждём", "ждем")):
        if push.startswith("нет"):
            return "decide" if push == "нет: сложно" else "skip"
        if "ответить" in a:
            return "reply"
        if "ждём" in a or "ждем" in a:
            return "wait"
        return "push"
    if "закрыть" in a:
        return "close"
    return "other"


SECTIONS = [
    ("reply", "1. Ответить клиенту — клиент ждёт", True),
    ("deal", "2. Создать сделку — клиент согласился или оплатил, сделки нет", True),
    ("push", "3. Дожать — простые заказы от 50 000 ₽", True),
    ("decide", "4. На решение Дмитрия — чек от 50 000 ₽, но может быть сложно", True),
    ("wait", "5. Ждём клиента — ничего не делать", False),
    ("skip", "6. Не пушим — меньше 50 000 ₽ или обклейка", False),
    ("close", "7. Закрыть — статус поставим после утверждения", False),
    ("other", "Прочее", False),
]


def money(r):
    s = r.get("Сумма, ₽") or ""
    if s:
        return f"{int(float(s)):,}".replace(",", " ") + " ₽"
    src = r.get("Откуда сумма") or ""
    if src.startswith("оценка ~"):
        return "оценка ~" + f"{int(float(src[8:])):,}".replace(",", " ") + " ₽"
    return ""


def build(rows, date):
    groups = {k: [] for k, _, _ in SECTIONS}
    for r in rows:
        groups[bucket(r)].append(r)
    out = ["<html><head><meta charset='utf-8'></head><body>",
           f"<h1>Задачи по лидам — {e(date)}</h1>",
           "<p>Разбор всех открытых лидов за 90 дней. Порядок — по срочности. Отмечайте сделанное: замените ☐ на ☑.</p>",
           "<p>Правило: дожимаем только простые заказы от 50 000 ₽. Чеки меньше 50 000 ₽, обклейку (металл на ЛДСП/МДФ, облицовка стен и фасадов, накладки) и сложное в реализации — не пушим.</p>",
           "<p>Черновики — заготовки: перед отправкой проверьте и поправьте. Цены и сроки в них только те, что уже были в переписке. "
           "Если все сообщения в переписке датированы 26.09 — это массовая загрузка в Битрикс, возможно, вы уже отвечали клиенту в другом канале.</p>"]
    out.append("<p><b>Итого:</b> " + e(", ".join(f"{t.split(' — ')[0][3:]}: {len(groups[k])}" for k, t, _ in SECTIONS if groups[k])) + ".</p>")
    for k, title, full in SECTIONS:
        g = groups[k]
        if not g:
            continue
        out.append(f"<h2>{e(title)} ({len(g)})</h2>")
        if not full:
            out.append("<ul>")
            for r in g:
                why = r.get("Пушим", "") if k == "skip" else ""
                waits = " Клиент ждёт ответа — достаточно коротко и вежливо закрыть." if k == "skip" and "ответить" in r["Действие"].lower() else ""
                m = money(r)
                out.append(f"<li>☐ <a href='{URL.format(r['Лид'])}'>Лид {r['Лид']}</a> — {e(r['Название'])}"
                           f"{' · ' + e(m) if m else ''}{' · ' + e(why) if why else ''}: "
                           f"{e(r.get('Оценка заказа') or r['Почему'])}{e(waits)}</li>")
            out.append("</ul>")
            continue
        for r in g:
            m = money(r)
            out.append(f"<h3>☐ Лид {r['Лид']} — {e(r['Название'])}</h3>")
            out.append(f"<p><a href='{URL.format(r['Лид'])}'>Открыть в Битриксе</a> · статус: {e(r['Предлагаю'])}"
                       f"{' · ' + e(m) if m else ''} · {e(r['Действие'])}</p>")
            out.append(f"<p><i>{e(r['Почему'])}</i></p>")
            if k == "decide":
                out.append(f"<p><b>Почему под вопросом:</b> {e(r.get('Оценка заказа', ''))}</p>")
                out.append("<p><b>Решение Дмитрия:</b> ☐ пушим ☐ не пушим</p>")
            if r["Черновик"]:
                for para in r["Черновик"].split("\n"):
                    if para.strip():
                        out.append(f"<p style='margin-left:24px'>{e(para)}</p>")
    out.append("</body></html>")
    return "\n".join(out), {t: len(groups[k]) for k, t, _ in SECTIONS if groups[k]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--file-id")
    ap.add_argument("--date", default="")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig"), delimiter=";"))
    date = args.date or Path(args.csv).stem.split("_")[1].split("-")[::-1]
    date = ".".join(date) if isinstance(date, list) else date
    text, counts = build(rows, date)
    sys.path.insert(0, str(ROOT / "app" / "kp"))
    from gdrive import service
    from googleapiclient.http import MediaFileUpload
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(text)
    media = MediaFileUpload(f.name, mimetype="text/html", resumable=True)
    svc = service()
    if args.file_id:
        res = svc.files().update(fileId=args.file_id, media_body=media, fields="id,webViewLink").execute()
    else:
        res = svc.files().create(body={"name": f"Задачи по лидам — {date}", "mimeType": "application/vnd.google-apps.document"},
                                 media_body=media, fields="id,webViewLink").execute()
    Path(f.name).unlink()
    print(res["id"], res["webViewLink"])
    for t, n in counts.items():
        print(f"  {t}: {n}")


if __name__ == "__main__":
    main()
