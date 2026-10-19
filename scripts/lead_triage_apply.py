"""Проставить в Б24 статусы лидов, утверждённые владельцем в data/leads/triage_<дата>.csv.

Колонка «Утвердить»: «да» — поставить предложенный код; код статуса (например 5, UC_VE9PFM) — поставить его;
пусто / «нет» — не трогать. Без --apply только показывает, что будет сделано.
Пишет через вебхук B24_AGENT_WEBHOOK только поле STATUS_ID (crm.lead.update). Журнал: data/leads/triage_applied.log.

  .venv/bin/python scripts/lead_triage_apply.py data/leads/triage_2026-10-19.csv [--apply] [--all-yes]
  --all-yes — считать «да» все строки с пустой колонкой (только если владелец утвердил таблицу целиком)
"""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "reports"))

from data import env_b24, load_env  # noqa: E402
from lead_triage import STATUSES  # noqa: E402

LOG = ROOT / "data" / "leads" / "triage_applied.log"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all-yes", action="store_true")
    args = ap.parse_args()
    plan = []
    with open(args.csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            ok = (r.get("Утвердить (да/нет/код)") or "").strip()
            if not ok and args.all_yes:
                ok = "да"
            if not ok or ok.lower() in ("нет", "no", "-"):
                continue
            code = r["Код"] if ok.lower() in ("да", "yes", "+") else ok
            if code not in STATUSES:
                print(f"лид {r['Лид']}: неизвестный код «{code}» — пропущен")
                continue
            plan.append((r["Лид"], r["Название"], r["Сейчас"], code))
    d = env_b24(load_env())
    changed = 0
    for lid, title, now_name, code in plan:
        cur = d.b24.call("crm.lead.get", {"id": lid})["result"]["STATUS_ID"]
        if cur == code:
            continue
        line = f"лид {lid} {title}: {STATUSES.get(cur, cur)} → {STATUSES[code]}"
        if not args.apply:
            print("[проверка] " + line)
            changed += 1
            continue
        d.b24.call("crm.lead.update", {"id": lid, "fields": {"STATUS_ID": code}})
        changed += 1
        print(line)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} {line}\n")
    print(f"{'изменено' if args.apply else 'будет изменено'}: {changed} (утверждено строк: {len(plan)})")


if __name__ == "__main__":
    main()
