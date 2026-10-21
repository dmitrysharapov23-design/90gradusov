"""Боевой режим агента (--live-until) для лидов из реестра скрипта заявок. Тест с 21.10.2026 до 23.10.2026 19:00.

Только подделанные данные: --offline --fake-llm, отправка и сообщения Екатерине пишутся в live_offline.json.
Запуск: .venv/bin/python -m unittest app/client_agent/test_live.py -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_bridge import GREETING, REG_ENTRY, WZ, our_lead, wz  # noqa: E402

NOW = "2026-10-22T12:00:00+03:00"      # четверг, рабочее время
UNTIL = "2026-10-23T19:00:00+03:00"


def run(fixture, now=NOW, state=None, until=UNTIL):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        if state:
            (tmp / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(HERE / "agent.py"), "--offline", str(tmp / "fixture.json"), "--fake-llm",
               "--now", now, "--state", str(tmp / "state.json")] + (["--live-until", until] if until else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           env={"CLIENT_AGENT_TG_NOTIFY": "0", "PATH": "/usr/bin:/bin"})
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        live = tmp / "live_offline.json"
        return (json.loads((tmp / "state.json").read_text(encoding="utf-8")),
                json.loads(live.read_text(encoding="utf-8")) if live.exists() else [], r.stdout)


def greet_and_reply(reply_author="Анна"):
    return [wz(10, "2026-10-22T11:00:05+03:00", "Телефон", GREETING),
            wz(11, "2026-10-22T11:30:00+03:00", reply_author, "Здравствуйте! Стол 120×60, одна штука")]


class Live(unittest.TestCase):
    def test_reply_sent_to_client(self):
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]}, "comments": {"1401": greet_and_reply()}}
        st, actions, _ = run(fx)
        sends = [a for a in actions if a["kind"] == "send"]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]["chat_id"], "555111")  # chatId из реестра
        self.assertEqual(st["1401"]["status"], "active")
        self.assertIn(sends[0]["text"], st["1401"]["sent"])

    def test_own_reply_with_other_signature_is_not_client(self):
        """Ответ агента пришёл в таймлайн с подписью не «Телефон» — агент не считает его сообщением клиента."""
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]}, "comments": {"1401": greet_and_reply()}}
        st, actions, _ = run(fx)
        text = actions[0]["text"]
        fx["comments"]["1401"].append(wz(12, "2026-10-22T12:00:10+03:00", "API", text))
        st2, actions2, _ = run(fx, now="2026-10-22T12:02:00+03:00", state=st)
        self.assertEqual(st2["1401"]["status"], "active")
        self.assertEqual(actions2, [])  # нового сообщения клиента нет — молчим

    def test_manager_wrote_agent_stops(self):
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1401": greet_and_reply() + [wz(12, "2026-10-22T11:40:00+03:00", "Телефон", "Анна, это Екатерина, сейчас посчитаю")]}}
        st, actions, _ = run(fx)
        self.assertEqual(st["1401"]["status"], "manager")
        self.assertEqual(actions, [])

    def test_handover_notifies_katya(self):
        """Лимит сообщений ассистента — передача: сообщение Екатерине с ТЗ."""
        drafts = [{"time": "22.10 11:00", "reply": f"вопрос {i}", "sent": True, "iso": "2026-10-22T11:00:00+03:00"} for i in range(4)]
        state = {"1401": {"status": "active", "drafts": drafts, "sent": [d["reply"] for d in drafts],
                          "last_action": "2026-10-22T11:50:00+03:00", "last_seen": 11, "tz": None}}
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]}, "comments": {"1401": greet_and_reply()}}
        st, actions, _ = run(fx, state=state)
        self.assertEqual(st["1401"]["status"], "handed_over")
        ho = [a for a in actions if a["kind"] == "handover"]
        self.assertEqual(len(ho), 1)
        self.assertIn("лид 1401", ho[0]["text"])

    def test_wazzup_lead_stays_shadow(self):
        """Лид Wazzup не из реестра — живой режим на него не действует."""
        lead = {"ID": "1500", "TITLE": "Клиент", "NAME": "Клиент", "SOURCE_ID": WZ, "DATE_CREATE": "2026-10-22T09:00:00+03:00"}
        fx = {"leads": [lead], "registry": {}, "comments": {"1500": [wz(20, "2026-10-22T09:00:00+03:00", "Клиент", "Сколько стоит стол?")]}}
        st, actions, _ = run(fx)
        self.assertEqual(actions, [])

    def test_after_deadline_no_send(self):
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]}, "comments": {"1401": greet_and_reply()}}
        st, actions, _ = run(fx, now="2026-10-23T19:05:00+03:00")
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
