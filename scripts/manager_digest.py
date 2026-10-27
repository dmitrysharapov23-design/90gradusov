"""Утренний план для менеджера (Екатерина) в Telegram: кому ответить, кого дожать, какие лиды без статуса.

Считает код по Б24 (только чтение), без Claude. Отправляет ботом сводки (TG_BOT_TOKEN) в чат менеджера.
ID чата менеджера — data/reports/digest_config.json {"manager_chat_id": ...} (не в git).
Правила (эталон 06, решения 19.10): ответ ≤ 2 раб. ч; после цены/КП касания на 2-й и 5-й раб. день, на 10-й закрыть с причиной;
не пушим < 50 000 ₽, обклейку и сложное — такие лиды уже закрыты статусами «Маленький чек» / «Сложно».

  .venv/bin/python scripts/manager_digest.py [--dry-run] [--to owner]
Запуск: launchd ru.90gradusov.manager-digest, пн–пт 10:00.
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "reports"))

from data import CHAT_RELIABLE_FROM, MSK, env_b24, load_env, messages, work_days_between, work_hours  # noqa: E402
from reports import RESPONSE_NORM_H, has_price, needs_reply, one_line  # noqa: E402

CONFIG = ROOT / "data" / "reports" / "digest_config.json"
LOG = ROOT / "data" / "reports" / "digest.log"
URL = "https://90gradusov.bitrix24.ru/crm/lead/details/{}/"


POLITE = re.compile(r"спасибо|благодар|хорошо|поняла?\b|понятно|договорились|вернусь|отлично|супер|^ок\b|^ok\b|хорошо сп", re.I)
WAITS_FOR_US = re.compile(r"(жд[уё]|ожида)\w*.{0,30}(расч|просч|кп|предлож|ответ|цен|стоимост)", re.I)
FILES = re.compile(r"\[(файл|изображение|видео|аудиосообщение)\]", re.I)


def wants_reply(text: str) -> bool:
    """Нужен ли ответ на последнее сообщение клиента. Вежливое завершение — нет; ждёт расчёт, вопрос, файл — да."""
    t = (text or "").strip()
    if WAITS_FOR_US.search(t) or FILES.search(t) or "?" in t:
        return True
    if len(t) <= 80 and POLITE.search(t):
        return False
    return needs_reply(t)


def build(d, now):
    leads = d.leads_open()
    tails = d.tails([l["ID"] for l in leads], n=20)
    wait, touch1, touch2, close, fresh = [], [], [], [], []
    for l in leads:
        msgs = messages(tails.get(l["ID"], []))
        if l["STATUS_ID"] == "NEW" and l["DATE_CREATE"] < now.strftime("%Y-%m-%d"):
            fresh.append(l)
        if not msgs:
            continue
        last = msgs[-1]
        if last["who"] == "c" and wants_reply(last["text"]):
            first = last
            for m in reversed(msgs):
                if m["who"] == "m":
                    break
                first = m
            if first["time"] >= CHAT_RELIABLE_FROM and work_hours(first["time"], now) > RESPONSE_NORM_H:
                wait.append((work_hours(first["time"], now), l, first))
            continue
        if l["STATUS_ID"] == "CONVERTED":
            continue
        kp_i = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i]["who"] == "m" and has_price(msgs[i]["text"])), None)
        if kp_i is None or any(m["who"] == "c" for m in msgs[kp_i + 1:]):
            continue
        after = sum(1 for m in msgs[kp_i + 1:] if m["who"] == "m")   # касаний после КП
        wd = work_days_between(msgs[kp_i]["time"], now)
        if wd >= 10:
            close.append((wd, l))
        elif after == 0 and wd >= 2:
            touch1.append((wd, l))
        elif after == 1 and wd >= 5:
            touch2.append((wd, l))

    out = [f"☀️ Доброе утро! План на {now:%d.%m}" if now.hour < 12 else f"📋 План на {now:%d.%m %H:%M}", ""]
    if wait:
        out.append(f"🔴 Ответить — клиент ждёт ({len(wait)}):")
        for h, l, m in sorted(wait, key=lambda x: -x[0]):
            out.append(f"• {l['TITLE']} — ждёт {h:.0f} раб. ч: «{one_line(m['text'], 50)}»\n  {URL.format(l['ID'])}")
        out.append("")
    if touch1:
        out.append(f"🟠 Первое касание после КП ({len(touch1)}):")
        out += [f"• {l['TITLE']} — {wd} раб. дн. тишины\n  {URL.format(l['ID'])}" for wd, l in sorted(touch1, key=lambda x: -x[0])]
        out.append("")
    if touch2:
        out.append(f"🟠 Второе касание ({len(touch2)}):")
        out += [f"• {l['TITLE']} — {wd} раб. дн.\n  {URL.format(l['ID'])}" for wd, l in sorted(touch2, key=lambda x: -x[0])]
        out.append("")
    if close:
        out.append(f"⚪ Закрыть с причиной — молчат 10+ раб. дн. ({len(close)}):")
        out += [f"• {l['TITLE']}  {URL.format(l['ID'])}" for wd, l in sorted(close, key=lambda x: -x[0])]
        out.append("")
    if fresh:
        out.append(f"🆕 Без статуса («Не обработан») — поставить статус ({len(fresh)}):")
        out += [f"• {l['TITLE']}  {URL.format(l['ID'])}" for l in fresh]
        out.append("")
    if len(out) == 2:
        out.append("Срочных дел по лидам нет 👍")
    else:
        out.append("Дожимаем только простые заказы от 50 000 ₽, без обклейки.")
    counts = {"ответить": len(wait), "касание1": len(touch1), "касание2": len(touch2), "закрыть": len(close), "без статуса": len(fresh)}
    return "\n".join(out), counts


def send(token, chat, text):
    chunk = ""
    for line in text.split("\n") + [None]:
        if line is None or len(chunk) + len(line) + 1 > 3900:
            if chunk.strip():
                req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                             data=json.dumps({"chat_id": chat, "text": chunk, "disable_web_page_preview": True}).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    if not json.load(r).get("ok"):
                        raise RuntimeError("Telegram не принял сообщение")
            chunk = ""
        if line is not None:
            chunk += line + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="только напечатать")
    ap.add_argument("--to", choices=["manager", "owner"], default="manager")
    args = ap.parse_args()
    now = datetime.now(MSK)
    if now.weekday() >= 5 and not args.dry_run:
        return
    env = load_env()
    text, counts = build(env_b24(env), now)
    if args.dry_run:
        print(text)
        print(counts)
        return
    chat = env["TG_CHAT_ID"] if args.to == "owner" else json.loads(CONFIG.read_text())["manager_chat_id"]
    send(env["TG_BOT_TOKEN"], chat, text)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} → {args.to}: {counts}\n")
    print("отправлено", counts)


if __name__ == "__main__":
    main()
