"""Стык скрипта входящих заявок и агента клиентов (docs/12, «Стык с агентом клиентов»).

Только подделанные данные: без Б24, Wazzup и Claude, ничего не отправляется.
Запуск: .venv/bin/python -m unittest app/client_agent/test_bridge.py -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import inbound_leads  # noqa: E402

WZ = "WZf1ffcdf2-1692-45ee-b704-78332dbfa91a"
NOW = "2026-10-16T12:00:00+03:00"  # пятница, рабочее время
REQ = {"source": "site", "name": "Анна", "phone": "+79110001122", "tg": "@anna_test", "request": "стол из нержавейки",
       "channel": "Telegram"}
GREETING = inbound_leads.greeting(REQ, assistant=True)
REG_ENTRY = {"time": "2026-10-16T11:00:00+03:00", "text": GREETING, "phone": "79110001122",
             "username": "@anna_test", "chat_id": "555111", "message_id": "m1"}


def wz(cid, created, author, text):
    """Комментарий таймлайна в формате Wazzup."""
    return {"ID": str(cid), "CREATED": created,
            "COMMENT": f"[img]https://static.wazzup24.com/images/bitrix/telegram.png[/img]&nbsp;  {author}:\n{text}"}


def run_agent(fixture, now=NOW, state=None):
    """Один проход агента в режиме --offline --fake-llm во временной папке. Возвращает (state, shadow-текст)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        if state:
            (tmp / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(HERE / "agent.py"), "--offline", str(tmp / "fixture.json"), "--fake-llm",
                            "--now", now, "--state", str(tmp / "state.json")],
                           capture_output=True, text=True, timeout=60, env={"CLIENT_AGENT_TG_NOTIFY": "0", "PATH": "/usr/bin:/bin"})
        if r.returncode:
            raise AssertionError(r.stdout + r.stderr)
        shadow = "".join(p.read_text(encoding="utf-8") for p in tmp.glob("shadow_*.md"))
        return json.loads((tmp / "state.json").read_text(encoding="utf-8")), shadow + r.stdout


def our_lead(lid="1401"):
    return {"ID": lid, "TITLE": "Сайт: Анна", "NAME": "Анна", "SOURCE_ID": "WEB", "DATE_CREATE": "2026-10-16T10:55:00+03:00",
            "PHONE": [{"VALUE": "+79110001122"}], "UF_CRM_TELEGRAMUSERNAME_WZ": "@anna_test"}


class AgentBridge(unittest.TestCase):
    def test_registry_lead_greeting_in_chat_agent_stays(self):
        """Приветствие скрипта видно в переписке как исходящее — агент не принимает его за Екатерину."""
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1401": [wz(10, "2026-10-16T11:00:05+03:00", "Телефон", GREETING)]}}
        st, _ = run_agent(fx)
        self.assertEqual(st["1401"]["status"], "active")
        self.assertEqual(len(st["1401"]["drafts"]), 1)  # приветствие идёт в лимит 4 сообщений
        self.assertIn(GREETING, st["1401"]["sent"])

    def test_registry_lead_not_taken_without_registry(self):
        """Лид «Сайт» без записи в реестре агент не берёт."""
        fx = {"leads": [our_lead()], "registry": {},
              "comments": {"1401": [wz(11, "2026-10-16T11:30:00+03:00", "Анна", "Стол 120х60")]}}
        st, _ = run_agent(fx)
        self.assertNotIn("1401", st)

    def test_client_replied_assistant_draft(self):
        """Клиент ответил на приветствие — сразу черновик ассистента, без ожидания 2 рабочих часов."""
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1401": [wz(10, "2026-10-16T11:00:05+03:00", "Телефон", GREETING),
                                    wz(11, "2026-10-16T11:50:00+03:00", "Анна", "Стол 120х60, одна штука")]}}
        st, out = run_agent(fx)
        s = st["1401"]
        self.assertEqual(s["status"], "active")
        self.assertEqual(len(s["drafts"]), 2)
        self.assertTrue(s["drafts"][1]["reply"].startswith("[ТЕСТ]"))  # не первое сообщение: без повторного «Здравствуйте»
        self.assertIn("Повод: новое сообщение клиента", out)
        self.assertEqual(out.count(GREETING.splitlines()[0]), 1)  # приветствие в переписке один раз

    def test_greeting_not_in_timeline_still_in_transcript(self):
        """Wazzup не записал исходящее в лид — приветствие всё равно в переписке для Claude, по времени."""
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1401": [wz(11, "2026-10-16T11:50:00+03:00", "Анна", "Стол 120х60")]}}
        st, out = run_agent(fx)
        self.assertEqual(st["1401"]["status"], "active")
        self.assertLess(out.index("Ассистент: Здравствуйте, Анна"), out.index("Клиент: Стол 120х60"))

    def test_manager_wrote_agent_exits(self):
        """Екатерина написала сама — агент выходит, как раньше."""
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1401": [wz(10, "2026-10-16T11:00:05+03:00", "Телефон", GREETING),
                                    wz(11, "2026-10-16T11:20:00+03:00", "Анна", "Стол 120х60"),
                                    wz(12, "2026-10-16T11:40:00+03:00", "Телефон", "Анна, добрый день! Это Екатерина.")]}}
        st, out = run_agent(fx)
        self.assertEqual(st["1401"]["status"], "manager")
        self.assertNotIn("Ассистент отправил бы", out)

    def test_client_silent_24h_handover(self):
        """Клиент не ответил на приветствие сутки — передача Екатерине."""
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]}, "comments": {"1401": []}}
        st, _ = run_agent(fx, now="2026-10-17T11:30:00+03:00")
        self.assertEqual(st["1401"]["status"], "handed_over")

    def test_limit_counts_greeting(self):
        """Лимит 4 сообщения ассистента считает и приветствие скрипта."""
        state = {"1401": {"status": "active", "last_seen": 11, "last_action": "2026-10-16T11:55:00+03:00",
                          "sent": [GREETING],
                          "drafts": [{"time": "16.10 11:00", "iso": REG_ENTRY["time"], "reply": GREETING, "external": True}]
                          + [{"time": "16.10 11:5" + str(i), "reply": f"вопрос {i}"} for i in range(3)]}}
        fx = {"leads": [our_lead()], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1401": [wz(11, "2026-10-16T11:50:00+03:00", "Анна", "Стол"),
                                    wz(13, "2026-10-16T11:58:00+03:00", "Анна", "Срок — месяц")]}}
        st, _ = run_agent(fx, state=state)
        self.assertEqual(st["1401"]["status"], "handed_over")

    def test_reply_in_new_wazzup_lead_linked(self):
        """Ответ клиента пришёл в новый лид Wazzup — связываем с нашим по Telegram ID, не «повторное обращение»."""
        wz_lead = {"ID": "1405", "TITLE": "Anna", "NAME": "Anna", "SOURCE_ID": WZ, "DATE_CREATE": "2026-10-16T11:50:00+03:00",
                   "UF_CRM_TELEGRAMID_WZ": "555111"}
        fx = {"leads": [our_lead(), wz_lead], "registry": {"1401": [REG_ENTRY]}, "repeat": {"1405": "лид 1401"},
              "comments": {"1401": [], "1405": [wz(20, "2026-10-16T11:50:00+03:00", "Anna", "Стол 120х60")]}}
        st, out = run_agent(fx)
        self.assertEqual(st["1401"]["status"], "linked")
        self.assertEqual(st["1405"]["status"], "active")
        self.assertIn("Ассистент: Здравствуйте, Анна", out)  # приветствие подтянуто из нашего лида
        self.assertNotIn("Первое сообщение ассистента: да", out)

    def test_link_by_phone_and_not_older_lead(self):
        import agent  # noqa: E402  (app/client_agent в sys.path ниже)
        reg = {"1401": [REG_ENTRY]}
        by_phone = {"ID": "1406", "SOURCE_ID": WZ, "DATE_CREATE": "2026-10-16T12:00:00+03:00",
                    "PHONE": [{"VALUE": "8 (911) 000-11-22"}]}
        older = {**by_phone, "ID": "1300", "DATE_CREATE": "2026-10-10T12:00:00+03:00"}
        stranger = {**by_phone, "ID": "1407", "PHONE": [{"VALUE": "+79990000000"}]}
        self.assertEqual(agent.link_external(by_phone, reg), "1401")
        self.assertIsNone(agent.link_external(older, reg))
        self.assertIsNone(agent.link_external(stranger, reg))

    def test_new_wazzup_client_unchanged(self):
        """Обычный новый клиент Wazzup без реестра — по-прежнему ждём Екатерину 2 рабочих часа."""
        lead = {"ID": "1410", "TITLE": "Иван", "NAME": "Иван", "SOURCE_ID": WZ, "UF_CRM_TELEGRAMID_WZ": "777"}
        fx = {"leads": [lead], "registry": {"1401": [REG_ENTRY]},
              "comments": {"1410": [wz(30, "2026-10-16T11:30:00+03:00", "Иван", "Нужен стол")]}}
        st, _ = run_agent(fx)
        self.assertEqual(st["1410"]["status"], "waiting")


class FakeB24:
    def __init__(self):
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        return 1


class InboundSend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = Path(self.tmp.name) / "external_sent.json"
        self.p = mock.patch.object(inbound_leads, "REGISTRY", self.reg)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_send_writes_registry_and_comment(self):
        b24 = FakeB24()
        with mock.patch.object(inbound_leads, "wazzup", return_value={"messageId": "m1", "chatId": "555111"}) as w:
            entry = inbound_leads.send_greeting(b24, {}, REQ, "1401", "ch-1")
        body = w.call_args.args[2]
        self.assertEqual(body, {"channelId": "ch-1", "chatType": "telegram", "text": GREETING, "phone": "79110001122"})
        reg = json.loads(self.reg.read_text(encoding="utf-8"))
        self.assertEqual(reg["1401"][0]["text"], GREETING)
        self.assertEqual(reg["1401"][0]["chat_id"], "555111")
        self.assertEqual(entry["phone"], "79110001122")
        self.assertIn("Приветствие отправлено ассистентом", b24.calls[0][1]["fields"]["COMMENT"])

    def test_no_phone_not_sent(self):
        b24 = FakeB24()
        with mock.patch.object(inbound_leads, "wazzup") as w:
            self.assertIsNone(inbound_leads.send_greeting(b24, {}, {**REQ, "phone": ""}, "1401", "ch-1"))
        w.assert_not_called()
        self.assertFalse(self.reg.exists())

    def test_send_is_off_by_default(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "inbound_leads.py"), "--send"],
                           capture_output=True, text=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--send работает только вместе с --create", r.stderr)
        plist = (ROOT / "scripts" / "launchd" / "ru.90gradusov.inbound-leads.plist")
        if plist.exists():  # отправка в расписании — только с датой окончания (тест 21–23.10.2026)
            text = plist.read_text(encoding="utf-8")
            if "--send" in text.replace("--send-until", ""):
                self.assertIn("--send-until", text)

    def test_send_error_leaves_draft(self):
        b24 = FakeB24()
        with mock.patch.object(inbound_leads, "wazzup", side_effect=RuntimeError("HTTP 400")), \
                mock.patch.object(inbound_leads, "log"), \
                mock.patch.object(inbound_leads, "datetime", wraps=inbound_leads.datetime) as dt:
            dt.now.return_value = inbound_leads.datetime(2026, 10, 16, 12, 0)
            res = inbound_leads.greet(b24, {}, REQ, "1401", {"text": "черновик", "when": "сразу"})
        self.assertTrue(res.startswith("ошибка"))
        self.assertIn("НЕ отправлено", b24.calls[-1][1]["fields"]["COMMENT"])
        self.assertFalse(self.reg.exists())


sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    unittest.main()
