"""Telegram-бот КП (@Kp90gradusovbot): задание Екатерины -> КП в фирменном шаблоне.

Как пользоваться — docs/14_КП_правила.md, раздел «Бот». Работает постоянно (launchd, KeepAlive)
на Mac владельца; опрашивает Telegram, ничего не пишет в Б24 (только читает название сделки).

Ключи в .env: KP_TG_BOT_TOKEN, KP_TG_ALLOWED_IDS (числовые ID или ники через запятую),
ANTHROPIC_API_KEY, B24_AGENT_WEBHOOK (необязательно — название сделки).

    .venv/bin/python app/kp/bot.py
"""
import base64
import json
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client_agent"))
import gdrive  # noqa: E402
import make_kp  # noqa: E402
import parse  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contracts"))
import make_contract  # noqa: E402
import parse_contract  # noqa: E402
from agent import load_env  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.invoice_stamp.stamp import StampError, stamp_pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "kp" / "bot"
FILES = DATA / "files"
OUT = ROOT / "data" / "kp" / "out"
STATE = DATA / "state.json"
QUIET_SEC = 6          # всё, что пришло с паузами меньше этого, — одна заявка
MAX_JOBS = 1500        # сколько ссылок «сообщение -> заявка» помнить для правок (несколько на заявку)
NET_TRIES, NET_PAUSE = 3, 5  # повторы при сбое сети: попыток, пауза (с, растёт)
TRIGGER = re.compile(r"#\d+|\bкп\b", re.IGNORECASE)
KP_FOR_CONTRACT_H = 24  # «договор» без ответа на КП и без #ID — берём последнее КП чата за столько часов
CONTRACT = re.compile(r"\bдоговор", re.IGNORECASE)
CONTRACTS_OUT = ROOT / "data" / "contracts" / "out"

HELP = """Присылайте всё, что есть, — файлы и пару слов. Бот поймёт, что нужно, или спросит кнопками.

СЧЁТ С ПОДПИСЬЮ: в 1С «Печать → Счет на оплату» (обычный) → сохранить PDF → прислать сюда. Бот вернёт его с подписью и печатью 90 ГРАДУСОВ.

КП: «КП» (или «#номер сделки») + позиции с утверждёнными ценами и срок, например:
КП #715
1. Стеллаж 1695×350×3000, нержавейка, сатин — 195 000
2. Полки 4 шт 800×250, RAL 9005 — 4 000 за все
Доставка с монтажом 125 000, срок 35
Чертежи и визуализации — фото или PDF в этом же альбоме.

ДОГОВОР — по шагам: напишите «договор» (или нажмите кнопку), дальше присылайте КП и реквизиты заказчика — по одному или вместе, файлом (PDF, Word, фото) или текстом. После каждого бот покажет, что понял и чего не хватает. Когда всё есть — кнопка «Собрать договор». Уточнения — текстом: «оплата 100%», «доставка и монтаж входят, адрес …», «чертежи 6-8 дней».

ПРАВКА готового: ответьте (Reply) на сводку КП или договора — «цена стеллажа 200 000», «убери доставку».
СЧЁТ: пока не выставляю (ждём доступ к 1С).

Файлы: фото, PDF (читаю целиком), PNG/JPG, Word (.docx). Голосовые не разбираю.
/new — начать заново."""


def log(msg):
    DATA.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(DATA / "bot.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


class TG:
    def __init__(self, token):
        self.sent = []  # id отправленных сообщений (execute собирает их по заявке)
        self.base = f"https://api.telegram.org/bot{token}/"
        self.file_base = f"https://api.telegram.org/file/bot{token}/"

    def call(self, method, params=None, timeout=40):
        """Запрос к Telegram. Сетевые сбои (не ответы Telegram) повторяются до 3 раз, кроме getUpdates."""
        data = json.dumps(params or {}).encode()
        tries = 1 if method == "getUpdates" else NET_TRIES
        for attempt in range(tries):
            req = urllib.request.Request(self.base + method, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    res = json.load(r)
                break
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Telegram {method}: HTTP {e.code} {e.read().decode(errors='ignore')[:200]}")
            except (urllib.error.URLError, OSError) as e:
                if attempt == tries - 1:
                    raise
                log(f"Telegram {method}: сбой сети ({e}), повтор")
                time.sleep(NET_PAUSE * (attempt + 1))
        if method != "getUpdates":
            log(f"Telegram {method}: ok")
        if not res.get("ok"):
            raise RuntimeError(f"Telegram {method}: {res.get('description')}")
        if method == "sendMessage":
            self.sent.append(res["result"]["message_id"])
        return res["result"]

    def send(self, chat, text, reply_to=None):
        p = {"chat_id": chat, "text": text[:4000]}
        if reply_to:
            p["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return self.call("sendMessage", p)

    def ask(self, chat, text, buttons, reply_to=None):
        """Сообщение с кнопками: buttons — [(подпись, данные)]."""
        p = {"chat_id": chat, "text": text[:4000],
             "reply_markup": {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}}
        if reply_to:
            p["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return self.call("sendMessage", p)

    def send_file(self, chat, path, caption="", reply_to=None):
        """Файл через curl (свой multipart на urllib Telegram отвергал с 413). Токен — через stdin, не в argv."""
        cfg = f'url = "{self.base}sendDocument"\n'
        args = ["curl", "-sS", "--max-time", "180", "--retry", str(NET_TRIES), "--retry-all-errors", "-K", "-", "-F", f"chat_id={chat}", "-F", f"document=@{path}"]
        if caption:
            args += ["-F", f"caption={caption[:1000]}"]
        if reply_to:
            args += ["-F", "reply_parameters=" + json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})]
        r = subprocess.run(args, input=cfg, capture_output=True, text=True, timeout=200)
        try:
            res = json.loads(r.stdout)
        except ValueError:
            raise RuntimeError(f"Telegram sendDocument: curl {r.returncode} {r.stderr.strip()[:200]}")
        if not res.get("ok"):
            raise RuntimeError(f"Telegram sendDocument: {res.get('description')}")
        log(f"Telegram sendDocument: ok ({path.name})")
        self.sent.append(res["result"]["message_id"])
        return res["result"]

    def download(self, file_id, dest):
        f = self.call("getFile", {"file_id": file_id})
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(self.file_base + f["file_path"], timeout=120) as r:
            dest.write_bytes(r.read())
        return dest


def load_state():
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"offset": 0, "jobs": {}}
    st.setdefault("sessions", {})  # чат -> договор, который собирается пошагово
    return st


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def rich_text(node):
    """Текст из rich_message (оформленное сообщение Telegram): блок — с новой строки, у пункта списка — его номер."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(rich_text(x) for x in node)
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("text"), str):
        return node["text"] + "\n"
    out = f"{node['label']} " if isinstance(node.get("label"), str) else ""
    return out + "".join(rich_text(v) for v in node.values() if isinstance(v, (dict, list)))


def message_text(msg):
    if msg.get("text") or msg.get("caption"):
        return (msg.get("text") or msg.get("caption")).strip()
    if msg.get("rich_message"):
        (DATA / "last_rich.json").write_text(json.dumps(msg["rich_message"], ensure_ascii=False, indent=1), encoding="utf-8")
        return rich_text(msg["rich_message"]).strip()
    return ""


def allowed(user, allow):
    return str(user.get("id")) in allow or (user.get("username") or "").lower() in allow


PDF_PAGES = 6  # страниц PDF -> картинки для КП (чертежи)
DOC_EXT = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
           "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}


def download(tg, msg):
    """Файлы сообщения -> локальные пути: фото (крупнейший размер), PDF, PNG/JPG, Word (.docx)."""
    chat = msg["chat"]["id"]
    out = []
    if msg.get("photo"):
        ph = msg["photo"][-1]
        out.append(tg.download(ph["file_id"], FILES / str(chat) / f"{ph['file_unique_id']}.jpg"))
    doc = msg.get("document")
    if doc:
        name = doc.get("file_name") or ""
        ext = DOC_EXT.get(doc.get("mime_type", "")) or (Path(name).suffix.lower() if Path(name).suffix.lower() in
                                                       (".pdf", ".png", ".jpg", ".jpeg", ".docx") else None)
        if ext:
            out.append(tg.download(doc["file_id"], FILES / str(chat) / f"{doc['file_unique_id']}{ext}"))
        else:
            tg.send(chat, f"Файл «{name}» не читаю: подходят фото, PDF, PNG/JPG и Word (.docx).", msg["message_id"])
    return out


INVOICE_NAME = re.compile(r"^сч[её]т на оплату", re.IGNORECASE)
INVOICE_WORDS = re.compile(r"подпис|печат|факсимил", re.IGNORECASE)


def is_invoice_pdf(msg, text):
    """PDF «Счет на оплату» из 1С (по имени файла) или PDF с подписью «подпись/печать»."""
    doc = msg.get("document") or {}
    name = doc.get("file_name") or ""
    if not (doc.get("mime_type") == "application/pdf" or name.lower().endswith(".pdf")):
        return False
    return bool(INVOICE_NAME.match(name) or INVOICE_WORDS.search(text or ""))


def stamp_invoice(tg, msg, files):
    """Подпись и печать 90 ГРАДУСОВ на счёт из 1С -> PDF в ответ (app/invoice_stamp)."""
    chat = msg["chat"]["id"]
    name = (msg.get("document") or {}).get("file_name") or "Счет.pdf"
    if not files:
        return
    try:
        out = stamp_pdf(files[0], FILES / str(chat) / f"{Path(name).stem} (подпись).pdf")
        tg.send_file(chat, out, "Подпись и печать поставлены. Проверьте перед отправкой клиенту.", msg["message_id"])
        log(f"счёт с подписью: {name}")
    except StampError as e:
        tg.send(chat, f"Не смог поставить подпись: {e}", msg["message_id"])
    except Exception as e:
        log(f"счёт: {e}\n{traceback.format_exc()}")
        tg.send(chat, "Не получилось поставить подпись на счёт — напишите Дмитрию.", msg["message_id"])


def pdf_pages(path, limit=PDF_PAGES):
    """PDF -> PNG страниц (для картинок КП)."""
    import fitz  # pymupdf
    doc = fitz.open(path)
    out = []
    for i in range(min(len(doc), limit)):
        png = path.with_name(f"{path.stem}_p{i + 1}.png")
        if not png.exists():
            doc[i].get_pixmap(dpi=150).save(png)
        out.append(png)
    return out


def docx_text(path):
    from docx import Document
    d = Document(str(path))
    lines = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = []
            for c in row.cells:
                if c.text.strip() and c.text.strip() not in cells:
                    cells.append(c.text.strip())
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def split_files(files):
    """-> (картинки, PDF, текст Word-файлов)."""
    files = [Path(f) for f in files]
    imgs = [f for f in files if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
    pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
    texts = [f"Файл «{f.name}» (Word):\n{docx_text(f)}" for f in files if f.suffix.lower() == ".docx"]
    return imgs, pdfs, "\n\n".join(texts)


def deal_title(env, deal_id):
    api = b24(env)
    if not deal_id or not api:
        return None
    try:
        return retry(lambda: api.call("crm.deal.get", {"id": deal_id}), "Б24: сделка")["result"].get("TITLE")
    except Exception as e:
        log(f"Б24: сделка {deal_id} не прочитана ({e})")
        return None


PP_DIR = Path.home() / "Library/Containers/com.microsoft.Powerpoint/Data/kp_render"  # своя папка PowerPoint: без запросов доступа


def render_pdf(pptx):
    """PDF через PowerPoint (для просмотра с телефона). Не вышло — None, КП всё равно отправляется.

    Файлы кладём в папку PowerPoint и с латинскими именами: в чужие папки он каждый раз спрашивает доступ,
    а PDF с кириллицей в имени не сохраняет."""
    pdf = pptx.resolve().with_suffix(".pdf")
    tag = uuid.uuid4().hex[:8]
    src, tmp = PP_DIR / f"kp_{tag}.pptx", PP_DIR / f"kp_{tag}.pdf"
    script = (f'tell application "Microsoft PowerPoint"\nopen POSIX file "{src}"\ndelay 4\n'
              f'save active presentation in POSIX file "{tmp}" as save as PDF\n'
              f'close active presentation saving no\nend tell')
    try:
        PP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pptx, src)
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=120)
        shutil.move(str(tmp), pdf)
        return pdf
    except Exception as e:
        err = getattr(e, "stderr", b"") or b""
        log(f"PDF не сделан ({type(e).__name__}: {err.decode(errors='ignore').strip() if isinstance(err, bytes) else err or e})")
        return None
    finally:
        for f in (src, tmp):
            f.unlink(missing_ok=True)


def retry(fn, what):
    """Повтор при сбое сети/сервиса (Drive): NET_TRIES попыток с растущей паузой."""
    for attempt in range(NET_TRIES):
        try:
            return fn()
        except Exception as e:
            if attempt == NET_TRIES - 1 or str(e).startswith("Б24 "):  # ответ Б24 (нет сделки, нет прав) не повторяем
                raise
            log(f"{what}: сбой ({type(e).__name__}: {e}), повтор")
            time.sleep(NET_PAUSE * (attempt + 1))


def b24(env):
    from b24 import B24
    hook = env.get("KP_B24_WEBHOOK") or env.get("B24_AGENT_WEBHOOK")
    return B24(hook) if hook else None


def deal_comment(env, deal_id, title, path, link, pdf, items_total, log_sum, term, sender):
    """Комментарий в сделку Б24: ссылка на КП и PDF (решение 15.10.2026: чтение сделки и комментарий с КП).
    Единственная запись бота в Б24. -> строка для сводки или None."""
    if not deal_id or env.get("KP_B24_COMMENT", "1") != "1":
        return None
    api = b24(env)
    if not api:
        return "Б24: вебхук не настроен, комментарий в сделку не добавлен."
    if not title:  # deal_title вернул None — сделки нет или Б24 недоступен
        return f"Б24: сделка #{deal_id} не найдена, комментарий не добавлен. Проверьте номер."
    lines = [f"КП: {path.stem}"]
    if link:
        lines.append(f"Google Презентация: {link}")
    lines.append(f"Изделия: {make_kp.money(items_total)}" + (f"; доставка/монтаж: {make_kp.money(log_sum)}" if log_sum else "")
                 + f"; срок {term} раб. дн.")
    lines.append(f"Собрано ботом КП по сообщению @{sender}." if sender else "Собрано ботом КП.")
    fields = {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal", "COMMENT": "\n".join(lines).replace("\u00a0", " ")}
    if pdf:
        fields["FILES"] = [[pdf.name, base64.b64encode(pdf.read_bytes()).decode()]]
    try:
        retry(lambda: api.call("crm.timeline.comment.add", {"fields": fields}), "Б24: комментарий")
        log(f"Б24: комментарий в сделку {deal_id} ({path.stem})")
        return f"Б24: ссылка и PDF добавлены комментарием в сделку #{deal_id}."
    except Exception as e:
        log(f"Б24: комментарий в сделку {deal_id} не добавлен ({type(e).__name__}: {e})")
        return f"Б24: не удалось добавить комментарий в сделку #{deal_id} ({e})."


def summary(kp, path, items_total, log_sum, warns, link=None):
    lines = [f"Готово: {path.stem}"] + ([f"Открыть и править: {link}"] if link else []) + [""]
    for it in kp.items:
        s = sum(p.price for p in it.parts) if it.parts else it.price
        lines.append(f"• {it.name}{f' ({it.quantity} шт.)' if it.quantity != 1 else ''}: {make_kp.money(s)}")
    lines.append(f"ИТОГО изделия: {make_kp.money(items_total)}")
    if log_sum:
        lines.append(f"Доставка/монтаж: {make_kp.money(log_sum)}")
    lines.append(f"Срок: {kp.production_term} раб. дней")
    notes = list(kp.questions) + warns
    if notes:
        lines += ["", "Проверьте:"] + [f"– {x}" for x in notes]
    lines += ["", "Правка — ответьте на это сообщение."]
    return "\n".join(lines).replace(" ", " ")


def run_job(tg, client, env, chat, reply_to, text, images, previous=None, sender=""):
    """Разобрать задание (или правку), собрать КП, ответить. -> (id сообщения с результатом, KP, название сделки)."""
    tg.send(chat, "Собираю КП, это около минуты…", reply_to)
    prev = parse.KP.model_validate(previous["kp"]) if previous else None
    kp = parse.parse(client, text, images, prev)
    title = (previous or {}).get("client") or deal_title(env, kp.deal_id)
    miss = parse.missing(kp)
    if miss:
        m = tg.send(chat, "Не хватает данных, КП не собрано:\n" + "\n".join(f"– {x}" for x in miss + kp.questions)
                    + "\n\nОтветьте на это сообщение недостающим (например, «срок 35»).", reply_to)
        return m["message_id"], kp, title
    data = parse.to_make_kp(kp, images, title, date.today().isoformat())
    try:
        path, items_total, log_sum, _, warns = make_kp.build(data, make_kp.TEMPLATE, OUT)
    except make_kp.KPError as e:
        m = tg.send(chat, f"КП не собрано: {e}\nОтветьте на это сообщение исправлением.", reply_to)
        return m["message_id"], kp, title
    link = pdf = None
    if gdrive.ready():
        try:
            fid, link = retry(lambda: gdrive.upload(path), "Drive: загрузка")
            pdf = retry(lambda: gdrive.export_pdf(fid, path.with_suffix(".pdf")), "Drive: PDF")
            tg.send_file(chat, pdf, f"{path.stem}\nGoogle Презентация: {link}", reply_to)
        except Exception as e:
            log(f"Drive: не загружено ({type(e).__name__}: {e})")
            link = pdf = None
    if not link:  # без Drive — исходный .pptx (+ PDF через PowerPoint, если включён)
        pdf = render_pdf(path) if env.get("KP_RENDER_PDF", "0") == "1" else None
        if pdf:
            tg.send_file(chat, pdf, "Для просмотра", reply_to)
        tg.send_file(chat, path, "", reply_to)
    b24_note = deal_comment(env, kp.deal_id, title, path, link, pdf, items_total, log_sum, kp.production_term, sender)
    m = tg.send(chat, summary(kp, path, items_total, log_sum, warns, link) + (f"\n\n{b24_note}" if b24_note else ""), reply_to)
    log(f"чат {chat}: {path.name}, позиций {len(kp.items)}, итого {items_total}, ссылка {'есть' if link else 'нет'}")
    return m["message_id"], kp, title


def find_kp(st, chat, text):
    """КП для договора без ответа на КП: по «#номеру» из текста, иначе последнее КП этого чата за сутки."""
    m = re.search(r"#(\d+)", text)
    since = (datetime.now() - timedelta(hours=KP_FOR_CONTRACT_H)).isoformat(timespec="seconds")
    for key, job in reversed(list(st["jobs"].items())):
        if not key.startswith(f"{chat}:") or job.get("kind", "kp") != "kp":
            continue
        if m and job["kp"].get("deal_id") == m.group(1):
            return job
        if not m and job.get("at", "") >= since:
            return job
    return None


def contract_summary(r, c, link, kp_name=None):
    pay = "; ".join(f"{p}% - {make_kp.money(a)}" for p, a in r["payments"])
    lines = [f"Договор № {r['number']} от {r['date']}"] + ([f"Открыть и править: {link}"] if link else []) + (
        [f"Позиции и цены — из КП: {kp_name}"] if kp_name else []) + [
        "", f"Заказчик: {make_contract.customer_short(make_contract.dashes(parse_contract.to_make_contract(c))['customer'])}",
        f"Итого: {make_kp.money(r['total'])}", f"Оплата: {pay}",
        f"Срок производства: {c.schedule.production_days} раб. дней"]
    notes = list(c.questions)[:3] + r["warnings"]
    if notes:
        lines += ["", "Проверьте:"] + [f"– {x}" for x in notes]
    lines += ["", "Перед отправкой клиенту просмотрите каждую страницу (чек-лист договоров).",
              "Правка — ответьте на это сообщение."]
    return "\n".join(lines).replace("\u00a0", " ")


def contract_missing(c):
    """Чего не хватает для договора — человеческими словами."""
    out, cu = [], c.customer
    if not cu.full_name or (cu.type in ("ООО", "ИП") and not cu.inn):
        out.append("реквизиты заказчика (карточка предприятия: наименование, ИНН, ОГРН) или данные физлица")
    signer = cu.representative.name_parts if cu.type == "ООО" else cu.name_parts
    if cu.type == "ООО" and cu.full_name and not (cu.representative.name and cu.representative.position):
        out.append("кто подписывает от заказчика: должность и ФИО полностью")
    elif cu.full_name and signer.last and not signer.middle:
        out.append("отчество подписанта (нужно для номера договора)")
    if not c.items:
        out.append("позиции с ценами (КП файлом или ответом на сводку КП)")
    elif any(not (it.unit_price or it.total) for it in c.items):
        out.append("цены у всех позиций")
    if not c.schedule.production_days:
        out.append("срок производства (рабочих дней)")
    return out


def contract_card(c):
    """Что бот понял по договору — показать перед сборкой. -> (текст, готово ли)."""
    cu, lines = c.customer, ["Договор — что есть сейчас:", ""]
    if cu.full_name:
        who = {"ООО": cu.short_name or cu.full_name, "ИП": f"ИП {cu.full_name}"}.get(cu.type, cu.full_name)
        ids = ", ".join(x for x in (f"ИНН {cu.inn}" if cu.inn else "", f"ОГРН {cu.ogrn}" if cu.ogrn else "",
                                    f"ОГРНИП {cu.ogrnip}" if cu.ogrnip else "") if x)
        lines.append(f"✅ Заказчик: {who}" + (f" ({ids})" if ids else ""))
        if cu.type == "ООО" and cu.representative.name:
            lines.append(f"✅ Подписывает: {cu.representative.position} {cu.representative.name}, "
                         f"на основании {cu.representative.basis_genitive or 'Устава'}")
        if cu.type == "физлицо":
            lines.append("✅ Паспорт: есть" if cu.passport.number else "❗ Паспорт: нет (можно без него, но лучше добавить)")
    if c.items:
        total = sum(it.total or it.unit_price * it.quantity for it in c.items)
        lines.append(f"✅ Позиции: {len(c.items)} на {make_kp.money(total)}")
        lines += [f"   • {it.name} - {it.quantity} {it.unit or 'шт.'}: {make_kp.money(it.total or it.unit_price * it.quantity)}"
                  for it in c.items[:8]]
    sv = c.services
    lines.append(f"✅ {sv.label or 'Доставка и монтаж'}: входят, {make_kp.money(sv.price)}" if sv.included
                 else "✅ Доставка и монтаж: не входят")
    if c.schedule.production_days:
        lines.append(f"✅ Срок производства: {c.schedule.production_days} раб. дней"
                     + (f"; чертежи {c.schedule.drawings_days} раб. дней" if c.schedule.drawings_days else ""))
    lines.append("✅ Оплата: " + (" / ".join(f"{p.percent}%" for p in c.payment) if c.payment else "70/30 (по умолчанию)"))
    miss = contract_missing(c)
    if miss:
        lines += ["", "Не хватает:"] + [f"❗ {x}" for x in miss]
    if c.questions:
        lines += ["", "Проверьте:"] + [f"– {q}" for q in c.questions[:3]]
    lines += ["", "Пришлите недостающее или исправление — файлом или текстом." if miss else
              "Если всё верно — «Собрать договор». Исправить можно текстом: «оплата 100%», «доставка входит, адрес …»."]
    return "\n".join(lines).replace("\u00a0", " "), not miss


def send_contract(tg, chat, reply_to, c, kp=None):
    """Собрать DOCX, Google Документ и PDF, отправить. -> id сообщения со сводкой."""
    tg.send(chat, "Собираю договор…", reply_to)
    try:
        r = make_contract.build(parse_contract.to_make_contract(c), CONTRACTS_OUT)
    except make_contract.ContractError as e:
        log(f"чат {chat}: договор не собран: {e}")
        return tg.send(chat, f"Договор не собран: {e}\nПришлите исправление ответом на это сообщение.", reply_to)["message_id"]
    path, link = r["path"], None
    if gdrive.ready():
        try:
            fid, link = retry(lambda: gdrive.upload(path, "contract"), "Drive: договор")
            pdf = retry(lambda: gdrive.export_pdf(fid, path.with_suffix(".pdf")), "Drive: PDF договора")
            tg.send_file(chat, pdf, f"Договор № {r['number']} (PDF)", reply_to)
        except Exception as e:
            log(f"Drive: договор не загружен ({type(e).__name__}: {e})")
            link = None
    tg.send_file(chat, path, "DOCX для Word / Google Docs", reply_to)
    kp_name = ", ".join(i.get("name", "") for i in (kp or {}).get("items", [])) if kp else None
    m = tg.send(chat, contract_summary(r, c, link, kp_name), reply_to)
    log(f"чат {chat}: договор {r['number']}, итого {r['total']}, ссылка {'есть' if link else 'нет'}")
    return m["message_id"]


INVOICE = re.compile(r"\b(выстав\w*|нуж\w*|сдела\w*|сформир\w*|подготов\w*)\b[^.\n]{0,25}\bсч[её]т|^\s*сч[её]т\b",
                     re.IGNORECASE | re.MULTILINE)
INVOICE_REPLY = ("Счета я пока не выставляю: ждём доступ к 1С, после него бот будет делать счёт по реквизитам "
                 "из договора или карточки. Сейчас счёт - в 1С вручную (инструкция docs/16).")
WAIT_MIN = 30  # заявка без понятного действия ждёт кнопку или уточнение столько минут


def decide(bundle, session=None):
    """Что делать с заявкой: (действие, доп.). Действия: kp, contract, session, build, invoice, ask."""
    text, job = bundle["text"], bundle.get("reply_job")
    if session and not job:  # идёт сбор договора: всё присланное — туда
        if BUILD_WORDS.match(text) and not bundle["files"]:
            return "build", {}
        if TRIGGER.search(text) and not CONTRACT.search(text) and re.search(r"\bкп\b", text, re.IGNORECASE):
            return "kp", {}
        return "session", {}
    if job and job.get("kind") == "contract":
        return "contract", {"previous": job}
    if job:  # ответ на КП
        if CONTRACT.search(text):
            return "contract", {"kp_job": job}
        if INVOICE.search(text):
            return "invoice", {}
        return "kp", {"previous": job}
    if CONTRACT.search(text):
        return "contract", {}
    if INVOICE.search(text):
        return "invoice", {}
    if TRIGGER.search(text):
        return "kp", {}
    return "ask", {}


SESSION_H = 3  # сессия договора живёт столько часов без сообщений
BUILD_WORDS = re.compile(r"^\s*(собер\w*|собрать|готово|ок|ok|да)\s*[.!]*\s*$", re.IGNORECASE)


def session_absorb(tg, client, st, chat, b, sess):
    """Добавить в сессию договора то, что прислали (файлы/текст), и показать карточку с кнопками."""
    imgs, pdfs, words = split_files(b["files"])
    text = "\n\n".join(x for x in (b["text"], words) if x) or "(файлы без текста)"
    tg.send(chat, "Читаю…" if sess.get("contract") else "Готовлю договор. Читаю присланное…", b["reply_to"])
    prev = parse_contract.Contract.model_validate(sess["contract"]) if sess.get("contract") else None
    c = parse_contract.parse(client, text, imgs, sess.get("kp"), prev, pdfs)
    sess.update(contract=c.model_dump(), at=datetime.now().isoformat(timespec="seconds"))
    sess.setdefault("files", []).extend(b["files"])
    card, ready = contract_card(c)
    buttons = ([("Собрать договор", f"build:{chat}")] if ready else []) + [("Отмена", f"cancel:{chat}")]
    sess["card"] = tg.ask(chat, card, buttons, b["reply_to"])["message_id"]
    st["sessions"][str(chat)] = sess
    save_state(st)
    log(f"чат {chat}: договор, сессия: позиций {len(c.items)}, заказчик {'есть' if c.customer.full_name else 'нет'}, готово {ready}")


def session_build(tg, st, chat, reply_to):
    sess = st["sessions"].pop(str(chat), None)
    save_state(st)
    if not sess or not sess.get("contract"):
        tg.send(chat, "Нет начатого договора. Напишите «договор» и пришлите КП и реквизиты.", reply_to)
        return
    c = parse_contract.Contract.model_validate(sess["contract"])
    tg.sent = []
    mid = send_contract(tg, chat, reply_to, c, sess.get("kp"))
    job = {"kind": "contract", "contract": sess["contract"], "kp": sess.get("kp"),
           "pdfs": [f for f in sess.get("files", []) if f.endswith(".pdf")], "at": datetime.now().isoformat(timespec="seconds")}
    for m_id in set(tg.sent + [mid]):
        st["jobs"][f"{chat}:{m_id}"] = job
    st["jobs"] = dict(list(st["jobs"].items())[-MAX_JOBS:])
    save_state(st)


def active_session(st, chat):
    sess = st["sessions"].get(str(chat))
    if sess and datetime.fromisoformat(sess["at"]) > datetime.now() - timedelta(hours=SESSION_H):
        return sess
    st["sessions"].pop(str(chat), None)
    return None


def execute(tg, client, env, st, chat, b, action, extra):
    """Запустить КП, начать/дополнить договор или поправить готовый договор."""
    tg.sent = []
    imgs, pdfs, words = split_files(b["files"])
    text = "\n\n".join(x for x in (b["text"], words) if x)
    if action == "contract" and extra.get("previous"):  # правка готового договора — сразу пересборка
        prev = extra["previous"]
        tg.send(chat, "Вношу исправление…", b["reply_to"])
        c = parse_contract.parse(client, text, imgs, prev.get("kp"), parse_contract.Contract.model_validate(prev["contract"]),
                                 pdfs + [Path(x) for x in prev.get("pdfs", [])])
        mid = send_contract(tg, chat, b["reply_to"], c, prev.get("kp"))
        job = {**prev, "contract": c.model_dump(), "pdfs": prev.get("pdfs", []) + [str(x) for x in pdfs],
               "at": datetime.now().isoformat(timespec="seconds")}
    elif action == "contract":  # новый договор — пошагово
        kp_job = extra.get("kp_job") or find_kp(st, chat, b["text"])
        sess = {"kp": (kp_job or {}).get("kp"), "at": datetime.now().isoformat(timespec="seconds")}
        if kp_job:
            tg.send(chat, "Беру позиции и цены из КП: " + ", ".join(i.get("name", "") for i in sess["kp"].get("items", [])), b["reply_to"])
        session_absorb(tg, client, st, chat, b, sess)
        return
    elif action == "session":
        session_absorb(tg, client, st, chat, b, active_session(st, chat))
        return
    else:
        prev = extra.get("previous")
        images = [str(x) for x in imgs] + [str(x) for f in pdfs for x in pdf_pages(f)]
        images = (prev or {}).get("images", []) + images
        mid, kp, title = run_job(tg, client, env, chat, b["reply_to"], text, [Path(x) for x in images], prev, b.get("sender", ""))
        job = {"kp": kp.model_dump(), "images": images, "client": title, "at": datetime.now().isoformat(timespec="seconds")}
    for m_id in set(tg.sent + [mid, b["reply_to"]] + b.get("msg_ids", [])):
        st["jobs"][f"{chat}:{m_id}"] = job
    st["jobs"] = dict(list(st["jobs"].items())[-MAX_JOBS:])
    save_state(st)


def main():
    env = load_env()
    tg = TG(env["KP_TG_BOT_TOKEN"])
    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    allow = {x.strip().lstrip("@").lower() for x in env.get("KP_TG_ALLOWED_IDS", "").split(",") if x.strip()}
    st = load_state()
    inbox = {}    # chat -> заявка, которая ещё собирается: {"texts", "files", "reply_to", "reply_job", "last", "sender"}
    waiting = {}  # id -> заявка, ждущая кнопку «КП / Договор»
    outage = None
    log("бот запущен")
    while True:
        try:
            ups = tg.call("getUpdates", {"offset": st["offset"], "timeout": 2 if inbox else 25,
                                         "allowed_updates": ["message", "callback_query"]}, timeout=40)
        except Exception as e:
            if outage is None:
                outage = {"since": time.time(), "count": 0}
                log(f"нет связи с Telegram: {e}")
            outage["count"] += 1
            time.sleep(10)
            continue
        if outage:
            log(f"связь с Telegram восстановлена: {outage['count']} неудачных запросов за {(time.time() - outage['since']) / 60:.0f} мин")
            outage = None

        for u in ups:
            st["offset"] = u["update_id"] + 1
            cb = u.get("callback_query")
            if cb:  # нажата кнопка «КП» / «Договор»
                try:
                    tg.call("answerCallbackQuery", {"callback_query_id": cb["id"]})
                except Exception:
                    pass
                if not allowed(cb.get("from", {}), allow):
                    continue
                action, _, bid = (cb.get("data") or "").partition(":")
                chat = cb["message"]["chat"]["id"]
                if action in ("build", "cancel"):
                    log(f"кнопка: {action} чат {chat}")
                    try:
                        if action == "build":
                            session_build(tg, st, chat, cb["message"]["message_id"])
                        else:
                            st["sessions"].pop(str(chat), None)
                            save_state(st)
                            tg.send(chat, "Договор отменён.")
                    except Exception as e:
                        log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                        tg.send(chat, f"Ошибка: {e}. Попробуйте ещё раз или напишите Дмитрию.")
                    continue
                b = waiting.pop(bid, None)
                log(f"кнопка: {action} заявка {bid} ({'есть' if b else 'нет'})")
                if not b:
                    tg.send(chat, "Эта заявка устарела. Пришлите файлы и задание ещё раз.")
                    continue
                try:
                    execute(tg, client, env, st, chat, b, action, {})
                except Exception as e:
                    log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                    tg.send(chat, f"Ошибка: {e}. Попробуйте ещё раз или напишите Дмитрию.", b["reply_to"])
                continue
            msg = u.get("message")
            if not msg or msg["chat"]["type"] != "private":
                continue
            chat, user = msg["chat"]["id"], msg.get("from", {})
            log(f"входящее: id={user.get('id')} @{user.get('username')} текст={len(message_text(msg))} "
                f"фото={bool(msg.get('photo'))} файл={bool(msg.get('document'))} ответ_на={msg.get('reply_to_message', {}).get('message_id')} "
                f"поля={sorted(k for k in msg if k not in ('chat', 'from', 'date', 'message_id'))}")
            if not allowed(user, allow):
                log(f"чужой: id={user.get('id')} @{user.get('username')}")
                tg.send(chat, "Бот только для сотрудников «90 градусов».")
                continue
            text = message_text(msg)
            if text in ("/start", "/help"):
                tg.send(chat, HELP)
                continue
            if text == "/new":
                inbox.pop(chat, None)
                st["sessions"].pop(str(chat), None)
                for k in [k for k, w in waiting.items() if w["chat"] == chat]:
                    waiting.pop(k)
                tg.send(chat, "Сбросил присланное. Можно начинать заново.")
                continue
            if not text and not (msg.get("photo") or msg.get("document")):
                kind = next((k for k in ("voice", "audio", "video", "video_note", "sticker", "animation") if msg.get(k)), None)
                if kind:
                    tg.send(chat, "Голосовые, видео и стикеры я не разбираю - напишите текстом. Файлы: фото, PDF, Word.", msg["message_id"])
                continue
            try:
                files = download(tg, msg)
            except Exception as e:
                log(f"файл не скачан: {e}")
                tg.send(chat, "Не удалось скачать файл, пришлите его ещё раз.", msg["message_id"])
                files = []
            if is_invoice_pdf(msg, text):
                stamp_invoice(tg, msg, files)
                continue
            reply = msg.get("reply_to_message", {}).get("message_id")
            job = st["jobs"].get(f"{chat}:{reply}") if reply else None
            b = inbox.get(chat)
            if b is None or (job and job is not b.get("reply_job")):
                b = inbox[chat] = {"chat": chat, "texts": [], "files": [], "reply_to": msg["message_id"], "reply_job": job,
                                   "sender": user.get("username") or ""}
            if text:
                b["texts"].append(text)
            rt = msg.get("reply_to_message") or {}
            if not job and (rt.get("photo") or rt.get("document")):  # ответ на ранее присланный файл — взять его
                try:
                    b["files"] += [str(f) for f in download(tg, rt) if str(f) not in b["files"]]
                except Exception as e:
                    log(f"файл из ответа не скачан: {e}")
            b["files"] += [str(f) for f in files]
            b.setdefault("msg_ids", []).append(msg["message_id"])
            b["last"] = time.time()
        save_state(st)

        # Заявка собрана (тишина QUIET_SEC) — решаем, что делать.
        for chat, b in list(inbox.items()):
            if time.time() - b["last"] < QUIET_SEC:
                continue
            inbox.pop(chat)
            if not b["texts"] and not b["files"]:
                continue
            if not b.get("reply_job"):  # дописали к заявке, которая ждёт кнопку, — объединяем
                for k, w in list(waiting.items()):
                    if w["chat"] == chat and time.time() - w["last"] < WAIT_MIN * 60:
                        b["texts"] = w["texts"] + b["texts"]
                        b["files"] = w["files"] + b["files"]
                        b["reply_to"] = w["reply_to"]
                        b["msg_ids"] = w.get("msg_ids", []) + b.get("msg_ids", [])
                        waiting.pop(k)
            b["text"] = "\n".join(b["texts"])
            action, extra = decide(b, active_session(st, chat))
            if action == "build":
                try:
                    session_build(tg, st, chat, b["reply_to"])
                except Exception as e:
                    log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                    tg.send(chat, f"Ошибка: {e}. Попробуйте ещё раз или напишите Дмитрию.", b["reply_to"])
                continue
            log(f"чат {chat}: заявка -> {action} (файлов {len(b['files'])}, текст {len(b['text'])})")
            if action == "invoice":
                tg.send(chat, INVOICE_REPLY, b["reply_to"])
                if b["files"]:
                    action = "ask"
                else:
                    continue
            if action == "ask":
                bid = uuid.uuid4().hex[:8]
                waiting[bid] = b
                n = len(b["files"])
                what = f"Получил {n} файл(а)" + (" и текст" if b["text"] else "") if n else "Получил текст"
                tg.ask(chat, f"{what}. Что сделать?", [("Сделать КП", f"kp:{bid}"), ("Сделать договор", f"contract:{bid}")],
                       b["reply_to"])
                continue
            try:
                execute(tg, client, env, st, chat, b, action, extra)
            except Exception as e:
                log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                try:
                    tg.send(chat, f"Ошибка: {e}. Попробуйте ещё раз или напишите Дмитрию.", b["reply_to"])
                except Exception:
                    pass
        for k, w in list(waiting.items()):  # старые заявки без выбора — забыть
            if time.time() - w["last"] > WAIT_MIN * 60:
                waiting.pop(k)


if __name__ == "__main__":
    main()
