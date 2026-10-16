"""КП «90 градусов» из фирменного шаблона: JSON с данными проекта -> редактируемый PPTX.

Эталон вида — КП uno+uno от 24.09.2026. Правила — docs/14_КП_правила.md.
Цены генератор не считает и не округляет: берёт утверждённые суммы как есть, сам считает только итоги.

    .venv/bin/python app/kp/make_kp.py data/kp/in/<файл>.json [--out data/kp/out]
"""
import argparse
import copy
import json
import re
import sys
from datetime import date
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR
from pptx.oxml.ns import qn
from pptx.util import Cm, Pt

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "data/kp/template/master.pptx"
TOTAL_PHOTO = ROOT / "data/kp/template/total_photo.jpeg"  # фото цеха на «Итоговых условиях»
OUT = ROOT / "data/kp/out"

# Слайды шаблона (1-based): обложка, изделие, условия, контакты.
S_COVER, S_ITEM, S_COND, S_LAST = 1, 2, 3, 4
TITLE_BOX, BODY_BOX, PRICE_BOX, DATE_BOX = "Google Shape;62;p3", "Google Shape;64;p3", "Google Shape;63;p3", "Google Shape;61;p3"
COND_BOX = "Google Shape;72;p4"

BLACK, BLUE, GREEN, GRAY = "000000", "0000FF", "38761D", "808080"
# Стили: шрифт, размер, жирный, курсив, цвет. Сняты с эталона.
TITLE = ("Unbounded SemiBold", 11, False, False, BLACK)
TITLE_TOTAL = ("Unbounded SemiBold", 12, False, False, BLACK)
TEXT = ("Roboto Mono Light", 7, False, False, BLACK)
PRICE = ("Roboto Mono", 8, True, False, BLUE)
PRICE_SUM = ("Roboto Mono", 9, True, False, BLUE)  # ИТОГО на слайде изделия из частей
GRAND = ("Roboto Mono", 10, True, False, GREEN)  # ИТОГО на «Итоговых условиях»
GRAND_NOTE = ("Roboto Mono", 9, True, False, BLUE)  # «без НДС»
LOGIST = ("Roboto Mono", 10, True, False, BLUE)
NOTE = ("Roboto Mono Light", 8, False, True, BLACK)
DATE = ("Roboto Mono", 7, False, False, GRAY)

# Геометрия (см), с эталона.
BODY = (0.6, 1.75, 8.3, 8.4)  # текст позиции: left, top, width, height
TITLE_TOP = 0.95  # заголовок ниже верхней линии, как в эталоне
PRICE_AREA = (0.6, 9.4, 8.3, 2.5)  # стоимость прижата к низу (низ ~11,9)
IMG_AREA = (9.3, 1.8, 24.9, 11.3)  # картинки позиции: left, top, right, bottom
TOTAL_BODY = (0.6, 2.4, 8.3, 9.4)
TOTAL_IMG = (9.35, 1.47, 24.47, 11.55)
IMG_GAP = 0.4
LIST_MARGIN, LIST_INDENT = 1.3, -0.9  # нумерация: номер на 0,4 см, текст на 1,3 см от края текста
BODY_CHARS, LIST_CHARS, BODY_ROWS, TITLE_CHARS = 52, 45, 24, 30  # оценка вместимости
# Фоновые линии (как в эталоне): верх и низ на всю ширину, вертикаль между текстом и картинками.
RULE_TOP, RULE_BOTTOM, RULE_LEFT, RULE_RIGHT, RULE_X, RULE_COLOR = 0.79, 12.69, 0.79, 24.59, 8.89, "CFCFCF"
FOOTER_TAGLINE, FOOTER_WIDTH = "Google Shape;59;p3", 6.0  # в PowerPoint строка подвала иначе переносится
NBSP = " "
UNIT_RE = re.compile(r"(\d) (мм|см|м²|м|кг|шт\.?)(?=[\s.,;:)]|$)")

NOTES_DEFAULT = [
    "Стоимость указана на основании предоставленных дизайн-чертежей и визуальных материалов.",
    "Финальная стоимость может быть уточнена после обсуждения текущего КП, согласования материалов, возможных альтернатив.",
]
NOTE_INSTALL = ("Монтаж выполняется при готовности объекта, наличии доступа к зонам установки и завершении "
                "необходимых подготовительных работ со стороны заказчика.")
NOTE_WARRANTY = "Гарантия на изготовленные изделия составляет 12 месяцев при соблюдении условий эксплуатации."

# Внутреннее — не должно попасть в клиентский текст.
FORBIDDEN = ["себестоим", "наценк", "коэффиц", "подрядчик", "дизайнерск", "изготовление и сборка", "закупочн"]


class KPError(Exception):
    pass


def money(n):
    """Разряды и «руб» — через неразрывный пробел, чтобы сумма не рвалась при переносе."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise KPError(f"сумма должна быть целым числом рублей без копеек: {n!r}")
    return f"{n:,}".replace(",", NBSP) + NBSP + "руб"


def clean(s, warns, where):
    """Длинные тире -> короткий дефис (правило вёрстки)."""
    s = str(s).strip()
    if re.search("[—–]", s):
        warns.append(f"{where}: длинное тире заменено на «-»")
        s = re.sub(r"\s*[—–]\s*", " - ", s)
    return UNIT_RE.sub(lambda m: m.group(1) + NBSP + m.group(2), s)  # «2 мм» не разрывать


# --- pptx ---

def shape(slide, name):
    for sh in slide.shapes:
        if sh.name == name:
            return sh
    raise KPError(f"в шаблоне нет фигуры {name!r} — шаблон изменён?")


def clone_slide(prs, src):
    new = prs.slides.add_slide(src.slide_layout)
    for ph in list(new.placeholders):
        ph._element.getparent().remove(ph._element)
    for sh in src.shapes:
        new.shapes._spTree.append(copy.deepcopy(sh._element))
    add_rules(new)
    widen_footer(shape(new, FOOTER_TAGLINE))
    return new


def widen_footer(sh):
    """Шире на FOOTER_WIDTH, центр на месте (текст по центру блока)."""
    c = sh.left + sh.width // 2
    sh.width = Cm(FOOTER_WIDTH)
    sh.left = c - sh.width // 2


def add_rules(slide):
    """Фоновые линии эталона; ставятся первыми, чтобы лежать под текстом и картинками."""
    tree = slide.shapes._spTree
    for x1, y1, x2, y2 in ((RULE_LEFT, RULE_TOP, RULE_RIGHT, RULE_TOP),
                           (RULE_LEFT, RULE_BOTTOM, RULE_RIGHT, RULE_BOTTOM),
                           (RULE_X, RULE_TOP, RULE_X, RULE_BOTTOM)):
        c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
        c.line.color.rgb = RGBColor.from_string(RULE_COLOR)
        c.line.width = Pt(0.75)
        st = c._element.find(qn("p:style"))  # стиль темы даёт тень и толщину — убрать
        if st is not None:
            c._element.remove(st)
        tree.remove(c._element)
        tree.insert(2, c._element)  # сразу после nvGrpSpPr и grpSpPr


def place(sh, box):
    sh.left, sh.top, sh.width, sh.height = (Cm(v) for v in box)


def set_body(sh, paras, anchor="t"):
    """paras: [(runs, numbered)], runs: [(текст, стиль)]; пустой runs — пустая строка."""
    bp = sh.text_frame._txBody.bodyPr
    for el in list(bp):
        bp.remove(el)
    etree.SubElement(bp, qn("a:noAutofit"))  # размер блока фиксирован, как в эталоне
    bp.set("anchor", anchor)
    tf = sh.text_frame
    tf.clear()
    for i, (runs, numbered) in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        for el in p._p.findall(qn("a:pPr")):
            p._p.remove(el)
        ppr = etree.SubElement(p._p, qn("a:pPr"))
        p._p.insert(0, ppr)
        ppr.set("algn", "l")
        size = (runs[0][1][1] if runs else 7)
        ln = etree.SubElement(ppr, qn("a:lnSpc"))
        etree.SubElement(ln, qn("a:spcPct")).set("val", "115000")
        for tag in ("a:spcBef", "a:spcAft"):
            etree.SubElement(etree.SubElement(ppr, qn(tag)), qn("a:spcPts")).set("val", "0")
        if numbered:
            ppr.set("marL", str(Cm(LIST_MARGIN)))
            ppr.set("indent", str(Cm(LIST_INDENT)))
            etree.SubElement(etree.SubElement(ppr, qn("a:buClr")), qn("a:srgbClr")).set("val", BLACK)
            etree.SubElement(ppr, qn("a:buSzPts")).set("val", str(size * 100))
            etree.SubElement(ppr, qn("a:buFont")).set("typeface", runs[0][1][0])
            etree.SubElement(ppr, qn("a:buAutoNum")).set("type", "arabicPeriod")
        else:
            ppr.set("marL", "0")
            ppr.set("indent", "0")
            etree.SubElement(ppr, qn("a:buNone"))
        for text, (font, sz, bold, italic, color) in runs or [("", TEXT)]:
            r = p.add_run()
            r.text = text
            r.font.name, r.font.size, r.font.bold, r.font.italic = font, Pt(sz), bold, italic
            r.font.color.rgb = RGBColor.from_string(color)


def line(text, style):
    return ([(text, style)], False)


BLANK = ([], False)


def add_images(slide, paths, area, warns, where, required=True):
    paths = [Path(p) if Path(p).is_absolute() else ROOT / p for p in paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise KPError(f"{where}: нет файлов картинок: {missing}")
    if not paths:
        if required:
            warns.append(f"{where}: нет изображения — нужен чертёж/визуализация из проекта")
        return
    left, top, right, bottom = area
    n = len(paths)
    col_w = (right - left - IMG_GAP * (n - 1)) / n
    box_h = bottom - top
    for i, p in enumerate(paths):
        x = left + i * (col_w + IMG_GAP)
        pic = slide.shapes.add_picture(str(p), Cm(x), Cm(top))
        k = min(Cm(col_w) / pic.width, Cm(box_h) / pic.height)  # вписать, пропорции сохранить
        w, h = int(pic.width * k), int(pic.height * k)
        pic.width, pic.height = w, h
        pic.left = int(Cm(x) + (Cm(col_w) - w) / 2)
        pic.top = int(Cm(top) + (Cm(box_h) - h) / 2)
    if n > 3:
        warns.append(f"{where}: {n} изображений — проверить читаемость чертежа (лучше собрать в один коллаж)")


def rows(paras):
    n = 0
    for runs, numbered in paras:
        t = "".join(r[0] for r in runs)
        n += max(1, -(-len(t) // (LIST_CHARS if numbered else BODY_CHARS)))
    return n


# --- слайды ---

def item_slide(prs, tpl, it, kp_date, w, where):
    title = clean(it["name"], w, where)
    if len(title) > TITLE_CHARS:
        w.append(f"{where}: длинный заголовок ({len(title)} симв.) — может не влезть в строку")
    body = []
    q = it["quantity"]
    if q != 1:
        body.append(line(f"Количество: {q} {it.get('unit', 'шт.')}", TEXT))
    for key, label in (("dimensions", "Габарит"), ("material", "Материал"), ("finish", "Финиш")):
        if it.get(key):
            body.append(line(f"{it.get(key + '_label', label)}: {clean(it[key], w, where)}", TEXT))
        else:
            w.append(f"{where}: не указано «{label}» — уточнить у Екатерины (строка пропущена)")
    feats = [clean(f, w, where) for f in it.get("features", [])]
    if feats:
        body += [BLANK, line("Технические характеристики:", TEXT), BLANK]
        body += [([(f, TEXT)], True) for f in feats]
    if rows(body) > BODY_ROWS:
        w.append(f"{where}: текст ~{rows(body)} строк при запасе {BODY_ROWS} — сократить характеристики")

    if it.get("parts"):
        prices = [line(f"{clean(pt['label'], w, where)}: {money(pt['price'])}", PRICE) for pt in it["parts"]]
        prices += [BLANK, line(f"ИТОГО: {money(item_price(it))}", PRICE_SUM)]
    elif q != 1:
        prices = [line(f"Стоимость за {q} {it.get('unit', 'шт.')}: {money(it['price'])}", PRICE)]
    else:
        prices = [line(f"Стоимость: {money(it['price'])}", PRICE)]

    s = clone_slide(prs, tpl)
    shape(s, TITLE_BOX).top = Cm(TITLE_TOP)
    set_body(shape(s, TITLE_BOX), [line(title, TITLE)])
    set_body(shape(s, DATE_BOX), [line(f"Цены актуальны на момент {kp_date:%d.%m.%Y}", DATE)])
    b = shape(s, BODY_BOX)
    place(b, BODY)
    set_body(b, body)
    pb = shape(s, PRICE_BOX)
    place(pb, PRICE_AREA)
    set_body(pb, prices, anchor="b")
    add_images(s, it.get("images", []), IMG_AREA, w, where)
    return s


def item_price(it):
    if it.get("parts"):
        return sum(pt["price"] for pt in it["parts"])
    return it["price"]


def logistics_lines(lg):
    """[(подпись, сумма)] для «Итоговых условий»."""
    if not lg:
        return []
    if lg.get("combined_price") is not None:
        return [(lg.get("label") or "Доставка с разгрузкой и монтажом", lg["combined_price"])]
    out = [(lbl, lg[k]) for lbl, k in (("Доставка", "delivery_price"), ("Монтаж", "installation_price")) if lg.get(k) is not None]
    if not out:
        raise KPError("logistics: нет ни одной суммы")
    return out


def total_slide(prs, tpl, items_total, lg, notes, w):
    s = clone_slide(prs, tpl)
    for name in (DATE_BOX, PRICE_BOX):
        el = shape(s, name)._element
        el.getparent().remove(el)
    shape(s, TITLE_BOX).top = Cm(TITLE_TOP + 0.3)  # на итоговом заголовок ниже, как в эталоне
    set_body(shape(s, TITLE_BOX), [line("Итоговые условия", TITLE_TOTAL)])
    paras = [([(f"ИТОГО: {money(items_total)} ", GRAND), ("без НДС", GRAND_NOTE)], False)]
    for lbl, v in logistics_lines(lg):
        paras += [BLANK, line(f"{clean(lbl, w, 'итог')}: {money(v)}", LOGIST)]
    for n in notes:
        paras += [BLANK, line(clean(n, w, "итог"), NOTE)]
    b = shape(s, BODY_BOX)
    place(b, TOTAL_BODY)
    set_body(b, paras)
    photo = [TOTAL_PHOTO] if TOTAL_PHOTO.exists() else []
    if not photo:
        w.append(f"нет фото для итогового слайда: {TOTAL_PHOTO}")
    add_images(s, photo, TOTAL_IMG, w, "итог", required=False)
    return s


def set_term(cond, term):
    """Условия — фирменный текст как есть, меняется только срок (вместо «__»)."""
    for p in shape(cond, COND_BOX).text_frame.paragraphs:
        for r in p.runs:
            if r.text.strip() == "__":
                r.text = str(term)
                return
    raise KPError("в слайде условий не найдено место для срока «__»")


def build(data, template, out_dir):
    w = []
    pr = data.get("project", {})
    kp_date = date.fromisoformat(pr.get("kp_date") or date.today().isoformat())
    items = data.get("items") or []
    if not items:
        raise KPError("нет позиций")
    for i, it in enumerate(items, 1):
        for k in ("name", "quantity"):
            if it.get(k) in (None, ""):
                raise KPError(f"позиция {i}: не заполнено поле {k!r}")
        if it.get("parts"):
            for pt in it["parts"]:
                money(pt["price"])
        elif it.get("price") is None:
            raise KPError(f"позиция {i}: нет цены (price или parts)")
        else:
            money(it["price"])
    term = (data.get("conditions") or {}).get("production_term")
    if not term:
        raise KPError("не указан срок производства (conditions.production_term)")

    prs = Presentation(str(template))
    sl = list(prs.slides)
    tpl, cond = sl[S_ITEM - 1], sl[S_COND - 1]

    new = [item_slide(prs, tpl, it, kp_date, w, f"позиция {i} «{it['name']}»") for i, it in enumerate(items, 1)]

    lg = data.get("logistics") or {}
    items_total = sum(item_price(it) for it in items)
    tot = data.get("totals") or {}
    notes = tot.get("notes")
    if notes is None:
        notes = NOTES_DEFAULT + ([NOTE_INSTALL] if lg.get("installation_price") or lg.get("combined_price") else []) + [NOTE_WARRANTY]
    new.append(total_slide(prs, tpl, items_total, lg, notes, w))
    set_term(cond, term)
    widen_footer(shape(cond, "Google Shape;69;p4"))

    # Порядок: обложка, позиции, итоговые условия, условия, контакты. Слайд-образец изделия убрать.
    ids = prs.slides._sldIdLst
    by_id = {int(el.get("id")): el for el in ids}
    order = [s.slide_id for s in [sl[S_COVER - 1]] + new + [cond, sl[S_LAST - 1]]]
    tpl_el = by_id[tpl.slide_id]
    for el in list(ids):
        ids.remove(el)
    for sid in order:
        ids.append(by_id[sid])
    prs.part.drop_rel(tpl_el.get(qn("r:id")))

    # Проверка сгенерированного текста: внутреннее и длинные тире.
    for n, s in enumerate(new, 2):
        txt = " ".join(sh.text_frame.text for sh in s.shapes if sh.has_text_frame).lower()
        bad = [f for f in FORBIDDEN if f in txt]
        if bad:
            raise KPError(f"слайд {n}: внутренние сведения в тексте КП: {bad}")
        if re.search("[—–]", txt):
            raise KPError(f"слайд {n}: осталось длинное тире")

    out_dir.mkdir(parents=True, exist_ok=True)
    client = re.sub(r"[^\w\- ]+", "", pr.get("client") or pr.get("name") or "клиент").strip().replace(" ", "_")[:40]
    deal = pr.get("deal_id") or "без-сделки"
    v = 1
    while (path := out_dir / f"КП_{deal}_{client}_v{v}_{kp_date:%Y-%m-%d}.pptx").exists():
        v += 1
    prs.save(str(path))
    log_sum = sum(v for _, v in logistics_lines(lg))
    return path, items_total, log_sum, len(prs.slides), w


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--template", default=str(TEMPLATE))
    a = ap.parse_args()
    data = json.loads(Path(a.input).read_text(encoding="utf-8"))
    try:
        path, items_total, log_sum, n, warns = build(data, Path(a.template), Path(a.out))
    except KPError as e:
        sys.exit(f"ОШИБКА: {e}")
    print(f"Готово: {path}\nСлайдов: {n}. ИТОГО изделия: {money(items_total)}"
          + (f". Доставка/монтаж: {money(log_sum)}" if log_sum else ""))
    for x in warns:
        print("ВНИМАНИЕ:", x)


if __name__ == "__main__":
    main()
