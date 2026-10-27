"""1С:КА, новая база EtlonKA26 — заказ клиента через OData для счёта из бота КП.

Что можно (решение 21.10.2026 в паспорте): создать и провести «Заказ клиента» ООО «90 ГРАДУСОВ».
Менять существующие документы и справочники и удалять — нельзя; этот модуль только читает и делает POST нового заказа.
Новый заказ — копия последнего проведённого заказа организации (шаблон): склад, соглашение, валюта, вид операции
и прочие обязательные поля берутся оттуда, а клиент, договор, сумма и дата — свои.

Ключи в .env: ONEC_USER, ONEC_PASSWORD (учётка Екатерины, решение 27.10.2026), необязательно ONEC_ODATA_URL, ONEC_TEMPLATE_ORDER.

    .venv/bin/python -m app.onec.odata probe              # что опубликовано, организация, шаблон
    .venv/bin/python -m app.onec.odata find ИНН [КПП]     # клиент и договор
    .venv/bin/python -m app.onec.odata draft ИНН СУММА    # заказ, который будет создан (без записи в 1С)
"""
import base64
import json
import re
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
URL = "https://1c.akno.one/EtlonKA26/odata/standard.odata"
ORG_INN = "7838125272"
ITEM = "Изготовление конструкции из металла"   # номенклатура в заказах 90 ГРАДУСОВ (инструкция 16)
EMPTY = "00000000-0000-0000-0000-000000000000"
NEEDED = ["Catalog_Организации", "Catalog_Контрагенты", "Catalog_Партнеры", "Catalog_ДоговорыКонтрагентов",
          "Catalog_Номенклатура", "Document_ЗаказКлиента"]
# поля шаблона, которые относятся к его клиенту — в новом заказе очищаются
CLIENT_FIELDS = re.compile(r"Контакт|БанковскийСчетКонтрагента|Грузополучатель|Адрес|ДокументОснование|Основание|"
                           r"Комментарий|НомерПоДаннымКлиента|ДатаПоДаннымКлиента|Сделка_Key|ТочкаДоставки")
# поля, которые 1С заводит на каждый заказ сама (в ГРЦБ-000007 — ссылки на него же): очищаются, 1С заполнит при записи
OWN_FIELDS = {"Назначение_Key", "ОбъектРасчетов_Key", "ИдентификаторПлатежа", "НазначениеПлатежа"}


class OneCError(Exception):
    pass


def configured(env):
    return bool(env.get("ONEC_USER") and env.get("ONEC_PASSWORD"))


def print_number(number):
    """Номер заказа 1С -> номер в печатной форме счёта: «ГРЦБ-000138» -> «ЦБ-138».
    ДОПУЩЕНИЕ: по паре ГРЦБ-000138 / «Счет на оплату № ЦБ-138» из старой базы (префикс организации «ГР» не печатается)."""
    pre, _, num = number.partition("-")
    if not num:
        return number
    return f"{pre[2:] if pre.startswith('ГР') else pre}-{int(num) if num.isdigit() else num}"


class OneC:
    def __init__(self, env):
        if not configured(env):
            raise OneCError("нет доступа к 1С: ONEC_USER / ONEC_PASSWORD в .env")
        self.url = env.get("ONEC_ODATA_URL", URL).rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{env['ONEC_USER']}:{env['ONEC_PASSWORD']}".encode()).decode()
        self.template_number = env.get("ONEC_TEMPLATE_ORDER", "")
        self._org = None

    def _req(self, method, path, params=None, body=None):
        q = {"$format": "json", **(params or {})}
        path = urllib.parse.quote(path, safe="/()=,'")
        url = f"{self.url}/{path}?{urllib.parse.urlencode(q, quote_via=urllib.parse.quote)}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": self.auth, "Accept": "application/json",
                                              "Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="ignore")
            try:
                text = json.loads(text)["odata.error"]["message"]["value"]
            except Exception:
                text = text[:300]
            raise OneCError(f"1С {method} {path}: HTTP {e.code}: {text}")
        except (urllib.error.URLError, OSError) as e:
            raise OneCError(f"1С недоступна: {e}")
        return json.loads(raw) if raw else {}

    def get(self, path, **params):
        return self._req("GET", path, params)

    def list(self, entity, flt, top=20, **params):
        return self.get(entity, **{"$filter": flt, "$top": str(top), **params}).get("value", [])

    def entity_sets(self):
        req = urllib.request.Request(f"{self.url}/$metadata", headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=60) as r:
            xml = r.read().decode()
        return set(re.findall(r'<EntitySet Name="([^"]+)"', xml))

    def org(self):
        if not self._org:
            rows = self.list("Catalog_Организации", f"ИНН eq '{ORG_INN}'")
            if not rows:
                raise OneCError(f"в 1С нет организации с ИНН {ORG_INN}")
            self._org = rows[0]
        return self._org

    def find_customer(self, inn="", kpp="", name=""):
        """Контрагент по ИНН (и КПП, если у ИНН несколько), для физлица — по наименованию."""
        if inn:
            rows = [r for r in self.list("Catalog_Контрагенты", f"ИНН eq '{inn}' and DeletionMark eq false")]
            if kpp and len(rows) > 1:
                rows = [r for r in rows if r.get("КПП") == kpp] or rows
        elif name:
            rows = self.list("Catalog_Контрагенты", f"Description eq '{name.replace(chr(39), chr(39) * 2)}' and DeletionMark eq false")
        else:
            rows = []
        if len(rows) > 1:
            raise OneCError(f"в 1С несколько клиентов с ИНН {inn or name} — выберите в 1С вручную")
        return rows[0] if rows else None

    def find_contract(self, customer):
        org = self.org()["Ref_Key"]
        rows = self.list("Catalog_ДоговорыКонтрагентов",
                         f"Контрагент_Key eq guid'{customer['Ref_Key']}' and Организация_Key eq guid'{org}' and DeletionMark eq false")
        active = [r for r in rows if r.get("Статус") in ("Действует", None, "")] or rows
        if len(active) > 1:
            named = [r for r in active if r.get("Description", "").startswith("Договор 90")]
            active = named or active
        return sorted(active, key=lambda r: r.get("Дата", ""))[-1] if active else None

    def template(self):
        org = self.org()["Ref_Key"]
        flt = f"Организация_Key eq guid'{org}' and Posted eq true"
        if self.template_number:
            flt += f" and Number eq '{self.template_number}'"
        rows = self.list("Document_ЗаказКлиента", flt, top=1, **{"$orderby": "Date desc"})
        if not rows:
            raise OneCError("в 1С нет проведённого заказа клиента 90 ГРАДУСОВ — не с чего взять шаблон")
        return rows[0]

    def draft(self, customer, contract, total, comment=""):
        """Новый заказ из шаблона: клиент, договор, одна строка на всю сумму, дата — сейчас."""
        t = self.template()
        old = {t.get("Партнер_Key"), t.get("Контрагент_Key"), t.get("Договор_Key")} - {None, EMPTY}
        new = {"Партнер_Key": customer.get("Партнер_Key") or EMPTY, "Контрагент_Key": customer["Ref_Key"],
               "Договор_Key": contract["Ref_Key"]}
        d = {}
        for k, v in t.items():
            if "@" in k or k in ("Ref_Key", "DataVersion", "Number", "Posted", "DeletionMark", "Predefined"):
                continue
            if k.endswith("_Type"):
                pass
            elif CLIENT_FIELDS.search(k) or k in OWN_FIELDS:
                v = (EMPTY if k.endswith("_Key") else [] if isinstance(v, list)
                     else "0001-01-01T00:00:00" if isinstance(v, str) and re.match(r"\d{4}-\d\d-\d\dT", v)
                     else "" if isinstance(v, str) else v)
            elif isinstance(v, str) and v in old:
                v = EMPTY
            d[k] = v
        d.update(new)
        now = datetime.now().replace(microsecond=0).isoformat()
        today = now[:10] + "T00:00:00"
        d["Date"] = now
        if d.get("Согласован"):
            d["ДатаСогласования"] = today
        d["Комментарий"] = comment
        d["СуммаДокумента"] = total
        if not t.get("Товары"):
            raise OneCError("в шаблоне нет строк товаров")
        row = {k: v for k, v in t["Товары"][0].items() if "@" not in k and k != "Ref_Key"}
        row["ИдентификаторСтроки"] = str(uuid.uuid4())
        for k in list(row):
            if k.startswith("Сумма") or k.startswith("Процент"):
                row[k] = 0
        row.update({"LineNumber": "1", "Количество": 1, "КоличествоУпаковок": 1, "Цена": total, "Сумма": total,
                    "СуммаСНДС": total})
        d["Товары"] = [row]
        if t.get("ЭтапыГрафикаОплаты"):
            stages, left = [], total
            for i, s in enumerate(t["ЭтапыГрафикаОплаты"]):
                pct = s.get("ПроцентПлатежа") or 0
                part = left if i == len(t["ЭтапыГрафикаОплаты"]) - 1 else round(total * pct / 100, 2)
                left -= part
                s = {k: v for k, v in s.items() if "@" not in k and k != "Ref_Key"}
                if not s.get("ДатаПлатежа", "").startswith("0001"):
                    s["ДатаПлатежа"] = today
                stages.append({**s, "СуммаПлатежа": part})
            d["ЭтапыГрафикаОплаты"] = stages
        return d, t

    def create_order(self, customer, contract, total, comment=""):
        """Создать и провести заказ -> {number, date, ref, posted}. Непроведённый заказ остаётся в 1С черновиком."""
        body, _ = self.draft(customer, contract, total, comment)
        doc = self._req("POST", "Document_ЗаказКлиента", body=body)
        ref = doc["Ref_Key"]
        out = {"number": doc.get("Number", ""), "date": doc.get("Date", ""), "ref": ref, "posted": False}
        try:
            self._req("POST", f"Document_ЗаказКлиента(guid'{ref}')/Post", {"PostingModeOperational": "false"})
            out["posted"] = True
        except OneCError as e:
            out["error"] = str(e)
        saved = self.get(f"Document_ЗаказКлиента(guid'{ref}')", **{"$select": "Number,ИдентификаторПлатежа"})
        out["number"] = saved.get("Number") or out["number"]
        out["payment_id"] = saved.get("ИдентификаторПлатежа", "")
        try:
            key = body["Товары"][0]["Номенклатура_Key"]
            out["item"] = self.get(f"Catalog_Номенклатура(guid'{key}')", **{"$select": "Description"})["Description"].strip()
        except (OneCError, KeyError):
            out["item"] = ITEM
        return out


def main(argv):
    sys.path.insert(0, str(ROOT / "app" / "client_agent"))
    from agent import load_env
    c = OneC(load_env())
    cmd = argv[0] if argv else "probe"
    if cmd == "probe":
        sets = c.entity_sets()
        print("опубликовано наборов:", len(sets))
        for n in NEEDED:
            print(("  есть  " if n in sets else "  НЕТ   ") + n)
        org = c.org()
        print("организация:", org.get("Description"), org["Ref_Key"])
        t = c.template()
        print("шаблон:", t.get("Number"), t.get("Date"), "сумма", t.get("СуммаДокумента"))
        print("поля шаблона:", ", ".join(k for k in t if "@" not in k))
        print("поля строки товаров:", ", ".join(k for k in (t.get("Товары") or [{}])[0] if "@" not in k))
    elif cmd == "find":
        k = c.find_customer(argv[1], argv[2] if len(argv) > 2 else "")
        print("клиент:", k and (k.get("Description"), k["Ref_Key"]))
        print("договор:", k and (c.find_contract(k) or {}).get("Description"))
    elif cmd == "draft":
        k = c.find_customer(argv[1])
        if not k:
            raise OneCError("клиент не найден")
        dog = c.find_contract(k)
        if not dog:
            raise OneCError("договор не найден")
        body, t = c.draft(k, dog, float(argv[2]), "проверка, не записано")
        changed = {k2: (t.get(k2), v) for k2, v in body.items() if t.get(k2) != v and k2 not in ("Товары", "ЭтапыГрафикаОплаты")}
        print(json.dumps(changed, ensure_ascii=False, indent=1))
        print("товары:", json.dumps(body["Товары"], ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
