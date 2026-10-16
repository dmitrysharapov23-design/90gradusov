"""Минимальный клиент Б24 через входящий вебхук (B24_AGENT_WEBHOOK в .env).

В режиме «тень» используется только чтение: лиды, сделки, комментарии таймлайна.
"""
import json
import time
import urllib.parse
import urllib.request


class B24:
    def __init__(self, webhook: str):
        self.base = webhook.rstrip("/") + "/"

    def call(self, method: str, params: dict) -> dict:
        req = urllib.request.Request(
            self.base + method + ".json",
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                # 503 — превышен лимит запросов портала, ждём и повторяем
                if e.code == 503 and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        if "error" in data:
            raise RuntimeError(f"Б24 {method}: {data['error']} {data.get('error_description', '')}")
        return data

    def list(self, method: str, filter_: dict, select: list, order: dict = None) -> list:
        params = {"filter": filter_, "select": select}
        if order:
            params["order"] = order
        return self.call(method, params)["result"]

    def lead_comments(self, lead_ids: list) -> dict:
        """Комментарии таймлайна по лидам пачками по 50 (метод batch). {lead_id: [comment, ...]}"""
        out = {}
        for i in range(0, len(lead_ids), 50):
            chunk = lead_ids[i:i + 50]
            cmd = {
                f"c{lid}": "crm.timeline.comment.list?" + urllib.parse.urlencode({
                    "filter[ENTITY_TYPE]": "lead",
                    "filter[ENTITY_ID]": lid,
                    "order[CREATED]": "ASC",
                })
                for lid in chunk
            }
            res = self.call("batch", {"halt": 0, "cmd": cmd})["result"]
            errors = res.get("result_error") or {}
            if errors:
                raise RuntimeError(f"Б24 batch: {errors}")
            for key, items in (res.get("result") or {}).items():
                out[key[1:]] = items or []
        return out
