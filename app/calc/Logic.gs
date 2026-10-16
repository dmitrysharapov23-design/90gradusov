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
    .replace(/[«»"'“”„\x60]/g, ' ')
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

// ---------- Счета: разбор и пересчёт цен (этап 3) ----------
// Счёт → строки {name, qty, unit, price, sum} → материал справочника → цена в единице калькулятора.
// Ничего не пишет в «Цены»: результат — предложение, которое человек принимает вручную.

// «12 345,67» / «1,250» / «95000.5» → число; не число → NaN.
function parseNum(s) {
  if (typeof s === 'number') return s;
  var t = String(s == null ? '' : s).replace(/[\s\u00a0]/g, '').replace(',', '.');
  return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : NaN;
}

var UNIT_RE = '(тн|тонн[аы]?|т|кг|м2|м²|кв\\.?\\s?м|п\\.?\\s?м|пог\\.?\\s?м|м\\.?\\s?п|м|шт|лист(?:ов|а)?|л)';

// Единица из счёта → t | kg | m2 | m | pc; иначе ''.
function normUnit(u) {
  var s = String(u || '').toLowerCase().replace(/[.\s]/g, '');
  if (/^(т|тн|тонн[аы]?|t)$/.test(s)) return 't';
  if (/^(кг|kg)$/.test(s)) return 'kg';
  if (/^(м2|м²|квм|m2)$/.test(s)) return 'm2';
  if (/^(м|пм|погм|мп|метр|m)$/.test(s)) return 'm';
  if (/^(шт|лист|листов|листа|л|штук|pc)$/.test(s)) return 'pc';
  if (/^час/.test(s)) return 'h';
  return '';
}

// Единица калькулятора по названию материала: «(м2)», «(метр)», «(шт)», «(час)».
function calcUnit(unit) {
  var s = String(unit || '').toLowerCase();
  if (/м2|м²/.test(s)) return 'm2';
  if (/метр|^м$|п\.?м/.test(s)) return 'm';
  if (/шт/.test(s)) return 'pc';
  if (/час/.test(s)) return 'h';
  return '';
}

var SKIP_LINE = /^(итого|всего|в\s*т\.?\s*ч|в\s+том\s+числе|ндс|сумма\s+ндс|без\s+ндс|к\s+оплате)/i;
var SERVICE_LINE = /(доставк|перевоз|резк|рубк|раскро|погрузк|разгрузк|транспорт|услуг|упаковк|паллет|поддон|тара\b|порезк|гибк|работ[аы]?\b)/i;

// Деньги в счетах печатаются с копейками: «1 234,56». По этому признаку их можно отделить от количества.
var MONEY_RE = /\d{1,3}(?:[ \u00a0]\d{3})*,\d{2}(?![\d])/g;

// Строка текста счёта → {name, qty, unit, price, sum, checked} или null.
// checked: кол-во × цена сошлось с суммой (±2%). Если не сошлось — строку всё равно отдаём, но с пометкой.
function parseInvoiceLine(line) {
  var s = String(line || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  if (!s || !/[А-Яа-яA-Za-z]/.test(s)) return null;
  var re = new RegExp('(?:^|\\s)(\\d+(?:[.,]\\d+)?)\\s*' + UNIT_RE + '\\.?(?=\\s)|(?:^|\\s)' + UNIT_RE + '\\.?\\s+(\\d+(?:[.,]\\d+)?)(?=\\s)', 'gi');
  var m, best = null;
  while ((m = re.exec(s)) !== null) {
    var qty = parseNum(m[1] || m[4]), unit = m[2] || m[3];
    var name = s.slice(0, m.index).trim(), rest = s.slice(m.index + m[0].length);
    var money = (rest.match(MONEY_RE) || []).map(parseNum);
    if (!name || !/[А-Яа-яA-Za-z]/.test(name) || !(qty > 0) || money.length < 1) { re.lastIndex = m.index + 1; continue; }
    var cand = null;
    var j = 0;
    for (var i = 0; i < money.length && !cand; i++) {
      for (j = i + 1; j < money.length; j++) {
        if (money[j] > 0 && Math.abs(qty * money[i] - money[j]) / money[j] <= 0.02) {
          cand = { price: money[i], sum: money[j], checked: true }; break;
        }
      }
    }
    if (!cand) cand = { price: money[0], sum: money[1] || '', checked: false };
    var row = { name: cleanItemName(name), qty: qty, unit: unit, price: cand.price, sum: cand.sum, checked: cand.checked };
    // НДС сверху: правее суммы есть «сумма с НДС». Мы не плательщики НДС — берём то, что платим.
    if (cand.checked) {
      var gross = grossOf(cand.sum, money.slice(j + 1));
      if (gross) { row.price = round2(gross / qty); row.sum = gross; row.vat = true; }
    }
    // Сошедшийся вариант главнее; из сошедшихся — с самым длинным названием («Труба 40х20х2 6 м | 120 м …»).
    if (!best || (row.checked && !best.checked) || (row.checked === best.checked && row.checked && row.name.length > best.name.length)) best = row;
    re.lastIndex = m.index + 1;
  }
  if (best && SKIP_LINE.test(best.name)) return null;
  return best;
}

var VAT_RATES = [0.05, 0.07, 0.1, 0.2, 0.22];

// Сумма с НДС среди чисел правее суммы без НДС: отношение = 1 + ставка (±0,3%).
function grossOf(net, later) {
  for (var k = 0; k < later.length; k++) {
    var r = later[k] / net - 1;
    for (var v = 0; v < VAT_RATES.length; v++) if (Math.abs(r - VAT_RATES[v]) <= 0.003) return later[k];
  }
  return 0;
}

// Множитель НДС для счёта, где строки без НДС, а он добавлен только в итоге:
// «Итого: 100 000,00 / НДС 22%: 22 000,00 / Всего к оплате: 122 000,00». Иначе 1.
function invoiceVatFactor(lines, items) {
  var text = (lines || []).join('\n').replace(/\u00a0/g, ' ');
  var money = '(\\d{1,3}(?:[ ]\\d{3})*,\\d{2})';
  var net = text.match(new RegExp('итого(?!\\s*(?:с\\s*ндс|к\\s*оплате))[^\\d\\n]*' + money, 'i'));
  var gross = text.match(new RegExp('(?:(?:всего|итого)\\s+к\\s+оплате|(?:итого|всего)\\s+с\\s+ндс)[^\\d\\n]*' + money, 'i'));
  if (!net || !gross) return 1;
  var n = parseNum(net[1]), g = parseNum(gross[1]);
  if (!(n > 0) || !grossOf(n, [g])) return 1;
  var sum = items.reduce(function (a, it) { return a + (Number(it.sum) || 0); }, 0);
  return Math.abs(sum - n) / n <= 0.01 ? g / n : 1; // строки складываются в «Итого» без НДС
}

// Убирает номер строки и артикул в начале: «1 Лист…», «12. Труба…», «1,00 Лист» (из ячеек таблицы).
function cleanItemName(name) {
  return String(name).replace(/^\s*\d+(?:,00)?[.)]?\s+(?=\D)/, '').replace(/\s+/g, ' ').trim();
}

// Ячейки строки таблицы (Google Doc / Sheet) → та же строка текста: числа печатаем как в счёте.
function invoiceCellsToLine(cells) {
  return cells.map(function (c) {
    if (typeof c === 'number') return formatMoneyRu(c);
    return String(c == null ? '' : c).replace(/\n/g, ' ').trim();
  }).filter(Boolean).join(' ');
}

function formatMoneyRu(n) {
  var neg = n < 0, v = Math.abs(n);
  var parts = v.toFixed(2).split('.');
  // Количество с дробью вида 0,512 сохраняем как есть — иначе потеряем знаки.
  if (Math.round(v * 100) / 100 !== v) parts = [String(Math.floor(v)), String(v).split('.')[1]];
  return (neg ? '-' : '') + parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ',' + parts[1];
}

var MONTHS = { 'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4, 'ма': 5, 'июн': 6, 'июл': 7, 'август': 8,
  'сентябр': 9, 'октябр': 10, 'ноябр': 11, 'декабр': 12 };

// Шапка счёта: {number, date: 'dd.MM.yyyy', supplier}. Чего нет — ''.
function parseInvoiceHeader(text) {
  var t = String(text || '').replace(/\u00a0/g, ' ');
  var out = { number: '', date: '', supplier: '' };
  var m = t.match(/сч[её]т(?:[\s-]*(?:на\s+оплату|оферта|-?фактура))?\s*(?:№|No|N)\s*([^\s,]+)\s+от\s+(\d{1,2})[\s.]+([а-яё]+|\d{1,2})[\s.]+(\d{4})/i);
  if (m) {
    out.number = m[1];
    var mon = /^\d+$/.test(m[3]) ? Number(m[3]) : monthNum(m[3]);
    if (mon) out.date = pad2(m[2]) + '.' + pad2(mon) + '.' + m[4];
  }
  var s = t.match(/(?:поставщик|продавец)[^:\n]*:\s*([^\n]+)/i); // «Исполнитель:» — это менеджер, не фирма
  if (s) out.supplier = s[1].split(/,\s*(?:ИНН|КПП|\d{6})/i)[0].trim();
  if (!out.supplier) {
    var orgs = t.match(/(?:ООО|АО|ПАО|ЗАО|ИП)\s*[«"][^»"\n]+[»"]|ИП\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\./g) || [];
    out.supplier = orgs.filter(function (o) { return !/шарапов|90\s*градус/i.test(o); })[0] || '';
  }
  return out;
}

function monthNum(w) {
  var s = String(w).toLowerCase();
  var keys = Object.keys(MONTHS).sort(function (a, b) { return b.length - a.length; });
  for (var i = 0; i < keys.length; i++) if (s.indexOf(keys[i]) === 0) return MONTHS[keys[i]];
  return 0;
}

function pad2(n) { return ('0' + Number(n)).slice(-2); }

// Название позиции → характеристики: form (sheet|profile|round|rod|other), metal (steel|stainless|galv|al), grade, размеры (мм).
function parseItemSpec(name) {
  var s = String(name || '').toLowerCase().replace(/ё/g, 'е')
    .replace(/(\d)\s*[xх×*]\s*(?=\d)/g, '$1x').replace(/(\d),(\d)/g, '$1.$2');
  var spec = { form: 'other', metal: '', grade: '' };
  if (/нерж|aisi|12x18|08x18|\b(430|304|201|321|316l?|439|441)\b/.test(s)) spec.metal = 'stainless';
  else if (/оцинк|\bоц\b|оц\./.test(s)) spec.metal = 'galv';
  else if (/алюм|\bамг|\bад31/.test(s)) spec.metal = 'al';
  else if (/ст\.?\s?3|сталь|г\/к|х\/к|\bгк\b|\bхк\b|09г2с|черн|\bст\b|эл\.?\s?св|вгп/.test(s)) spec.metal = 'steel';
  var g = s.match(/\b(430|304|201|321|316l?|439|441)\b/);
  if (g) spec.grade = g[1];
  var num = '(\\d+(?:\\.\\d+)?)';
  var d3 = s.match(new RegExp(num + 'x' + num + 'x' + num));
  var d2 = s.match(new RegExp(num + 'x' + num + '(?!x|\\d)'));

  if (/пруток|\bкруг\b|\bкруг\s*\d/.test(s) && !/труб/.test(s)) {
    spec.form = 'rod';
    var r = s.match(/(?:ф|ø|д|d|круг|пруток)\s*(\d+(?:\.\d+)?)/) || s.match(/(\d+(?:\.\d+)?)\s*мм/);
    if (r) spec.d = Number(r[1]);
  } else if (/лист|рулон|полоса|штрипс/.test(s) && !/труб/.test(s)) {
    spec.form = /лист/.test(s) ? 'sheet' : 'other';
    if (d3) {
      var a = Number(d3[1]), b = Number(d3[2]), c = Number(d3[3]);
      if (a <= 30) { spec.t = a; spec.w = b; spec.l = c; } else if (c <= 30) { spec.t = c; spec.w = a; spec.l = b; }
    }
    if (!spec.t) {
      var t = s.match(/(?:\bs\s*=?\s*|толщ\S*\s*)(\d+(?:\.\d+)?)/) || s.match(/(\d+(?:\.\d+)?)\s*мм/);
      if (t && Number(t[1]) <= 30) spec.t = Number(t[1]);
    }
    if (!spec.t) {
      // Без «мм»: первое число до 30, кроме марок (ст3, 09г2с, 12х18н10т, AISI 430).
      var bare = s.replace(/ст\.?\s?3\S*|09г2с|\d+x\d+н\d+т?|aisi\s*\d+|\b(430|304|201|321|316l?|439|441)\b/g, ' ')
        .match(/(?:^|[^\d.x])(\d+(?:\.\d+)?)(?![\d.]|x\d)/g) || [];
      for (var i = 0; i < bare.length; i++) {
        var v = Number(bare[i].replace(/^[^\d]+/, ''));
        if (v > 0 && v <= 30) { spec.t = v; break; }
      }
    }
    if (!spec.w && d2) {
      var p = Number(d2[1]), q = Number(d2[2]);
      if (p >= 500 && q >= 500) { spec.w = p; spec.l = q; }
    }
  } else if (/труб|профил/.test(s)) {
    var round = /кругл|эл\.?\s?св|вгп|ø|\bф\s?\d|бесшов|водогаз/.test(s) || (!d3 && d2 && !/проф|квадр|прямоуг/.test(s));
    if (!round && d3) { spec.form = 'profile'; spec.a = Number(d3[1]); spec.b = Number(d3[2]); spec.wall = Number(d3[3]); }
    else if (!round && d2 && /проф|квадр|прямоуг/.test(s)) { spec.form = 'profile'; spec.a = Number(d2[1]); spec.b = Number(d2[2]); }
    else if (d2 || d3) {
      spec.form = 'round';
      var dd = d2 || d3;
      spec.d = Number(dd[1]); spec.wall = Number(dd[2]);
    }
  }
  if (spec.form !== 'other' && !spec.metal) spec.metal = 'steel'; // ДОПУЩЕНИЕ: лист/труба без марки — чёрная сталь
  var len = s.match(/(?:l\s*=?\s*|дл\.?\s*|длин\S*\s*)(\d+(?:\.\d+)?)\s*(мм|м)?/);
  if (len) spec.len = Number(len[1]) * (len[2] === 'мм' || Number(len[1]) > 100 ? 0.001 : 1);
  return spec;
}

// Материал справочника калькулятора («раздел», «название (ед.)») → характеристики в том же виде.
function parseCatalogSpec(section, name) {
  var sec = String(section || '').toLowerCase(), base = String(name || '').replace(/\([^)]*\)\s*$/, '').trim();
  var nerzh = /нерж/.test(sec);
  if (/доп\.?\s*работ/.test(sec)) return { form: 'service' };
  if (/листовые металлы/.test(sec)) {
    var spec = parseItemSpec('лист ' + base);
    if (!spec.t) { var t = base.match(/(\d+(?:[.,]\d+)?)\s*мм/); if (t) spec.t = parseNum(t[1]); }
    return spec;
  }
  if (/труба профильная/.test(sec)) {
    if (/пруток/i.test(base)) return { form: 'rod', metal: nerzh ? 'stainless' : 'steel', grade: '', d: parseNum((base.match(/(\d+(?:[.,]\d+)?)/) || [])[1]) };
    var d = base.replace(/(\d),(\d)/g, '$1.$2').match(/(\d+(?:\.\d+)?)\s*[xх]\s*(\d+(?:\.\d+)?)/i);
    return d ? { form: 'profile', metal: nerzh ? 'stainless' : 'steel', grade: '', a: Number(d[1]), b: Number(d[2]) } : { form: 'other' };
  }
  if (/труба круглая/.test(sec)) {
    var r = base.replace(/(\d),(\d)/g, '$1.$2').match(/^(\d+(?:\.\d+)?)(?:\s*[xх]\s*(\d+(?:\.\d+)?))?/i);
    if (!r) return { form: 'other' };
    var out = { form: 'round', metal: nerzh ? 'stainless' : 'steel', grade: '', d: Number(r[1]) };
    if (r[2]) out.wall = Number(r[2]);
    return out;
  }
  return { form: 'other' };
}

function sameNum(a, b) { return a > 0 && b > 0 && Math.abs(a - b) < 0.011; }

// Позиция счёта → ключ справочника. catalog: [{key, section, name}]. Возвращает {key, note}.
function matchMaterial(itemName, catalog) {
  var it = parseItemSpec(itemName);
  var hits = [];
  catalog.forEach(function (c) {
    var cs = parseCatalogSpec(c.section, c.name);
    if (cs.form === 'service') return;
    if (cs.form === 'other') {
      if (it.form === 'other' && tokensMatch(c.name, itemName)) hits.push(c.key);
      return;
    }
    if (cs.form !== it.form || cs.metal !== it.metal) return;
    if (cs.form === 'sheet') {
      if (cs.grade && cs.grade !== it.grade) return;
      if (sameNum(cs.t, it.t)) hits.push(c.key);
    } else if (cs.form === 'profile') {
      var x = [cs.a, cs.b].sort(), y = [it.a, it.b].sort();
      if (sameNum(x[0], y[0]) && sameNum(x[1], y[1])) hits.push(c.key);
    } else if (cs.form === 'round') {
      if (sameNum(cs.d, it.d) && (!cs.wall || sameNum(cs.wall, it.wall))) hits.push(c.key);
    } else if (cs.form === 'rod') {
      if (sameNum(cs.d, it.d)) hits.push(c.key);
    }
  });
  if (hits.length === 1) return { key: hits[0], note: '' };
  if (hits.length > 1) return { key: '', note: 'несколько вариантов: ' + hits.join('; ') };
  if (it.metal === 'stainless' && it.form === 'sheet' && !it.grade) return { key: '', note: 'нет марки нержавейки' };
  return { key: '', note: 'нет в справочнике — выберите материал' };
}

// Для прочего (сетка, ЛДСП, ручки): все слова и числа из названия справочника есть в позиции счёта.
function tokensMatch(catalogName, itemName) {
  var norm = function (x) { return String(x).toLowerCase().replace(/ё/g, 'е').replace(/\([^)]*\)\s*$/, '').replace(/(\d)\s*[xх×*]\s*(?=\d)/g, '$1x').replace(/(\d)\s+мм/g, '$1мм'); };
  var item = norm(itemName);
  var toks = norm(catalogName).split(/[^0-9a-zа-я.x]+/).filter(function (w) { return w.length >= 2; });
  if (!toks.length) return false;
  return toks.every(function (w) {
    if (/^\d/.test(w)) return new RegExp('(^|[^0-9.])' + w.replace(/\./g, '\\.') + '($|[^0-9.])').test(item);
    return item.indexOf(w.length > 5 ? w.slice(0, 5) : w) >= 0;
  });
}

// Плотность, кг/м³. ДОПУЩЕНИЕ: справочные значения; нерж 430 — 7700, остальная нерж — 7900.
function density(spec) {
  if (spec.metal === 'stainless') return spec.grade === '430' ? 7700 : 7900;
  if (spec.metal === 'al') return 2700;
  return 7850;
}

// Масса 1 м профиля, кг. Профильная — ГОСТ 30245 (радиусы гиба R = 2s, r = s), круглая — по стенке, пруток — сплошной.
function massPerMeter(spec) {
  var rho = density(spec), area = 0;
  if (spec.form === 'profile' && spec.wall) {
    var s = spec.wall;
    area = 2 * s * (spec.a + spec.b - 2 * s) - (4 - Math.PI) * (4 * s * s - s * s);
  } else if (spec.form === 'round' && spec.wall) {
    area = Math.PI * (spec.d - spec.wall) * spec.wall;
  } else if (spec.form === 'rod' && spec.d) {
    area = Math.PI * spec.d * spec.d / 4;
  }
  return area > 0 ? area * rho / 1e6 : 0;
}

// Цена из счёта → цена в единице калькулятора. row: {name, unit, price}; target: {name, section, unit}.
// Возвращает {price, how} или {price: '', how: причина}.
function convertPrice(row, target) {
  var from = normUnit(row.unit), to = calcUnit(target.unit || (String(target.name).match(/\(([^)]*)\)\s*$/) || [, ''])[1]);
  var p = Number(row.price);
  if (!(p > 0)) return { price: '', how: 'нет цены в счёте' };
  if (!from) return { price: '', how: 'не понял единицу «' + row.unit + '»' };
  if (!to) return { price: '', how: 'нет единицы у материала' };
  if (from === to) return { price: round2(p), how: 'как в счёте, ₽/' + unitRu(to) };

  var it = parseItemSpec(row.name), cs = parseCatalogSpec(target.section, target.name);
  var spec = mergeSpec(it, cs);
  var perKg = from === 't' ? p / 1000 : from === 'kg' ? p : 0;

  if (to === 'm2') {
    if (perKg && spec.t) {
      var kgm2 = spec.t * density(spec) / 1000;
      return { price: round2(perKg * kgm2), how: fmt(p) + ' ₽/' + unitRu(from) + ' × ' + fmt(kgm2, 3) + ' кг/м² (лист ' + fmt(spec.t) + ' мм)' };
    }
    if (from === 'pc' && spec.w && spec.l) {
      var area = spec.w * spec.l / 1e6;
      return { price: round2(p / area), how: fmt(p) + ' ₽/лист ÷ ' + fmt(area, 3) + ' м² (' + spec.w + '×' + spec.l + ')' };
    }
    return { price: '', how: perKg ? 'нет толщины листа' : 'нет размера листа — пересчитайте вручную' };
  }
  if (to === 'm') {
    if (perKg) {
      var kgm = massPerMeter(spec);
      if (!kgm) return { price: '', how: 'нет толщины стенки — пересчитайте вручную' };
      return { price: round2(perKg * kgm), how: fmt(p) + ' ₽/' + unitRu(from) + ' × ' + fmt(kgm, 3) + ' кг/м' };
    }
    if (from === 'pc' && spec.len) {
      return { price: round2(p / spec.len), how: fmt(p) + ' ₽/шт ÷ ' + fmt(spec.len) + ' м' };
    }
    return { price: '', how: 'нет длины хлыста — пересчитайте вручную' };
  }
  return { price: '', how: 'нет пересчёта ' + unitRu(from) + ' → ' + unitRu(to) };
}

// Размеры выбранного материала главнее: цена за тонну почти не зависит от толщины, и если для «0,8 мм»
// выбрали лист 0,7 из счёта, пересчёт должен идти по 0,8. Чего нет в справочнике (стенка, размер листа) — из счёта.
function mergeSpec(it, cs) {
  var out = {};
  Object.keys(it).forEach(function (k) { if (it[k] !== '' && it[k] != null) out[k] = it[k]; });
  Object.keys(cs).forEach(function (k) { if (cs[k] !== '' && cs[k] != null && !(k === 'form' && cs[k] === 'other')) out[k] = cs[k]; });
  if (!out.metal) out.metal = 'steel';
  return out;
}

function unitRu(u) { return { t: 'т', kg: 'кг', m2: 'м²', m: 'м', pc: 'шт', h: 'час' }[u] || u; }
function round2(n) { return Math.round(n * 100) / 100; }
function fmt(n, d) {
  var v = Number(n).toFixed(d == null ? 2 : d).replace(/\.?0+$/, '');
  return v.replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

// Распознанный PDF часто разносит ячейки таблицы по строкам: «1 Лист …», «183 498», «0,175», «Т», «32 112,15».
// Позиция — строка «N название» (N идут подряд с 1), её числа — в строках до следующей позиции.
// Цена, кол-во и сумма — тройка чисел, где цена × кол-во = сумма (±1%); кол-во — рядом с единицей.
function parseInvoiceBlocks(lines) {
  var L = (lines || []).map(function (l) { return String(l).replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim(); })
    .filter(Boolean);
  var starts = [], next = 1;
  L.forEach(function (l, i) {
    var m = l.match(/^(\d{1,3})[.)]?\s+(?=.*[А-Яа-яA-Za-z]{3})(.+)$/);
    if (m && Number(m[1]) === next && !/^\d+[.,]\d/.test(m[2])) { starts.push(i); next++; }
  });
  var endRe = /^(итого|всего|ориентировочный|в том числе)/i;
  var unitRe = new RegExp('^' + UNIT_RE + '\\.?$', 'i');
  var out = [];
  starts.forEach(function (st, k) {
    var stop = k + 1 < starts.length ? starts[k + 1] : L.length;
    var name = L[st].replace(/^\d{1,3}[.)]?\s+/, ''), nums = [], unit = '', unitAt = -1;
    for (var i = st + 1; i < stop; i++) {
      var l = L[i];
      if (endRe.test(l)) break;
      if (unitRe.test(l)) { unit = l.replace(/\.$/, ''); unitAt = nums.length; continue; }
      var n = l.match(/^(\d{1,3}(?:[ ]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?![\d.,]*\s*[xх×*]\s*\d)(\s+(\S+))?/);
      if (n && !/[А-Яа-яA-Za-z]{2}/.test(l.replace(/\/.*$/, '').replace(/шт\.?|кг|т\b/gi, ''))) {
        nums.push(parseNum(n[1]));
        if (!unit && n[3] && unitRe.test(n[3])) { unit = n[3]; unitAt = nums.length; }
        continue;
      }
      if (!nums.length && (/^\|/.test(l) || /\d\s*[xх×*]\s*\d/.test(l))) name += ' ' + l.replace(/^\|\s*/, '');
    }
    var best = null;
    for (var a = 0; a < nums.length; a++) for (var b = 0; b < nums.length; b++) for (var c = 0; c < nums.length; c++) {
      if (a === b || a === c || b === c || !(nums[c] > 0)) continue;
      if (Math.abs(nums[a] * nums[b] - nums[c]) / nums[c] > 0.01) continue;
      // b — количество: ближе к единице измерения; иначе меньшее из двух
      var score = (unitAt >= 0 ? -Math.min(Math.abs(b + 1 - unitAt), Math.abs(b - unitAt)) : 0) + (nums[b] <= nums[a] ? 0.5 : 0);
      if (!best || score > best.score) best = { price: nums[a], qty: nums[b], sum: nums[c], score: score };
    }
    if (best) out.push({ name: cleanItemName(name), qty: best.qty, unit: unit || 'шт', price: best.price, sum: best.sum, checked: true });
  });
  return out;
}

// Все строки счёта: строки текста + строки таблиц (ячейки). Услуги и итоги — отдельно.
function parseInvoice(lines, tableRows) {
  var items = [], skipped = [], seen = {};
  var add = function (row) {
    if (!row) return;
    var k = row.name + '|' + row.qty + '|' + row.price;
    if (seen[k]) return;
    seen[k] = true;
    if (SKIP_LINE.test(row.name)) return;
    // «Резка листа» — услуга; «Лист 2 мм (с резкой)» — материал.
    var first = row.name.split(/\s+/)[0];
    if (SERVICE_LINE.test(first) || (SERVICE_LINE.test(row.name) && !/лист|труб|пруток|сетк/i.test(row.name))) skipped.push(row);
    else items.push(row);
  };
  (tableRows || []).forEach(function (cells) { add(parseInvoiceLine(invoiceCellsToLine(cells))); });
  (lines || []).forEach(function (l) { add(parseInvoiceLine(l)); });
  if (!items.length) parseInvoiceBlocks(lines).forEach(add); // таблица разнесена по строкам
  var f = items.some(function (it) { return it.vat; }) ? 1 : invoiceVatFactor((lines || []).concat((tableRows || []).map(invoiceCellsToLine)), items.concat(skipped));
  if (f !== 1) items.forEach(function (it) { it.price = round2(it.price * f); it.sum = round2(it.sum * f); it.vat = true; });
  return { items: items, skipped: skipped };
}

if (typeof module !== 'undefined') {
  module.exports = {
    STATUS: STATUS, parseCalcRequest: parseCalcRequest, cleanClientName: cleanClientName,
    makeSheetName: makeSheetName, sheetsForDeal: sheetsForDeal, parseMaterialRows: parseMaterialRows,
    priceKey: priceKey, telegramMessageLink: telegramMessageLink, escapeHtml: escapeHtml, formatRub: formatRub,
    parseNum: parseNum, normUnit: normUnit, parseInvoiceLine: parseInvoiceLine, parseInvoiceHeader: parseInvoiceHeader,
    parseItemSpec: parseItemSpec, parseCatalogSpec: parseCatalogSpec, matchMaterial: matchMaterial,
    massPerMeter: massPerMeter, convertPrice: convertPrice, parseInvoice: parseInvoice, parseInvoiceBlocks: parseInvoiceBlocks,
    invoiceCellsToLine: invoiceCellsToLine
  };
}
