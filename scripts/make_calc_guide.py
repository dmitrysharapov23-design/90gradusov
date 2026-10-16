"""Инструкция для сотрудников по файлу «Расчёты 90 градусов» → docs/инструкция_расчёты.pdf.

Запуск: .venv/bin/python scripts/make_calc_guide.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

FONTS = Path("/System/Library/Fonts/Supplemental")
pdfmetrics.registerFont(TTFont("Arial", str(FONTS / "Arial.ttf")))
pdfmetrics.registerFont(TTFont("Arial-Bold", str(FONTS / "Arial Bold.ttf")))
pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold")

OUT = Path(__file__).resolve().parent.parent / "docs" / "инструкция_расчёты.pdf"
FILE_URL = "https://docs.google.com/spreadsheets/d/191J6lqWOAS0KsM7E4KqtXFKv7hL5WznZpw41EIgjGFk/edit"
BLUE = colors.HexColor("#1a3c8f")
LIGHT = colors.HexColor("#eef2fb")
YELLOW = colors.HexColor("#fff8e1")

base = ParagraphStyle("base", fontName="Arial", fontSize=10, leading=14, alignment=TA_LEFT)
title = ParagraphStyle("title", parent=base, fontName="Arial-Bold", fontSize=18, leading=22,
                       textColor=BLUE, spaceAfter=2)
sub = ParagraphStyle("sub", parent=base, textColor=colors.HexColor("#555555"), spaceAfter=8)
h1 = ParagraphStyle("h1", parent=base, fontName="Arial-Bold", fontSize=13, leading=17,
                    textColor=BLUE, spaceBefore=10, spaceAfter=4)
item = ParagraphStyle("item", parent=base, leftIndent=12, bulletIndent=2, spaceAfter=2)
note = ParagraphStyle("note", parent=base, fontSize=9, leading=12)
cell = ParagraphStyle("cell", parent=base, fontSize=9, leading=12)
cellb = ParagraphStyle("cellb", parent=cell, fontName="Arial-Bold")


def p(text, style=base):
    return Paragraph(text, style)


def bullets(lines):
    return [Paragraph(t, item, bulletText="•") for t in lines]


def steps(lines):
    return [Paragraph(t, item, bulletText=f"{i}.") for i, t in enumerate(lines, 1)]


def box(flowables, bg=LIGHT):
    t = Table([[flowables]], colWidths=[174 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d3ea")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def table(rows, widths):
    data = [[Paragraph(c, cellb if i == 0 else cell) for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3ea")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    return t


story = [
    p("Расчёты 90 градусов", title),
    p("Инструкция для Екатерины и Александра Андреева · с 16.10.2026", sub),
    box([p(f"<b>Файл:</b> «Расчёты 90 градусов» — <link href='{FILE_URL}' color='blue'>открыть</link>"),
         p("<b>Чат:</b> Telegram «Расчет Заказов», бот @Rascheti90gradusovbot"),
         p("Старый файл «Просчет 90гр» больше не используем. Все прежние расчёты есть в новом файле: "
           "ищите их на листе «Главная».")]),

    p("Как идёт расчёт", h1),
    table([["Шаг", "Кто", "Что делает", "Что происходит само"],
           ["1", "Екатерина", "Пишет в «Расчет Заказов»: <b>#номер_сделки ТЗ</b>",
            "Бот создаёт лист «номер_Клиент», вставляет ТЗ, отвечает ссылкой, пишет Андрееву"],
           ["2", "Андреев", "Заполняет лист, ставит статус <b>«Заполнено»</b>",
            "Екатерине приходит сообщение с итогом и ссылкой"],
           ["3", "Екатерина", "Проверяет. Замечания — в «Комментарий проверки» и статус <b>«На доработку»</b>",
            "Андрееву приходит комментарий. Все правки пишутся в «Журнал_правок»"],
           ["4", "Екатерина", "Всё верно — статус <b>«Утверждено»</b>",
            "Цены в листе фиксируются, лист закрывается от правок, обоим приходит сообщение"]],
          [12, 24, 66, 72]),

    p("1. Заявка на расчёт (Екатерина)", h1),
    *bullets([
        "Новым сообщением в группу «Расчет Заказов», текст начинается с решётки и номера сделки из Битрикса: "
        "<b>#715 столы гостевые 6 шт, нерж 430, ножки 40х40</b>.",
        "Перед # ничего не пишем. Не «#ID», не «# 715», не правка старого сообщения — бот видит только новые.",
        "Ответ бота приходит через 1–2 минуты.",
        "Повторное <b>#715 …</b>, пока расчёт не утверждён, дописывает ТЗ в тот же лист. "
        "После утверждения бот создаст новую версию «…_v2».",
    ]),

    p("2. Как заполнить лист (Андреев)", h1),
    p("Лист устроен как прежний шаблон, сверху добавлена шапка: статус, ссылка на сделку, ТЗ, "
      "комментарий проверки, история."),
    Spacer(1, 4),
    table([["Где", "Что вписать"],
           ["Строка 6, колонки C…U", "Названия изделий. Одна колонка — одно изделие, до 19 штук"],
           ["Строки материалов", "Количество в единицах из названия строки: м², метры, штуки. "
                                  "<b>Цену в колонке B не трогаем</b> — она берётся из листа «Цены»"],
           ["«Красится?»", "да / нет для каждого изделия"],
           ["Коэффициенты", "Профиль — по умолчанию 7, листы — 4. Подсказки внизу листа: "
                            "нерж. труба 6, сталь 8, лист нерж 3, лист сталь 5"],
           ["«Доп. работы / доп. стоимость, руб.»", "То, чего нет в справочнике: подряд (буквы, короба), "
                                                  "покупное (электрика, фурнитура), разовые работы. "
                                                  "Рублями на изделие, без коэффициента. Ниже — что входит"],
           ["Покраска, Логистика (колонка B)", "Общая сумма на весь заказ — формула сама разделит по изделиям"]],
          [55, 119]),
    Spacer(1, 6),
    KeepTogether([
        p("<b>Как считается итог изделия</b>"),
        *bullets([
            "Листы = Σ(кол-во × цена) × коэф. листов; профиль = Σ(кол-во × цена) × коэф. профиля.",
            "Доп. работы из справочника (зенковка, гибы, токарка…) — без коэффициента.",
            "Покраска делится между изделиями с «да» пропорционально стоимости, логистика — между всеми.",
            "<b>ИТОГО изделия</b> = листы + профиль + работы + покраска + логистика + доп. стоимость. "
            "Колонка V — итог по заказу.",
        ]),
    ]),
    Spacer(1, 4),
    box([p("Заполнили — в ячейке <b>«Статус»</b> (вверху справа) выберите <b>«Заполнено»</b>.")], YELLOW),

    p("3. Проверка (Екатерина)", h1),
    *bullets([
        "Можно править прямо в листе — каждая правка записывается в «Журнал_правок»: ячейка, было, стало, когда.",
        "Есть замечания: текст в строку «Комментарий проверки», статус <b>«На доработку»</b>. "
        "Андреев исправит и снова поставит «Заполнено».",
        "Всё верно: статус <b>«Утверждено»</b>. Лист закрывается от правок. "
        "Нужно изменить — новое сообщение #номер в чат, бот создаст «…_v2».",
    ]),

    p("4. Поиск расчётов — лист «Главная»", h1),
    *bullets([
        "Жёлтая ячейка <b>B3</b>: номер сделки, часть имени клиента или изделие — «715», «Обинов», «стойка». "
        "Ниже появятся все подходящие расчёты, новые и архивные. Ссылка в колонке «Ссылка» открывает лист.",
        "B3 пустая — список всех текущих (не архивных) расчётов.",
        "Слева — сколько расчётов в каждом статусе.",
        "Лист «Реестр» — полный список: сделка, клиент, статус, даты, итог, изделия. Обновляется сам.",
        "Старые расчёты до 16.10.2026 открываются из отдельного файла-архива. Номеров сделок у них нет — "
        "ищите по клиенту или изделию.",
    ]),

    p("5. Цены материалов", h1),
    *bullets([
        "Все цены — только в листе <b>«Цены»</b>: цена, дата, источник (поставщик, № счёта).",
        "Новые расчёты сразу берут новую цену, утверждённые не меняются.",
        "Нужен новый материал — напишите Дмитрию: его надо добавить и в «Цены», и в шаблон.",
    ]),

    p("Если что-то не работает", h1),
    table([["Ситуация", "Что сделать"],
           ["Бот не ответил за 3 минуты", "Проверьте: сообщение новое, в «Расчет Заказов», начинается с #номер. "
                                          "Если всё так — напишите Дмитрию"],
           ["Лист создан без имени клиента", "Бот не смог прочитать сделку в Битриксе — причина в его ответе. "
                                             "Считать можно, напишите Дмитрию"],
           ["Не приходят личные сообщения", "Откройте @Rascheti90gradusovbot и нажмите «Start»"],
           ["Нужно изменить утверждённый расчёт", "Новое сообщение #номер в чат — появится «…_v2»"]],
          [58, 116]),
    Spacer(1, 8),
    p("Пока не сделано: автоматическое КП после утверждения и обновление цен из счетов — следующие этапы.", note),
]


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Arial", 8)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(18 * mm, 10 * mm, "90 градусов · Расчёты · инструкция")
    canvas.drawRightString(192 * mm, 10 * mm, f"стр. {doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                        topMargin=16 * mm, bottomMargin=16 * mm,
                        title="Расчёты 90 градусов — инструкция", author="90 градусов")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print(OUT)
