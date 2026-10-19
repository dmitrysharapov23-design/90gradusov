"""Отчёты по запросу для бота сводки. Цифры считает код; Claude пишет только саммари клиента.

Запуск из консоли (проверка):
  .venv/bin/python app/reports/reports.py period 2026-09-01 2026-10-01
  .venv/bin/python app/reports/reports.py hanging | funnel | client 1251
"""
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta

from data import (CHAT_RELIABLE_FROM, load_env, LEAD_LOST, LEAD_NOT_CLIENT, MSK, ROOT, WAZZUP_PREFIX, Data,
                  env_b24, messages, work_days_between, work_hours)

RESPONSE_NORM_H = 2.0            # норма первого ответа, раб. ч (эталон 06)
STUCK_DAYS = {"0": 7, "3": 14}
NOT_STUCK = {"C3:UC_LTK51B", "C3:UC_ZHOAYR"}   # отгружено, ответственное хранение — показываем отдельно   # ДОПУЩЕНИЕ: сколько календарных дней на стадии считать «зависла» (продажи / производство)
BBCODE = re.compile(r"\[/?[A-Za-z]+[^\]]*\]")
PRICE = re.compile(r"(стоимост|цена|итого)[^\n]{0,80}?\d[\d\s]{2,}\s*(руб|₽|р\.)", re.I)


def fmt_money(x) -> str:
    return f"{round(float(x or 0)):,}".replace(",", " ") + " ₽"


def fmt_d(d: datetime) -> str:
    return d.astimezone(MSK).strftime("%d.%m.%Y")


def pct(a, b) -> str:
    return f"{round(100 * a / b)}%" if b else "—"


def delta(cur, prev) -> str:
    if not prev:
        return ""
    d = round(100 * (cur - prev) / prev)
    return f" ({'+' if d >= 0 else ''}{d}% к прошлому периоду: {prev})"


# Короткие завершающие реплики клиента — ответа не требуют
CLOSER = re.compile(r"^(хорошо|ок|окей|ok|спасибо|благодарю|понял|поняла|понятно|принято|договорились|отлично|супер|ясно|да|👍|🙏|🙂)[\s!.,)👍🙏🌸😊]*(спасибо|большое|огромное|вам)?[\s!.,)👍🙏🌸😊]*$", re.I)


def needs_reply(text: str) -> bool:
    t = (text or "").strip()
    return not CLOSER.match(t)


def one_line(text: str, n: int = 60) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:n]


def has_price(text: str) -> bool:
    return bool(PRICE.search(text or ""))


# ---------- отчёт за период ----------

def period(d: Data, a: datetime, b: datetime, leads_only: bool = False) -> str:
    """a — начало (включительно), b — конец (не включительно)."""
    names = d.dicts()
    leads = d.leads_created(a, b)
    prev_a = a - (b - a)
    prev_leads = d.leads_created(prev_a, a)

    by_src = Counter(d.source_name(l.get("SOURCE_ID")) for l in leads)
    by_st = Counter(l["STATUS_ID"] for l in leads)
    not_client = sum(by_st[s] for s in LEAD_NOT_CLIENT)
    real = len(leads) - not_client
    converted = by_st["CONVERTED"]
    lost = sum(by_st[s] for s in LEAD_LOST)
    in_work = real - converted - lost

    out = [f"📊 Отчёт за {fmt_d(a)} – {fmt_d(b - timedelta(seconds=1))}", ""]
    out.append(f"ЛИДЫ: {len(leads)}{delta(len(leads), len(prev_leads))}")
    out.append("По источникам: " + ", ".join(f"{k} {v}" for k, v in by_src.most_common()))
    out.append(f"Клиентов (без спама и дублей): {real}")
    out.append(f"  в работе {in_work}, качественные (сделка) {converted} — {pct(converted, real)}, отказ {lost}")
    if lost:
        reasons = ", ".join(f"{names['STATUS'].get(s, s)} {by_st[s]}" for s in sorted(LEAD_LOST, key=lambda s: -by_st[s]) if by_st[s])
        out.append(f"  причины отказа: {reasons}")
    if not_client:
        out.append(f"Спам / дубли / не клиенты: {not_client}")
    open_st = Counter(names["STATUS"].get(l["STATUS_ID"], l["STATUS_ID"]) for l in leads
                      if l["STATUS_ID"] in ("NEW", "IN_PROCESS", "UC_VE9PFM"))
    if open_st:
        out.append("Статусы открытых: " + ", ".join(f"{k} {v}" for k, v in open_st.most_common()))
    if leads_only:
        out[0] = out[0].replace("Отчёт", "Лиды")
        out.append("Все статусы: " + ", ".join(f"{names['STATUS'].get(k, k)} {v}" for k, v in by_st.most_common()))
        if a < CHAT_RELIABLE_FROM:
            out.append("⚠️ До 26.09 лиды Wazzup созданы массовой загрузкой — даты могут быть сдвинуты.")
        return "\n".join(out)

    # скорость первого ответа и КП — по переписке Wazzup
    wz = [l for l in leads if (l.get("SOURCE_ID") or "").startswith(WAZZUP_PREFIX) and l["STATUS_ID"] not in LEAD_NOT_CLIENT]
    comms = d.comments("lead", [l["ID"] for l in wz]) if wz else {}
    waits, kp, unreliable = [], 0, 0
    for l in wz:
        msgs = messages(comms.get(l["ID"], []))
        if any(m["who"] == "m" and has_price(m["text"]) for m in msgs):
            kp += 1
        first_c = next((m for m in msgs if m["who"] == "c"), None)
        if not first_c:
            continue
        if first_c["time"] < CHAT_RELIABLE_FROM:
            unreliable += 1
            continue
        reply = next((m for m in msgs if m["who"] == "m" and m["time"] > first_c["time"]), None)
        end = reply["time"] if reply else datetime.now(MSK)
        waits.append((work_hours(first_c["time"], end), l, reply is None))
    out.append("")
    out.append("ПЕРВЫЙ ОТВЕТ КЛИЕНТУ (Telegram, рабочие часы пн–пт 10–19):")
    if waits:
        hs = [w[0] for w in waits]
        ok = sum(1 for h in hs if h <= RESPONSE_NORM_H)
        out.append(f"  диалогов {len(hs)}, медиана {statistics.median(hs):.1f} ч, в норме ≤2 ч: {ok} ({pct(ok, len(hs))})")
        worst = sorted(waits, key=lambda w: -w[0])[:3]
        bad = [w for w in worst if w[0] > RESPONSE_NORM_H]
        if bad:
            out.append("  дольше всех: " + "; ".join(
                f"лид {w[1]['ID']} {w[1]['TITLE']} — {w[0]:.1f} ч{' (ещё без ответа)' if w[2] else ''}" for w in bad))
    else:
        out.append("  нет данных")
    if unreliable:
        out.append(f"  ⚠️ {unreliable} диалогов начаты до 26.09 — время недостоверно (массовая загрузка), не учтены")
    out.append(f"Названа цена / КП в переписке: {kp} лидов (поиск по тексту, может пропускать КП файлом)")

    # сделки
    won = d.stage_moves(a, b, ["WON"])
    won_prev = d.stage_moves(prev_a, a, ["WON"])
    lose = d.stage_moves(a, b, ["LOSE"])
    shipped = d.stage_moves(a, b, ["C3:UC_LTK51B", "C3:WON"])
    ids = sorted({int(x["OWNER_ID"]) for x in won + lose + shipped})
    deals = {x["ID"]: x for x in d.deals({"@ID": ids})} if ids else {}
    won_ids = sorted({str(x["OWNER_ID"]) for x in won})
    won_sum = sum(float(deals.get(i, {}).get("OPPORTUNITY") or 0) for i in won_ids)
    ship_ids = sorted({str(x["OWNER_ID"]) for x in shipped})
    out.append("")
    out.append("СДЕЛКИ:")
    out.append(f"  оплата получена: {len(won_ids)} на {fmt_money(won_sum)}"
               f"{', средний чек ' + fmt_money(won_sum / len(won_ids)) if won_ids else ''}"
               f"{delta(len(won_ids), len({x['OWNER_ID'] for x in won_prev}))}")
    out.append(f"  провалено: {len({x['OWNER_ID'] for x in lose})}")
    out.append(f"  отгружено / получено клиентом: {len(ship_ids)} на "
               f"{fmt_money(sum(float(deals.get(i, {}).get('OPPORTUNITY') or 0) for i in ship_ids))}")
    zero = [i for i in won_ids if not float(deals.get(i, {}).get("OPPORTUNITY") or 0)]
    if zero:
        out.append(f"  ⚠️ без суммы в сделке: {', '.join(zero)}")
    if a < CHAT_RELIABLE_FROM:
        out.append("")
        out.append("⚠️ Период захватывает время до 26.09: лиды Wazzup того времени созданы массовой загрузкой, даты лидов могут быть сдвинуты.")
    return "\n".join(out)


# ---------- висят сейчас ----------

def hanging(d: Data) -> str:
    now = datetime.now(MSK)
    leads = d.leads_open()
    tails = d.tails([l["ID"] for l in leads], n=15)
    wait, touch = [], []
    for l in leads:
        msgs = messages(tails.get(l["ID"], []))
        if not msgs:
            continue
        last = msgs[-1]
        if last["who"] == "c" and needs_reply(last["text"]):
            first_unanswered = last
            for m in reversed(msgs):
                if m["who"] == "m":
                    break
                first_unanswered = m
            if first_unanswered["time"] < CHAT_RELIABLE_FROM:
                continue   # хвост массовой загрузки 26.09 — время недостоверно
            h = work_hours(first_unanswered["time"], now)
            if h > RESPONSE_NORM_H:
                wait.append((h, l, first_unanswered))
        elif l["STATUS_ID"] != "CONVERTED":
            kp = next((m for m in reversed(msgs) if m["who"] == "m" and has_price(m["text"])), None)
            if kp and not any(m["who"] == "c" and m["time"] > kp["time"] for m in msgs):
                wd = work_days_between(kp["time"], now)
                if wd >= 2:
                    step = "закрыть с причиной" if wd >= 10 else "второе касание" if wd >= 5 else "первое касание"
                    touch.append((wd, l, step))
    out = [f"⏳ Висят сейчас ({now:%d.%m %H:%M})", ""]
    fresh = [w for w in wait if w[0] <= 27]          # до 3 рабочих дней (9 ч × 3)
    old = [w for w in wait if w[0] > 27]
    out.append(f"🔴 Ждут ответа дольше {RESPONSE_NORM_H:.0f} раб. ч: {len(fresh)}")
    for h, l, m in sorted(fresh, key=lambda x: -x[0]):
        out.append(f"  лид {l['ID']} {l['TITLE']} — {h:.1f} ч, с {m['time']:%d.%m %H:%M}: «{one_line(m['text'])}»")
    if old:
        out.append(f"Давно без ответа (больше 3 раб. дн., возможно ответили не в Telegram): {len(old)} — лиды "
                   + ", ".join(w[1]["ID"] for w in sorted(old, key=lambda x: x[0])))
    out.append("")
    out.append(f"🟠 После КП клиент молчит: {len(touch)}")
    for wd, l, step in sorted(touch, key=lambda x: -x[0]):
        out.append(f"  лид {l['ID']} {l['TITLE']} — {wd} раб. дн. → {step}")
    out.append("")
    out.append(f"Проверено открытых лидов Telegram за 45 дней: {len(leads)}")
    return "\n".join(out)


# ---------- воронка ----------

def funnel(d: Data) -> str:
    names = d.dicts()
    now = datetime.now(MSK)
    out = [f"🧭 Воронка на {now:%d.%m %H:%M}", ""]
    leads = d.all("crm.lead.list", {"@STATUS_ID": ["NEW", "IN_PROCESS", "UC_VE9PFM"],
                                    ">=DATE_CREATE": (now - timedelta(days=90)).isoformat()},
                  ["ID", "STATUS_ID", "SOURCE_ID"])
    st = Counter(names["STATUS"].get(l["STATUS_ID"], l["STATUS_ID"]) for l in leads)
    out.append(f"ЛИДЫ открытые (за 90 дней): {len(leads)} — " + ", ".join(f"{k} {v}" for k, v in st.most_common()))
    for cat, title, key in (("0", "ПРОДАЖИ", "DEAL_STAGE"), ("3", "ПРОИЗВОДСТВО", "DEAL_STAGE_3")):
        deals = d.deals({"CATEGORY_ID": cat, "CLOSED": "N"})
        order = list(names[key])
        groups = {}
        for x in deals:
            groups.setdefault(x["STAGE_ID"], []).append(x)
        total = sum(float(x.get("OPPORTUNITY") or 0) for x in deals)
        out.append("")
        out.append(f"{title}: {len(deals)} сделок на {fmt_money(total)}")
        for s in sorted(groups, key=lambda s: order.index(s) if s in order else 99):
            g = groups[s]
            out.append(f"  {names[key].get(s, s)}: {len(g)} — {fmt_money(sum(float(x.get('OPPORTUNITY') or 0) for x in g))}")
        stuck = []
        for x in deals:
            moved = datetime.fromisoformat(x["MOVED_TIME"]) if x.get("MOVED_TIME") else datetime.fromisoformat(x["DATE_CREATE"])
            days = (now - moved).days
            if days > STUCK_DAYS[cat] and x["STAGE_ID"] not in NOT_STUCK:
                stuck.append((days, x))
        if stuck:
            out.append(f"  зависли > {STUCK_DAYS[cat]} дн. на стадии: {len(stuck)}")
            for days, x in sorted(stuck, key=lambda t: -t[0])[:10]:
                out.append(f"    сд. {x['ID']} {x['TITLE'][:40]} — {names[key].get(x['STAGE_ID'], x['STAGE_ID'])}, {days} дн.")
        shipped = [x for x in deals if x["STAGE_ID"] == "C3:UC_LTK51B"]
        if shipped:
            old = sum(1 for x in shipped if (now - datetime.fromisoformat(x.get("MOVED_TIME") or x["DATE_CREATE"])).days > 14)
            out.append(f"  «Отгружено» и не закрыто (не переведено в «Клиент получил»): {len(shipped)}, из них дольше 14 дн.: {old}")
        no_sum = [x["ID"] for x in deals if not float(x.get("OPPORTUNITY") or 0)]
        if no_sum:
            out.append(f"  без суммы: {len(no_sum)} (сд. {', '.join(no_sum[:12])}{'…' if len(no_sum) > 12 else ''})")
    return "\n".join(out)


# ---------- клиент ----------

CLIENT_SYSTEM = """Ты помогаешь владельцу компании «90 градусов» (металлообработка, мебель и изделия из металла на заказ) быстро понять историю клиента. Тебе дают карточки CRM и переписку менеджера Екатерины с клиентом (Telegram через Wazzup) и внутренние комментарии в сделках.

Напиши саммари для Telegram простым текстом без markdown (без звёздочек и решёток), до 2500 знаков:
Клиент: кто, откуда пришёл, когда.
Что заказывает: изделия, размеры, материал, финиш.
Договорённости: цена, состав, срок, оплата, доставка — с датами, когда договорились. Если цена или условия менялись — покажи как менялись.
Обещания менеджера и их выполнение.
Где сейчас: стадия, что ждём и от кого.
Риски и несостыковки: расхождения между перепиской и CRM (сумма, стадия, контакт), уступки без согласования, ожидания клиента, которые могут не совпасть с результатом.
Открытые вопросы.

Правила: только факты из данных, неизвестное — «НЕ ИЗВЕСТНО». Не выдумывай. Не переносить паспортные данные, телефоны, почты, адреса. Даты в формате ДД.ММ."""


def find_client(d: Data, q: str):
    """→ (lead|None, [deals]) или строка с вариантами/ошибкой."""
    q = q.strip()
    m = re.fullmatch(r"(л|лид|lead|с|сд|сделка|deal)?\s*(\d+)", q, re.I)
    lead, deals = None, []
    if m:
        kind, num = (m.group(1) or "").lower(), m.group(2)
        if kind in ("", "л", "лид", "lead"):
            r = d.all("crm.lead.list", {"ID": num}, ["*"])
            lead = r[0] if r else None
        if not lead and kind in ("", "с", "сд", "сделка", "deal"):
            r = d.deals({"ID": num})
            if r:
                deals = r
                if r[0].get("LEAD_ID"):
                    lr = d.all("crm.lead.list", {"ID": r[0]["LEAD_ID"]}, ["*"])
                    lead = lr[0] if lr else None
        if not lead and not deals:
            return f"Не нашёл лид или сделку {num}."
    else:
        found = d.all("crm.lead.list", {"%TITLE": q}, ["ID", "TITLE", "STATUS_ID", "DATE_CREATE"], {"ID": "DESC"}, limit=50)
        found_d = d.deals({"%TITLE": q})
        if len(found) + len(found_d) == 0:
            return f"По «{q}» ничего не нашёл. Пришлите номер лида или сделки."
        if len(found) == 1 and not found_d:
            lead = d.all("crm.lead.list", {"ID": found[0]["ID"]}, ["*"])[0]
        elif not found and len(found_d) == 1:
            deals = found_d
        else:
            opts = [f"лид {x['ID']} — {x['TITLE']} ({x['DATE_CREATE'][:10]})" for x in found[:8]]
            opts += [f"сд {x['ID']} — {x['TITLE']}" for x in found_d[:8]]
            return "Нашлось несколько, уточните номером (например «клиент л1251» или «клиент сд761»):\n" + "\n".join(opts)
    if lead:
        deals = {x["ID"]: x for x in deals + d.deals({"LEAD_ID": lead["ID"]})}
        contact = lead.get("CONTACT_ID")
        for x in list(deals.values()):
            contact = contact or x.get("CONTACT_ID")
        if contact:
            for x in d.deals({"CONTACT_ID": contact}):
                deals[x["ID"]] = x
        deals = list(deals.values())
    return lead, deals


def client(d: Data, q: str, llm) -> str:
    res = find_client(d, q)
    if isinstance(res, str):
        return res
    lead, deals = res
    names = d.dicts()
    parts, title = [], ""
    if lead:
        title = f"лид {lead['ID']} {lead['TITLE']}"
        parts.append(f"ЛИД {lead['ID']}: {lead['TITLE']}; статус {names['STATUS'].get(lead['STATUS_ID'])}; "
                     f"источник {d.source_name(lead.get('SOURCE_ID'))}; создан {lead['DATE_CREATE'][:16]}")
        msgs = messages(d.comments("lead", [lead["ID"]], full=True)[str(lead["ID"])])
        parts.append("ПЕРЕПИСКА:\n" + "\n".join(
            f"[{m['time']:%d.%m %H:%M}] {'Екатерина' if m['who'] == 'm' else 'Клиент'}: {m['text']}" for m in msgs))
    for x in deals:
        key = "DEAL_STAGE" if x["CATEGORY_ID"] == "0" else "DEAL_STAGE_3"
        title = title or f"сд {x['ID']} {x['TITLE']}"
        parts.append(f"СДЕЛКА {x['ID']} ({'продажи' if x['CATEGORY_ID'] == '0' else 'производство'}): {x['TITLE']}; "
                     f"стадия {names[key].get(x['STAGE_ID'])}; сумма {fmt_money(x.get('OPPORTUNITY'))}; "
                     f"контакт {'есть' if x.get('CONTACT_ID') else 'НЕТ'}; создана {x['DATE_CREATE'][:10]}")
        com = d.comments("deal", [x["ID"]], full=True)[str(x["ID"])]
        notes = [c for c in com if not messages([c])]
        if notes:
            lines = [f"[{c['CREATED'][:16]}] {BBCODE.sub(' ', c['COMMENT'])[:1500]}" for c in notes]
            parts.append(f"Комментарии в сделке {x['ID']}:\n" + "\n".join(lines))
    text = "\n\n".join(parts)
    if len(text) > 60000:   # длинная история — оставляем начало и конец
        text = text[:20000] + "\n…[середина пропущена]…\n" + text[-40000:]
    return f"👤 {title}\n\n" + llm(CLIENT_SYSTEM, text)


def claude_llm(env):
    import anthropic
    cl = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])

    def call(system, text):
        resp = cl.beta.messages.create(
            model="claude-opus-5-5", max_tokens=8000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": text}],
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        out = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not out:
            raise RuntimeError(f"Claude не ответил: {resp.stop_reason}")
        return out
    return call


if __name__ == "__main__":
    env = load_env()
    d = env_b24(env)
    cmd = sys.argv[1]
    if cmd == "period":
        a = datetime.fromisoformat(sys.argv[2]).replace(tzinfo=MSK)
        b = datetime.fromisoformat(sys.argv[3]).replace(tzinfo=MSK)
        print(period(d, a, b))
    elif cmd == "hanging":
        print(hanging(d))
    elif cmd == "funnel":
        print(funnel(d))
    elif cmd == "client":
        print(client(d, " ".join(sys.argv[2:]), claude_llm(env)))
