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
from datetime import date, datetime
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client_agent"))
import gdrive  # noqa: E402
import make_kp  # noqa: E402
import parse  # noqa: E402
from agent import load_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "kp" / "bot"
FILES = DATA / "files"
OUT = ROOT / "data" / "kp" / "out"
STATE = DATA / "state.json"
QUIET_SEC = 4          # ждём хвост альбома после последнего сообщения
BUFFER_MIN = 30        # картинки без задания живут столько минут
MAX_JOBS = 300         # сколько последних КП помнить для правок
NET_TRIES, NET_PAUSE = 3, 5  # повторы при сбое сети: попыток, пауза (с, растёт)
TRIGGER = re.compile(r"#\d+|\bкп\b", re.IGNORECASE)

HELP = """Пришлите задание на КП одним сообщением (или подписью к альбому):

#715 КП
1. Стеллаж 1695×350×3000, нержавейка, сатин, полки 2 мм, подсветка — 195 000
2. Полки 4 шт 800×250, черная сталь RAL 9005 — 4 шт по 1 000
Доставка с монтажом 125 000
Срок 35

Картинки (чертежи, визуализации) прикладывайте к этому же сообщению альбомом или присылайте до него. PDF — тоже можно (берётся первая страница).
Цены и срок — только утверждённые: бот их не считает и не меняет.

Правка: ответьте (Reply) на сообщение с КП — «цена стеллажа 200 000», «убери доставку», «у полок финиш сатин». Придёт новая версия.
/new — сбросить присланные картинки."""


def log(msg):
    DATA.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(DATA / "bot.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


class TG:
    def __init__(self, token):
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
        return res["result"]

    def send(self, chat, text, reply_to=None):
        p = {"chat_id": chat, "text": text[:4000]}
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
        return res["result"]

    def download(self, file_id, dest):
        f = self.call("getFile", {"file_id": file_id})
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(self.file_base + f["file_path"], timeout=120) as r:
            dest.write_bytes(r.read())
        return dest


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"offset": 0, "jobs": {}}


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


def to_image(path):
    """PDF -> PNG первой страницы; картинки как есть."""
    if path.suffix.lower() != ".pdf":
        return path
    import fitz  # pymupdf
    doc = fitz.open(path)
    png = path.with_suffix(".png")
    doc[0].get_pixmap(dpi=150).save(png)
    return png


def attachments(tg, msg):
    """Файлы сообщения -> локальные пути (фото — самый крупный размер; документы — картинки и PDF)."""
    chat = msg["chat"]["id"]
    out = []
    if msg.get("photo"):
        ph = msg["photo"][-1]
        out.append(tg.download(ph["file_id"], FILES / str(chat) / f"{ph['file_unique_id']}.jpg"))
    doc = msg.get("document")
    if doc:
        mime = doc.get("mime_type", "")
        ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(mime)
        if ext:
            out.append(to_image(tg.download(doc["file_id"], FILES / str(chat) / f"{doc['file_unique_id']}{ext}")))
        else:
            tg.send(chat, f"Файл «{doc.get('file_name')}» не подходит: нужны фото, PNG/JPG или PDF.", msg["message_id"])
    return out


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


def main():
    env = load_env()
    tg = TG(env["KP_TG_BOT_TOKEN"])
    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"])
    allow = {x.strip().lstrip("@").lower() for x in env.get("KP_TG_ALLOWED_IDS", "").split(",") if x.strip()}
    st = load_state()
    pending = {}  # chat -> {"text", "reply_to", "images", "last"}
    buffer = {}   # chat -> [(time, path)] картинки до задания
    captions = {}  # chat -> [подписи к картинкам без «КП»] — войдут в задание
    acks = {}     # chat -> {"last", "reply_to"} — ответить «картинки получил», когда альбом дойдёт целиком
    outage = None  # {"since", "count"} — пока нет связи с Telegram
    log("бот запущен")
    while True:
        try:
            ups = tg.call("getUpdates", {"offset": st["offset"], "timeout": 2 if pending or acks else 25}, timeout=40)
        except Exception as e:
            if outage is None:
                outage = {"since": time.time(), "count": 0}
                log(f"нет связи с Telegram: {e}")
            outage["count"] += 1
            time.sleep(10)
            continue
        if outage:
            log(f"связь с Telegram восстановлена: {outage['count']} неудачных запросов за "
                f"{(time.time() - outage['since']) / 60:.0f} мин")
            outage = None
        for u in ups:
            st["offset"] = u["update_id"] + 1
            msg = u.get("message")
            if not msg or msg["chat"]["type"] != "private":
                log(f"пропущено: {[k for k in u if k != 'update_id']} {msg and msg['chat']['type']}")
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
                buffer.pop(chat, None)
                captions.pop(chat, None)
                acks.pop(chat, None)
                pending.pop(chat, None)
                tg.send(chat, "Картинки сброшены.")
                continue
            try:
                files = attachments(tg, msg)
            except Exception as e:
                log(f"файл не скачан: {e}")
                tg.send(chat, "Не удалось скачать файл, пришлите ещё раз.", msg["message_id"])
                files = []
            reply = msg.get("reply_to_message", {}).get("message_id")
            job = st["jobs"].get(f"{chat}:{reply}") if reply else None
            p = pending.get(chat)
            if p and msg.get("media_group_id") and msg.get("media_group_id") == p.get("group"):
                p["images"] += files
                p["last"] = time.time()
            elif job and text:  # правка готового КП
                pending[chat] = {"text": text, "reply_to": msg["message_id"], "images": job["images"] + [str(f) for f in files],
                                 "last": time.time(), "group": msg.get("media_group_id"), "previous": job,
                                 "sender": user.get("username") or ""}
            elif text and TRIGGER.search(text):
                old = [str(f) for t, f in buffer.pop(chat, []) if time.time() - t < BUFFER_MIN * 60]
                caps = captions.pop(chat, [])
                text = "\n".join((caps if old else []) + [text])  # подписи — только если их картинки ещё не устарели
                acks.pop(chat, None)
                pending[chat] = {"text": text, "reply_to": msg["message_id"], "images": old + [str(f) for f in files],
                                 "last": time.time(), "group": msg.get("media_group_id"), "sender": user.get("username") or ""}
            elif files:
                buffer.setdefault(chat, []).extend((time.time(), f) for f in files)
                if text:
                    captions.setdefault(chat, []).append(text)
                acks[chat] = {"last": time.time(), "reply_to": acks.get(chat, {}).get("reply_to") or msg["message_id"]}
            elif not text:
                kind = next((k for k in ("voice", "audio", "video", "video_note", "sticker", "animation", "location", "contact")
                             if msg.get(k)), "другое")
                log(f"чат {chat}: не текст и не картинка ({kind})")
                tg.send(chat, "Голосовые, видео и стикеры я не разбираю. Пришлите задание текстом "
                              "(картинки и PDF - можно). /help - подсказка.", msg["message_id"])
            elif text:
                tg.send(chat, "Не понял. Задание на КП — с «#номер сделки» или словом «КП». Правка — ответом на сообщение с КП. /help — подсказка.",
                        msg["message_id"])
        save_state(st)

        for chat, a in list(acks.items()):  # альбом без «КП» дошёл — сказать, что ждём задание
            if time.time() - a["last"] < QUIET_SEC:
                continue
            acks.pop(chat)
            n = sum(1 for t, _ in buffer.get(chat, []) if time.time() - t < BUFFER_MIN * 60)
            note = " Подпись к ним тоже учту." if captions.get(chat) else ""
            tg.send(chat, f"Картинки получил ({n}).{note} Теперь пришлите задание с «#номером сделки» или словом «КП» - "
                          f"соберу КП с этими картинками. Картинки ждут {BUFFER_MIN} мин, /new - сбросить.", a["reply_to"])

        for chat, p in list(pending.items()):
            if time.time() - p["last"] < QUIET_SEC:
                continue
            pending.pop(chat)
            try:
                images = [Path(x) for x in p["images"]]
                mid, kp, title = run_job(tg, client, env, chat, p["reply_to"], p["text"], images, p.get("previous"), p.get("sender", ""))
                st["jobs"][f"{chat}:{mid}"] = {"kp": kp.model_dump(), "images": p["images"], "client": title,
                                               "at": datetime.now().isoformat(timespec="seconds")}
                st["jobs"] = dict(list(st["jobs"].items())[-MAX_JOBS:])
                save_state(st)
            except Exception as e:
                log(f"чат {chat}: ошибка {e}\n{traceback.format_exc()}")
                try:
                    tg.send(chat, f"Ошибка, КП не собрано: {e}. Попробуйте ещё раз или напишите Дмитрию.", p["reply_to"])
                except Exception:
                    pass


if __name__ == "__main__":
    main()
