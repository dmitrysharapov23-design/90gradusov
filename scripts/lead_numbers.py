"""Номер лида на виду (просьба Екатерины 27.10.2026: «не понимаем, где смотреть номер лида»).

Лид:   номер в начале названия — «№1359 · Marina» (видно в карточке, канбане, поиске Б24 по «1359»).
Сделка: поле «Номер лида» (UF_CRM_LEAD_NO) в верхнем блоке карточки — у продажной сделки и у её производственной.
        Название сделки не трогаем: бот КП берёт его как имя клиента в КП.

  python3 scripts/lead_numbers.py                 # план: сколько и что поменяется
  python3 scripts/lead_numbers.py --apply         # все лиды и сделки (разово)
  python3 scripts/lead_numbers.py --apply --days 3  # только свежие (так зовёт inbound_leads.py каждые 10 мин)
Повторный запуск ничего не портит: пронумерованные лиды и заполненные сделки пропускаются.
"""
import argparse
import re
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inbound_leads import B24, load_env  # noqa: E402

FIELD = "UF_CRM_LEAD_NO"
PROD_ID = "UF_CRM_1774429302636"     # «id сделки производства» в продажной сделке
CATEGORIES = (0, 3)                  # продажи, производство
NUMBERED = re.compile(r"^№\d+ · ")


def title_with_no(lead):
    t = NUMBERED.sub("", lead.get("TITLE") or "").strip()
    if not t or re.fullmatch(r"Лид #\d+", t):  # заглушка Б24 — номер и так будет в начале
        t = lead.get("NAME") or "Без имени"
    return f"№{lead['ID']} · {t}"


def all_items(b24, method, params):
    """Постранично (по 50), с фильтром по ID — без медленного start на больших выборках."""
    out, last = [], 0
    while True:
        p = {**params, "order": {"ID": "ASC"}, "filter": {**params.get("filter", {}), ">ID": last}, "start": -1}
        page = b24.call(method, p) or []
        out += page
        if len(page) < 50:
            return out
        last = int(page[-1]["ID"])


def batch(b24, cmds):
    """[(метод, параметры)] пачками по 50 через batch."""
    for i in range(0, len(cmds), 50):
        chunk = {f"c{j}": f"{m}?{urllib.parse.urlencode(flat(p))}" for j, (m, p) in enumerate(cmds[i:i + 50])}
        res = b24.call("batch", {"halt": 0, "cmd": chunk})
        errors = (res or {}).get("result_error") or {}
        if errors:
            raise RuntimeError(f"batch: {errors}")


def flat(p, prefix=""):
    out = {}
    for k, v in p.items():
        key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            out.update(flat(v, key))
        else:
            out[key] = v
    return out


def ensure_field(b24, apply):
    exists = bool(b24.call("crm.deal.userfield.list", {"filter": {"FIELD_NAME": FIELD}}))
    if apply and not exists:
        label = {"ru": "Номер лида", "en": "Lead No."}
        b24.call("crm.deal.userfield.add", {"fields": {"FIELD_NAME": "LEAD_NO", "USER_TYPE_ID": "string", "XML_ID": "LEAD_NO",
                                                       "EDIT_FORM_LABEL": label, "LIST_COLUMN_LABEL": label,
                                                       "LIST_FILTER_LABEL": label, "SHOW_FILTER": "E"}})
    if apply:
        for cat in CATEGORIES:   # в общий вид карточки (для всех), сразу под названием
            cfg = b24.call("crm.deal.details.configuration.get", {"scope": "C", "extras": {"dealCategoryId": cat}})
            for s in cfg:  # пустые ссылки на удалённые поля — Б24 сам не даёт сохранить с ними
                s["elements"] = [e for e in s["elements"] if e.get("name")]
            els = cfg[0]["elements"]
            if all(e["name"] != FIELD for s in cfg for e in s["elements"]):
                pos = next((i + 1 for i, e in enumerate(els) if e["name"] == "TITLE"), 0)
                els.insert(pos, {"name": FIELD, "optionFlags": "0"})
                b24.call("crm.deal.details.configuration.set", {"scope": "C", "extras": {"dealCategoryId": cat}, "data": cfg})
    return not exists


def by_title(b24, since, skip):
    """Производственные без номера, у которых продажная не указала «id сделки производства» (так у большинства старых):
    берём продажную из лида с тем же названием — ровно одну, и контакт не должен расходиться."""
    prods = [p for p in all_items(b24, "crm.deal.list", {"filter": {**since, "CATEGORY_ID": 3},
                                                        "select": ["ID", "TITLE", "CONTACT_ID", FIELD]})
             if not p.get(FIELD) and p["ID"] not in skip]
    if not prods:
        return []
    sales = {}
    for s in all_items(b24, "crm.deal.list", {"filter": {"CATEGORY_ID": 0, ">LEAD_ID": 0},
                                              "select": ["ID", "TITLE", "LEAD_ID", "CONTACT_ID"]}):
        sales.setdefault((s["TITLE"] or "").strip().lower(), []).append(s)
    cmds = []
    for p in prods:
        same = sales.get((p["TITLE"] or "").strip().lower(), [])
        if len(same) != 1:
            continue
        s = same[0]
        if str(p.get("CONTACT_ID") or "0") not in ("0", str(s.get("CONTACT_ID") or "0")) and str(s.get("CONTACT_ID") or "0") != "0":
            continue
        cmds.append(("crm.deal.update", {"id": p["ID"], "fields": {FIELD: str(s["LEAD_ID"])}}))
    return cmds


def main(argv=None, b24=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, help="только созданные за N дней")
    args = ap.parse_args(argv)
    b24 = b24 or B24(load_env()["B24_LEADS_WEBHOOK"])
    since = {">=DATE_CREATE": (date.today() - timedelta(days=args.days)).isoformat()} if args.days else {}

    leads = all_items(b24, "crm.lead.list", {"filter": since, "select": ["ID", "TITLE", "NAME"]})
    lead_cmds = [("crm.lead.update", {"id": l["ID"], "fields": {"TITLE": title_with_no(l)},
                                      "params": {"REGISTER_SONET_EVENT": "N"}})
                 for l in leads if not NUMBERED.match(l.get("TITLE") or "")]

    new_field = ensure_field(b24, args.apply and not args.days)  # поле и карточку — только при полном прогоне
    deals = all_items(b24, "crm.deal.list", {"filter": {**since, "CATEGORY_ID": 0, ">LEAD_ID": 0},
                                              "select": ["ID", "LEAD_ID", PROD_ID] + ([] if new_field and not args.apply else [FIELD])})
    deal_cmds = []
    for d in deals:
        no = str(d["LEAD_ID"])
        if str(d.get(FIELD) or "") != no:
            deal_cmds.append(("crm.deal.update", {"id": d["ID"], "fields": {FIELD: no}}))
        prod = str(d.get(PROD_ID) or "")
        if prod.isdigit():  # производственную проверяем отдельно: её могли создать позже продажной
            deal_cmds.append(("crm.deal.update", {"id": prod, "fields": {FIELD: no}, "_check": True}))
    if deal_cmds and any(c[1].get("_check") for c in deal_cmds):
        prod_ids = [c[1]["id"] for c in deal_cmds if c[1].get("_check")]
        have = {}
        for i in range(0, len(prod_ids), 50):
            for p in b24.call("crm.deal.list", {"filter": {"ID": prod_ids[i:i + 50]},
                                                 "select": ["ID"] + ([] if new_field and not args.apply else [FIELD])}) or []:
                have[p["ID"]] = str(p.get(FIELD) or "")
        deal_cmds = [c for c in deal_cmds if not c[1].pop("_check", False)
                     or (c[1]["id"] in have and have[c[1]["id"]] != c[1]["fields"][FIELD])]
    deal_cmds += by_title(b24, since, {c[1]["id"] for c in deal_cmds}) if not (new_field and not args.apply) else []

    if args.days and not (lead_cmds or deal_cmds):
        return 0
    print(f"Лидов: {len(leads)}, переименовать: {len(lead_cmds)}; поле в сделках: {'создать' if new_field else 'есть'}; "
          f"сделок заполнить: {len(deal_cmds)}")
    for m, p in (lead_cmds[:3] + deal_cmds[:3]):
        print("  ", m, p["id"], p["fields"])
    if not args.apply:
        print("Только план. Для выполнения — --apply.")
        return
    batch(b24, lead_cmds)
    batch(b24, deal_cmds)
    print("Готово.")
    return len(lead_cmds) + len(deal_cmds)


if __name__ == "__main__":
    main()
