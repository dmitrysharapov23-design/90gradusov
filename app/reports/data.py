"""Данные для отчётов: лиды, сделки, переписка Wazzup из Б24. Только чтение (вебхук B24_AGENT_WEBHOOK)."""
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "client_agent"))

import chat  # noqa: E402  разбор переписки Wazzup и рабочие часы
from b24 import B24  # noqa: E402

MSK = chat.MSK
MANAGER_ID = "13"  # Екатерина Вульпя
WAZZUP_PREFIX = "WZ"
# Переписка до этого момента загружена в Б24 разом 26.09 19:40–20:30: время сообщений недостоверно (паспорт)
CHAT_RELIABLE_FROM = datetime(2026, 9, 26, 20, 31, tzinfo=MSK)

LEAD_CLOSED = {"CONVERTED", "JUNK", "1", "2", "3", "4", "5", "6", "7", "8"}
LEAD_LOST = {"JUNK", "1", "2", "3", "4", "5", "7"}   # отказ с причиной
LEAD_NOT_CLIENT = {"6", "8"}                          # спам, дубль / не клиент


def load_env() -> dict:
    """Ключи из .env (значения не выводить)."""
    import os
    env, path = {}, ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return {**env, **os.environ}


def env_b24(env) -> "Data":
    if not env.get("B24_AGENT_WEBHOOK"):
        raise SystemExit("Нет B24_AGENT_WEBHOOK в .env")
    return Data(B24(env["B24_AGENT_WEBHOOK"]))


def parse_dt(s):
    return datetime.fromisoformat(s) if s else None


class Data:
    def __init__(self, b24: B24):
        self.b24 = b24
        self._dicts = None

    # ---------- общее ----------

    def all(self, method: str, filter_: dict, select: list, order: dict = None, limit: int = 2000) -> list:
        """Все страницы списка (Б24 отдаёт по 50)."""
        out, start = [], 0
        while True:
            params = {"filter": filter_, "select": select, "start": start}
            if order:
                params["order"] = order
            r = self.b24.call(method, params)
            out += r.get("result") or []
            if "next" not in r or len(out) >= limit:
                return out
            start = r["next"]

    def dicts(self) -> dict:
        """Названия: статусы лидов, источники, стадии сделок."""
        if self._dicts is None:
            d = {}
            for eid in ("STATUS", "SOURCE", "DEAL_STAGE", "DEAL_STAGE_3"):
                for x in self.b24.call("crm.status.list", {"filter": {"ENTITY_ID": eid}})["result"]:
                    d.setdefault(eid, {})[x["STATUS_ID"]] = x["NAME"]
            d["SOURCE"]["WZ"] = "Telegram (Wazzup)"
            self._dicts = d
        return self._dicts

    def source_name(self, sid: str) -> str:
        if (sid or "").startswith(WAZZUP_PREFIX):
            return "Telegram (Wazzup)"
        return self.dicts()["SOURCE"].get(sid or "", sid or "не указан")

    # ---------- лиды и сделки ----------

    def leads_created(self, a: datetime, b: datetime) -> list:
        return self.all("crm.lead.list", {">=DATE_CREATE": a.isoformat(), "<DATE_CREATE": b.isoformat()},
                        ["ID", "TITLE", "STATUS_ID", "SOURCE_ID", "DATE_CREATE", "ASSIGNED_BY_ID", "OPPORTUNITY"],
                        {"ID": "ASC"})

    def leads_open(self, days: int = 45) -> list:
        since = datetime.now(MSK) - timedelta(days=days)
        rows = self.all("crm.lead.list", {"ASSIGNED_BY_ID": MANAGER_ID, ">=DATE_CREATE": since.isoformat(),
                                          "!@STATUS_ID": sorted(LEAD_CLOSED - {"CONVERTED"})},
                        ["ID", "TITLE", "STATUS_ID", "SOURCE_ID", "DATE_CREATE"], {"ID": "ASC"})
        return [r for r in rows if (r.get("SOURCE_ID") or "").startswith(WAZZUP_PREFIX)]

    def deals(self, filter_: dict) -> list:
        return self.all("crm.deal.list", filter_,
                        ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "OPPORTUNITY", "LEAD_ID", "CONTACT_ID",
                         "DATE_CREATE", "MOVED_TIME", "CLOSEDATE", "ASSIGNED_BY_ID"], {"ID": "ASC"})

    def stage_moves(self, a: datetime, b: datetime, stage_ids: list) -> list:
        """Переходы сделок в стадии за период (история стадий)."""
        out, start = [], 0
        while True:
            r = self.b24.call("crm.stagehistory.list", {
                "entityTypeId": 2, "start": start, "order": {"ID": "ASC"},
                "filter": {">=CREATED_TIME": a.isoformat(), "<CREATED_TIME": b.isoformat(), "@STAGE_ID": stage_ids},
                "select": ["OWNER_ID", "STAGE_ID", "CREATED_TIME", "CATEGORY_ID"],
            })
            out += (r.get("result") or {}).get("items") or []
            if "next" not in r:
                return out
            start = r["next"]

    # ---------- переписка ----------

    def comments(self, entity: str, ids: list, full: bool = False) -> dict:
        """{id: [комментарии по возрастанию]}. full=True — все страницы (для саммари клиента), иначе первые 50."""
        ids = [str(i) for i in ids]
        if full:
            return {i: self.all("crm.timeline.comment.list", {"ENTITY_TYPE": entity, "ENTITY_ID": i},
                                ["ID", "CREATED", "COMMENT", "AUTHOR_ID"], {"CREATED": "ASC"}) for i in ids}
        out = {}
        for k in range(0, len(ids), 50):
            chunk = ids[k:k + 50]
            cmd = {f"c{i}": "crm.timeline.comment.list?" + urllib.parse.urlencode({
                "filter[ENTITY_TYPE]": entity, "filter[ENTITY_ID]": i, "order[CREATED]": "ASC"}) for i in chunk}
            res = self.b24.call("batch", {"halt": 0, "cmd": cmd})["result"]
            for key, items in (res.get("result") or {}).items():
                out[key[1:]] = items or []
        return out

    def tails(self, ids: list, n: int = 10) -> dict:
        """Последние n комментариев по лидам (по убыванию → разворачиваем)."""
        ids = [str(i) for i in ids]
        out = {}
        for k in range(0, len(ids), 50):
            chunk = ids[k:k + 50]
            cmd = {f"c{i}": "crm.timeline.comment.list?" + urllib.parse.urlencode({
                "filter[ENTITY_TYPE]": "lead", "filter[ENTITY_ID]": i, "order[CREATED]": "DESC"}) for i in chunk}
            res = self.b24.call("batch", {"halt": 0, "cmd": cmd})["result"]
            for key, items in (res.get("result") or {}).items():
                out[key[1:]] = list(reversed((items or [])[:n]))
        return out


def messages(comments: list) -> list:
    return chat.parse(comments)


def work_hours(a, b) -> float:
    return chat.work_hours(a, b)


def work_days_between(a: datetime, b: datetime) -> int:
    """Полные рабочие дни (пн–пт) после даты a до даты b включительно."""
    d, n = a.astimezone(MSK).date(), 0
    end = b.astimezone(MSK).date()
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n
