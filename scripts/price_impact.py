"""Как изменится итог старых расчётов, если цены материалов заменить ценами из счетов.

Вход: выгрузка «Просчет 90гр» (data/calc/prosch.xlsx), цены — словарь NEW ниже (из «Цены_предложения», 16.10.2026).
Формула калькулятора (docs/03): по изделию листы × коэф. листов + профиль × коэф. профиля + работы;
покраска и логистика — постоянные суммы на заказ, от цен материалов не зависят.
Выход: по каждому листу — итог было/стало, изменение, и какие коэффициенты сохранили бы прежний итог.

Запуск: python3 scripts/price_impact.py [--new-k] [имя листа ...]
  --new-k — заодно заменить коэффициенты по умолчанию на новые (листы 4 → 6,5, профиль 7 → 8);
  --scale-all — умножить все коэффициенты в той же пропорции (листы ×1,625, профиль ×8/7).
"""
import sys
import openpyxl

BOOK = 'data/calc/prosch.xlsx'

# Новая цена, ₽ за ед. калькулятора. Сценарий: последний счёт на лист той же толщины (без пересчёта с другой толщины).
NEW = {
    ('листовые металлы', 'нержавейка 430 0,8мм (м2)'): 1444.44,  # Атвет МП, 31.08.2026, лист 0,8×1500×3000
    ('листовые металлы', 'нержавейка 430 2мм (м2)'): 3636.89,    # СК СТМ, 08.10.2026, шлиф
    ('листовые металлы', 'нержавейка 430 3мм (м2)'): 4795.11,    # СК СТМ, 08.10.2026, шлиф
    ('листовые металлы', 'нержавейка 304 3мм (м2)'): 8884.44,    # Атвет МП, 11.08.2026
    ('листовые металлы', 'сталь 1мм (м2)'): 793.11,              # Атвет МП, 09.09.2026
    ('листовые металлы', 'сталь 2мм (м2)'): 1361.28,             # Атвет МП, 31.08.2026
    ('труба профильная', '20х20 (метр)'): 76.01,                 # Атвет МП, 15.09.2026, 20×20×1,5
    ('труба профильная нерж', '20х20 (метр)'): 356.00,           # Атвет МП, 15.09.2026, AISI 304 (430 — 152 ₽/м)
}

DEFAULT = ['Анар Суши бар Сургут', 'ИП Обинов Чайковского 59', 'ИП Русских Черниговская 17 пере',
           'ИП Деев Таврический 21-23-25']

FIRST_COL, LAST_COL, TOTAL_COL = 3, 21, 22  # C…U — изделия, V — итог

# Коэффициенты по умолчанию: старые и новые (решение 16.10.2026). Меняются только изделия со старым значением по умолчанию —
# коэффициенты, которые расчётчик выставил вручную, остаются.
OLD_K = {'sheets': 4, 'prof': 7}
NEW_K = {'sheets': 6.5, 'prof': 8}


def labels(ws):
    out = {}
    for r in range(1, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if isinstance(a, str):
            out.setdefault(a.strip(), r)
    return out


def num(v):
    return v if isinstance(v, (int, float)) else 0


def analyse(ws, new_k=False, scale_all=False):
    lab = labels(ws)
    r_paint = lab['Красится?']
    r_kprof, r_klist = lab['Коэффициент профиля'], lab['Коэффициент листов']
    r_total = lab['ИТОГО Стоимость руб']
    r_prof_start = lab.get('труба профильная', 40)
    r_works = lab.get('доп.работы', 103)

    sheets_old = sheets_new = prof_old = prof_new = 0.0  # Σ по изделиям с коэффициентами
    base_sheets = base_prof = 0.0                         # без коэффициентов — для подбора коэффициента
    changed = []
    section = ''
    for r in range(3, r_paint):
        a = ws.cell(r, 1).value
        if not isinstance(a, str) or not a.strip():
            continue
        a = a.strip()
        if '(' not in a:
            section = a
            continue
        if r >= r_works:
            continue  # работы — без коэффициента и не меняются
        old = num(ws.cell(r, 2).value)
        new = NEW.get((section, a), old)
        is_sheet = r < r_prof_start
        qty_total = 0.0
        for c in range(FIRST_COL, LAST_COL + 1):
            q = num(ws.cell(r, c).value)
            if not q:
                continue
            k = num(ws.cell(r_klist if is_sheet else r_kprof, c).value)
            grp = 'sheets' if is_sheet else 'prof'
            k2 = k * NEW_K[grp] / OLD_K[grp] if scale_all else NEW_K[grp] if new_k and k == OLD_K[grp] else k
            qty_total += q
            if is_sheet:
                sheets_old += q * old * k; sheets_new += q * new * k2; base_sheets += q * new
            else:
                prof_old += q * old * k; prof_new += q * new * k2; base_prof += q * new
        if qty_total and new != old:
            changed.append((section, a, qty_total, old, new))

    total_old = num(ws.cell(r_total, TOTAL_COL).value)
    delta = (sheets_new - sheets_old) + (prof_new - prof_old)
    return {
        'total_old': total_old, 'total_new': total_old + delta, 'delta': delta,
        'sheets_old': sheets_old, 'sheets_new': sheets_new, 'prof_old': prof_old, 'prof_new': prof_new,
        # Во сколько раз поднять коэффициенты изделий, чтобы группа стоила как раньше.
        'mult_sheets': sheets_old / sheets_new if sheets_new else None,
        'mult_prof': prof_old / prof_new if prof_new else None,
        # Металл по ценам из счетов, без коэффициентов, — примерная закупка под заказ.
        'metal_cost': base_sheets + base_prof,
        'changed': changed,
    }


def rub(x):
    return f'{x:,.0f}'.replace(',', ' ')


def main():
    args = sys.argv[1:]
    new_k = '--new-k' in args          # новые коэффициенты только вместо значений по умолчанию (4 и 7)
    scale_all = '--scale-all' in args  # все коэффициенты в той же пропорции (3 → 4,9; 6 → 6,9 и т. д.)
    names = [a for a in args if not a.startswith('--')] or DEFAULT
    wb = openpyxl.load_workbook(BOOK, data_only=True)
    sum_old = sum_new = 0.0
    for name in names:
        ws = wb[name] if name in wb.sheetnames else next(s for s in wb.worksheets if s.title.strip() == name.strip())
        r = analyse(ws, new_k, scale_all)
        sum_old += r['total_old']; sum_new += r['total_new']
        print(f'\n== {ws.title.strip()}')
        print(f'итого было {rub(r["total_old"])} ₽, стало {rub(r["total_new"])} ₽, '
              f'изменение {rub(r["delta"])} ₽ ({r["delta"] / r["total_old"] * 100:+.1f}%)')
        print(f'  листы: {rub(r["sheets_old"])} → {rub(r["sheets_new"])}; профиль: {rub(r["prof_old"])} → {rub(r["prof_new"])}')
        if r['mult_sheets'] and abs(r['mult_sheets'] - 1) > 0.005:
            print(f'  чтобы листы стоили как раньше: коэффициенты листов × {r["mult_sheets"]:.2f} (4 → {4 * r["mult_sheets"]:.1f})')
        if r['mult_prof'] and abs(r['mult_prof'] - 1) > 0.005:
            print(f'  чтобы профиль стоил как раньше: коэффициенты профиля × {r["mult_prof"]:.2f} (7 → {7 * r["mult_prof"]:.1f})')
        print(f'  металл по ценам счетов (без коэф.): {rub(r["metal_cost"])} ₽ — '
              f'{r["metal_cost"] / r["total_old"] * 100:.0f}% прежнего итога, {r["metal_cost"] / r["total_new"] * 100:.0f}% нового')
        for sec, mat, q, old, new in r['changed']:
            print(f'  {mat} [{sec}]: {q:g} × {old:g} → {new:g} ₽ ({(new / old - 1) * 100:+.0f}%)' if old else
                  f'  {mat} [{sec}]: {q:g} × (нет цены) → {new:g} ₽')
    if len(names) > 1:
        print(f'\nВсего по {len(names)} расчётам: {rub(sum_old)} → {rub(sum_new)} ₽ ({(sum_new / sum_old - 1) * 100:+.1f}%)')


if __name__ == '__main__':
    main()
