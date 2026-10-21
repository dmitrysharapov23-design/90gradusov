"""Агент по работе с клиентами — один проход (запускается launchd каждые 2 минуты).

Механика — docs/13_агент_клиентов.md. Сейчас только режим «тень»: агент читает Б24, решает,
что ответил бы клиенту, и пишет черновик в data/client_agent/shadow_<дата>.md (+ уведомление
владельцу в Telegram). Клиентам ничего не отправляет, в Б24 ничего не пишет.

Запуск:
  .venv/bin/python app/client_agent/agent.py                  # боевой проход (режим «тень»)
  .venv/bin/python app/client_agent/agent.py --offline F.json --fake-llm --now 2026-10-15T16:30:00+03:00
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import chat

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "client_agent"
WAIT_WORK_HOURS = 2         # норма первого ответа (решение 14.10.2026)
LOOKBACK_DAYS = 3           # смотрим только свежие лиды
REPEAT_DAYS = 60            # окно «тот же запрос» (решение 16.10.2026)
MAX_AGENT_MSGS = 4          # после стольких сообщений без полного ТЗ — передаём
CLIENT_SILENCE_HOURS = 24   # клиент молчит — передаём, что есть
TG_ID_LEAD = "UF_CRM_TELEGRAMID_WZ"
TG_USER_LEAD = "UF_CRM_TELEGRAMUSERNAME_WZ"
TG_ID_DEAL = "UF_CRM_69DD014382FF7"
LEAD_FIELDS = ["ID", "TITLE", "NAME", "STATUS_ID", "SOURCE_ID", "DATE_CREATE", "PHONE", TG_ID_LEAD, TG_USER_LEAD]
# Приветствия, которые клиенту отправил не агент, а скрипт входящих заявок (scripts/inbound_leads.py, docs/12):
# {ID лида: [{"time", "text", "phone", "username", "chat_id", ...}]}. Эти тексты агент считает своими.
REGISTRY = ROOT / "data" / "client_agent" / "external_sent.json"
OUR_SOURCES = ("WEBFORM", "WEB")  # «Инстаграм» и «Веб-сайт» — лиды скрипта заявок
# Боевой режим (тест с 21.10.2026 до 23.10.2026 19:00, решение владельца): ТОЛЬКО для лидов, которым приветствие
# отправил скрипт заявок (реестр). Агент сам отвечает клиенту через Wazzup; при передаче — комментарий «ТЗ»
# в лид и сообщение Екатерине в Telegram. Лиды Wazzup — по-прежнему «тень». Включается --live-until.
NOTIFY_CFG = ROOT / "data" / "client_agent" / "notify.json"  # {"katya_chat_id": "..."} — не секрет, не в git


def load_env():
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return {**env, **os.environ}


def log(msg: str):
    DATA.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(chat.MSK):%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with open(DATA / "agent.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- источники данных ----------

class LiveSource:
    def __init__(self, env):
        from b24 import B24
        if not env.get("B24_AGENT_WEBHOOK"):
            sys.exit("Нет B24_AGENT_WEBHOOK в .env")
        self.b24 = B24(env["B24_AGENT_WEBHOOK"])

    def leads(self, now, extra_ids=()):
        since = (now - timedelta(days=LOOKBACK_DAYS)).isoformat()
        leads = self.b24.list("crm.lead.list", {"STATUS_ID": "NEW", ">=DATE_CREATE": since}, LEAD_FIELDS, {"ID": "ASC"})
        # лиды из реестра ведём и после смены статуса (например, Екатерина поставила «В работе»)
        missing = [i for i in extra_ids if i not in {l["ID"] for l in leads}]
        if missing:
            leads += self.b24.list("crm.lead.list", {"ID": missing, ">=DATE_CREATE": since}, LEAD_FIELDS, {"ID": "ASC"})
        return leads

    def comments(self, lead_ids):
        return self.b24.lead_comments(lead_ids)

    def add_comment(self, lead_id, text):
        self.b24.call("crm.timeline.comment.add", {"fields": {"ENTITY_ID": int(lead_id), "ENTITY_TYPE": "lead", "COMMENT": text}})

    def repeat_of(self, lead, now):
        """Было ли обращение с того же Telegram ID за 60 дней: другой лид или сделка. Возвращает описание или ''."""
        tg = lead.get(TG_ID_LEAD)
        if not tg:
            return ""
        since = (now - timedelta(days=REPEAT_DAYS)).isoformat()
        other = self.b24.list("crm.lead.list", {TG_ID_LEAD: tg, "!ID": lead["ID"], ">=DATE_CREATE": since}, ["ID"])
        if other:
            return "лид " + ", ".join(x["ID"] for x in other)
        deals = self.b24.list("crm.deal.list", {TG_ID_DEAL: tg, ">=DATE_MODIFY": since}, ["ID"])
        if deals:
            return "сделка " + ", ".join(x["ID"] for x in deals)
        return ""


class OfflineSource:
    """Для проверки без ключей: JSON {"leads": [...], "comments": {id: [...]}, "repeat": {id: "описание"}}."""

    def __init__(self, path):
        self.d = json.loads(Path(path).read_text(encoding="utf-8"))

    def leads(self, now, extra_ids=()):
        return self.d["leads"]

    def comments(self, ids):
        return {i: self.d["comments"].get(i, []) for i in ids}

    def repeat_of(self, lead, now):
        return self.d.get("repeat", {}).get(lead["ID"], "")

    def add_comment(self, lead_id, text):
        pass  # в тестах комментарий виден в DATA/live_offline.json (Live.handover)


class Live:
    """Боевые действия по лидам из реестра: ответ клиенту, комментарий «ТЗ», сообщение Екатерине.
    offline=True — ничего не отправляет, пишет в DATA/live_offline.json (для тестов)."""

    def __init__(self, env, source, offline=False):
        self.env, self.source, self.offline = env, source, offline
        self.channel = None

    def _offline(self, kind, **kw):
        path = DATA / "live_offline.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        rows.append({"kind": kind, **kw})
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    def send(self, lead, external, text):
        """Ответ клиенту через Wazzup: chatId из реестра или Telegram ID лида, иначе телефон."""
        chat_id = next((str(e["chat_id"]) for e in reversed(external) if e.get("chat_id")), "") or str(lead.get(TG_ID_LEAD) or "")
        phone = next((digits(e["phone"]) for e in reversed(external) if e.get("phone")), "") \
            or next((digits(p.get("VALUE")) for p in lead.get("PHONE") or []), "")
        if not (chat_id or phone):
            raise RuntimeError("некуда отправить: нет chatId и телефона")
        if self.offline:
            return self._offline("send", lead=lead["ID"], chat_id=chat_id, phone=phone, text=text)
        sys.path.insert(0, str(ROOT / "scripts"))
        from inbound_leads import wazzup, wazzup_channel
        self.channel = self.channel or wazzup_channel(self.env)
        body = {"channelId": self.channel, "chatType": "telegram", "text": text}
        body.update({"chatId": chat_id} if chat_id else {"phone": phone})
        wazzup(self.env, "message", body)

    def handover(self, lead, reason, tz):
        """Комментарий «ТЗ (собрал ассистент)» в лид и сообщение Екатерине в Telegram (копия владельцу)."""
        title = lead.get("TITLE") or lead.get("NAME") or lead["ID"]
        text = (f"Катя, ассистент собрал ТЗ: {title} (лид {lead['ID']}).\nПричина передачи: {reason}\n\n{tz}\n\n"
                f"Лид: https://90gradusov.bitrix24.ru/crm/lead/details/{lead['ID']}/")
        if self.offline:
            return self._offline("handover", lead=lead["ID"], text=text)
        self.source.add_comment(lead["ID"], f"ТЗ (собрал ассистент). Причина передачи: {reason}\n\n{tz}")
        cfg = json.loads(NOTIFY_CFG.read_text(encoding="utf-8")) if NOTIFY_CFG.exists() else {}
        katya = str(cfg.get("katya_chat_id") or "")
        sent_via = ""
        if katya:
            # решение владельца 21.10: Екатерине — в чат бота КП; если она его ещё не запускала (Telegram не даёт
            # боту писать первым) — через бот сводки, где она уже нажала «Старт»
            for token_key in ("KP_TG_BOT_TOKEN", "TG_BOT_TOKEN"):
                if not self.env.get(token_key):
                    continue
                try:
                    tg_message(self.env, katya, text, token_key)
                    sent_via = "бот КП" if token_key == "KP_TG_BOT_TOKEN" else "бот сводки"
                    break
                except Exception as e:
                    log(f"лид {lead['ID']}: Екатерине через {token_key} не ушло ({e})")
        if sent_via:
            tg_message(self.env, self.env["TG_CHAT_ID"], f"Копия (тест ассистента, Екатерине ушло через {sent_via}):\n\n" + text)
        else:
            tg_message(self.env, self.env["TG_CHAT_ID"], "⚠️ Екатерине не ушло — перешлите ей:\n\n" + text)


def tg_message(env, chat_id, text, token_key="TG_BOT_TOKEN"):
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000]}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{env[token_key]}/sendMessage", data=data, timeout=30)


def fake_decide(transcript, context):
    from llm import TZ, Decision
    first = "Первое сообщение ассистента: да" in context
    return Decision(
        reply=("Здравствуйте! Я ассистент компании «90 градусов». " if first else "") + "[ТЕСТ] Уточните, пожалуйста, размеры и количество.",
        tz=TZ(izdelie_i_razmery="[тест]", kolichestvo="", material_i_cvet="", gorod_dostavka_montazh="",
              srok="", zakazchik="", budzhet="", faily="", prochee=""),
        missing=["2", "3"], project=False, handover=False, handover_reason="",
    )


# ---------- логика ----------

def wanted(lead, registry):
    """Лиды Wazzup, а также лиды «Инстаграм»/«Сайт», которым скрипт заявок отправил приветствие."""
    src = lead.get("SOURCE_ID") or ""
    return src.startswith("WZ") or (src in OUR_SOURCES and lead["ID"] in registry)


def digits(s):
    d = "".join(ch for ch in str(s or "") if ch.isdigit())
    return "7" + d[1:] if len(d) == 11 and d[0] == "8" else d


def link_external(lead, registry):
    """Ответ на наше приветствие мог прийти в НОВЫЙ лид Wazzup, а не в наш. Ищем наш лид по Telegram ID
    (chatId из ответа Wazzup), нику или телефону. Возвращает ID нашего лида или None.
    НЕ ПРОВЕРЕНО: куда Wazzup кладёт ответ клиента и что приходит в chatId — проверить на первой живой отправке."""
    if not (lead.get("SOURCE_ID") or "").startswith("WZ") or lead["ID"] in registry:
        return None
    tg_id = str(lead.get(TG_ID_LEAD) or "")
    user = (lead.get(TG_USER_LEAD) or "").lstrip("@").lower()
    phones = {digits(p.get("VALUE")) for p in lead.get("PHONE") or []} - {""}
    created = datetime.fromisoformat(lead["DATE_CREATE"]) if lead.get("DATE_CREATE") else None
    for our_id, entries in registry.items():
        for e in entries:
            if created and created < datetime.fromisoformat(e["time"]):
                continue  # лид Wazzup старше приветствия — это не ответ на него
            if ((tg_id and tg_id == str(e.get("chat_id") or ""))
                    or (user and user == (e.get("username") or "").lstrip("@").lower())
                    or (phones and digits(e.get("phone")) in phones)):
                return our_id
    return None


def adopt_external(st, external):
    """Тексты из реестра — свои: не считаются ответом Екатерины и идут в лимит сообщений ассистента."""
    have = {chat.norm(x) for x in st.get("sent", [])}
    for e in external:
        if chat.norm(e["text"]) in have:
            continue
        have.add(chat.norm(e["text"]))
        st.setdefault("sent", []).append(e["text"])
        t = datetime.fromisoformat(e["time"])
        st.setdefault("drafts", []).append({"time": f"{t.astimezone(chat.MSK):%d.%m %H:%M}", "iso": e["time"],
                                            "reply": e["text"], "external": True})

def next_work_start(now):
    t = now.astimezone(chat.MSK)
    start = t.replace(hour=chat.WORK_START, minute=0, second=0, microsecond=0)
    while start <= t or start.weekday() not in chat.WORK_DAYS:
        start += timedelta(days=1)
    days = (start.date() - t.date()).days
    when = "сегодня" if days == 0 else "завтра" if days == 1 else ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу"][start.weekday()]
    return f"{when} с {chat.WORK_START}:00"


def transcript(msgs, drafts):
    lines = []
    for m in msgs:
        who = "Клиент" if m["who"] == "c" else "Ассистент"
        lines.append((m["time"], f"[{m['time'].astimezone(chat.MSK):%d.%m %H:%M}] {who}: {m['text']}"))
    in_chat = {chat.norm(m["text"]) for m in msgs}
    tail = []
    for d in drafts:
        if chat.norm(d["reply"]) in in_chat:
            continue  # уже есть в переписке
        if d.get("external") or d.get("sent"):  # реально отправлено (скриптом заявок или агентом) — по времени
            lines.append((datetime.fromisoformat(d["iso"]), f"[{d['time']}] Ассистент: {d['reply']}"))
        else:  # в режиме «тень» сообщения ассистента в чат не уходят — добавляем черновики, чтобы диалог был связным
            tail.append(f"[{d['time']}] Ассистент (черновик): {d['reply']}")
    lines.sort(key=lambda x: x[0])
    return "\n".join([x[1] for x in lines] + tail)


def own_account(lead, ignore_tg):
    username = "@" + (lead.get(TG_USER_LEAD) or "").lstrip("@").lower()
    return lead.get(TG_ID_LEAD) in ignore_tg or username in ignore_tg


def process(lead, msgs, st, now, source, decide_fn, out, ignore_tg=frozenset(), external=(), live=None):
    """external — записи реестра приветствий по этому лиду (или по нашему лиду, с которым он связан).
    live — боевые действия (Live); применяются только к лидам из реестра."""
    lid = lead["ID"]
    live = live if external else None
    if external:
        adopt_external(st, external)
    sent = {chat.norm(x) for x in st.get("sent", [])}
    # свои тексты — не сообщения клиента, даже если Wazzup подписал их не «Телефон:»
    # (НЕ ПРОВЕРЕНО, как Wazzup подписывает в таймлайне сообщения, отправленные через API)
    client_msgs = [m for m in msgs if m["who"] == "c" and chat.norm(m["text"]) not in sent]
    manager_msgs = [m for m in msgs if m["who"] == "m" and chat.norm(m["text"]) not in sent]

    if st.get("status") in ("manager", "handed_over", "skip", "linked"):
        return
    if manager_msgs:
        first_c = client_msgs[0]["time"] if client_msgs else None
        delay = chat.work_hours(first_c, manager_msgs[0]["time"]) if first_c and manager_msgs[0]["time"] > first_c else None
        st.update(status="manager", note=f"Екатерина ответила" + (f" через {delay:.1f} раб. ч" if delay is not None else " первой"))
        log(f"лид {lid}: {st['note']} — агент не вступает")
        return
    if st.get("status") is None and external:
        if own_account(lead, ignore_tg):
            st.update(status="skip", note="свой аккаунт (ignore_tg.txt)")
            log(f"лид {lid}: {st['note']}")
            return
        # приветствие уже ушло — без ожидания Екатерины, дальше по ответу клиента; дубли проверил скрипт заявок
        st.update(status="active", last_action=external[-1]["time"], note="приветствие отправил скрипт заявок")
        log(f"лид {lid}: {st['note']} — ассистент ведёт диалог")
    if not client_msgs and st.get("status") != "active":
        return

    if st.get("status") is None:
        if own_account(lead, ignore_tg):
            st.update(status="skip", note="свой аккаунт (ignore_tg.txt)")
            log(f"лид {lid}: {st['note']}")
            return
        rep = source.repeat_of(lead, now)
        if rep:
            st.update(status="skip", note=f"повторное обращение за {REPEAT_DAYS} дней: {rep}")
            log(f"лид {lid}: {st['note']}")
            return
        st["status"] = "waiting"

    last_seen = st.get("last_seen", 0)
    new_client = [m for m in client_msgs if m["id"] > last_seen]

    if st["status"] == "waiting":
        t0 = client_msgs[0]["time"]
        working = chat.is_work_time(now)
        if working and chat.work_hours(t0, now) < WAIT_WORK_HOURS:
            return
        reason = f"нет ответа {chat.work_hours(t0, now):.1f} раб. ч" if working else "нерабочее время"
    elif st["status"] == "active":
        drafts = st.get("drafts", [])
        if len(drafts) >= MAX_AGENT_MSGS:
            return handover(lead, st, now, out, "ассистент написал 4 сообщения, ТЗ не собрано", live)
        if not new_client:
            last_t = datetime.fromisoformat(st["last_action"])
            if (now - last_t).total_seconds() > CLIENT_SILENCE_HOURS * 3600:
                return handover(lead, st, now, out, f"клиент молчит {CLIENT_SILENCE_HOURS} ч", live)
            return
        reason = "новое сообщение клиента"
    else:
        return

    first = st["status"] == "waiting"
    context = "\n".join([
        f"Сейчас: {now.astimezone(chat.MSK):%d.%m.%Y %H:%M} (МСК), {'рабочее' if chat.is_work_time(now) else 'нерабочее'} время.",
        f"Если нерабочее время — Екатерина ответит {next_work_start(now)}." if not chat.is_work_time(now) else "",
        f"Первое сообщение ассистента: {'да' if first else 'нет'}.",
        f"Сообщений ассистента уже: {len(st.get('drafts', []))}.",
        f"Клиент: {lead.get('NAME') or lead.get('TITLE')}.",
    ])
    tr = transcript(msgs, st.get("drafts", []))
    d = decide_fn(tr, context)
    if live and d.reply:
        live.send(lead, external, d.reply)  # ошибка — исключение до изменения состояния: следующий проход повторит

    st["status"] = "active"
    st["last_seen"] = msgs[-1]["id"] if msgs else st.get("last_seen", 0)
    st["last_action"] = now.isoformat()
    draft = {"time": f"{now.astimezone(chat.MSK):%d.%m %H:%M}", "reply": d.reply}
    if live and d.reply:
        draft.update(sent=True, iso=now.isoformat())
        st.setdefault("sent", []).append(d.reply)
    st.setdefault("drafts", []).append(draft)
    st["tz"] = d.tz.model_dump()

    from llm import tz_text
    out.append(
        f"## Лид {lid} — {lead.get('TITLE')} — {now.astimezone(chat.MSK):%d.%m %H:%M}{' — ЖИВОЙ РЕЖИМ' if live else ''}\n"
        f"Повод: {reason}. {'Передать Екатерине: ' + d.handover_reason if d.handover else ''}\n\n"
        f"Переписка:\n```\n{tr}\n```\n\n"
        f"Ассистент {'отправил' if live and d.reply else 'отправил бы'}:\n> " + (d.reply.replace("\n", "\n> ") or "(ничего)") + "\n\n"
        f"ТЗ сейчас{' (проект)' if d.project else ''}:\n```\n{tz_text(d.tz)}\n```\n"
        f"Не хватает: {', '.join(d.missing) or '—'}\n"
    )
    log(f"лид {lid}: {'отправлено клиенту' if live and d.reply else 'черновик'} ({reason})"
        f"{', передача: ' + d.handover_reason if d.handover else ''}")
    if d.handover:
        handover(lead, st, now, out, d.handover_reason, live, record=False)


def handover(lead, st, now, out, reason, live=None, record=True):
    from llm import TZ, tz_text
    st.update(status="handed_over", note=reason, last_action=now.isoformat())
    tz = tz_text(TZ(**st["tz"])) if st.get("tz") else "—"
    if record:
        out.append(f"## Лид {lead['ID']} — {lead.get('TITLE')} — передача Екатерине\nПричина: {reason}\n\n```\n{tz}\n```\n")
    log(f"лид {lead['ID']}: передача Екатерине ({reason})")
    if live:
        try:
            live.handover(lead, reason, tz)
            log(f"лид {lead['ID']}: ТЗ записано в лид, Екатерине отправлено в Telegram")
        except Exception as e:  # передача уже в состоянии — не повторяем, но пишем ОШИБКУ в журнал
            log(f"лид {lead['ID']}: ОШИБКА передачи Екатерине: {type(e).__name__}: {e}")


def notify(env, text):
    if env.get("CLIENT_AGENT_TG_NOTIFY", "1") != "1" or not env.get("TG_BOT_TOKEN"):
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(("👤 Ассистент (живой режим)" if "ЖИВОЙ РЕЖИМ" in text else "👤 Ассистент (тень)") + "\n\n" + text)
    try:
        subprocess.run([str(ROOT / "scripts" / "tg_send.sh"), f.name], check=True, capture_output=True, timeout=60)
    except Exception as e:
        log(f"Telegram: не отправлено ({e})")
    finally:
        os.unlink(f.name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", help="JSON с лидами и комментариями вместо Б24")
    ap.add_argument("--fake-llm", action="store_true", help="не вызывать Claude (проверка логики)")
    ap.add_argument("--now", help="подставить текущее время (ISO)")
    ap.add_argument("--state", help="файл состояния (по умолчанию data/client_agent/state.json)")
    ap.add_argument("--live-until", help="боевой режим для лидов из реестра до этого момента (ISO), потом снова «тень»")
    args = ap.parse_args()

    global DATA
    if args.offline:  # тестовые прогоны пишут рядом с файлом состояния, не в рабочие логи
        DATA = Path(args.state).parent if args.state else Path(args.offline).parent
    env = load_env()
    if env.get("CLIENT_AGENT_MODE", "shadow") != "shadow":
        sys.exit("Поддерживается только режим «тень» (CLIENT_AGENT_MODE=shadow)")
    DATA.mkdir(parents=True, exist_ok=True)
    lock = open(DATA / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # предыдущий проход ещё идёт

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(chat.MSK)
    source = OfflineSource(args.offline) if args.offline else LiveSource(env)
    if args.fake_llm:
        decide_fn = fake_decide
    else:
        import anthropic
        import llm
        if not env.get("ANTHROPIC_API_KEY"):
            sys.exit("Нет ANTHROPIC_API_KEY в .env")
        client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
        decide_fn = lambda tr, ctx: llm.decide(client, tr, ctx)

    state_path = Path(args.state) if args.state else DATA / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if args.offline:
        registry = source.d.get("registry", {})
    else:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {}

    ignore_path = ROOT / "data" / "client_agent" / "ignore_tg.txt"  # свои аккаунты: бот, сотрудники, партнёры — по одному Telegram ID в строке
    ignore_tg = {x.split("#")[0].strip().lower() for x in ignore_path.read_text(encoding="utf-8").splitlines()} if ignore_path.exists() else set()
    ignore_tg.discard("")
    live = None
    if args.live_until and now < datetime.fromisoformat(args.live_until):
        live = Live(env, source, offline=bool(args.offline))
    leads = [l for l in source.leads(now, list(registry)) if wanted(l, registry)]
    comments = source.comments([l["ID"] for l in leads]) if leads else {}
    out = []
    links = {}  # лид Wazzup → наш лид (ответ клиента пришёл не в наш лид)
    for lead in leads:
        our = link_external(lead, registry)
        if our:
            links[lead["ID"]] = our
            ours, st = state.setdefault(our, {}), state.setdefault(lead["ID"], {})
            if st.get("status") is None and ours.get("status") in ("manager", "handed_over", "skip"):
                st.update(status=ours["status"], note=f"связан с лидом {our}: {ours.get('note', '')}")
            if ours.get("status") in (None, "active"):
                ours.update(status="linked", note=f"переписка идёт в лиде Wazzup {lead['ID']}")
                log(f"лид {our}: ответ клиента пришёл в лид Wazzup {lead['ID']} — ведём диалог там")
    for lead in leads:
        st = state.setdefault(lead["ID"], {})
        try:
            process(lead, chat.parse(comments.get(lead["ID"], [])), st, now, source, decide_fn, out, ignore_tg,
                    registry.get(links.get(lead["ID"], lead["ID"]), ()), live)
        except Exception as e:  # одна ошибка не должна останавливать остальные лиды
            log(f"лид {lead['ID']}: ОШИБКА {type(e).__name__}: {e}")

    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    (DATA / "heartbeat").write_text(now.isoformat(), encoding="utf-8")
    if out:
        shadow = DATA / f"shadow_{now.astimezone(chat.MSK):%Y-%m-%d}.md"
        with open(shadow, "a", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        if not args.offline:
            for block in out:
                notify(env, block)


if __name__ == "__main__":
    main()
