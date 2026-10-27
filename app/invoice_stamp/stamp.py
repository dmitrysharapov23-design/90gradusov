"""Подпись и печать ООО «90 ГРАДУСОВ» на PDF счёта из 1С.

Берёт PDF печатной формы «Счет на оплату» (обычной, без факсимиле), находит
строки «Руководитель» и «Бухгалтер», ставит на линию подписи картинку подписи,
рядом — печать. Картинки — файлы из 1С (data/1c_facsimile/, в git не попадают).

    python -m app.invoice_stamp.stamp вход.pdf [выход.pdf]
"""
import os
import sys
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[2]
FAX_DIR = Path(os.environ.get("FACSIMILE_DIR", ROOT / "data" / "1c_facsimile"))
SIGN = FAX_DIR / "90-подпись.png"
STAMP = FAX_DIR / "90-печать.png"

ROLES = ("Руководитель", "Бухгалтер")
SIGN_W = 95       # ширина подписи, pt
STAMP_D = 110     # диаметр печати, pt


class StampError(Exception):
    pass


def _find_sign_lines(page):
    """Для каждой роли — (центр x, y линии) по подписи «подпись» под строкой."""
    words = page.get_text("words")
    found = {}
    for role in ROLES:
        row = [w for w in words if w[4] == role]
        if not row:
            continue
        r = row[-1]
        hints = [w for w in words if w[4] == "подпись"
                 and r[3] - 2 < w[1] < r[3] + 15]
        if not hints:
            continue
        h = min(hints, key=lambda w: w[1])
        found[role] = ((h[0] + h[2]) / 2, h[1] - 1)
    return found


def stamp_pdf(src, dst=None, sign=SIGN, stamp=STAMP):
    """Ставит подписи и печать, возвращает путь к новому PDF."""
    src = Path(src)
    dst = Path(dst) if dst else src.with_name(src.stem + " (подпись).pdf")
    for f in (sign, stamp):
        if not Path(f).exists():
            raise StampError(f"нет файла {f}")
    ratio = fitz.Pixmap(str(sign)).height / fitz.Pixmap(str(sign)).width

    doc = fitz.open(src)
    lines, page = {}, None
    for p in reversed(doc):          # подписи — на последней странице с ними
        lines = _find_sign_lines(p)
        if lines:
            page = p
            break
    if "Руководитель" not in lines:
        raise StampError("не нашёл строку «Руководитель … подпись» — это точно «Счет на оплату» из 1С?")

    sign_h = SIGN_W * ratio
    x0, y0 = lines["Руководитель"]
    zone = fitz.Rect(x0 - SIGN_W, y0 - sign_h, x0 + SIGN_W * 2 + STAMP_D, y0 + 40)
    if any(fitz.Rect(i["bbox"]).intersects(zone) for i in page.get_image_info()):
        raise StampError("на счёте уже есть подпись или печать — пришлите счёт без факсимиле")
    for x, y in lines.values():
        rect = fitz.Rect(x - SIGN_W / 2, y - sign_h + 8, x + SIGN_W / 2, y + 8)
        page.insert_image(rect, filename=str(sign), overlay=True)

    xs = [x for x, _ in lines.values()]
    ys = [y for _, y in lines.values()]
    cx = max(xs) + SIGN_W / 2 + STAMP_D / 2 - 10
    cy = (min(ys) + max(ys)) / 2
    page.insert_image(fitz.Rect(cx - STAMP_D / 2, cy - STAMP_D / 2,
                                cx + STAMP_D / 2, cy + STAMP_D / 2),
                      filename=str(stamp), overlay=True)
    doc.save(dst, garbage=3, deflate=True)
    return dst


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(stamp_pdf(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
