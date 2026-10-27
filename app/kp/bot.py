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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tz_cards"))
import card  # noqa: E402
import gdrive  # noqa: E402
import tgread  # noqa: E402
import make_kp  # noqa: E402
import orders  # noqa: E402
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
ALBUM_QUIET_SEC = 15   # для альбома (media_group): Telegram досылает файлы альбома с задержкой
MAX_JOBS = 1500        # сколько ссылок «сообщение -> заявка» помнить для правок (несколько на заявку)
NET_TRIES, NET_PAUSE = 3, 5  # повторы при сбое сети: попыток, пауза (с, растёт)
CONTRACTS_OUT = ROOT / "data" / "contracts" / "out"

HELP = """Бот ведёт ЗАКАЗЫ. Присылайте всё по клиенту — задание, КП, реквизиты, чертежи, — в любом порядке, файлами или текстом. Бот сам поймёт, что это, и сложит в заказ.

• Новый заказ начинается сам (или «#715» — заказ по сделке, или кнопка «Новый заказ»). Переключиться — «#номер» или кнопка «Заказы».
• После каждого сообщения — карточка заказа: что есть, чего не хватает, кнопки «Собрать КП», «Собрать договор».
• КП собирается сразу, когда есть позиции с ценами и срок. Пример задания:
  КП #715: Стеллаж 1695×350×3000, нержавейка, сатин — 195 000; полки 4 шт 800×250, RAL 9005 — 4 000 за все; доставка с монтажом 125 000; срок 35
• Договор — когда есть КП и реквизиты заказчика: кнопка «Собрать договор» или слово «договор». Условия — текстом: «оплата 100%», «чертежи 6-8 дней», «доставка и монтаж входят, адрес …».
• Исправить — просто напишите: «цена стеллажа 200 000». Документы, которые устарели, карточка предложит обновить.
• Счёт: выставьте в 1С, PDF сюда — поставлю подпись и печать.

Файлы: фото, PDF, PNG/JPG, Word. Голосовые не разбираю.
/orders — заказы, /new — новый заказ."""


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

    def ask(self, chat, text, buttons, reply_to=None, per_row=2):
        """Сообщение с кнопками: buttons — [(подпись, данные)], по per_row в ряд."""
        keys = [{"text": t, "callback_data": d} for t, d in buttons]
        p = {"chat_id": chat, "text": text[:4000],
             "reply_markup": {"inline_keyboard": [keys[i:i + per_row] for i in range(0, len(keys), per_row)]}}
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
    for k in ("orders", "active", "msg_orders"):  # заказы; активный заказ чата; сообщение -> заказ
        st.setdefault(k, {})
    for k in ("tz", "tz_msgs", "tz_last"):  # черновики карточек ТЗ; сообщение -> черновик; чат -> последнее прочитанное
        st.setdefault(k, {})
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
        out.append(tg.download(ph["file_id"], FILES / str(chat) / ph["file_unique_id"] / "фото.jpg"))
    doc = msg.get("document")
    if doc:
        name = doc.get("file_name") or ""
        ext = DOC_EXT.get(doc.get("mime_type", "")) or (Path(name).suffix.lower() if Path(name).suffix.lower() in
                                                       (".pdf", ".png", ".jpg", ".jpeg", ".docx") else None)
        if ext:
            safe = re.sub(r'[\\/:*?"<>|]+', "_", Path(name).stem)[:80] or "файл"
            out.append(tg.download(doc["file_id"], FILES / str(chat) / doc["file_unique_id"] / f"{safe}{ext}"))
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
    notes = clean_questions(kp.questions) + warns
    if notes:
        lines += ["", "Проверьте:"] + [f"– {x}" for x in notes]
    lines += ["", "Правка — ответьте на это сообщение."]
    return "\n".join(lines).replace(" ", " ")


def contract_summary(r, c, link, kp_name=None):
    pay = "; ".join(f"{p}% - {make_kp.money(a)}" for p, a in r["payments"])
    lines = [f"Договор № {r['number']} от {r['date']}"] + ([f"Открыть и править: {link}"] if link else []) + (
        [f"Позиции и цены — из КП: {kp_name}"] if kp_name else []) + [
        "", f"Заказчик: {make_contract.customer_short(make_contract.dashes(parse_contract.to_make_contract(c))['customer'])}",
        f"Итого: {make_kp.money(r['total'])}", f"Оплата: {pay}",
        f"Срок производства: {c.schedule.production_days} раб. дней"]
    notes = clean_questions(c.questions)[:3] + r["warnings"]
    if notes:
        lines += ["", "Проверьте:"] + [f"– {x}" for x in notes]
    lines += ["", "Перед отправкой клиенту просмотрите каждую страницу (чек-лист договоров).",
              "Правка — просто напишите, что исправить."]
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


def send_contract(tg, chat, reply_to, c, kp=None, folder=None):
    """Собрать DOCX, Google Документ и PDF, отправить. -> (id сообщения со сводкой, результат make_contract или None)."""
    tg.send(chat, "Собираю договор…", reply_to)
    try:
        r = make_contract.build(parse_contract.to_make_contract(c), CONTRACTS_OUT)
    except make_contract.ContractError as e:
        log(f"чат {chat}: договор не собран: {e}")
        return tg.send(chat, f"Договор не собран: {e}\nПришлите исправление.", reply_to)["message_id"], None
    path, link = r["path"], None
    if gdrive.ready():
        try:
            fid, link = retry(lambda: gdrive.upload(path, "contract", folder), "Drive: договор")
            pdf = retry(lambda: gdrive.export_pdf(fid, path.with_suffix(".pdf")), "Drive: PDF договора")
            tg.send_file(chat, pdf, f"Договор № {r['number']} (PDF)", reply_to)
        except Exception as e:
            log(f"Drive: договор не загружен ({type(e).__name__}: {e})")
            link = None
    tg.send_file(chat, path, "DOCX для Word / Google Docs", reply_to)
    kp_name = ", ".join(i.get("name", "") for i in (kp or {}).get("items", [])) if kp else None
    m = tg.send(chat, contract_summary(r, c, link, kp_name), reply_to)
    log(f"чат {chat}: договор {r['number']}, итого {r['total']}, ссылка {'есть' if link else 'нет'}")
    return m["message_id"], {**r, "link": link}


INVOICE = re.compile(r"\b(выстав\w*|нуж\w*|сдела\w*|сформир\w*|подготов\w*)\b[^.\n]{0,25}\bсч[её]т|^\s*сч[её]т\b",
                     re.IGNORECASE | re.MULTILINE)
INVOICE_REPLY = ("Счёт с нуля пока не выставляю: ждём доступ к 1С. Сейчас так: выставьте счёт в 1С (Печать → «Счет на оплату»), "
                 "сохраните PDF и пришлите сюда — поставлю подпись и печать.")
MAX_MSG_LINKS = 3000  # сколько ссылок «сообщение -> заказ» помнить


def contract_model(o):
    return parse_contract.Contract.model_validate(o["contract"]) if o.get("contract") else None


def folder_name(o):
    who = re.sub(r"[^\w\- «»]+", "", o.get("client") or "").strip()[:40]
    return " ".join(x for x in (o.get("deal_id") or o["id"], who) if x)


def link_msgs(st, chat, oid, ids):
    """Ответ на любое из этих сообщений относится к заказу oid."""
    for m in ids:
        st["msg_orders"][f"{chat}:{m}"] = oid
    st["msg_orders"] = dict(list(st["msg_orders"].items())[-MAX_MSG_LINKS:])


def show_card(tg, st, o, reply_to=None):
    text, buttons = orders.card(o, lambda d: contract_missing(parse_contract.Contract.model_validate(d)), None)
    m = tg.ask(o["chat"], text, buttons, reply_to)
    link_msgs(st, o["chat"], o["id"], [m["message_id"]])


def build_kp(tg, env, o, reply_to, sender=""):
    """КП из данных заказа -> PDF, ссылка, сводка, комментарий в Б24."""
    chat = o["chat"]
    kp = parse.KP.model_validate(o["kp"])
    tg.send(chat, "Собираю КП…", reply_to)
    images = [Path(x) for x in o.get("kp_images", [])]
    data = parse.to_make_kp(kp, images, o.get("client") or None, date.today().isoformat())
    if o.get("deal_id"):
        data["project"]["deal_id"] = o["deal_id"]
    try:
        path, items_total, log_sum, _, warns = make_kp.build(data, make_kp.TEMPLATE, OUT)
    except make_kp.KPError as e:
        tg.send(chat, f"КП не собрано: {e}. Пришлите исправление.", reply_to)
        return
    link = pdf = None
    if gdrive.ready():
        try:
            fid, link = retry(lambda: gdrive.upload(path, "kp", folder_name(o)), "Drive: загрузка")
            pdf = retry(lambda: gdrive.export_pdf(fid, path.with_suffix(".pdf")), "Drive: PDF")
            tg.send_file(chat, pdf, f"{path.stem}\nGoogle Презентация: {link}", reply_to)
        except Exception as e:
            log(f"Drive: не загружено ({type(e).__name__}: {e})")
            link = pdf = None
    if not link:
        pdf = render_pdf(path) if env.get("KP_RENDER_PDF", "0") == "1" else None
        if pdf:
            tg.send_file(chat, pdf, "Для просмотра", reply_to)
        tg.send_file(chat, path, "", reply_to)
    b24_note = deal_comment(env, o.get("deal_id"), o.get("client") or deal_title(env, o.get("deal_id")), path, link, pdf, items_total, log_sum,
                            kp.production_term, sender)
    text = summary(kp, path, items_total, log_sum, warns, link).replace("Правка — ответьте на это сообщение.",
                                                                        "Правка — просто напишите, что исправить.")
    tg.send(chat, text + (f"\n\n{b24_note}" if b24_note else ""), reply_to)
    prev = o["docs"].get("kp", {})
    o["docs"]["kp"] = {"version": prev.get("version", 0) + 1, "name": path.stem, "link": link,
                       "total": items_total + log_sum, "at": datetime.now().isoformat(timespec="seconds")}
    log(f"чат {chat}: заказ {o['id']}: КП {path.name}, итого {items_total}, ссылка {'есть' if link else 'нет'}")


def build_contract(tg, client, o, reply_to):
    """Договор из данных заказа; если КП менялось после разбора договора — сначала подтянуть позиции."""
    chat = o["chat"]
    if orders.contract_stale(o):
        tg.send(chat, "Подтягиваю в договор изменения из КП…", reply_to)
        c = parse_contract.parse(client, "Обнови по актуальному КП: позиции, цены, количество, срок, доставку и монтаж "
                                 "(если в КП есть их стоимость — они входят в договор с этой ценой). Реквизиты и условия оплаты не меняй.", [], o["kp"], contract_model(o))
        o["contract"], o["contract_updated"] = c.model_dump(), datetime.now().isoformat(timespec="seconds")
    c = contract_model(o)
    miss = contract_missing(c)
    if miss:
        tg.send(chat, "Для договора не хватает: " + "; ".join(miss) + ". Пришлите — файлом или текстом.", reply_to)
        return
    mid, r = send_contract(tg, chat, reply_to, c, o.get("kp"), folder_name(o))
    if r:
        o["docs"]["contract"] = {"number": r["number"], "link": r.get("link"), "total": r["total"],
                                 "at": datetime.now().isoformat(timespec="seconds")}


def ingest(tg, client, env, st, o, b):
    """Разложить присланное по заказу, обновить данные, при необходимости собрать КП; показать карточку."""
    chat = o["chat"]
    files = [Path(f) for f in b["files"]]
    text = b["text"]
    tg.send(chat, "Читаю…", b["reply_to"])
    previews, pages1, words_by_file = [], {}, {}
    for i, f in enumerate(files):
        if f.suffix.lower() == ".pdf":
            pages1[f] = pdf_pages(f, 1)[0]
        if f.suffix.lower() == ".docx":
            words_by_file[f] = docx_text(f)
        previews.append((f, orders.preview(f, i, pages1.get(f), words_by_file.get(f, ""))))
    rt = orders.route(client, orders.order_text(o), text, previews)
    kinds = {fk.index: fk for fk in rt.files}
    notes = []
    for i, f in enumerate(files):
        fk = kinds.get(i)
        kind, note = (fk.kind, fk.note) if fk else ("other", f.name)
        o["files"].append({"path": str(f), "kind": kind, "note": note})
        notes.append(f"• {f.name} → {note}")
    if rt.deal_id and not o.get("deal_id"):
        o["deal_id"] = rt.deal_id
    if o.get("deal_id") and not o.get("client"):
        o["client"] = deal_title(env, o["deal_id"]) or ""
    if rt.client_hint and not o.get("client"):
        o["client"] = rt.client_hint
    if notes:
        tg.send(chat, "Принял:\n" + "\n".join(notes), b["reply_to"])
    if rt.invoice and not (rt.kp_update or rt.contract_update):
        tg.send(chat, INVOICE_REPLY, b["reply_to"])

    by = lambda *k: [f for i, f in enumerate(files) if (kinds.get(i) and kinds[i].kind in k)]
    drawings = by("drawing")
    for f in drawings:  # картинки к позициям КП
        o["kp_images"] += [str(x) for x in (pdf_pages(f) if f.suffix.lower() == ".pdf" else [f])]
    words = "\n\n".join(f"Файл «{f.name}» (Word):\n{words_by_file[f]}" for f in files if f in words_by_file)
    now = datetime.now().isoformat(timespec="seconds")
    kp_changed = False
    task_files = by("task", "kp")
    if rt.kp_update or task_files or (drawings and o.get("kp")):
        src = "\n\n".join(x for x in (text, words) + tuple(
            f"Файл «{f.name}» ({'готовое КП' if kinds[files.index(f)].kind == 'kp' else 'задание'}), текст:\n{pdf_text_safe(f)}"
            for f in task_files if f.suffix.lower() == ".pdf") if x)
        prev = parse.KP.model_validate(o["kp"]) if o.get("kp") else None
        kp = parse.parse(client, src or "Добавлены картинки к позициям — распредели их.", [Path(x) for x in o["kp_images"]], prev)
        if o.get("deal_id") and not kp.deal_id:
            kp.deal_id = o["deal_id"]
        o["kp"], o["kp_updated"], kp_changed = kp.model_dump(), now, True
        o["questions"] = clean_questions(kp.questions)
    contract_inputs = by("requisites", "contract", "kp")
    if rt.contract_update or rt.build_contract or contract_inputs:
        imgs = [f for f in by("requisites", "contract") if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
        pdfs = [f for f in contract_inputs if f.suffix.lower() == ".pdf"]
        o["contract_docs"] = list(dict.fromkeys(o.get("contract_docs", []) + [str(f) for f in pdfs]))
        c = parse_contract.parse(client, "\n\n".join(x for x in (text, words) if x) or "(файлы без текста)", imgs,
                                 o.get("kp"), contract_model(o), pdfs)
        o["contract"], o["contract_updated"] = c.model_dump(), now
        o["questions"] = clean_questions(c.questions)
    o["updated"] = now
    if rt.reply and not (kp_changed or o["contract_updated"] == now):
        tg.send(chat, rt.reply, b["reply_to"])

    # КП собираем сразу, когда пришло задание или правка и данных хватает; договор — по кнопке или просьбе.
    kp_ready = o.get("kp") and not parse.missing(parse.KP.model_validate(o["kp"]))
    if kp_ready and (rt.build_kp or (kp_changed and not by("kp") and (text or task_files))):
        build_kp(tg, env, o, b["reply_to"], b.get("sender", ""))
    contract_changed = o.get("contract_updated") == now
    if o.get("contract") and (rt.build_contract or (o["docs"].get("contract") and (contract_changed or kp_changed))):
        build_contract(tg, client, o, b["reply_to"])  # собранный договор держим актуальным без лишней кнопки
    log(f"чат {chat}: заказ {o['id']}: файлов {len(files)}, КП {'да' if kp_changed else 'нет'}, "
        f"договор {'да' if o['contract_updated'] == now else 'нет'}, сборка КП {rt.build_kp}, договора {rt.build_contract}")


def clean_questions(qs):
    """Вопросы Claude без повторного «Проверьте:» в начале."""
    return [re.sub(r"^\s*проверьте\s*:\s*", "", q, flags=re.IGNORECASE).strip() for q in qs if q.strip()]


def pdf_text_safe(f):
    try:
        return orders.pdf_text(f)
    except Exception:
        return ""


def find_order(st, chat, deal_id):
    for o in st["orders"].values():
        if o["chat"] == chat and o.get("deal_id") == deal_id:
            return o
    return None


def resolve_order(st, env, chat, b):
    """К какому заказу относится заявка. -> (заказ, пояснение для Екатерины или "")."""
    reply_oid = st["msg_orders"].get(f"{chat}:{b.get('reply_id')}") if b.get("reply_id") else None
    if reply_oid and reply_oid in st["orders"]:
        return st["orders"][reply_oid], ""
    m = re.search(r"#(\d+)", b["text"])
    active = st["orders"].get(st["active"].get(str(chat), ""))
    if m:
        o = find_order(st, chat, m.group(1))
        if o:
            return o, ("" if active is o else f"Открыл {orders.title(o)}.")
        if active and not active.get("deal_id") and datetime.fromisoformat(active["updated"]) > datetime.now() - timedelta(hours=2):
            active["deal_id"] = m.group(1)
            active["client"] = active.get("client") or deal_title(env, m.group(1)) or ""
            return active, ""
        o = orders.new_order(chat, m.group(1), deal_title(env, m.group(1)) or "")
        st["orders"][o["id"]] = o
        return o, f"Новый заказ: {orders.title(o)}."
    if active and datetime.fromisoformat(active["updated"]) > datetime.now() - timedelta(hours=orders.ORDER_IDLE_H):
        return active, ""
    o = orders.new_order(chat)
    st["orders"][o["id"]] = o
    note = "Начал новый заказ." + (f" Если это продолжение «{orders.title(active)}» — нажмите «Заказы» и откройте его." if active else "")
    return o, note


def handle_bundle(tg, client, env, st, chat, b):
    o, note = resolve_order(st, env, chat, b)
    st["active"][str(chat)] = o["id"]
    if note:
        tg.send(chat, note, b["reply_to"])
    tg.sent = []
    try:
        ingest(tg, client, env, st, o, b)
    finally:
        link_msgs(st, chat, o["id"], tg.sent + b.get("msg_ids", []))
        save_state(st)
    show_card(tg, st, o)
    save_state(st)


def on_button(tg, client, env, st, chat, data, msg_id):
    action, _, arg = data.partition(":")
    if action == "orders":
        mine = sorted((o for o in st["orders"].values() if o["chat"] == chat), key=lambda o: o["updated"], reverse=True)[:8]
        if not mine:
            tg.send(chat, "Заказов пока нет. Пришлите задание или файлы — заказ создастся сам.")
            return
        tg.ask(chat, "Последние заказы:", [(orders.title(o)[:60], f"open:{o['id']}") for o in mine], msg_id, per_row=1)
        return
    if action == "new":
        o = orders.new_order(chat)
        st["orders"][o["id"]] = o
        st["active"][str(chat)] = o["id"]
        save_state(st)
        tg.send(chat, "Новый заказ. Присылайте задание, КП, реквизиты, чертежи — в любом порядке. Номер сделки — «#715».")
        return
    o = st["orders"].get(arg)
    if not o:
        tg.send(chat, "Заказ не найден. «Заказы» — список.")
        return
    st["active"][str(chat)] = o["id"]
    tg.sent = []
    try:
        if action == "kp":
            build_kp(tg, env, o, msg_id)
        elif action == "contract":
            build_contract(tg, client, o, msg_id)
        link_msgs(st, chat, o["id"], tg.sent)
    finally:
        save_state(st)
    show_card(tg, st, o)
    save_state(st)


# --- карточки с ТЗ из Telegram-чатов владельца (docs/18_карточки_ТЗ.md) ---

TZ_DEPTHS = [("За сутки", "1d"), ("За 3 дня", "3d"), ("За неделю", "7d"), ("Последние 50", "50")]
TZ_ASSIGNEES = "Василиса:81,Андреев:27,Полина:49,Савин:89"


def tz_owner(user, env):
    owners = {x.strip().lstrip("@").lower() for x in env.get("TZ_OWNER_IDS", "").split(",") if x.strip()}
    return allowed(user, owners)


def tz_assignees(env):
    return [(n.strip(), int(i)) for n, i in (x.split(":") for x in env.get("TZ_ASSIGNEES", TZ_ASSIGNEES).split(","))]


def tz_previews(files):
    out = []
    for i, f in enumerate(files):
        suf = f.suffix.lower()
        try:
            if suf in (".jpg", ".jpeg", ".png"):
                out.append((f, orders.preview(f, i)))
            elif suf == ".pdf":
                out.append((f, orders.preview(f, i, pdf_pages(f, 1)[0])))
            elif suf == ".docx":
                out.append((f, orders.preview(f, i, words=docx_text(f))))
            else:
                out.append((f, [{"type": "text", "text": f"Файл {i}: «{f.name}» (содержимое не показано)"}]))
        except Exception as e:
            log(f"ТЗ: превью {f.name} не сделано ({e})")
            out.append((f, [{"type": "text", "text": f"Файл {i}: «{f.name}» (не открылся)"}]))
    return out


def tz_show(tg, env, st, chat, did, reply_to=None):
    t = st["tz"][did]
    name = next((n for n, i in tz_assignees(env) if i == t.get("assignee")), "")
    buttons = [(("✓ " if n == name else "") + n, f"tza:{did}:{i}") for n, i in tz_assignees(env)]
    buttons += [("Создать в Б24", f"tzok:{did}"), ("Отмена", f"tzx:{did}")]
    m = tg.ask(chat, card.summary(t["draft"], t["files"], name), buttons, reply_to)
    st["tz_msgs"][f"{chat}:{m['message_id']}"] = did


def tz_command(tg, env, chat, query, reply_to):
    tg.send(chat, "Ищу чаты…", reply_to)
    found = tgread.dialogs(env, query)
    if not found:
        tg.send(chat, f"Чатов «{query}» не нашёл. Попробуйте часть названия: /tz обинов")
        return
    tg.ask(chat, "Из какого чата карточка?", [(n[:40], f"tzc:{i}") for i, n in found], reply_to, per_row=1)


def tz_button(tg, client, env, st, chat, data, msg_id):
    action, _, arg = data.partition(":")
    if action == "tzc":
        buttons = [(t, f"tzd:{arg}:{d}") for t, d in TZ_DEPTHS]
        if st["tz_last"].get(arg):
            buttons.insert(0, ("С прошлой карточки", f"tzd:{arg}:last"))
        tg.ask(chat, "Сколько переписки брать?", buttons, msg_id)
        return
    if action == "tzd":
        src, _, depth = arg.partition(":")
        did = "t" + uuid.uuid4().hex[:6]
        tg.send(chat, "Читаю переписку и файлы…", msg_id)
        got = tgread.fetch(env, int(src), depth, DATA / "tz" / did, st["tz_last"].get(src, 0))
        if not got["text"]:
            tg.send(chat, "В этом периоде сообщений нет. Возьмите период больше.")
            return
        skipped = f", пропущено {len(got['skipped'])}" if got["skipped"] else ""
        tg.send(chat, f"«{got['chat']}»: {got['text'].count(chr(10)) + 1} сообщений, файлов {len(got['files'])}{skipped}. Собираю черновик…")
        d = card.extract(client, got["chat"], got["text"], tz_previews(got["files"]))
        st["tz"][did] = {"chat": chat, "src": src, "src_title": got["chat"], "text": got["text"], "last_id": got["last_id"],
                         "files": [str(f) for f in got["files"]], "draft": d.model_dump(), "assignee": None, "deal_id": None,
                         "created": datetime.now().isoformat(timespec="seconds")}
        tz_show(tg, env, st, chat, did)
        return
    did, _, uid = arg.partition(":")
    t = st["tz"].get(did)
    if not t:
        tg.send(chat, "Черновик не найден. /tz — заново.")
        return
    if t.get("deal_id"):
        tg.send(chat, f"Карточка уже создана: сделка {t['deal_id']}.")
        return
    if action == "tzx":
        st["tz"].pop(did)
        tg.send(chat, "Черновик отменён, в Б24 ничего не создано.")
        return
    if action == "tza":
        t["assignee"] = int(uid)
        tz_show(tg, env, st, chat, did, msg_id)
        return
    if action == "tzok":
        if not t.get("assignee"):
            tg.send(chat, "Сначала выберите ответственного (кнопка с именем).", msg_id)
            return
        hook = env.get("TZ_B24_WEBHOOK")
        if not hook:
            tg.send(chat, "Нет TZ_B24_WEBHOOK в .env — создавать сделки нечем.")
            return
        from b24 import B24
        api = B24(hook)
        tz_file = card.tz_docx(t["draft"], t["src_title"], DATA / "tz" / did)
        deal_id = card.create(api, t["draft"], t["files"], t["assignee"], t["src_title"], tz_file,
                              env.get("TZ_STAGE", card.STAGE))
        t["deal_id"] = deal_id
        st["tz_last"][t["src"]] = t["last_id"]
        log(f"ТЗ: создана сделка {deal_id} «{t['draft']['title']}» из чата {t['src']}")
        tg.send(chat, f"Создано: сделка {deal_id} «{t['draft']['title']}»\n{card.deal_url(hook, deal_id)}", msg_id)


def tz_fix(tg, client, env, st, chat, did, text, reply_to):
    t = st["tz"][did]
    if t.get("deal_id"):
        tg.send(chat, f"Карточка уже создана (сделка {t['deal_id']}) — правьте в Б24.", reply_to)
        return
    tg.send(chat, "Правлю черновик…", reply_to)
    files = [Path(f) for f in t["files"]]
    d = card.extract(client, t["src_title"], t["text"], tz_previews(files), t["draft"], text)
    t["draft"] = d.model_dump()
    tz_show(tg, env, st, chat, did)


def main():
    env = load_env()
    tg = TG(env["KP_TG_BOT_TOKEN"])
    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    allow = {x.strip().lstrip("@").lower() for x in env.get("KP_TG_ALLOWED_IDS", "").split(",") if x.strip()}
    st = load_state()
    inbox = {}  # chat -> заявка, которая ещё собирается (сообщения с паузами < QUIET_SEC)
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
            if cb:
                try:
                    tg.call("answerCallbackQuery", {"callback_query_id": cb["id"]})
                except Exception:
                    pass
                if not allowed(cb.get("from", {}), allow):
                    continue
                chat = cb["message"]["chat"]["id"]
                log(f"кнопка: {cb.get('data')} чат {chat}")
                data = cb.get("data") or ""
                try:
                    if data.startswith("tz"):
                        if tz_owner(cb.get("from", {}), env):
                            tz_button(tg, client, env, st, chat, data, cb["message"]["message_id"])
                            save_state(st)
                        continue
                    on_button(tg, client, env, st, chat, data, cb["message"]["message_id"])
                except Exception as e:
                    log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                    tg.send(chat, f"Ошибка: {e}. Попробуйте ещё раз или напишите Дмитрию.")
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
            if text in ("/new", "/orders"):
                on_button(tg, client, env, st, chat, "new:" if text == "/new" else "orders:", msg["message_id"])
                continue
            tz_reply = st["tz_msgs"].get(f"{chat}:{msg.get('reply_to_message', {}).get('message_id')}")
            if tz_owner(user, env) and (text.split(" ")[0] == "/tz" or (tz_reply and text)):
                try:
                    if tz_reply:
                        tz_fix(tg, client, env, st, chat, tz_reply, text, msg["message_id"])
                    else:
                        tz_command(tg, env, chat, text[3:].strip(), msg["message_id"])
                except Exception as e:
                    log(f"ТЗ: ошибка {e}\n{traceback.format_exc()}")
                    tg.send(chat, f"Ошибка: {e}", msg["message_id"])
                save_state(st)
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
            b = inbox.get(chat)
            if b is None or (reply and b.get("reply_id") and reply != b["reply_id"]):
                b = inbox[chat] = {"chat": chat, "texts": [], "files": [], "reply_to": msg["message_id"], "reply_id": reply,
                                   "sender": user.get("username") or ""}
            b["reply_id"] = b.get("reply_id") or reply
            if text:
                b["texts"].append(text)
            rt = msg.get("reply_to_message") or {}
            if not st["msg_orders"].get(f"{chat}:{reply}") and (rt.get("photo") or rt.get("document")):
                try:  # ответ на ранее присланный файл — взять его
                    b["files"] += [str(f) for f in download(tg, rt) if str(f) not in b["files"]]
                except Exception as e:
                    log(f"файл из ответа не скачан: {e}")
            b["files"] += [str(f) for f in files]
            b.setdefault("msg_ids", []).append(msg["message_id"])
            b["album"] = b.get("album") or bool(msg.get("media_group_id"))
            b["last"] = time.time()
        save_state(st)

        for chat, b in list(inbox.items()):  # заявка собрана (тишина QUIET_SEC, у альбома — ALBUM_QUIET_SEC)
            if time.time() - b["last"] < (ALBUM_QUIET_SEC if b.get("album") else QUIET_SEC):
                continue
            inbox.pop(chat)
            if not b["texts"] and not b["files"]:
                continue
            b["text"] = "\n".join(b["texts"])
            try:
                handle_bundle(tg, client, env, st, chat, b)
            except Exception as e:
                log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                try:
                    tg.send(chat, f"Ошибка: {e}. Попробуйте ещё раз или напишите Дмитрию.", b["reply_to"])
                except Exception:
                    pass


if __name__ == "__main__":
    main()
