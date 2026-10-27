"""Счёт на оплату ООО «90 ГРАДУСОВ» в формате печатной формы 1С, с подписью и печатью.

Номер и дата — из заказа клиента в 1С (app/onec), покупатель — из реквизитов заказа в боте.
Подпись и печать ставит app/invoice_stamp (те же картинки, что в 1С).

    python app/invoice/make_invoice.py пример.json [выход.pdf]
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.invoice_stamp.stamp import stamp_pdf  # noqa: E402

SELLER = {
    "name": "ООО «90 ГРАДУСОВ»", "inn": "7838125272", "kpp": "783801001",
    "address": "190020, Город Санкт-Петербург, вн.тер.г. муниципальный округ Екатерингофский, "
               "наб Обводного канала, д. 199-201, литера Н, помещ. 7",
    "phone": "+79118482281",
    "bank": "СЕВЕРО-ЗАПАДНЫЙ БАНК ПАО СБЕРБАНК г. Санкт-Петербург", "bik": "044030653",
    "corr": "30101810500000000653", "account": "40702810855000137337",
    "director": "Шарапов Д. А.", "accountant": "Шарапов Д. А.",
}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

_ONES = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_ONES_F = ["", "одна", "две"] + _ONES[3:]
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
          "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят",
         "девяносто"]
_HUND = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
_SCALES = [(("рубль", "рубля", "рублей"), False), (("тысяча", "тысячи", "тысяч"), True),
           (("миллион", "миллиона", "миллионов"), False), (("миллиард", "миллиарда", "миллиардов"), False)]


def _plural(n, forms):
    n = n % 100
    if 10 < n < 20:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def rubles_words(amount):
    """188000 -> «Сто восемьдесят восемь тысяч рублей 00 копеек» (как в 1С)."""
    rub, kop = int(amount), round((amount - int(amount)) * 100)
    words, n, i = [], rub, 0
    while n or i == 0:
        n, tri = divmod(n, 1000)
        forms, fem = _SCALES[i]
        part = []
        if tri:
            part += [_HUND[tri // 100]]
            t = tri % 100
            part += [_TEENS[t - 10]] if 10 <= t < 20 else [_TENS[t // 10], (_ONES_F if fem else _ONES)[t % 10]]
        if tri or i == 0:
            part.append(_plural(tri, forms))
        words = [w for w in part if w] + words
        i += 1
    if rub == 0:
        words = ["ноль"] + words
    s = " ".join(words) + f" {kop:02d} {_plural(kop, ('копейка', 'копейки', 'копеек'))}"
    return s[0].upper() + s[1:]


def money(x):
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def date_words(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} г."


def party(p):
    s = p["name"]
    for k, lab in (("inn", "ИНН"), ("kpp", "КПП")):
        if p.get(k):
            s += f", {lab} {p[k]}"
    if p.get("address"):
        s += f", {p['address']}"
    if p.get("phone"):
        s += f", тел.: {p['phone']}"
    return s


FONT_DIRS = [os.environ.get("FONT_DIR", ""), "/System/Library/Fonts/Supplemental",
             "/usr/share/fonts/truetype/msttcorefonts", "/usr/share/fonts/truetype/liberation"]
FONT_FILES = {"r": ("Arial.ttf", "LiberationSans-Regular.ttf"), "b": ("Arial Bold.ttf", "LiberationSans-Bold.ttf")}


def _font(kind):
    for d in FONT_DIRS:
        for name in FONT_FILES[kind]:
            if d and (Path(d) / name).exists():
                return str(Path(d) / name)
    raise FileNotFoundError("нет шрифта Arial (или Liberation Sans); укажите папку в FONT_DIR")


_FONTS = {}


class Sheet:
    """Координаты — как в печатной форме 1С «Счет на оплату» (снято с ЦБ-138), pt от левого верхнего угла."""

    def __init__(self, page):
        self.p = page
        for k in ("r", "b"):
            page.insert_font(k, fontfile=_font(k))
            _FONTS.setdefault(k, fitz.Font(fontfile=_font(k)))

    def text(self, x, y, s, size=8.6, bold=False, align="l", width=0):
        """y — верх строки. align l/c/r относительно x (для c и r — x+width)."""
        f = "b" if bold else "r"
        w = _FONTS[f].text_length(s, fontsize=size)
        if align == "c":
            x = x + (width - w) / 2
        elif align == "r":
            x = x + width - w
        self.p.insert_text((x, y + size * 0.93), s, fontname=f, fontsize=size)

    def wrap(self, x, y, width, s, size=8.6, bold=False, lead=1.16):
        """Текст с переносом по словам, возвращает y после последней строки."""
        font = _FONTS["b" if bold else "r"]
        line = ""
        for word in s.split(" "):
            cand = (line + " " + word).strip()
            if line and font.text_length(cand, fontsize=size) > width:
                self.text(x, y, line, size, bold)
                y += size * lead
                line = word
            else:
                line = cand
        if line:
            self.text(x, y, line, size, bold)
            y += size * lead
        return y

    def line(self, x0, y0, x1, y1, w=0.65):
        self.p.draw_line((x0, y0), (x1, y1), width=w)

    def box(self, x0, y0, x1, y1, w=0.65):
        self.p.draw_rect(fitz.Rect(x0, y0, x1, y1), width=w)


def draw(page, inv):
    s, b, d = SELLER, inv["buyer"], inv["date"]
    t = Sheet(page)
    valid = inv.get("valid_until") or d
    notice = [f"Внимание! Счет действителен до {valid.strftime('%d.%m.%Y')}.",
              "Оплата данного счета означает согласие с условиями поставки товара.",
              "Уведомление об оплате обязательно, в противном случае не гарантируется",
              "наличие товара на складе. Товар отпускается по факту прихода денег",
              "на р/с Поставщика, самовывозом, при наличии доверенности и паспорта."]
    for i, line in enumerate(notice):
        t.text(33, 37 + i * 8.05, line, 6.8, align="c", width=438)
    t.text(33, 95.7, "Образец заполнения платежного поручения", 8.6, True, "c", 532)

    # образец платёжки
    X = (33, 60, 155, 182, 277, 317.5, 358, 409.5, 565)
    for x0, y0, x1, y1 in ((X[0], 106.5, X[4], 137), (X[4], 106.5, X[5], 118.2), (X[5], 106.5, X[8], 118.2),
                           (X[4], 118.2, X[5], 137), (X[5], 118.2, X[8], 137),
                           (X[0], 137, X[1], 148.6), (X[1], 137, X[2], 148.6), (X[2], 137, X[3], 148.6),
                           (X[3], 137, X[4], 148.6), (X[4], 137, X[5], 148.6), (X[5], 137, X[8], 148.6),
                           (X[0], 148.6, X[4], 189), (X[0], 189, X[8], 216)):
        t.box(x0, y0, x1, y1)
    for y0, y1 in ((148.6, 159.8), (159.8, 171), (171, 189)):
        for x0, x1 in ((X[4], X[5]), (X[5], X[6]), (X[6], X[7]), (X[7], X[8])):
            t.box(x0, y0, x1, y1)
    t.wrap(34.8, 107.1, 240, s["bank"], 8.6)
    t.text(34.3, 128.4, "Банк получателя", 6.8)
    t.text(278.5, 107.1, "БИК")
    t.text(319.2, 107.1, s["bik"])
    t.text(278.5, 118.8, "Сч. №")
    t.text(319.2, 118.8, s["corr"])
    t.text(34.8, 137.6, "ИНН")
    t.text(61.8, 137.6, s["inn"])
    t.text(156.7, 137.6, "КПП")
    t.text(183.7, 137.6, s["kpp"])
    t.text(278.5, 137.6, "Сч. №")
    t.text(319.2, 137.6, s["account"])
    t.text(34.8, 149.3, s["name"])
    t.text(34.3, 180.4, "Получатель", 6.8)
    for x, y, lab in ((278.2, 150, "Вид оп."), (359.5, 150, "Срок плат."), (278.2, 161.1, "Наз. пл."),
                      (359.5, 161.1, "Очер. плат."), (278.2, 175.6, "Код"), (359.5, 175.6, "Рез. поле")):
        t.text(x, y, lab, 7.1)
    t.text(319.1, 149.2, "01", 8.4)
    t.text(411.1, 160.4, "5", 8.4)
    pid = inv.get("payment_id") or ""   # «Код» — идентификатор платежа заказа 1С (ЗК2610ГРЦБ0001380004)
    if pid:
        t.text(318.6, 171.5, pid[:10], 6.0)
        t.text(318.6, 178.5, pid[10:], 6.0)
    t.text(34.8, 192.8, f"Оплата по заказу клиента №{inv['number']}")
    t.text(34.3, 207.4, "Назначение платежа", 6.8)

    t.text(35.8, 233, f"Счет на оплату № {inv['number']} от {date_words(d)}", 12, True)
    t.line(33, 254.9, 565, 254.9, 1.29)
    y = 264.3
    for lab, who in (("Поставщик:", s), ("Покупатель:", b)):
        y_end = t.wrap(88.9, y, 474, party(who), 8.4, True, lead=1.19)
        t.text(34.7, (y + y_end) / 2 - 5, lab, 8.4)
        y = max(y_end, y + 20) + 10

    # таблица товаров
    C = (33.4, 60, 385, 412, 453, 507, 565)
    y = max(y + 0.3, 324.2)
    t.line(C[0], y, C[-1], y, 1.29)
    for x, lab, w in ((C[0], "№", C[1] - C[0]), (C[1], "Товары (работы, услуги)", C[2] - C[1]),
                      (C[2], "Количество", C[4] - C[2]), (C[4], "Цена", C[5] - C[4]), (C[5], "Сумма", C[6] - C[5])):
        t.text(x, y + 3.6, lab, 8.6, True, "c", w)
    head = y
    y += 17.6
    t.line(C[0], y, C[-1], y)
    for x in (C[1], C[2], C[4], C[5]):
        t.line(x, head, x, y)
    total = 0
    for i, it in enumerate(inv["items"], 1):
        qty, price = it.get("qty", 1), it["price"]
        total += qty * price
        top = y
        y_end = t.wrap(C[1] + 2, y + 0.5, C[2] - C[1] - 4, it["name"], 6.8)
        t.text(C[0], y + 0.5, str(i), 6.8, align="r", width=C[1] - C[0] - 2)
        t.text(C[2], y + 0.5, f"{qty:g}", 6.8, align="r", width=C[3] - C[2] - 2)
        t.text(C[3], y + 0.5, it.get("unit") or "-", 6.8, align="r", width=C[4] - C[3] - 2)
        t.text(C[4], y + 0.5, money(price), 6.8, align="r", width=C[5] - C[4] - 2)
        t.text(C[5], y + 0.5, money(qty * price), 6.8, align="r", width=C[6] - C[5] - 2.5)
        y = max(y_end + 1, top + 9)
        t.line(C[0], y, C[-1], y)
        for x in C[1:-1]:
            t.line(x, top, x, y)
    t.line(C[0], head, C[0], y, 1.29)
    t.line(C[-1], head, C[-1], y, 1.29)
    t.line(C[0], y + 0.4, C[-1], y + 0.4, 1.29)

    y += 4.8
    for lab, val in (("Итого:", money(total)), ("В т.ч. НДС (Без НДС):", "-"), ("Итого с НДС:", money(total))):
        t.text(380, y, lab, 8.6, True, "r", 126)
        t.text(507, y, val, 8.6, True, "r", 55.8)
        y += 11.65
    t.text(34.8, y, f"Всего наименований {len(inv['items'])}, на сумму {money(total)} RUB")
    t.text(34.9, y + 11.7, rubles_words(total), 8.6, True)
    y += 28.9
    t.line(33, y, 565, y, 1.29)

    y += 15.4
    for role, name in (("Руководитель", s["director"]), ("Бухгалтер", s["accountant"]), ("Менеджер", "")):
        t.text(34.8, y, role, 8.4, True)
        if name:
            t.text(303.6, y, name, 8.6, True, "c", 190.7)
        t.line(114, y + 10.7, 276.5, y + 10.7)
        t.line(303.6, y + 10.7, 494.3, y + 10.7)
        t.text(114, y + 11.1, "подпись", 6.8, align="c", width=162.5)
        t.text(303.6, y + 11.1, "расшифровка подписи", 6.8, align="c", width=190.7)
        y += 31.9
    if y > 820:
        raise ValueError("счёт не поместился на страницу: слишком много позиций")


def make_invoice(inv, out, stamp=True):
    """inv: number, date (date), buyer {name, inn, kpp, address, phone}, items [{name, qty, unit, price}], payment_id.
    Возвращает путь к PDF (с подписью и печатью, если stamp)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = out.with_name(out.stem + " (без подписи).pdf") if stamp else out
    doc = fitz.open()
    draw(doc.new_page(width=595.32, height=841.92), inv)
    doc.set_metadata({"title": f"Счет на оплату № {inv['number']}", "creator": "90 градусов"})
    doc.save(raw, garbage=3, deflate=True)
    if not stamp:
        return raw
    stamp_pdf(raw, out)
    raw.unlink()
    return out


def from_json(path):
    inv = json.loads(Path(path).read_text())
    inv["date"] = date.fromisoformat(inv["date"]) if inv.get("date") else date.today()
    if inv.get("valid_until"):
        inv["valid_until"] = date.fromisoformat(inv["valid_until"])
    return inv


if __name__ == "__main__":
    inv = from_json(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else ROOT / "data" / "invoices" / "out" / f"Счет на оплату № {inv['number']}.pdf"
    print(make_invoice(inv, out))
