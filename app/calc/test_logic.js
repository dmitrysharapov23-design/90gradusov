// Проверка чистых функций: node app/calc/test_logic.js [путь к json со строками A:B шаблона]
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

const ctx = { module: { exports: {} } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'Logic.gs'), 'utf8'), ctx);
const L0 = ctx.module.exports;
const plain = (v) => (v && typeof v === "object" ? JSON.parse(JSON.stringify(v)) : v);
const L = Object.fromEntries(Object.entries(L0).map(([k, f]) => [k, typeof f === "function" ? (...a) => plain(f(...a)) : f]));

assert.deepStrictEqual(L.parseCalcRequest('#1234 стол 2 шт'), { dealId: '1234', tz: 'стол 2 шт' });
assert.deepStrictEqual(L.parseCalcRequest('  #77: каркас'), { dealId: '77', tz: 'каркас' });
assert.deepStrictEqual(L.parseCalcRequest('#1234\nстрока1\nстрока2'), { dealId: '1234', tz: 'строка1\nстрока2' });
assert.strictEqual(L.parseCalcRequest('сделка #1234'), null);
assert.strictEqual(L.parseCalcRequest('#12ab'), null);
assert.strictEqual(L.parseCalcRequest('#тег'), null);

assert.strictEqual(L.cleanClientName('ООО "Си Лав Металл"'), 'Си Лав Металл');
assert.strictEqual(L.cleanClientName('ИП Обинов'), 'Обинов');
assert.strictEqual(L.cleanClientName('Иванов Ипполит'), 'Иванов Ипполит');
assert.strictEqual(L.cleanClientName('«HIRCOST» / кофейня'), 'HIRCOST кофейня');
assert.ok(L.cleanClientName('А'.repeat(50)).length <= 30);

assert.strictEqual(L.makeSheetName('1234', 'Си Лав', ['шаблон']), '1234_Си Лав');
assert.strictEqual(L.makeSheetName('1234', 'Си Лав', ['1234_Си Лав']), '1234_Си Лав_v2');
assert.strictEqual(L.makeSheetName('1234', 'Си Лав', ['1234_Си Лав', '1234_си лав_v2']), '1234_Си Лав_v3');
assert.strictEqual(L.makeSheetName('1234', '', []), '1234');

assert.deepStrictEqual(Array.from(L.sheetsForDeal('12', ['12_А', '123_Б', '12_А_v3', '12_А_v2'])),
  ['12_А_v3', '12_А_v2', '12_А']);

const mats = L.parseMaterialRows([['листовые', null], ['лист 1мм (м2)', 100], ['труба нерж', null], ['50х25 (метр)', 5], ['50х25 (метр)', 6]], 3);
assert.strictEqual(mats.length, 3);
assert.strictEqual(mats[0].key, 'листовые | лист 1мм (м2)');
assert.strictEqual(mats[0].unit, 'м2');
assert.strictEqual(mats[1].row, 6);

assert.strictEqual(L.telegramMessageLink(-1001234567890, 55), 'https://t.me/c/1234567890/55');
assert.strictEqual(L.formatRub(2304500.4), '2 304 500 ₽');

// ---------- Счета (этап 3). Строки — типовой «Счёт на оплату»; реальных счетов ещё нет. ----------
assert.strictEqual(L.parseNum('12 345,67'), 12345.67);
assert.strictEqual(L.parseNum('0,512'), 0.512);
assert.ok(isNaN(L.parseNum('abc')));

let r = L.parseInvoiceLine('1 Лист г/к 3х1250х2500 ст3сп 0,736 т 89 900,00 66 166,40');
assert.deepStrictEqual(r, { name: 'Лист г/к 3х1250х2500 ст3сп', qty: 0.736, unit: 'т', price: 89900, sum: 66166.4, checked: true });
r = L.parseInvoiceLine('2 Труба профильная 40х20х2 6 м 120 м 185,50 22 260,00');
assert.strictEqual(r.qty, 120); assert.strictEqual(r.unit, 'м'); assert.strictEqual(r.price, 185.5); assert.ok(r.checked);
assert.strictEqual(r.name, 'Труба профильная 40х20х2 6 м');
r = L.parseInvoiceLine('3 Лист нерж. AISI 430 0,8х1250х2500 BA+PE 5 шт 7 650,00 38 250,00');
assert.strictEqual(r.unit, 'шт'); assert.strictEqual(r.qty, 5); assert.strictEqual(r.price, 7650);
r = L.parseInvoiceLine('Труба 89х3,5 эл.св. шт 2,00 4 800,00 9 600,00'); // единица перед количеством
assert.strictEqual(r.qty, 2); assert.strictEqual(r.price, 4800); assert.ok(r.checked);
// С колонками НДС: цена — та, что сходится с суммой.
r = L.parseInvoiceLine('Пруток 10 ст3 0,1 т 95 000,00 9 500,00 22% 1 712,30 9 500,00');
assert.strictEqual(r.price, 95000);
assert.strictEqual(L.parseInvoiceLine('Итого: 66 166,40'), null);
assert.strictEqual(L.parseInvoiceLine('В том числе НДС 22%: 11 931,64'), null);
assert.strictEqual(L.invoiceCellsToLine([1, 'Лист 2х1250х2500', 0.512, 'т', 92000, 47104]),
  '1,00 Лист 2х1250х2500 0,512 т 92 000,00 47 104,00');
r = L.parseInvoiceLine(L.invoiceCellsToLine([1, 'Лист 2х1250х2500', 0.512, 'т', 92000, 47104]));
assert.strictEqual(r.name, 'Лист 2х1250х2500'); assert.ok(r.checked);

const h = L.parseInvoiceHeader('Поставщик: ООО «Металлсервис», ИНН 7801234567\nСчет на оплату № 1543 от 14 октября 2026 г.\nПокупатель: ИП Шарапов Д.А.');
assert.deepStrictEqual(h, { number: '1543', date: '14.10.2026', supplier: 'ООО «Металлсервис»' });
assert.strictEqual(L.parseInvoiceHeader('Счёт № А-77 от 03.09.2026').date, '03.09.2026');

let sp = L.parseItemSpec('Лист г/к 3х1250х2500 ст3сп');
assert.strictEqual(sp.form, 'sheet'); assert.strictEqual(sp.metal, 'steel'); assert.strictEqual(sp.t, 3); assert.strictEqual(sp.w, 1250);
sp = L.parseItemSpec('Лист нерж. AISI 430 0,8х1250х2500 BA+PE');
assert.strictEqual(sp.metal, 'stainless'); assert.strictEqual(sp.grade, '430'); assert.strictEqual(sp.t, 0.8);
sp = L.parseItemSpec('Лист AISI 304 2 мм 1000х2000');
assert.strictEqual(sp.t, 2); assert.strictEqual(sp.w, 1000); assert.strictEqual(sp.l, 2000);
assert.strictEqual(L.parseItemSpec('Лист х/к ст3 1,0').t, 1);
sp = L.parseItemSpec('Труба профильная 40х20х2');
assert.deepStrictEqual([sp.form, sp.a, sp.b, sp.wall], ['profile', 40, 20, 2]);
sp = L.parseItemSpec('Труба 89х3,5 эл.св.');
assert.deepStrictEqual([sp.form, sp.d, sp.wall], ['round', 89, 3.5]);
sp = L.parseItemSpec('Труба проф. нерж AISI 304 20х20х1,5 L=6000');
assert.deepStrictEqual([sp.form, sp.metal, sp.a, sp.len], ['profile', 'stainless', 20, 6]);
sp = L.parseItemSpec('Пруток ф10 ст3');
assert.deepStrictEqual([sp.form, sp.d], ['rod', 10]);
assert.strictEqual(L.parseItemSpec('Лист оцинк. 1х1250х2500').metal, 'galv');

const catalog = L.parseMaterialRows([
  ['листовые металлы', null], ['нержавейка 430 0,8мм (м2)', 2354.89], ['нержавейка 304 2мм (м2)', 7665],
  ['сталь 1мм (м2)', 1163.97], ['сталь 3мм (м2)', 3955.95],
  ['Прочее', null], ['сетка ПВЛ 20х8 (м2)', 1102.5],
  ['труба профильная', null], ['40х20 (метр)', 218.4], ['20х20 (метр)', 160.65], ['пруток 10мм (метр)', 73.5],
  ['труба профильная нерж', null], ['20х20 (метр)', 392.37],
  ['труба круглая', null], ['25х1,5 (метр)', 249.67], ['89 (метр)', 800],
  ['доп.материалы', null], ['ручки (шт)', 72.8],
  ['доп.работы', null], ['гибы (шт)', 670]
], 3);
const key = (n) => L.matchMaterial(n, catalog).key;
assert.strictEqual(key('Лист г/к 3х1250х2500 ст3сп'), 'листовые металлы | сталь 3мм (м2)');
assert.strictEqual(key('Лист нерж. AISI 430 0,8х1250х2500'), 'листовые металлы | нержавейка 430 0,8мм (м2)');
assert.strictEqual(key('Лист нерж. AISI 304 0,8х1250х2500'), ''); // 304 0,8 в справочнике нет
assert.strictEqual(key('Лист оцинк. 1х1250х2500'), '');            // оцинковка ≠ сталь
assert.strictEqual(key('Труба профильная 20х40х2'), 'труба профильная | 40х20 (метр)');
assert.strictEqual(key('Труба проф. нерж AISI 304 20х20х1,5'), 'труба профильная нерж | 20х20 (метр)');
assert.strictEqual(key('Труба 89х3,5 эл.св.'), 'труба круглая | 89 (метр)');
assert.strictEqual(key('Труба 25х2 эл.св.'), '');                   // в справочнике 25х1,5
assert.strictEqual(key('Пруток ф10 ст3'), 'труба профильная | пруток 10мм (метр)');
assert.strictEqual(key('Сетка ПВЛ 506 20х8 1000х2000'), 'Прочее | сетка ПВЛ 20х8 (м2)');
assert.strictEqual(key('Ручки мебельные 128 мм'), 'доп.материалы | ручки (шт)');
assert.ok(/нет марки/.test(L.matchMaterial('Лист нерж 2мм', catalog).note));

const cat = (k) => catalog.find((c) => c.key === k);
// ₽/т → ₽/м²: 89 900 ₽/т × 3 мм × 7,85 = 2 117,15 ₽/м²
let c = L.convertPrice({ name: 'Лист г/к 3х1250х2500 ст3сп', unit: 'т', price: 89900 }, cat('листовые металлы | сталь 3мм (м2)'));
assert.strictEqual(c.price, 2117.15);
// ₽/лист → ₽/м²: 7 650 / 3,125 = 2 448
c = L.convertPrice({ name: 'Лист нерж. AISI 430 0,8х1250х2500', unit: 'шт', price: 7650 }, cat('листовые металлы | нержавейка 430 0,8мм (м2)'));
assert.strictEqual(c.price, 2448);
// ₽/т → ₽/м профиль 40х20х2: по ГОСТ 30245 ≈ 1,68 кг/м
assert.ok(Math.abs(L.massPerMeter({ form: 'profile', metal: 'steel', a: 40, b: 20, wall: 2 }) - 1.68) < 0.01);
assert.ok(Math.abs(L.massPerMeter({ form: 'round', metal: 'steel', d: 89, wall: 3.5 }) - 7.38) < 0.01);
c = L.convertPrice({ name: 'Труба профильная 40х20х2', unit: 'т', price: 98000 }, cat('труба профильная | 40х20 (метр)'));
assert.ok(c.price > 164 && c.price < 165, JSON.stringify(c));
c = L.convertPrice({ name: 'Труба профильная 40х20', unit: 'т', price: 98000 }, cat('труба профильная | 40х20 (метр)'));
assert.strictEqual(c.price, ''); assert.ok(/стенки/.test(c.how));
c = L.convertPrice({ name: 'Труба проф. нерж 20х20х1,5 L=6000', unit: 'шт', price: 2400 }, cat('труба профильная нерж | 20х20 (метр)'));
assert.strictEqual(c.price, 400);
c = L.convertPrice({ name: 'Труба профильная 40х20х2', unit: 'м', price: 185.5 }, cat('труба профильная | 40х20 (метр)'));
assert.strictEqual(c.price, 185.5);

const inv = L.parseInvoice(['Счет на оплату № 1 от 01.10.2026', '1 Лист г/к 3х1250х2500 ст3сп 0,736 т 89 900,00 66 166,40',
  '2 Резка листа 4 шт 150,00 600,00', '3 Доставка 1 шт 3 000,00 3 000,00', 'Итого: 69 766,40'], []);
assert.strictEqual(inv.items.length, 1);
assert.strictEqual(inv.skipped.length, 2);

// НДС: мы не плательщики — в цену идёт то, что платим.
r = L.parseInvoiceLine('1 Лист г/к 2х1250х2500 ст3 0,5 т 80 000,00 40 000,00 22% 8 800,00 48 800,00');
assert.strictEqual(r.price, 97600); assert.strictEqual(r.sum, 48800); assert.ok(r.vat);
r = L.parseInvoiceLine('1 Лист г/к 2х1250х2500 ст3 0,5 т 97 600,00 48 800,00'); // НДС внутри цены
assert.strictEqual(r.price, 97600); assert.ok(!r.vat);
let v = L.parseInvoice(['1 Лист г/к 2х1250х2500 ст3 0,5 т 80 000,00 40 000,00', '2 Доставка 1 шт 2 000,00 2 000,00',
  'Итого: 42 000,00', 'НДС 20%: 8 400,00', 'Всего к оплате: 50 400,00'], []);
assert.strictEqual(v.items[0].price, 96000); assert.ok(v.items[0].vat);
v = L.parseInvoice(['1 Лист г/к 2х1250х2500 ст3 0,5 т 96 000,00 48 000,00', 'Итого: 48 000,00',
  'В том числе НДС 20%: 8 000,00', 'Всего к оплате: 48 000,00'], []);
assert.strictEqual(v.items[0].price, 96000); assert.ok(!v.items[0].vat);
v = L.parseInvoice([], [[1, 'Лист г/к 2х1250х2500 ст3', 0.5, 'т', 80000, 40000], ['Итого:', 40000], ['Всего к оплате:', 48800]]);
assert.strictEqual(v.items[0].price, 97600);

// OCR разнёс ячейки по строкам (формат счёта металлобазы, 16.10.2026).
const ocr = ['Счет No 3296355', '', 'от 28 Сентября 2026 г.', 'Склад Цена с НДС Кол-во', 'Ед.', 'Сумма, руб',
  '1 сталь нержав х/к б/н лист AISI 430 (08Х17)', '|ВА+РЕ 0,7х1250х2500', 'BX1H', '183 498', '0,175', 'Т',
  '32 112,15/10 шт.', '2 Поддоны 1250х2500', 'BX1H', '3 Автоперевозка', '2300', '6 800', '1', 'шт', '2 300,00',
  '1 шт', '6 800,00', 'Итого:', '0,22 тонн', 'В том числе НДС:', '41 212,15 руб. 7 431,70 руб.', 'Итого к оплате:'];
const hb = L.parseInvoiceHeader(ocr.join('\n'));
assert.strictEqual(hb.number, '3296355'); assert.strictEqual(hb.date, '28.09.2026');
v = L.parseInvoice(ocr, []);
assert.strictEqual(v.items.length, 1); assert.strictEqual(v.skipped.length, 1); // автоперевозка — услуга
assert.deepStrictEqual([v.items[0].qty, v.items[0].price, v.items[0].sum, v.items[0].unit], [0.175, 183498, 32112.15, 'Т']);
assert.ok(/0,7х1250х2500/.test(v.items[0].name));
// Выбран материал другой толщины: ₽/т пересчитывается по толщине материала (0,8), а не листа из счёта (0,7).
c = L.convertPrice(v.items[0], cat('листовые металлы | нержавейка 430 0,8мм (м2)'));
assert.strictEqual(c.price, 1130.35, JSON.stringify(c));

// Вся таблица в одной строке, размеры через «*», НДС только в итоге (формат счёта металлобазы №2, 16.10.2026).
v = L.parseInvoice(['Счёт на оплату №МП02453МО14 от 15 сентября 2026 г.',
  '1 Лист AISI 430 2*1500*3000(Нерж. В пленке) 3 шт 16 122,13 48 366,39 2 Труба профильная 20*20*1,5 120 м 62,30 7 475,41',
  'Итого: 55 841,80 Сумма НДС: 12 285,20 Всего к оплате: 68 127,00'], []);
assert.strictEqual(v.items.length, 2);
assert.strictEqual(v.items[0].price, 19669); assert.ok(v.items[0].vat); // 16 122,13 × 1,22
assert.strictEqual(L.matchMaterial(v.items[1].name, catalog).key, 'труба профильная | 20х20 (метр)');
assert.strictEqual(L.parseInvoiceHeader('Счёт на оплату №МП02453МО14 от 15 сентября 2026 г.').number, 'МП02453МО14');

assert.ok(L.isCalcMaterial('Лист алюм. АМг5М 3*1500*3000', catalog));       // металл, материал выберут вручную
assert.ok(L.isCalcMaterial('Сетка ПВЛ 506 20х8 1000х2000', catalog));        // есть в справочнике
assert.ok(!L.isCalcMaterial('7016;п/э;шагрень;PE7016WG85C20 краска порошковая', catalog));
assert.ok(!L.isCalcMaterial('Газ аргон в/ч в баллоне (40л)', catalog));

assert.strictEqual(L.parseItemSpec('Лист х/к 2x1500х3000 4N+PE шлиф. 08Х17 AISI430').grade, '430');
assert.strictEqual(L.parseItemSpec('Лист х/к 2x1500х3000 2В мат. 08Х17').grade, '430');
assert.strictEqual(key('Лист х/к 0,8x1500х3000 2В мат. 08Х17 AISI430'), 'листовые металлы | нержавейка 430 0,8мм (м2)');
v = L.parseInvoice(['1 Распил ЛДСП. 1 шт 14 000,00 14 000,00'], []);
assert.strictEqual(v.items.length, 0); assert.strictEqual(v.skipped.length, 1);

// Реальный справочник шаблона: ключи «раздел | материал» не должны повторяться.
const rowsFile = process.argv[2];
if (rowsFile) {
  const rows = JSON.parse(fs.readFileSync(rowsFile, 'utf8'));
  const m = L.parseMaterialRows(rows, 3);
  const keys = new Set(m.map(x => x.key));
  const dup = m.filter((x, i) => m.findIndex(y => y.key === x.key) !== i).map(x => x.key);
  console.log(`шаблон: материалов ${m.length}, с ценой ${m.filter(x => x.price !== '').length}, разделов ${new Set(m.map(x => x.section)).size}`);
  assert.strictEqual(keys.size, m.length, 'повторяются ключи: ' + dup.join('; '));
}
console.log('OK');
