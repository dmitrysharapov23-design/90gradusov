"""Заполненность полей сделок Б24: какие поля менеджеры никогда не заполняют.

Вход: выгрузки crm_list (JSON {"items": [...]}) по воронкам, сделанные с select ["ID", ..., "UF_*"].
Коннектор убирает пустые поля из ответа, поэтому «поле есть в записи» = «заполнено».
Выход: по каждой воронке — доля сделок, где поле заполнено; список полей с 0%.
"""
import json
import sys

TITLES = {
    "CONTACT_ID": "Контакт", "COMPANY_ID": "Компания", "COMMENTS": "Комментарий", "SOURCE_ID": "Источник",
    "UF_CRM_1774210276012": "Тип продукта",
    "UF_CRM_1774210737236": "Дата готовности заказа",
    "UF_CRM_1774210750399": "Техническое задание",
    "UF_CRM_1774211130851": "Дата предоставления ТЗ",
    "UF_CRM_1774211146637": "Город доставки",
    "UF_CRM_1774211209893": "Пожелания для логистики",
    "UF_CRM_1774211225095": "Желаемый срок отгрузки",
    "UF_CRM_1774211558445": "Допустимое отклонение бюджета",
    "UF_CRM_1774211737154": "Файл коммерческого предложения",
    "UF_CRM_1774214827793": "Ориентировочный срок изготовления (раб. дн.)",
    "UF_CRM_1774215276577": "Причина отказа",
    "UF_CRM_1774215954": "Менеджер по продажам",
    "UF_CRM_1774429292445": "id сделки просчета",
    "UF_CRM_1774429302636": "id сделки производства",
    "UF_CRM_1774430429996": "Ссылка на файл ТЗ",
    "UF_CRM_1774431125524": "Ссылка на файл с просчетами",
    "UF_CRM_1774472295770": "Чертежи клиента",
    "UF_CRM_1774472476819": "Визуал // фото проекта",
    "UF_CRM_1774475283010": "Чертежи по расчетам",
    "UF_CRM_1774477351864": "Фото изделия в упаковке",
    "UF_CRM_1774477431267": "Общий вес (кг)",
    "UF_CRM_1774477444661": "Количество мест",
    "UF_CRM_1774477746295": "Тип сделки",
    "UF_CRM_1774536950329": "Договор",
    "UF_CRM_1774538280769": "Фото готового изделия без упаковки",
    "UF_CRM_1774538341604": "Длина",
    "UF_CRM_1774538352320": "Ширина",
    "UF_CRM_1774538361454": "Высота",
    "UF_CRM_1774538514568": "Финальные чертежи",
    "UF_CRM_1774538759335": "Фото в кузове автомобиля",
    "UF_CRM_1774539334964": "УПД",
    "UF_CRM_1774610516019": "Подписанная спецификация от клиента",
    "UF_CRM_1774810580219": "Монтаж с нашей стороны",
    "UF_CRM_1774810585708": "Логистика",
    "UF_CRM_1774810591199": "Бюджет",
    "UF_CRM_1774875031127": "% оплаты",
    "UF_CRM_1774877991338": "Сумма доплаты",
    "UF_CRM_1774878024989": "Бонус дизайнеру",
    "UF_CRM_69DD014378F08": "TelegramUsername_WZ (Wazzup)",
    "UF_CRM_69DD014382FF7": "TelegramId_WZ (Wazzup)",
    "UF_CRM_69DD01438A8EC": "Instagram_WZ (Wazzup)",
    "UF_CRM_69DD014392213": "VK_WZ (Wazzup)",
    "UF_CRM_69DD0143998D7": "Avito_WZ (Wazzup)",
    "UF_CRM_69DD0143A138E": "MaxId_WZ (Wazzup)",
    "UF_CRM_69DD0143A7DCC": "WhatsappUsername_WZ (Wazzup)",
    "UF_CRM_69DD0143AFBF2": "WhatsappLid_WZ (Wazzup)",
    "UF_CRM_69FB17272DE86": "CianProfileId_WZ (Wazzup)",
    "UF_CRM_69E75D34ADE5E": "messenger-type (форма сайта)",
    "UF_CRM_69E75D34B9E62": "messenger-id (форма сайта)",
    "UF_CRM_69E75D34C29C8": "Расскажите_что_вы_задумали (форма сайта)",
    "UF_CRM_69E75D34CAAAB": "agreement (форма сайта)",
    "UF_CRM_69E75D34D365A": "tranid (форма сайта)",
    "UF_CRM_69E75D34DDAB7": "formname (форма сайта)",
    "UF_CRM_69E75D34E5D85": "file (форма сайта)",
    "UF_CRM_69FB17273873E": "file_0 (форма сайта)",
    "UF_CRM_69FB17273FFE2": "file_1 (форма сайта)",
    "UF_CRM_69FD9DC192D24": "file_2 (форма сайта)",
    "UF_CRM_69FD9DC19B59E": "file_3 (форма сайта)",
    "UF_CRM_6A15A1AE5A257": "file_4 (форма сайта)",
    "UF_CRM_6A8209A7700E0": "delivery (корзина сайта)",
    "UF_CRM_6A8209A77CAC8": "Checkbox (корзина сайта)",
    "UF_CRM_6A8209A784E2B": "orderid (корзина сайта)",
    "UF_CRM_6A8209A78D30E": "paymentid (корзина сайта)",
    "UF_CRM_6A8209A7959AE": "product (корзина сайта)",
    "UF_CRM_6A9EC93E5C6BD": "Телеграм (корзина сайта)",
    "UF_CRM_6A9EC93E691E6": "paymentsystem (корзина сайта)",
    "UF_CRM_6A9EC93E74312": "requisites (корзина сайта)",
}


def fill(path):
    items = json.load(open(path, encoding="utf-8"))["items"]
    n = len(items)
    cnt = {k: 0 for k in TITLES}
    for it in items:
        for k, v in it.items():
            if k in cnt and v not in (None, "", [], "0", 0):
                cnt[k] += 1
    return n, cnt


def main(paths):
    results = {p: fill(p) for p in paths}
    for p, (n, cnt) in results.items():
        print(f"\n== {p}: сделок {n}")
        for k, c in sorted(cnt.items(), key=lambda x: -x[1]):
            if c:
                print(f"  {c / n * 100:4.0f}%  {TITLES[k]}")
        print("  Ни разу не заполнено:")
        for k, c in cnt.items():
            if not c:
                print(f"    - {TITLES[k]}")
    if len(results) > 1:
        never_all = [k for k in TITLES if all(cnt[k] == 0 for _, cnt in results.values())]
        print(f"\nПусто во всех воронках ({len(never_all)}):")
        for k in never_all:
            print(f"  - {TITLES[k]}")


if __name__ == "__main__":
    main(sys.argv[1:])
