// Чистые функции без обращений к Google и Telegram — проверяются локально: node app/calc/test_logic.js

var STATUS = {
  DRAFT: 'Черновик',
  FILLED: 'Заполнено',
  REWORK: 'На доработку',
  APPROVED: 'Утверждено'
};
var STATUS_LIST = [STATUS.DRAFT, STATUS.FILLED, STATUS.REWORK, STATUS.APPROVED];

// «#1234 текст ТЗ» → {dealId: '1234', tz: 'текст ТЗ'}; иначе null.
function parseCalcRequest(text) {
  if (!text) return null;
  var m = String(text).match(/^\s*#(\d{1,7})(?![\d\wа-яё])[\s,.:;—-]*([\s\S]*)$/i);
  if (!m) return null;
  return { dealId: m[1], tz: m[2].trim() };
}

// «ООО "Си Лав Металл"» → «Си Лав Металл»
function cleanClientName(name, maxLen) {
  maxLen = maxLen || 30;
  var s = String(name || '')
    .replace(/[«»"'“”„`]/g, ' ')
    .replace(/(^|[\s,])(ООО|ОАО|ЗАО|ПАО|АО|ИП|OOO|LLC)(?=[\s,.]|$)\.?/gi, ' ')
    .replace(/[^0-9A-Za-zА-Яа-яЁё\s.\-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (s.length > maxLen) s = s.slice(0, maxLen).trim();
  return s;
}

// Имя листа: «1234_Клиент», при занятом — «1234_Клиент_v2», «_v3» …
function makeSheetName(dealId, client, existingNames) {
  var base = String(dealId) + (client ? '_' + client : '');
  var taken = {};
  (existingNames || []).forEach(function (n) { taken[String(n).trim().toLowerCase()] = true; });
  if (!taken[base.toLowerCase()]) return base;
  for (var v = 2; v < 100; v++) {
    var name = base + '_v' + v;
    if (!taken[name.toLowerCase()]) return name;
  }
  throw new Error('Слишком много версий листа ' + base);
}

// Листы расчёта по сделке: «1234_…», новые сначала (по номеру версии).
function sheetsForDeal(dealId, names) {
  var prefix = String(dealId) + '_';
  return (names || [])
    .filter(function (n) { return n === String(dealId) || n.indexOf(prefix) === 0; })
    .sort(function (a, b) { return versionOf(b) - versionOf(a); });
}

function versionOf(name) {
  var m = String(name).match(/_v(\d+)$/);
  return m ? Number(m[1]) : 1;
}

// Разметка справочника материалов шаблона: строка с «(» в названии — материал, без — раздел.
// rows: [[A, B], …] начиная со строки firstRow. Возвращает материалы с ключом «раздел | материал».
function parseMaterialRows(rows, firstRow) {
  var section = '';
  var out = [];
  rows.forEach(function (r, i) {
    var label = String(r[0] || '').trim();
    if (!label) return;
    if (label.indexOf('(') < 0) { section = label; return; }
    var unit = (label.match(/\(([^)]*)\)\s*$/) || [, ''])[1];
    out.push({
      row: firstRow + i,
      section: section,
      name: label,
      key: priceKey(section, label),
      unit: unit,
      price: typeof r[1] === 'number' ? r[1] : ''
    });
  });
  return out;
}

function priceKey(section, name) {
  return String(section).trim() + ' | ' + String(name).trim();
}

// Ссылка на сообщение в группе Telegram (для супергрупп id вида -100…).
function telegramMessageLink(chatId, messageId) {
  var s = String(chatId);
  if (s.indexOf('-100') !== 0) return '';
  return 'https://t.me/c/' + s.slice(4) + '/' + messageId;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatRub(n) {
  var v = Math.round(Number(n) || 0);
  return String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₽';
}

if (typeof module !== 'undefined') {
  module.exports = {
    STATUS: STATUS, parseCalcRequest: parseCalcRequest, cleanClientName: cleanClientName,
    makeSheetName: makeSheetName, sheetsForDeal: sheetsForDeal, parseMaterialRows: parseMaterialRows,
    priceKey: priceKey, telegramMessageLink: telegramMessageLink, escapeHtml: escapeHtml, formatRub: formatRub
  };
}
