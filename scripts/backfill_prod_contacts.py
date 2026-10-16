"""Разовое заполнение контакта и Telegram в уже созданных производственных сделках (16.10.2026).

Пары берутся из поля продажной сделки «id сделки производства» (UF_CRM_1774429302636).
Переносится: контакты (crm.deal.contact.items) и TelegramId/Username, только если в производственной
сделке их нет. Новые сделки это делает робот «Создать заявку на производство» (с 16.10).

Запуск: python3 scripts/backfill_prod_contacts.py [--apply]   (без --apply — только показать)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inbound_leads import B24, load_env  # noqa: E402

PROD_ID = "UF_CRM_1774429302636"
TG_USER, TG_ID = "UF_CRM_69DD014378F08", "UF_CRM_69DD014382FF7"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    b24 = B24(load_env()["B24_LEADS_WEBHOOK"])

    sales = b24.call("crm.deal.list", {"filter": {"CATEGORY_ID": 0, f"!{PROD_ID}": False},
                                       "select": ["ID", "TITLE", PROD_ID, TG_USER, TG_ID]}) or []
    changed = skipped = 0
    for s in sales:
        pid = str(s.get(PROD_ID) or "").strip()
        if not pid.isdigit():
            continue
        prod = b24.call("crm.deal.get", {"id": pid})
        s_contacts = b24.call("crm.deal.contact.items.get", {"id": s["ID"]}) or []
        p_contacts = b24.call("crm.deal.contact.items.get", {"id": pid}) or []
        todo = []
        if s_contacts and not p_contacts:
            todo.append(f"контакты {[c['CONTACT_ID'] for c in s_contacts]}")
        fields = {}
        for f in (TG_USER, TG_ID):
            if s.get(f) and not prod.get(f):
                fields[f] = s[f]
        if fields:
            todo.append("Telegram")
        line = f"{s['ID']} → {pid} «{prod.get('TITLE', '')[:40]}»: "
        if not todo:
            skipped += 1
            print(line + ("в продаже нет контакта" if not s_contacts else "уже заполнено"))
            continue
        print(line + ", ".join(todo))
        if args.apply:
            if s_contacts and not p_contacts:
                items = [{"CONTACT_ID": int(c["CONTACT_ID"]), "SORT": c.get("SORT", 10),
                          "IS_PRIMARY": c.get("IS_PRIMARY", "N")} for c in s_contacts]
                b24.call("crm.deal.contact.items.set", {"id": pid, "items": items})
            if fields:
                b24.call("crm.deal.update", {"id": pid, "fields": fields})
        changed += 1
    print(f"\nИтого: {'изменено' if args.apply else 'к изменению'} {changed}, без изменений {skipped}")


if __name__ == "__main__":
    main()
