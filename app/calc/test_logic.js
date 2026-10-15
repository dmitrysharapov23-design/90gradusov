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
