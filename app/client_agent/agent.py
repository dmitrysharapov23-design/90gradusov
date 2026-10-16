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
TG_ID_DEAL = "UF_CRM_69DD014382FF7"
LEAD_FIELDS = ["ID", "TITLE", "NAME", "STATUS_ID", "SOURCE_ID", "DATE_CREATE", TG_ID_LEAD, "UF_CRM_TELEGRAMUSERNAME_WZ"]


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

    def leads(self, now):
        leads = self.b24.list("crm.lead.list", {
            "STATUS_ID": "NEW",
            ">=DATE_CREATE": (now - timedelta(days=LOOKBACK_DAYS)).isoformat(),
        }, LEAD_FIELDS, {"ID": "ASC"})
        return [l for l in leads if (l.get("SOURCE_ID") or "").startswith("WZ")]  # только Wazzup

    def comments(self, lead_ids):
        return self.b24.lead_comments(lead_ids)

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

    def leads(self, now):
        return self.d["leads"]

    def comments(self, ids):
        return {i: self.d["comments"].get(i, []) for i in ids}

    def repeat_of(self, lead, now):
        return self.d.get("repeat", {}).get(lead["ID"], "")


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

def next_work_start(now):
    t = now.astimezone(chat.MSK)
    start = t.replace(hour=chat.WORK_START, minute=0, second=0, microsecond=0)
    while start <= t or start.weekday() not in chat.WORK_DAYS:
        start += timedelta(days=1)
    days = (start.date() - t.date()).days
    when = "сегодня" if days == 0 else "завтра" if days == 1 else ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу"][start.weekday()]
    return f"{when} с {chat.WORK_START}:00"


def transcript(msgs, agent_texts):
    lines = []
    for m in msgs:
        who = "Клиент" if m["who"] == "c" else "Ассистент"
        lines.append(f"[{m['time'].astimezone(chat.MSK):%d.%m %H:%M}] {who}: {m['text']}")
    # в режиме «тень» сообщения ассистента в чат не уходят — добавляем черновики, чтобы диалог был связным
    for t, text in agent_texts:
        lines.append(f"[{t}] Ассистент (черновик): {text}")
    return "\n".join(lines)


def process(lead, msgs, st, now, source, decide_fn, out, ignore_tg=frozenset()):
    lid = lead["ID"]
    sent = {chat.norm(x) for x in st.get("sent", [])}
    client_msgs = [m for m in msgs if m["who"] == "c"]
    manager_msgs = [m for m in msgs if m["who"] == "m" and chat.norm(m["text"]) not in sent]

    if st.get("status") in ("manager", "handed_over", "skip"):
        return
    if manager_msgs:
        first_c = client_msgs[0]["time"] if client_msgs else None
        delay = chat.work_hours(first_c, manager_msgs[0]["time"]) if first_c and manager_msgs[0]["time"] > first_c else None
        st.update(status="manager", note=f"Екатерина ответила" + (f" через {delay:.1f} раб. ч" if delay is not None else " первой"))
        log(f"лид {lid}: {st['note']} — агент не вступает")
        return
    if not client_msgs:
        return

    if st.get("status") is None:
        username = "@" + (lead.get("UF_CRM_TELEGRAMUSERNAME_WZ") or "").lstrip("@").lower()
        if lead.get(TG_ID_LEAD) in ignore_tg or username in ignore_tg:
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
            return handover(lead, st, now, out, "ассистент написал 4 сообщения, ТЗ не собрано")
        if not new_client:
            last_t = datetime.fromisoformat(st["last_action"])
            if (now - last_t).total_seconds() > CLIENT_SILENCE_HOURS * 3600:
                return handover(lead, st, now, out, f"клиент молчит {CLIENT_SILENCE_HOURS} ч")
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
    tr = transcript(msgs, [(d["time"], d["reply"]) for d in st.get("drafts", [])])
    d = decide_fn(tr, context)

    st["status"] = "active"
    st["last_seen"] = msgs[-1]["id"]
    st["last_action"] = now.isoformat()
    st.setdefault("drafts", []).append({"time": f"{now.astimezone(chat.MSK):%d.%m %H:%M}", "reply": d.reply})
    st["tz"] = d.tz.model_dump()

    from llm import tz_text
    out.append(
        f"## Лид {lid} — {lead.get('TITLE')} — {now.astimezone(chat.MSK):%d.%m %H:%M}\n"
        f"Повод: {reason}. {'Передать Екатерине: ' + d.handover_reason if d.handover else ''}\n\n"
        f"Переписка:\n```\n{tr}\n```\n\n"
        f"Ассистент отправил бы:\n> " + (d.reply.replace("\n", "\n> ") or "(ничего)") + "\n\n"
        f"ТЗ сейчас{' (проект)' if d.project else ''}:\n```\n{tz_text(d.tz)}\n```\n"
        f"Не хватает: {', '.join(d.missing) or '—'}\n"
    )
    log(f"лид {lid}: черновик ({reason}){', передача: ' + d.handover_reason if d.handover else ''}")
    if d.handover:
        st.update(status="handed_over", note=d.handover_reason)


def handover(lead, st, now, out, reason):
    from llm import TZ, tz_text
    st.update(status="handed_over", note=reason, last_action=now.isoformat())
    tz = tz_text(TZ(**st["tz"])) if st.get("tz") else "—"
    out.append(f"## Лид {lead['ID']} — {lead.get('TITLE')} — передача Екатерине\nПричина: {reason}\n\n```\n{tz}\n```\n")
    log(f"лид {lead['ID']}: передача Екатерине ({reason})")


def notify(env, text):
    if env.get("CLIENT_AGENT_TG_NOTIFY", "1") != "1" or not env.get("TG_BOT_TOKEN"):
        return
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("👤 Ассистент (тень)\n\n" + text)
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

    ignore_path = ROOT / "data" / "client_agent" / "ignore_tg.txt"  # свои аккаунты: бот, сотрудники, партнёры — по одному Telegram ID в строке
    ignore_tg = {x.split("#")[0].strip().lower() for x in ignore_path.read_text(encoding="utf-8").splitlines()} if ignore_path.exists() else set()
    ignore_tg.discard("")
    leads = source.leads(now)
    comments = source.comments([l["ID"] for l in leads]) if leads else {}
    out = []
    for lead in leads:
        st = state.setdefault(lead["ID"], {})
        try:
            process(lead, chat.parse(comments.get(lead["ID"], [])), st, now, source, decide_fn, out, ignore_tg)
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
