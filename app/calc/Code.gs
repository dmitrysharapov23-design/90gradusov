// Автоматизация «Просчет 90гр», этап 1: лист по сообщению «#ID ТЗ», статусы, оповещения, журнал правок, цены.
// Секреты — только в свойствах скрипта: TG_TOKEN (бот «90 Расчёты»), B24_WEBHOOK (входящий вебхук Б24, можно позже).
// Описание процесса — docs/08_автоматизация_просчётов.md, установка — app/calc/README.md.

// Скрипт может быть и привязан к таблице, и отдельным проектом (тогда таблица — по ID).
var BOOK_ID = '191J6lqWOAS0KsM7E4KqtXFKv7hL5WznZpw41EIgjGFk';    // «Расчёты 90 градусов» (рабочий, владелец — Дмитрий)
var ARCHIVE_ID = '1VI36984Xhtkz7AB8cujKvE_EadZ-U9NW5ppvisBnrw4'; // «Расчёты 90 градусов — архив до 16.10.2026»
var OLD_ID = '1aSDeqzJZiJEopmCDlf1OBlXvd61KgujujRvuNW6UAaI';     // «Просчет 90гр» (старый, владелец — Тимонт), только чтение

var _book = null;
function book() {
  if (!_book) _book = SpreadsheetApp.getActiveSpreadsheet() || SpreadsheetApp.openById(BOOK_ID);
  return _book;
}

var SH = {
  TEMPLATE_OLD: 'шаблон',
  TEMPLATE: 'шаблон_v2',
  HOME: 'Главная',
  SETTINGS: 'Настройки',
  PRICES: 'Цены',
  REGISTRY: 'Реестр',
  EDITS: 'Журнал_правок',
  INVOICES: 'Счета_входящие',
  OFFERS: 'Цены_предложения',
  PRICE_LOG: 'Цены_история'
};

// Шапка листа расчёта (4 строки над старым шаблоном).
var CELL = { NAME: 'B1', STATUS: 'J1', DEAL: 'N1', TZ: 'B2', COMMENT: 'B3', HISTORY: 'B4' };
var HEADER_ROWS = 4;
var ITEM_ROW = HEADER_ROWS + 2;      // строка с названиями изделий
var FIRST_ITEM_COL = 3, LAST_ITEM_COL = 21, TOTAL_COL = 22; // C…U, V
var LABEL = {
  PAINTED: 'Красится?',
  TOTAL: 'ИТОГО Стоимость руб',
  EXTRA: 'Доп. работы / доп. стоимость, ₽',
  EXTRA_NOTE: 'Что входит в доп. стоимость'
};

var REGISTRY_HEAD = ['ID сделки', 'Клиент', 'Лист', 'Ссылка', 'Статус', 'Создан', 'Автор ТЗ',
  'Заполнен', 'Утверждён', 'Итого, ₽', 'Сделка Б24', 'Изделия'];
var STATUS_ARCHIVE = 'Архив';

var INVOICE_HEAD = ['Дата', 'От', 'Файл', 'Тип', 'file_id', 'Подпись', 'Сообщение', 'Обработан',
  'Файл Drive', 'Распознано', 'Поставщик', '№ счёта', 'Дата счёта', 'Позиций', 'На подтверждение'];
// Колонки «Цены_предложения» (с 1).
var OFFER_HEAD = ['Дата счёта', 'Поставщик', '№ счёта', 'Позиция в счёте', 'Кол-во', 'Ед.', 'Цена в счёте, ₽',
  'Материал в калькуляторе', 'Ед. калькулятора', 'Новая цена, ₽', 'Как пересчитано', 'Текущая цена, ₽',
  'Изменение', 'Решение', 'Статус', 'Счёт', 'служебное'];
var OF = { DATE: 1, SUPPLIER: 2, NUMBER: 3, ITEM: 4, QTY: 5, UNIT: 6, PRICE: 7, KEY: 8, CUNIT: 9, NEW: 10,
  HOW: 11, CUR: 12, DELTA: 13, DECISION: 14, STATUS: 15, LINK: 16, RAW: 17 };
var DECISION = { ACCEPT: 'Принять', REJECT: 'Отклонить' };
var PRICE_LOG_HEAD = ['Когда', 'Ключ', 'Было, ₽', 'Стало, ₽', 'Дата счёта', 'Поставщик', '№ счёта', 'Кто'];
var BIG_CHANGE = 0.3; // изменение больше ±30% подсвечивается — проверить, не ошибка ли

// Коэффициенты по умолчанию (решение 16.10.2026): цены материалов теперь закупочные из счетов (ниже старых на 25–50%),
// поэтому коэффициенты подняты, чтобы итоги остались на прежнем уровне. Подсказки в листе — в той же пропорции.
var K_DEFAULT = { PROFILE: 8, SHEETS: 6.5 };
var K_HINTS = [
  ['нерж труба кф 6', 'нерж труба кф 7'],
  ['сталь труба кф 8', 'сталь труба кф 9'],
  ['лист без обработки нерж кф 3', 'лист без обработки нерж кф 5'],
  ['если 3мм и более то кф 2,5', 'если 3мм и более то кф 4'],
  ['лист сталь кф 5', 'лист сталь кф 8'],
  ['если 8мм то кф 4', 'если 8мм то кф 6,5']
];

// ---------- Меню и установка ----------

function onOpen() {
  SpreadsheetApp.getUi().createMenu('90°')
    .addItem('Установить / обновить автоматизацию', 'setup')
    .addItem('Проверить подключения', 'checkConnections')
    .addToUi();
}

function setup() {
  var ss = book();
  ensureSettings(ss);
  ensurePrices(ss);
  ensureSheet(ss, SH.REGISTRY, REGISTRY_HEAD);
  ensureSheet(ss, SH.EDITS, ['Когда', 'Лист', 'Ячейка', 'Строка', 'Изделие', 'Было', 'Стало', 'Кто', 'Статус листа']);
  ensureHeader(ensureSheet(ss, SH.INVOICES, INVOICE_HEAD), INVOICE_HEAD);
  ensureOffers(ss);
  ensureSheet(ss, SH.PRICE_LOG, PRICE_LOG_HEAD);
  ensureSetting('tg_prices', '', 'Кто подтверждает цены из счетов: логин Telegram без @ или ID из tg_started (пусто — без оповещений)');
  ensureSetting('invoices_folder', '', 'Папка Drive для счетов из «Админ» — создаётся сама');
  ensureSetting('invoices_import', '', 'ID папки Drive со старыми счетами (экспорт чата) — для разового importInvoiceFolder');
  var tpl = ss.getSheetByName(SH.TEMPLATE);
  if (!tpl) buildTemplateV2(ss);
  else setPriceFormulas(tpl, HEADER_ROWS); // переписать формулы цен (например, после смены разделителя)
  if (!ss.getSheetByName(SH.HOME)) buildHome(ss);

  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (['pollTelegram', 'onEditCalc', 'processInvoices'].indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('pollTelegram').timeBased().everyMinutes(1).create();
  ScriptApp.newTrigger('processInvoices').timeBased().everyMinutes(10).create();
  ScriptApp.newTrigger('onEditCalc').forSpreadsheet(ss).onEdit().create();

  var msg = 'Готово: листы служебные, «' + SH.TEMPLATE + '», триггеры.';
  if (!prop('TG_TOKEN')) msg += '\nНЕТ токена бота: редактор Apps Script → Настройки проекта → Свойства скрипта → TG_TOKEN.';
  if (!prop('B24_WEBHOOK')) msg += '\nНет B24_WEBHOOK: без него лист назовётся только по ID сделки, без клиента.';
  safeAlert(msg);
}

function checkConnections() {
  var lines = [];
  try { lines.push('Telegram: бот @' + tg('getMe', {}).username); } catch (e) { lines.push('Telegram: ' + e.message); }
  if (prop('B24_WEBHOOK')) {
    try { var p = b24('profile', {}); lines.push('Б24: ' + p.NAME + ' ' + p.LAST_NAME); } catch (e) { lines.push('Б24: ' + e.message); }
  } else lines.push('Б24: вебхук не задан');
  var s = readSettings();
  lines.push('Чат «Расчеты»: ' + (s.chat_raschety || 'не найден — напишите в чат любое сообщение'));
  lines.push('Чат «Админ»: ' + (s.chat_admin || 'не найден — напишите в чат любое сообщение'));
  safeAlert(lines.join('\n'));
}

function ensureSheet(ss, name, head) {
  var sh = ss.getSheetByName(name);
  if (sh) return sh;
  sh = ss.insertSheet(name, ss.getNumSheets());
  sh.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold');
  sh.setFrozenRows(1);
  return sh;
}

// Дописывает недостающие колонки справа (лист создан старой версией).
function ensureHeader(sh, head) {
  if (sh.getLastColumn() < head.length) sh.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold');
}

function ensureSetting(key, value, note) {
  if (key in readSettings()) return;
  book().getSheetByName(SH.SETTINGS).appendRow([key, value, note]);
}

function ensureSettings(ss) {
  var sh = ss.getSheetByName(SH.SETTINGS);
  if (sh) return sh;
  sh = ensureSheet(ss, SH.SETTINGS, ['Ключ', 'Значение', 'Пояснение']);
  sh.getRange(2, 1, 7, 3).setValues([
    ['chat_raschety', '', 'ID чата «Расчеты» — заполнится сам, когда бот увидит сообщение в чате'],
    ['chat_admin', '', 'ID чата «Админ» — заполнится сам'],
    ['tg_manager', 'devyanosto_gradusov', 'Екатерина (проверяет расчёт): логин Telegram без @ или числовой ID из tg_started'],
    ['tg_raschetchik', '', 'Андреев (заполняет расчёт): логин Telegram без @ или числовой ID из tg_started'],
    ['b24_portal', '90gradusov.bitrix24.ru', 'Адрес портала для ссылок на сделку'],
    ['chats_seen', '', 'Чаты, где бот видел сообщения (для проверки)'],
    ['tg_started', '', 'Кто написал боту /start: имя @логин = ID']
  ]);
  sh.setColumnWidth(1, 140).setColumnWidth(2, 220).setColumnWidth(3, 480);
  return sh;
}

// Лист «Цены» из справочника старого шаблона. Ключ — «раздел | материал»: названия труб повторяются в разных разделах.
function ensurePrices(ss) {
  var sh = ss.getSheetByName(SH.PRICES);
  if (sh) return sh;
  var src = ss.getSheetByName(SH.TEMPLATE_OLD);
  var mats = readMaterials(src, 0);
  sh = ensureSheet(ss, SH.PRICES, ['Ключ', 'Раздел', 'Материал', 'Ед.', 'Цена', 'Дата цены', 'Источник', 'Комментарий']);
  var rows = mats.map(function (m) {
    return [m.key, m.section, m.name, m.unit, m.price, '', 'из шаблона, дата НЕ ИЗВЕСТНА', ''];
  });
  sh.getRange(2, 1, rows.length, 8).setValues(rows);
  sh.getRange(2, 5, rows.length, 1).setNumberFormat('#,##0.00');
  sh.getRange(2, 6, rows.length, 1).setNumberFormat('dd.MM.yyyy');
  sh.setColumnWidth(1, 60).setColumnWidth(2, 170).setColumnWidth(3, 260).setColumnWidth(7, 260);
  return sh;
}

// Материалы листа: строки от 3 до «Красится?» (со сдвигом шапки offset).
function readMaterials(sheet, offset) {
  var first = 3 + offset;
  var painted = findRow(sheet, LABEL.PAINTED);
  var vals = sheet.getRange(first, 1, painted - first, 2).getValues();
  return parseMaterialRows(vals, first);
}

// Новый шаблон: копия «шаблон» без данных + цены из «Цены» + строки доп. стоимости + шапка со статусом.
function buildTemplateV2(ss) {
  var t = ss.getSheetByName(SH.TEMPLATE_OLD).copyTo(ss).setName(SH.TEMPLATE);
  ss.setActiveSheet(t); ss.moveActiveSheet(1);

  var mats = readMaterials(t, 0);
  t.getRange(2, FIRST_ITEM_COL, 1, LAST_ITEM_COL - FIRST_ITEM_COL + 1).clearContent();
  mats.forEach(function (m) {
    t.getRange(m.row, FIRST_ITEM_COL, 1, LAST_ITEM_COL - FIRST_ITEM_COL + 1).clearContent();
  });
  setPriceFormulas(t, 0);

  // Доп. работы / доп. стоимость по изделию (подряд, покупное) — добавляется к итогу как есть, без коэффициента.
  var totalRow = findRow(t, LABEL.TOTAL);
  t.insertRowsBefore(totalRow, 2);
  var extraRow = totalRow;
  totalRow += 2;
  t.getRange(extraRow, 1, 2, 1).setValues([[LABEL.EXTRA], [LABEL.EXTRA_NOTE]]);
  t.getRange(extraRow, TOTAL_COL).setFormula('=SUM(C' + extraRow + ':U' + extraRow + ')');
  t.getRange(extraRow, 1, 2, TOTAL_COL).setBackground('#fff8e1');
  for (var c = FIRST_ITEM_COL; c <= LAST_ITEM_COL; c++) {
    var cell = t.getRange(totalRow, c);
    var f = cell.getFormula();
    if (f) cell.setFormula(f + '+' + colLetter(c) + extraRow);
  }

  // Шапка.
  t.insertRowsBefore(1, HEADER_ROWS);
  t.getRange('A1:A4').setValues([['Расчёт'], ['ТЗ'], ['Комментарий проверки'], ['История']]).setFontWeight('bold');
  t.getRange('B1:H1').merge().setFontWeight('bold').setFontSize(12);
  t.getRange('I1').setValue('Статус').setFontWeight('bold').setHorizontalAlignment('right');
  t.getRange('J1:L1').merge().setValue(STATUS.DRAFT).setFontWeight('bold').setBackground('#e3f2fd')
    .setDataValidation(SpreadsheetApp.newDataValidation().requireValueInList(STATUS_LIST, true).build());
  t.getRange('M1').setValue('Сделка Б24').setFontWeight('bold').setHorizontalAlignment('right');
  t.getRange('N1:V1').merge();
  ['B2:V2', 'B3:V3', 'B4:V4'].forEach(function (a) {
    t.getRange(a).merge().setWrap(true).setVerticalAlignment('top');
  });
  t.getRange('B2:V2').setWrapStrategy(SpreadsheetApp.WrapStrategy.CLIP);
  t.getRange('B4:V4').setFontColor('#777777').setFontSize(9);
  t.setRowHeight(2, 60);
  t.setFrozenRows(HEADER_ROWS + 2);
  setTemplateCoefficients(t);
  return t;
}

// Применяет K_DEFAULT к шаблону один раз после смены значений (вызывается из триггера processInvoices).
function ensureTemplateCoefficients() {
  var want = K_DEFAULT.PROFILE + '/' + K_DEFAULT.SHEETS, props = PropertiesService.getScriptProperties();
  if (props.getProperty('K_DEFAULT_APPLIED') === want) return;
  setTemplateCoefficients();
  props.setProperty('K_DEFAULT_APPLIED', want);
}

// Коэффициенты по умолчанию и подсказки в «шаблон_v2». Новые расчёты получают их при создании листа;
// уже созданные листы и архив не меняются. После смены K_DEFAULT применяется сам при ближайшем processInvoices.
function setTemplateCoefficients(t) {
  t = t || book().getSheetByName(SH.TEMPLATE);
  var n = LAST_ITEM_COL - FIRST_ITEM_COL + 1;
  [['Коэффициент профиля', K_DEFAULT.PROFILE], ['Коэффициент листов', K_DEFAULT.SHEETS]].forEach(function (x) {
    var r = findRow(t, x[0]);
    if (!r) throw new Error('В шаблоне нет строки «' + x[0] + '»');
    var row = []; for (var i = 0; i < n; i++) row.push(x[1]);
    t.getRange(r, FIRST_ITEM_COL, 1, n).setValues([row]);
  });
  var hints = 0;
  K_HINTS.forEach(function (h) {
    t.createTextFinder(h[0]).matchEntireCell(true).findAll().forEach(function (c) { c.setValue(h[1]); hints++; });
  });
  console.log('Коэффициенты по умолчанию: профиль ' + K_DEFAULT.PROFILE + ', листы ' + K_DEFAULT.SHEETS +
    '; подсказок обновлено: ' + hints);
}

// Колонка B шаблона: цена из листа «Цены» по ключу «раздел | материал».
function setPriceFormulas(t, offset) {
  var sep = argSep();
  readMaterials(t, offset).forEach(function (m) {
    var key = m.key.replace(/"/g, '""');
    t.getRange(m.row, 2).setFormula('=IFERROR(INDEX(\'' + SH.PRICES + '\'!$E:$E' + sep + ' MATCH("' + key + '"' + sep +
      ' \'' + SH.PRICES + '\'!$A:$A' + sep + ' 0))' + sep + ' "")');
  });
}

// Разделитель аргументов формул зависит от локали таблицы: в русской setFormula с запятыми даёт #ERROR!.
var _sep = null;
function argSep() {
  if (_sep) return _sep;
  var c = book().getSheetByName(SH.SETTINGS).getRange('Z1');
  c.setFormula('=SUM(1, 2)');
  SpreadsheetApp.flush();
  _sep = c.getValue() === 3 ? ',' : ';';
  c.clearContent();
  return _sep;
}

// ---------- Telegram ----------

// Запускается триггером раз в минуту. Вебхук не используем: у веб-приложений Apps Script Telegram получает 302 и шлёт дубли.
function pollTelegram() {
  if (!prop('TG_TOKEN')) return;
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    var offset = Number(prop('TG_OFFSET') || 0);
    var updates = tg('getUpdates', { offset: offset, timeout: 0, allowed_updates: ['message', 'my_chat_member'] });
    updates.forEach(function (u) {
      try { handleUpdate(u); } catch (e) { console.error('update ' + u.update_id + ': ' + e.stack); }
      PropertiesService.getScriptProperties().setProperty('TG_OFFSET', String(u.update_id + 1));
    });
  } finally {
    lock.releaseLock();
  }
}

function handleUpdate(u) {
  // Бота добавили в группу или изменили его права — запоминаем чат, даже если там ещё не писали.
  if (u.my_chat_member && u.my_chat_member.chat && u.my_chat_member.chat.type !== 'private') {
    rememberChat(u.my_chat_member.chat);
    return;
  }
  var msg = u.message;
  if (!msg || !msg.chat) return;
  var chat = msg.chat;
  var text = msg.text || msg.caption || '';

  if (chat.type === 'private') {
    if (/^\/start/.test(text) && msg.from) {
      // Запоминаем и по логину, и по числовому ID: у части людей логина в Telegram нет.
      var props = PropertiesService.getScriptProperties();
      props.setProperty('tgchat:' + msg.from.id, String(chat.id));
      if (msg.from.username) props.setProperty('tgchat:' + msg.from.username.toLowerCase(), String(chat.id));
      var started = String(readSettings().tg_started || '');
      var who = personName(msg.from) + (msg.from.username ? ' @' + msg.from.username : '') + ' = ' + msg.from.id;
      if (started.indexOf(String(msg.from.id)) < 0) writeSetting('tg_started', (started ? started + '; ' : '') + who);
      tg('sendMessage', { chat_id: chat.id, text: 'Готово. Оповещения по расчётам будут приходить сюда.' });
    }
    return;
  }

  var s = rememberChat(chat);
  if (String(chat.id) === String(s.chat_raschety)) {
    var req = parseCalcRequest(text);
    if (req) onCalcRequest(req, msg);
  } else if (String(chat.id) === String(s.chat_admin)) {
    if (msg.document || msg.photo) logInvoice(msg);
  }
}

// Запоминает чат; «Расчеты» и «Админ» находит по названию, если ID ещё не задан.
function rememberChat(chat) {
  var s = readSettings();
  var id = String(chat.id), title = chat.title || '';
  if (!s.chat_raschety && /расч[её]т/i.test(title)) { writeSetting('chat_raschety', id); s.chat_raschety = id; }
  if (!s.chat_admin && /админ/i.test(title)) { writeSetting('chat_admin', id); s.chat_admin = id; }
  var seen = String(s.chats_seen || '');
  if (seen.indexOf(id) < 0) writeSetting('chats_seen', (seen ? seen + '; ' : '') + title + ' = ' + id);
  return s;
}

function onCalcRequest(req, msg) {
  var ss = book();
  var names = ss.getSheets().map(function (x) { return x.getName(); });
  var author = personName(msg.from);
  var link = telegramMessageLink(msg.chat.id, msg.message_id);
  var now = nowStr();

  // Есть неутверждённый лист по сделке — дописываем ТЗ, новый не создаём.
  var latest = sheetsForDeal(req.dealId, names)
    .map(function (n) { return ss.getSheetByName(n); })
    .filter(isCalcSheet)[0];
  if (latest && latest.getRange(CELL.STATUS).getValue() !== STATUS.APPROVED) {
    var tzCell = latest.getRange(CELL.TZ);
    tzCell.setValue(tzCell.getValue() + '\n\n— дополнение ' + now + ' (' + author + '): ' + req.tz);
    appendHistory(latest, 'ТЗ дополнено ' + now + ' (' + author + ')' + (link ? ' ' + link : ''));
    reply(msg, 'Дописал к ТЗ листа «' + latest.getName() + '»: ' + sheetUrl(latest));
    notify('tg_raschetchik', 'ТЗ по расчёту «' + escapeHtml(latest.getName()) + '» дополнено.\n' + sheetUrl(latest), msg.chat.id);
    return;
  }

  var deal, dealError = '';
  try { deal = getDealInfo(req.dealId); } catch (e) {
    // Б24 недоступен или у вебхука нет прав — лист всё равно создаём, но говорим об этом в чат.
    dealError = e.message;
    deal = { found: null, title: '', client: '' };
  }
  if (deal.found === false) {
    reply(msg, 'Сделка #' + req.dealId + ' в Б24 не найдена — проверьте номер. Лист не создан.');
    return;
  }
  var client = cleanClientName(deal.client);
  var name = makeSheetName(req.dealId, client, names);

  var sh = ss.getSheetByName(SH.TEMPLATE).copyTo(ss).setName(name);
  ss.setActiveSheet(sh); ss.moveActiveSheet(3); // после «Главной» и «Реестра»
  sh.getRange(CELL.NAME).setValue(name + (deal.title ? ' — ' + deal.title : ''));
  sh.getRange(CELL.STATUS).setValue(STATUS.DRAFT);
  sh.getRange(CELL.DEAL).setValue(dealUrl(req.dealId));
  sh.getRange(CELL.TZ).setValue(req.tz || '(ТЗ в сообщении: ' + link + ')');
  appendHistory(sh, 'создан ' + now + ' по ТЗ от ' + author + (link ? ' ' + link : ''));

  appendRow(SH.REGISTRY, [req.dealId, deal.client || '', name, sheetUrl(sh), STATUS.DRAFT, now, author,
    '', '', '', dealUrl(req.dealId)]);
  reply(msg, 'Лист расчёта «' + name + '» создан: ' + sheetUrl(sh) +
    (dealError ? '\n(Не удалось прочитать сделку из Б24: ' + dealError + ' — клиент в названии не указан)' :
      deal.found == null ? '\n(Б24 не подключён — клиент в названии не указан)' : ''));
  notify('tg_raschetchik', 'Новый расчёт «' + escapeHtml(name) + '».\n' + sheetUrl(sh), msg.chat.id);
}

// ---------- Счета → предложения цен (этап 3) ----------

function ensureOffers(ss) {
  var sh = ss.getSheetByName(SH.OFFERS);
  if (sh) return sh;
  sh = ensureSheet(ss, SH.OFFERS, OFFER_HEAD);
  var sep = argSep();
  sh.getRange(2, OF.KEY, sh.getMaxRows() - 1, 1).setDataValidation(SpreadsheetApp.newDataValidation()
    .requireValueInRange(ss.getSheetByName(SH.PRICES).getRange('A2:A'), true).setAllowInvalid(true).build());
  sh.getRange(2, OF.DECISION, sh.getMaxRows() - 1, 1).setDataValidation(SpreadsheetApp.newDataValidation()
    .requireValueInList([DECISION.ACCEPT, DECISION.REJECT], true).build());
  sh.getRange(1, OF.NEW, sh.getMaxRows(), 1).setBackground('#fff8e1');
  sh.getRange(1, OF.DECISION, sh.getMaxRows(), 1).setBackground('#e3f2fd');
  var all = sh.getRange(2, 1, sh.getMaxRows() - 1, OFFER_HEAD.length);
  var delta = sh.getRange(2, OF.DELTA, sh.getMaxRows() - 1, 1);
  sh.setConditionalFormatRules([
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=LEFT($O2' + sep + ' 7)="Принято"')
      .setBackground('#e8f5e9').setRanges([all]).build(),
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=LEFT($O2' + sep + ' 9)="Отклонено"')
      .setFontColor('#999999').setRanges([all]).build(),
    // Без дробей и запятых в формуле: в русской локали десятичный разделитель — запятая.
    SpreadsheetApp.newConditionalFormatRule().whenFormulaSatisfied('=ABS(N($M2))*100>' + Math.round(BIG_CHANGE * 100))
      .setBackground('#f8d7da').setRanges([delta]).build()
  ]);
  sh.hideColumns(OF.RAW);
  sh.setColumnWidth(OF.ITEM, 280).setColumnWidth(OF.KEY, 260).setColumnWidth(OF.HOW, 300).setColumnWidth(OF.STATUS, 260);
  sh.setFrozenRows(1);
  return sh;
}

// Триггер раз в 10 минут: новые файлы из «Счета_входящие» → Drive → распознавание → «Цены_предложения».
function processInvoices() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  var started = Date.now();
  try {
    var sh = book().getSheetByName(SH.INVOICES);
    var data = sh.getDataRange().getValues();
    for (var r = 1; r < data.length && Date.now() - started < 4 * 60 * 1000; r++) {
      if (!data[r][4]) continue;
      // Ошибку (нет доступа, сбой Telegram) пробуем ещё раз, всего до 3 попыток.
      var st = String(data[r][7] || ''), err = st.match(/^ошибка(?: \(попытка (\d+)\))?/);
      if (st && !err) continue;
      var tries = err ? Number(err[1] || 1) : 0;
      if (tries >= 3) continue;
      var res;
      try { res = processInvoiceRow(data[r]); } catch (e) {
        console.error('счёт, строка ' + (r + 1) + ': ' + e.stack);
        res = { status: 'ошибка (попытка ' + (tries + 1) + '): ' + e.message };
      }
      sh.getRange(r + 1, 8, 1, 8).setValues([[res.status, res.fileUrl || '', res.ocrUrl || '', res.supplier || '',
        res.number || '', res.date || '', res.items == null ? '' : res.items, res.offers == null ? '' : res.offers]]);
      SpreadsheetApp.flush();
      if (res.offers) notifyPrices(res);
    }
    try { rematchOffers(); } catch (e) { console.error('rematchOffers: ' + e.stack); }
    try { ensureTemplateCoefficients(); } catch (e) { console.error('ensureTemplateCoefficients: ' + e.stack); }
  } finally {
    lock.releaseLock();
  }
}

// Запуск из редактора, если счёт упал с «нет разрешения»: заново спрашивает все доступы (Google показывает окно,
// где нужно отметить все галочки, включая Google Диск), сбрасывает ошибки и «нет позиций» в «Счета_входящие»
// (например, после доработки разбора) и разбирает счета заново.
function retryInvoices() {
  ScriptApp.requireAllScopes(ScriptApp.AuthMode.FULL);
  DriveApp.getRootFolder(); // проверка доступа к Диску: без него здесь будет ошибка
  var sh = book().getSheetByName(SH.INVOICES);
  var col = sh.getRange(1, 8, Math.max(sh.getLastRow(), 1), 1).getValues();
  var n = 0;
  for (var r = 1; r < col.length; r++) {
    if (/^(ошибка|нет позиций)/.test(String(col[r][0]))) { sh.getRange(r + 1, 8).clearContent(); n++; }
  }
  SpreadsheetApp.flush();
  console.log('Сброшено ошибок: ' + n);
  processInvoices();
}

// Строка «Счета_входящие» → {status, fileUrl, ocrUrl, supplier, number, date, items, offers}.
function processInvoiceRow(row) {
  var name = String(row[2] || 'файл'), mime = String(row[3] || ''), fileId = String(row[4]);
  var kind = /pdf/i.test(mime) || /\.pdf$/i.test(name) ? 'doc'
    : /^image\//i.test(mime) ? 'doc'
    : /spreadsheet|excel/i.test(mime) || /\.xlsx?$/i.test(name) ? 'sheet' : '';
  if (!kind) return { status: 'пропущено: не счёт (' + (mime || name) + ')' };

  var folders = invoiceFolders();
  var file, saved = String(row[8] || '').match(/\/d\/([\w-]{20,})/); // повторная попытка: файл уже на Диске
  if (fileId.indexOf('drive:') === 0) file = DriveApp.getFileById(fileId.slice(6));
  else if (saved) file = DriveApp.getFileById(saved[1]);
  else {
    var blob = telegramFile(fileId).setName(dayStr(row[0]) + ' ' + name);
    file = folders.files.createFile(blob);
  }
  var conv = Drive.Files.create({
    name: 'распознано ' + file.getName(), parents: [folders.ocr.getId()],
    mimeType: kind === 'doc' ? MimeType.GOOGLE_DOCS : MimeType.GOOGLE_SHEETS
  }, file.getBlob(), { ocrLanguage: 'ru', fields: 'id' });

  var text = '', lines = [], tableRows = [];
  if (kind === 'doc') {
    var body = DocumentApp.openById(conv.id).getBody();
    text = body.getText();
    lines = text.split(/\n/);
    body.getTables().forEach(function (t) {
      for (var i = 0; i < t.getNumRows(); i++) {
        var tr = t.getRow(i), cells = [];
        for (var j = 0; j < tr.getNumCells(); j++) cells.push(tr.getCell(j).getText());
        tableRows.push(cells);
      }
    });
  } else {
    var ss = SpreadsheetApp.openById(conv.id);
    ss.getSheets().forEach(function (s) {
      s.getDataRange().getValues().forEach(function (cells) { tableRows.push(cells); });
    });
    text = tableRows.map(function (c) { return c.join(' '); }).join('\n');
  }

  var head = parseInvoiceHeader(text);
  var inv = parseInvoice(lines, tableRows);
  var res = {
    fileUrl: file.getUrl(), ocrUrl: 'https://drive.google.com/open?id=' + conv.id,
    supplier: head.supplier, number: head.number, date: head.date || dayStr(row[0]),
    items: inv.items.length, offers: 0, caption: row[5], link: row[6]
  };
  if (!inv.items.length) {
    res.status = 'нет позиций' + (inv.skipped.length ? ' (только услуги: ' + inv.skipped.length + ')' : '') +
      ' — проверьте распознанный файл';
    return res;
  }
  // На подтверждение — только то, что есть в справочнике, и металл (лист, труба, пруток); краска, газ и т. п. — мимо.
  var catalog = readPrices();
  var mats = inv.items.filter(function (it) { return isCalcMaterial(it.name, catalog); });
  res.offers = mats.length ? addOffers(res, mats) : 0;
  res.status = 'разобрано ' + nowStr() + ': позиций ' + inv.items.length +
    (mats.length < inv.items.length ? ', не из справочника (краска, газ и т. п.): ' + (inv.items.length - mats.length) : '') +
    (inv.skipped.length ? ', услуги/доставка пропущены: ' + inv.skipped.length : '');
  return res;
}

// Ячейка даты из таблицы (Date или «dd.MM.yyyy HH:mm») → «dd.MM.yyyy».
function dayStr(v) {
  return v instanceof Date ? nowStr(v).slice(0, 10) : String(v || '').slice(0, 10);
}

function addOffers(inv, items) {
  var sh = book().getSheetByName(SH.OFFERS);
  var catalog = readPrices();
  var start = sh.getLastRow() + 1, sep = argSep();
  var rows = items.map(function (it, i) {
    var r = start + i;
    var m = matchMaterial(it.name, catalog);
    var calc = offerCalc(it, m.key, catalog);
    var notes = [];
    if (!it.checked) notes.push('кол-во × цена ≠ сумма — проверьте строку');
    if (it.vat) notes.push('НДС в счёте сверху — взята цена с НДС');
    if (!m.key) notes.push(m.note);
    else if (calc.price === '') notes.push('впишите новую цену вручную');
    return [inv.date, inv.supplier, inv.number, it.name, it.qty, it.unit, it.price,
      m.key, calc.unit, calc.price, calc.how, currentPriceFormula(r, sep),
      '=IF(AND(ISNUMBER(J' + r + ')' + sep + ' ISNUMBER(L' + r + ')' + sep + ' L' + r + '>0)' + sep + ' J' + r + '/L' + r + '-1' + sep + ' "")',
      '', 'ждёт решения' + (notes.length ? ': ' + notes.join('; ') : ''),
      inv.link || inv.fileUrl, JSON.stringify(it)];
  });
  sh.getRange(start, 1, rows.length, OFFER_HEAD.length).setValues(rows);
  sh.getRange(start, OF.DELTA, rows.length, 1).setNumberFormat('+0%;-0%;0%');
  sh.getRange(start, OF.NEW, rows.length, 1).setNumberFormat('#,##0.00');
  sh.getRange(start, OF.CUR, rows.length, 1).setNumberFormat('#,##0.00');
  return rows.length;
}

// Строки без материала, ждущие решения, заново сверяются со справочником: разбор доработали
// или в «Цены» добавили материал. Решённые строки и выбранный вручную материал не трогаются.
function rematchOffers() {
  var sh = book().getSheetByName(SH.OFFERS), last = sh.getLastRow();
  if (last < 2) return 0;
  var vals = sh.getRange(2, 1, last - 1, OFFER_HEAD.length).getValues(), catalog = readPrices(), n = 0;
  vals.forEach(function (v, i) {
    if (v[OF.KEY - 1] || !/^ждёт решения/.test(String(v[OF.STATUS - 1]))) return;
    var it = offerItem(v), m = matchMaterial(it.name, catalog);
    if (!m.key) return;
    var calc = offerCalc(it, m.key, catalog), notes = [];
    if (it.vat) notes.push('НДС в счёте сверху — взята цена с НДС');
    if (calc.price === '') notes.push('впишите новую цену вручную');
    sh.getRange(i + 2, OF.KEY, 1, 4).setValues([[m.key, calc.unit, calc.price, calc.how]]);
    sh.getRange(i + 2, OF.STATUS).setValue('ждёт решения' + (notes.length ? ': ' + notes.join('; ') : ''));
    n++;
  });
  return n;
}

// Текущая цена — живая ссылка на «Цены»: после принятия одной строки остальные сравниваются с новой ценой.
function currentPriceFormula(r, sep) {
  var P = "'" + SH.PRICES + "'!";
  return '=IFERROR(INDEX(' + P + '$E:$E' + sep + ' MATCH(H' + r + sep + ' ' + P + '$A:$A' + sep + ' 0))' + sep + ' "")';
}

// Цена в единице калькулятора для выбранного материала.
function offerCalc(it, key, catalog) {
  var c = key && catalog.filter(function (x) { return x.key === key; })[0];
  if (!c) return { unit: '', price: '', how: '' };
  var conv = convertPrice(it, c);
  return { unit: c.unit, price: conv.price, how: conv.how };
}

// Лист «Цены» → [{row, key, section, name, unit, price, date}].
function readPrices() {
  var vals = book().getSheetByName(SH.PRICES).getDataRange().getValues();
  var out = [];
  for (var r = 1; r < vals.length; r++) {
    if (!vals[r][0]) continue;
    out.push({ row: r + 1, key: String(vals[r][0]), section: vals[r][1], name: vals[r][2], unit: vals[r][3],
      price: vals[r][4], date: vals[r][5] });
  }
  return out;
}

// Правка в «Цены_предложения»: выбран материал → пересчёт; решение → цена в «Цены».
function onEditOffers(e) {
  var sh = e.range.getSheet(), row = e.range.getRow(), col = e.range.getColumn();
  if (row < 2 || e.range.getNumRows() > 1 || e.range.getNumColumns() > 1) return;
  var who = (e.user && e.user.getEmail && e.user.getEmail()) || '';
  var vals = sh.getRange(row, 1, 1, OFFER_HEAD.length).getValues()[0];
  var status = String(vals[OF.STATUS - 1]);
  var done = /^(Принято|Отклонено)/.test(status);

  if (col === OF.KEY && !done) {
    var it = offerItem(vals);
    var calc = offerCalc(it, String(e.value || ''), readPrices());
    sh.getRange(row, OF.CUNIT, 1, 3).setValues([[calc.unit, calc.price, calc.how]]);
    sh.getRange(row, OF.STATUS).setValue('ждёт решения' + (!e.value ? ': выберите материал'
      : calc.price === '' ? ': впишите новую цену вручную' : ''));
  } else if (col === OF.NEW && !done) {
    sh.getRange(row, OF.HOW).setValue(String(vals[OF.HOW - 1]).replace(/ · вручную.*$/, '') +
      ' · вручную' + (e.oldValue !== undefined ? ' (было ' + e.oldValue + ')' : ''));
  } else if (col === OF.DECISION) {
    if (done) { // решение уже применено — не меняем
      sh.getRange(row, OF.DECISION).setValue(e.oldValue === undefined ? '' : e.oldValue);
      return;
    }
    if (e.value === DECISION.REJECT) {
      sh.getRange(row, OF.STATUS).setValue('Отклонено ' + nowStr() + (who ? ' (' + who + ')' : ''));
    } else if (e.value === DECISION.ACCEPT) {
      var res = acceptOffer(vals, who);
      if (res.error) {
        sh.getRange(row, OF.DECISION).setValue('');
        sh.getRange(row, OF.STATUS).setValue('не принято: ' + res.error);
      } else {
        // Строку фиксируем значениями: формула текущей цены после принятия показывала бы уже новую.
        sh.getRange(row, OF.CUR, 1, 2).setValues([[res.old, res.old > 0 ? Number(vals[OF.NEW - 1]) / res.old - 1 : '']]);
        sh.getRange(row, OF.STATUS).setValue('Принято ' + nowStr() + (who ? ' (' + who + ')' : '') +
          ', было ' + (res.old === '' ? 'пусто' : fmt(res.old)));
      }
    }
  }
}

function offerItem(vals) {
  try { return JSON.parse(vals[OF.RAW - 1]); } catch (e) {
    return { name: vals[OF.ITEM - 1], qty: vals[OF.QTY - 1], unit: vals[OF.UNIT - 1], price: vals[OF.PRICE - 1] };
  }
}

// Пишет цену в «Цены» и «Цены_история». Возвращает {error} или {old: прежняя цена}.
function acceptOffer(vals, who) {
  var key = String(vals[OF.KEY - 1] || ''), price = Number(vals[OF.NEW - 1]);
  if (!key) return { error: 'не выбран материал' };
  if (!(price > 0)) return { error: 'нет новой цены' };
  var c = readPrices().filter(function (x) { return x.key === key; })[0];
  if (!c) return { error: 'материала нет в «Цены»' };
  var invDate = parseRuDate(vals[OF.DATE - 1]) || new Date();
  if (c.date instanceof Date && c.date.getTime() > invDate.getTime()) {
    return { error: 'в «Цены» цена от более свежей даты (' + nowStr(c.date).slice(0, 10) + ')' };
  }
  var src = [vals[OF.SUPPLIER - 1], vals[OF.NUMBER - 1] ? 'счёт № ' + vals[OF.NUMBER - 1] : 'счёт',
    'от ' + nowStr(invDate).slice(0, 10)].filter(Boolean).join(', ');
  book().getSheetByName(SH.PRICES).getRange(c.row, 5, 1, 4).setValues([[price, invDate, src,
    'принято ' + nowStr() + (who ? ' ' + who : '') + '; было ' + (c.price === '' ? 'пусто' : c.price)]]);
  appendRow(SH.PRICE_LOG, [nowStr(), key, c.price, price, nowStr(invDate).slice(0, 10), vals[OF.SUPPLIER - 1],
    vals[OF.NUMBER - 1], who]);
  return { old: c.price };
}

// Ручная правка цены прямо в «Цены» — тоже в историю, чтобы было видно, откуда цена.
function onEditPrices(e) {
  if (e.range.getColumn() !== 5 || e.range.getNumColumns() > 1 || e.range.getRow() < 2) return;
  var who = (e.user && e.user.getEmail && e.user.getEmail()) || '';
  var single = e.range.getNumRows() === 1;
  appendRow(SH.PRICE_LOG, [nowStr(), single ? e.range.offset(0, -4).getValue() : '(несколько строк)',
    single && e.oldValue !== undefined ? e.oldValue : '', single && e.value !== undefined ? e.value : '',
    '', 'вручную в «Цены»', '', who]);
}

function parseRuDate(v) {
  if (v instanceof Date) return v;
  var m = String(v || '').match(/^(\d{2})\.(\d{2})\.(\d{4})/);
  return m ? new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1])) : null;
}

function notifyPrices(res) {
  var user = String(readSettings().tg_prices || '').replace(/^@/, '');
  var chat = user && prop('tgchat:' + user.toLowerCase());
  if (!chat) return;
  var sh = book().getSheetByName(SH.OFFERS);
  tg('sendMessage', { chat_id: chat, parse_mode: 'HTML', disable_web_page_preview: true,
    text: '📄 Счёт' + (res.number ? ' № ' + escapeHtml(res.number) : '') + (res.supplier ? ' от ' + escapeHtml(res.supplier) : '') +
      ': цен на подтверждение — ' + res.offers + '.\n' + sheetUrl(sh) });
}

function telegramFile(fileId) {
  var f = tg('getFile', { file_id: fileId });
  var r = UrlFetchApp.fetch('https://api.telegram.org/file/bot' + prop('TG_TOKEN') + '/' + f.file_path, { muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw new Error('Telegram: файл не скачался (' + r.getResponseCode() + ')');
  return r.getBlob();
}

// Папки Drive: «Счета из чата Админ» (рядом с таблицей) и в ней «распознано».
function invoiceFolders() {
  var id = readSettings().invoices_folder, root;
  if (id) root = DriveApp.getFolderById(String(id));
  else {
    var parents = DriveApp.getFileById(book().getId()).getParents();
    var base = parents.hasNext() ? parents.next() : DriveApp.getRootFolder();
    root = base.createFolder('Счета из чата Админ');
    writeSetting('invoices_folder', root.getId());
  }
  var sub = root.getFoldersByName('распознано');
  return { files: root, ocr: sub.hasNext() ? sub.next() : root.createFolder('распознано') };
}

// Разово: старые счета (экспорт чата из Telegram Desktop, загруженный на Drive) → «Счета_входящие».
// ID папки — в «Настройки» → invoices_import. Повторный запуск не дублирует файлы.
function importInvoiceFolder() {
  var id = readSettings().invoices_import;
  if (!id) return safeAlert('Укажите ID папки в «Настройки» → invoices_import.');
  var sh = book().getSheetByName(SH.INVOICES);
  // Уже известные файлы: по ID и по имени — бот сохраняет счёт из чата как «дата имя», в экспорте он же как «имя».
  var have = {}, names = [];
  sh.getDataRange().getValues().forEach(function (r) {
    have[String(r[4])] = true;
    var d = String(r[8] || '').match(/\/d\/([\w-]{20,})/);
    if (d) have['drive:' + d[1]] = true;
    if (r[2] && String(r[1]) !== 'импорт') names.push(String(r[2]));
  });
  var known = function (name) {
    return names.some(function (k) { return name === k || name.slice(-k.length - 1) === ' ' + k; });
  };
  var rows = [], skipped = 0, stack = [DriveApp.getFolderById(String(id))];
  while (stack.length) {
    var folder = stack.pop();
    var subs = folder.getFolders();
    while (subs.hasNext()) { var sub = subs.next(); if (sub.getName() !== 'распознано') stack.push(sub); }
    var files = folder.getFiles();
    while (files.hasNext()) {
      var f = files.next(), name = f.getName();
      if (have['drive:' + f.getId()]) continue;
      // Платёжки и миниатюры из экспорта Telegram — не счета; копии счетов, которые бот уже разобрал, — тоже мимо.
      if (/_thumb\.jpe?g$|плат[её]жн|поручени/i.test(name) || known(name)) { skipped++; continue; }
      rows.push([nowStr(f.getDateCreated()), 'импорт', name, f.getMimeType(), 'drive:' + f.getId(), '', '', '']);
    }
  }
  if (rows.length) sh.getRange(sh.getLastRow() + 1, 1, rows.length, 8).setValues(rows);
  safeAlert('Добавлено файлов: ' + rows.length + ', пропущено (платёжки, миниатюры, уже разобранные): ' + skipped +
    '. Разбор идёт триггером раз в 10 минут (или запустите processInvoices).');
}

function logInvoice(msg) {
  var f = msg.document || (msg.photo && msg.photo[msg.photo.length - 1]);
  appendRow(SH.INVOICES, [
    nowStr(new Date(msg.date * 1000)), personName(msg.from),
    msg.document ? msg.document.file_name : 'фото', msg.document ? msg.document.mime_type : 'image/jpeg',
    f.file_id, msg.caption || '', telegramMessageLink(msg.chat.id, msg.message_id), ''
  ]);
}

// Личное сообщение, если человек написал боту /start; иначе — в чат «Расчеты» с упоминанием.
// skipChatId: не дублировать в чат, куда бот уже ответил.
function notify(roleKey, html, skipChatId) {
  var s = readSettings();
  var user = String(s[roleKey] || '').replace(/^@/, '');
  var personal = user && prop('tgchat:' + user.toLowerCase());
  if (personal) return tg('sendMessage', { chat_id: personal, text: html, parse_mode: 'HTML', disable_web_page_preview: true });
  if (!s.chat_raschety || String(s.chat_raschety) === String(skipChatId)) return;
  tg('sendMessage', { chat_id: s.chat_raschety, text: (user ? '@' + user + ' ' : '') + html,
    parse_mode: 'HTML', disable_web_page_preview: true });
}

function reply(msg, text) {
  tg('sendMessage', { chat_id: msg.chat.id, text: text, reply_to_message_id: msg.message_id,
    disable_web_page_preview: true });
}

function tg(method, payload) {
  var token = prop('TG_TOKEN');
  if (!token) throw new Error('нет TG_TOKEN в свойствах скрипта');
  var r = UrlFetchApp.fetch('https://api.telegram.org/bot' + token + '/' + method, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(payload), muteHttpExceptions: true
  });
  var j = JSON.parse(r.getContentText());
  if (!j.ok) throw new Error('Telegram ' + method + ': ' + j.description);
  return j.result;
}

function personName(from) {
  if (!from) return '';
  return [from.first_name, from.last_name].filter(Boolean).join(' ') || from.username || String(from.id);
}

// ---------- Битрикс24 (чтение сделки) ----------

// {found: true/false/null (Б24 не подключён), title, client}
function getDealInfo(id) {
  if (!prop('B24_WEBHOOK')) return { found: null, title: '', client: '' };
  var d;
  try { d = b24('crm.deal.get', { id: id }); } catch (e) {
    if (/not found/i.test(e.message)) return { found: false };
    throw e;
  }
  var client = '';
  if (Number(d.COMPANY_ID) > 0) client = b24('crm.company.get', { id: d.COMPANY_ID }).TITLE;
  else if (Number(d.CONTACT_ID) > 0) {
    var c = b24('crm.contact.get', { id: d.CONTACT_ID });
    client = [c.LAST_NAME, c.NAME].filter(Boolean).join(' ');
  }
  return { found: true, title: d.TITLE || '', client: client || d.TITLE || '' };
}

function b24(method, params) {
  var base = String(prop('B24_WEBHOOK')).replace(/\/?$/, '/');
  var r = UrlFetchApp.fetch(base + method + '.json', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(params), muteHttpExceptions: true
  });
  var j = JSON.parse(r.getContentText());
  if (j.error) throw new Error('Б24 ' + method + ': ' + (j.error_description || j.error));
  return j.result;
}

// ---------- Правки и статусы ----------

// Устанавливаемый триггер «при изменении» (работает от имени установившего; простой onEdit не может слать сообщения).
function onEditCalc(e) {
  var sh = e.range.getSheet();
  if (sh.getName() === SH.OFFERS) return onEditOffers(e);
  if (sh.getName() === SH.PRICES) return onEditPrices(e);
  if (!isCalcSheet(sh)) return;
  var a1 = e.range.getA1Notation();
  var who = (e.user && e.user.getEmail && e.user.getEmail()) || '';
  if (a1 === CELL.STATUS || a1 === 'J1:L1') return onStatusChange(sh, e.oldValue, e.value, who);

  var status = sh.getRange(CELL.STATUS).getValue();
  if (status !== STATUS.FILLED && status !== STATUS.REWORK) return;
  var row = e.range.getRow(), col = e.range.getColumn();
  var single = e.range.getNumRows() === 1 && e.range.getNumColumns() === 1;
  appendRow(SH.EDITS, [nowStr(), sh.getName(), a1, sh.getRange(row, 1).getValue(),
    col >= FIRST_ITEM_COL && col <= LAST_ITEM_COL ? sh.getRange(ITEM_ROW, col).getValue() : '',
    single ? (e.oldValue === undefined ? '' : e.oldValue) : '(несколько ячеек)',
    single ? (e.value === undefined ? '' : e.value) : '', who, status]);
}

function onStatusChange(sh, oldStatus, newStatus, who) {
  if (oldStatus === newStatus) return;
  var now = nowStr(), name = escapeHtml(sh.getName()), url = sheetUrl(sh);
  var total = calcTotal(sh);
  appendHistory(sh, newStatus + ' ' + now + (who ? ' (' + who + ')' : ''));
  var upd = { 'Статус': newStatus, 'Итого, ₽': total, 'Изделия': calcItems(sh, ITEM_ROW) };

  if (newStatus === STATUS.FILLED) {
    upd['Заполнен'] = now;
    notify('tg_manager', '✅ Расчёт «' + name + '» заполнен. Итого ' + formatRub(total) + '.\nПроверьте: ' + url);
  } else if (newStatus === STATUS.REWORK) {
    var comment = sh.getRange(CELL.COMMENT).getValue();
    notify('tg_raschetchik', '↩️ Расчёт «' + name + '» на доработку' +
      (comment ? ':\n' + escapeHtml(comment) : ' (комментарий в строке «Комментарий проверки»).') + '\n' + url);
  } else if (newStatus === STATUS.APPROVED) {
    freezePrices(sh);
    protectSheet(sh, 'Утверждено ' + now);
    upd['Утверждён'] = now;
    var text = '🟢 Расчёт «' + name + '» утверждён. Итого ' + formatRub(total) + '.\n' + url;
    notify('tg_manager', text);
    notify('tg_raschetchik', text);
  }
  updateRegistry(sh.getName(), upd);
}

// Цены из «Цены» → значения, чтобы утверждённый расчёт не менялся при обновлении цен.
function freezePrices(sh) {
  var last = findRow(sh, LABEL.PAINTED) - 1;
  var rng = sh.getRange(HEADER_ROWS + 3, 2, last - HEADER_ROWS - 2, 1);
  var formulas = rng.getFormulas(), values = rng.getValues();
  var out = formulas.map(function (f, i) { return [f[0] && f[0].indexOf(SH.PRICES) < 0 ? f[0] : values[i][0]]; });
  rng.setValues(out);
}

function protectSheet(sh, description) {
  var p = sh.protect().setDescription(description);
  p.addEditor(Session.getEffectiveUser());
  p.removeEditors(p.getEditors());
  if (p.canDomainEdit()) p.setDomainEdit(false);
}

// Названия изделий через запятую — для поиска на «Главной».
function calcItems(sh, row) {
  return sh.getRange(row, FIRST_ITEM_COL, 1, LAST_ITEM_COL - FIRST_ITEM_COL + 1).getValues()[0]
    .map(function (v) { return String(v).trim(); }).filter(Boolean).join(', ');
}

function calcTotal(sh) {
  var r = findRow(sh, LABEL.TOTAL);
  return r ? Number(sh.getRange(r, TOTAL_COL).getValue()) || 0 : 0;
}

function appendHistory(sh, line) {
  var c = sh.getRange(CELL.HISTORY);
  var v = String(c.getValue() || '');
  c.setValue(v ? v + ' · ' + line : line);
}

// ---------- Служебное ----------

function isCalcSheet(sh) {
  return !!sh && sh.getName() !== SH.TEMPLATE && sh.getRange('A1').getValue() === 'Расчёт' &&
    sh.getRange('A2').getValue() === 'ТЗ';
}

function updateRegistry(sheetName, fields) {
  var sh = book().getSheetByName(SH.REGISTRY);
  var data = sh.getDataRange().getValues();
  for (var r = 1; r < data.length; r++) {
    if (String(data[r][2]) !== String(sheetName)) continue; // лист «765» Google хранит в реестре как число
    Object.keys(fields).forEach(function (k) {
      var c = REGISTRY_HEAD.indexOf(k);
      if (c >= 0) sh.getRange(r + 1, c + 1).setValue(fields[k]);
    });
    return;
  }
}

function appendRow(sheetName, row) {
  book().getSheetByName(sheetName).appendRow(row);
}

function findRow(sheet, label) {
  if (sheet.getLastRow() < 1) return 0; // пустой лист
  var vals = sheet.getRange(1, 1, sheet.getLastRow(), 1).getValues();
  for (var i = 0; i < vals.length; i++) if (String(vals[i][0]).trim() === label) return i + 1;
  return 0;
}

function readSettings() {
  var sh = book().getSheetByName(SH.SETTINGS);
  var out = {};
  if (!sh) return out;
  sh.getDataRange().getValues().slice(1).forEach(function (r) { if (r[0]) out[r[0]] = r[1]; });
  return out;
}

function writeSetting(key, value) {
  var sh = book().getSheetByName(SH.SETTINGS);
  var data = sh.getDataRange().getValues();
  for (var r = 1; r < data.length; r++) if (data[r][0] === key) return sh.getRange(r + 1, 2).setValue(value);
  sh.appendRow([key, value, '']);
}

function prop(k) { return PropertiesService.getScriptProperties().getProperty(k); }

function sheetUrl(sh) { return book().getUrl() + '#gid=' + sh.getSheetId(); }

function dealUrl(id) {
  return 'https://' + (readSettings().b24_portal || '90gradusov.bitrix24.ru') + '/crm/deal/details/' + id + '/';
}

function nowStr(d) {
  return Utilities.formatDate(d || new Date(), Session.getScriptTimeZone(), 'dd.MM.yyyy HH:mm');
}

function colLetter(c) {
  var s = '';
  while (c > 0) { var m = (c - 1) % 26; s = String.fromCharCode(65 + m) + s; c = Math.floor((c - 1) / 26); }
  return s;
}

function safeAlert(msg) {
  try { SpreadsheetApp.getUi().alert(msg); } catch (e) { console.log(msg); }
}

// ---------- Новый файл: перенос, архив, «Главная» ----------

// Разовый перенос (16.10.2026): из старого файла — служебные листы и шаблон, старые расчёты остаются в архиве,
// в «Реестр» попадает их список со ссылками. Старый файл не меняется.
function migrateToNewFile() {
  var old = SpreadsheetApp.openById(OLD_ID), dst = book(), arc = SpreadsheetApp.openById(ARCHIVE_ID);
  var initial = dst.getSheets();
  [SH.PRICES, SH.SETTINGS, SH.TEMPLATE, SH.EDITS, SH.INVOICES].forEach(function (n) {
    if (dst.getSheetByName(n)) return;
    old.getSheetByName(n).copyTo(dst).setName(n);
  });
  setPriceFormulas(dst.getSheetByName(SH.TEMPLATE), HEADER_ROWS); // ссылки на «Цены» — уже внутри нового файла
  var reg = ensureSheet(dst, SH.REGISTRY, REGISTRY_HEAD);
  if (reg.getLastRow() < 2) {
    var rows = archiveIndex(arc);
    if (rows.length) reg.getRange(2, 1, rows.length, REGISTRY_HEAD.length).setValues(rows);
  }
  buildHome(dst);
  initial.forEach(function (sh) { if (dst.getSheets().length > 1 && sh.getLastRow() === 0) dst.deleteSheet(sh); });
  dst.setActiveSheet(dst.getSheetByName(SH.REGISTRY)); dst.moveActiveSheet(2);
  dst.setActiveSheet(dst.getSheetByName(SH.HOME)); dst.moveActiveSheet(1);
  console.log('Перенос готов: в реестре ' + (reg.getLastRow() - 1) + ' строк архива.');
}

// Строки «Реестра» для старых расчётов из архива: название листа, итог, изделия, ссылка.
function archiveIndex(arc) {
  var skip = /^(шаблон|шаблон_v2|Цены|Настройки|Реестр|Журнал_правок|Счета_входящие|Главная|765|715_.*)$/i;
  var url = arc.getUrl();
  var out = [];
  arc.getSheets().forEach(function (sh) {
    var name = sh.getName();
    if (skip.test(name.trim())) return;
    var r = findRow(sh, LABEL.TOTAL);
    if (!r) return; // не расчёт («Лист2» и т. п.)
    var total = Number(sh.getRange(r, TOTAL_COL).getValue()) || 0;
    out.push(['', name.trim(), name.trim(), url + '#gid=' + sh.getSheetId(), STATUS_ARCHIVE, '', '', '', '',
      total, '', calcItems(sh, 2)]);
  });
  return out;
}

// «Главная»: поиск по реестру (номер сделки, клиент, лист, изделие) и сводка по статусам.
function buildHome(ss) {
  var h = ss.getSheetByName(SH.HOME) || ss.insertSheet(SH.HOME, 0);
  h.clear();
  var sep = argSep(), R = "'" + SH.REGISTRY + "'!";
  var hay = R + 'A2:A&" "&' + R + 'B2:B&" "&' + R + 'C2:C&" "&' + R + 'L2:L';
  h.getRange('A1').setValue('Расчёты 90 градусов').setFontSize(14).setFontWeight('bold');
  h.getRange('A3').setValue('Поиск').setFontWeight('bold');
  h.getRange('B3').setBackground('#fff8e1').setBorder(true, true, true, true, false, false);
  h.getRange('C3').setValue('номер сделки, клиент или изделие; пусто — все текущие расчёты').setFontColor('#777777');
  var cnt = function (st) { return '=COUNTIF(' + R + 'E2:E' + sep + ' "' + st + '")'; };
  h.getRange('A5:B9').setValues([
    ['Черновик', ''], ['Заполнено', ''], ['На доработку', ''], ['Утверждено', ''], ['Архив', '']]);
  ['Черновик', 'Заполнено', 'На доработку', 'Утверждено', STATUS_ARCHIVE].forEach(function (st, i) {
    h.getRange(5 + i, 2).setFormula(cnt(st));
  });
  h.getRange('A11:L11').setValues([REGISTRY_HEAD]).setFontWeight('bold').setBackground('#e3f2fd');
  h.getRange('A12').setFormula('=IF($B$3=""' + sep +
    ' IFERROR(FILTER(' + R + 'A2:L' + sep + ' ' + R + 'E2:E<>"' + STATUS_ARCHIVE + '"' + sep + ' ' + R + 'C2:C<>"")' + sep + ' "Текущих расчётов нет")' + sep +
    ' IFERROR(FILTER(' + R + 'A2:L' + sep + ' ISNUMBER(SEARCH($B$3' + sep + ' ' + hay + ')))' + sep + ' "Ничего не найдено"))');
  h.setFrozenRows(11);
  h.setColumnWidth(1, 90).setColumnWidth(2, 200).setColumnWidth(3, 220).setColumnWidth(4, 120).setColumnWidth(12, 300);
  h.getRange('J12:J').setNumberFormat('#,##0');
  return h;
}
