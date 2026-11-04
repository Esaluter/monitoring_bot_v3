/**
 * Отдельный API Monitoring Bot V3.
 *
 * Script Properties:
 *   V3_SPREADSHEET_ID — ID существующей таблицы с листами ОБЪЕКТЫ/АНАЛИЗ/Настройки
 *   V3_API_KEY        — отдельный длинный случайный ключ V3
 *
 * Скрипт только читает ОБЪЕКТЫ и АНАЛИЗ. Запись разрешена исключительно в
 * Настройки!A2:A и B2:B через независимые операции получателей и расписания.
 */

const OBJECTS_SHEET = 'ОБЪЕКТЫ';
const ANALYSIS_SHEET = 'АНАЛИЗ';
const SETTINGS_SHEET = 'Настройки';
const SETTINGS_HEADER = 'Получатели письма';
const SCHEDULE_HEADER = 'Время отправки';
const YEKATERINBURG_OFFSET_MINUTES = 5 * 60;

const REQUIRED_OBJECT_HEADERS = [
  'ID анализа', 'Номер объекта', 'Модель', 'Название объекта', 'Тип объекта',
  'Организация', 'Регион', 'Населённый пункт', 'Адрес / ориентир',
  'Кадастровый номер', 'Повреждения', 'Подтверждающий фрагмент',
  'Примечание ИИ', 'Дата и время новости', 'Кадастровый квартал',
  'Все кадастровые', 'Джемини'
];

const REQUIRED_ANALYSIS_HEADERS = [
  'ID анализа', 'Ссылка на новость', 'Статус анализа', 'Тип происшествия',
  'Краткое описание'
];


function doPost(e) {
  try {
    const request = parseRequest_(e);
    const properties = PropertiesService.getScriptProperties();
    authorize_(request, properties);

    if (request.action === 'read_monitoring_data') {
      return jsonResponse_(readMonitoringData_(properties, request));
    }
    if (request.action === 'read_recipients') {
      return jsonResponse_(readRecipients_(properties));
    }
    if (request.action === 'add_recipient') {
      return jsonResponse_(addRecipient_(properties, request.email));
    }
    if (request.action === 'delete_recipient') {
      return jsonResponse_(deleteRecipient_(properties, request.email));
    }
    if (request.action === 'read_schedule_times') {
      return jsonResponse_(readScheduleTimes_(properties));
    }
    if (request.action === 'add_schedule_time') {
      return jsonResponse_(addScheduleTime_(properties, request.time));
    }
    if (request.action === 'delete_schedule_time') {
      return jsonResponse_(deleteScheduleTime_(properties, request.time));
    }
    throw new Error('unknown_action');
  } catch (error) {
    return jsonResponse_({ok: false, error: publicError_(error)});
  }
}


function parseRequest_(e) {
  if (!e || !e.postData || !e.postData.contents) throw new Error('missing_request');
  let request;
  try {
    request = JSON.parse(e.postData.contents);
  } catch (error) {
    throw new Error('invalid_json');
  }
  if (!request || request.version !== 1 || typeof request.action !== 'string') {
    throw new Error('invalid_request');
  }
  return request;
}


function authorize_(request, properties) {
  const expected = properties.getProperty('V3_API_KEY');
  if (!expected) throw new Error('missing_v3_api_key');
  if (typeof request.api_key !== 'string' || request.api_key !== expected) {
    throw new Error('unauthorized');
  }
}


function spreadsheet_(properties) {
  const id = properties.getProperty('V3_SPREADSHEET_ID');
  if (!id) throw new Error('missing_v3_spreadsheet_id');
  return SpreadsheetApp.openById(id);
}


function sheet_(spreadsheet, name) {
  const sheet = spreadsheet.getSheetByName(name);
  if (!sheet) throw new Error('required_sheet_missing_' + name);
  return sheet;
}


function headerMap_(sheet, required) {
  if (sheet.getLastRow() < 1) throw new Error('missing_headers_' + sheet.getName());
  const lastColumn = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastColumn).getDisplayValues()[0];
  const mapping = {};
  headers.forEach(function(header, index) {
    const value = String(header || '').trim();
    if (value && mapping[value] === undefined) mapping[value] = index;
  });
  required.forEach(function(header) {
    if (mapping[header] === undefined) {
      throw new Error('missing_header_' + sheet.getName() + '_' + header);
    }
  });
  return {headers: headers, mapping: mapping};
}


function readMonitoringData_(properties, request) {
  const start = parseBoundary_(request.start, 'invalid_start');
  const end = parseBoundary_(request.end, 'invalid_end');
  if (start.getTime() >= end.getTime()) throw new Error('invalid_interval');

  const spreadsheet = spreadsheet_(properties);
  const objectsSheet = sheet_(spreadsheet, OBJECTS_SHEET);
  const analysisSheet = sheet_(spreadsheet, ANALYSIS_SHEET);
  const objectContract = headerMap_(objectsSheet, REQUIRED_OBJECT_HEADERS);
  const analysisContract = headerMap_(analysisSheet, REQUIRED_ANALYSIS_HEADERS);
  const analysisById = readAnalysisIndex_(analysisSheet, analysisContract);

  const lastRow = objectsSheet.getLastRow();
  if (lastRow < 2) {
    return {ok: true, records: [], matched_rows: 0, invalid_date_rows: 0};
  }

  const rawRows = objectsSheet
    .getRange(2, 1, lastRow - 1, objectsSheet.getLastColumn())
    .getValues();
  const displayRows = objectsSheet
    .getRange(2, 1, lastRow - 1, objectsSheet.getLastColumn())
    .getDisplayValues();
  const m = objectContract.mapping;
  const records = [];
  let invalidDateRows = 0;

  rawRows.forEach(function(rawRow, index) {
    const displayRow = displayRows[index];
    const rawDate = rawRow[m['Дата и время новости']];
    const displayDate = displayRow[m['Дата и время новости']];
    const newsDate = parseSheetDate_(rawDate, displayDate);
    if (!newsDate) {
      if (rawDate !== '' || displayDate !== '') invalidDateRows += 1;
      return;
    }
    if (newsDate.getTime() < start.getTime() || newsDate.getTime() > end.getTime()) {
      return;
    }

    const analysisId = cell_(displayRow[m['ID анализа']]);
    const allCadastral = cell_(displayRow[m['Все кадастровые']]);
    records.push({
      analysis_id: analysisId,
      object_number: cell_(displayRow[m['Номер объекта']]),
      model: cell_(displayRow[m['Модель']]),
      name: cell_(displayRow[m['Название объекта']]),
      object_type: cell_(displayRow[m['Тип объекта']]),
      organization: cell_(displayRow[m['Организация']]),
      region: cell_(displayRow[m['Регион']]),
      locality: cell_(displayRow[m['Населённый пункт']]),
      address: cell_(displayRow[m['Адрес / ориентир']]),
      cadastral_number: cell_(displayRow[m['Кадастровый номер']]),
      damage: cell_(displayRow[m['Повреждения']]),
      evidence_fragment: cell_(displayRow[m['Подтверждающий фрагмент']]),
      ai_note: cell_(displayRow[m['Примечание ИИ']]),
      news_datetime: newsDate.toISOString(),
      cadastral_quarter: cell_(displayRow[m['Кадастровый квартал']]),
      all_cadastral_numbers: allCadastral
        ? allCadastral.split(/[\n;]+/).map(function(value) { return value.trim(); })
            .filter(function(value) { return value !== ''; })
        : [],
      gemini: cell_(displayRow[m['Джемини']]),
      analysis: analysisById[analysisId] || null
    });
  });

  return {
    ok: true,
    records: records,
    matched_rows: records.length,
    invalid_date_rows: invalidDateRows
  };
}


function readAnalysisIndex_(sheet, contract) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {};
  const rows = sheet
    .getRange(2, 1, lastRow - 1, sheet.getLastColumn())
    .getDisplayValues();
  const m = contract.mapping;
  const result = {};
  rows.forEach(function(row) {
    const id = cell_(row[m['ID анализа']]);
    if (!id) return;
    if (result[id]) throw new Error('duplicate_analysis_id');
    result[id] = {
      analysis_id: id,
      source_url: cell_(row[m['Ссылка на новость']]),
      status: cell_(row[m['Статус анализа']]),
      incident_type: cell_(row[m['Тип происшествия']]),
      summary: cell_(row[m['Краткое описание']])
    };
  });
  return result;
}


function parseBoundary_(value, errorName) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(errorName);
  const parsed = new Date(value);
  if (isNaN(parsed.getTime())) throw new Error(errorName);
  return parsed;
}


function parseSheetDate_(rawValue, displayValue) {
  if (Object.prototype.toString.call(rawValue) === '[object Date]') {
    return isNaN(rawValue.getTime()) ? null : rawValue;
  }
  if (typeof rawValue === 'number' && isFinite(rawValue)) {
    return new Date(
      (rawValue - 25569) * 86400000
      - YEKATERINBURG_OFFSET_MINUTES * 60 * 1000
    );
  }
  const text = String(displayValue || rawValue || '').trim();
  if (!text) return null;
  if (/^\d+(?:[.,]\d+)?$/.test(text)) {
    return new Date(
      (Number(text.replace(',', '.')) - 25569) * 86400000
      - YEKATERINBURG_OFFSET_MINUTES * 60 * 1000
    );
  }
  const match = text.match(
    /^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?$/
  );
  if (match) {
    const utcMilliseconds = Date.UTC(
      Number(match[3]), Number(match[2]) - 1, Number(match[1]),
      Number(match[4]), Number(match[5]), Number(match[6] || 0)
    ) - YEKATERINBURG_OFFSET_MINUTES * 60 * 1000;
    const parsed = new Date(utcMilliseconds);
    return isNaN(parsed.getTime()) ? null : parsed;
  }
  const iso = new Date(text);
  return isNaN(iso.getTime()) ? null : iso;
}


function settingsSheet_(properties) {
  const sheet = sheet_(spreadsheet_(properties), SETTINGS_SHEET);
  const header = String(sheet.getRange(1, 1).getDisplayValue() || '').trim();
  if (header !== SETTINGS_HEADER) throw new Error('invalid_settings_header');
  return sheet;
}


function scheduleSheet_(properties) {
  const sheet = settingsSheet_(properties);
  const header = String(sheet.getRange(1, 2).getDisplayValue() || '').trim();
  if (header !== SCHEDULE_HEADER) throw new Error('invalid_schedule_header');
  return sheet;
}


function readRecipients_(properties) {
  const sheet = settingsSheet_(properties);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {ok: true, recipients: []};
  const values = sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues();
  const recipients = [];
  const seen = {};
  values.forEach(function(row) {
    const email = normalizeEmail_(row[0]);
    if (email && !seen[email]) {
      recipients.push(email);
      seen[email] = true;
    }
  });
  return {ok: true, recipients: recipients};
}


function addRecipient_(properties, rawEmail) {
  const email = requiredEmail_(rawEmail);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('busy');
  try {
    const sheet = settingsSheet_(properties);
    const lastRow = Math.max(sheet.getLastRow(), 1);
    const values = lastRow >= 2
      ? sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues()
      : [];
    let firstEmpty = null;
    for (let index = 0; index < values.length; index += 1) {
      const current = normalizeEmail_(values[index][0]);
      if (current === email) return {ok: true, status: 'exists'};
      if (!current && firstEmpty === null) firstEmpty = index + 2;
    }
    const targetRow = firstEmpty || Math.max(2, lastRow + 1);
    sheet.getRange(targetRow, 1).setValue(email);
    return {ok: true, status: 'added'};
  } finally {
    lock.releaseLock();
  }
}


function deleteRecipient_(properties, rawEmail) {
  const email = requiredEmail_(rawEmail);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('busy');
  try {
    const sheet = settingsSheet_(properties);
    const lastRow = sheet.getLastRow();
    if (lastRow < 2) return {ok: true, status: 'not_found'};
    const values = sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues();
    const rows = [];
    values.forEach(function(row, index) {
      if (normalizeEmail_(row[0]) === email) rows.push(index + 2);
    });
    if (!rows.length) return {ok: true, status: 'not_found'};
    rows.forEach(function(rowNumber) {
      sheet.getRange(rowNumber, 1).clearContent();
    });
    return {ok: true, status: 'deleted'};
  } finally {
    lock.releaseLock();
  }
}


function readScheduleTimes_(properties) {
  const sheet = scheduleSheet_(properties);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return {ok: true, times: []};
  const range = sheet.getRange(2, 2, lastRow - 1, 1);
  const rawValues = range.getValues();
  const displayValues = range.getDisplayValues();
  const times = [];
  const seen = {};
  rawValues.forEach(function(row, index) {
    const rawValue = row[0];
    const displayValue = displayValues[index][0];
    if (isBlankTimeValue_(rawValue, displayValue)) return;
    const value = normalizeTime_(rawValue, displayValue);
    if (!value) {
      throw new Error(
        'invalid_schedule_time_row_' + (index + 2) + '_value_' +
        String(displayValue || rawValue).slice(0, 40)
      );
    }
    if (!seen[value]) {
      times.push(value);
      seen[value] = true;
    }
  });
  times.sort();
  return {ok: true, times: times};
}


function addScheduleTime_(properties, rawTime) {
  const value = requiredTime_(rawTime);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('busy');
  try {
    const sheet = scheduleSheet_(properties);
    const lastRow = Math.max(sheet.getLastRow(), 1);
    const range = lastRow >= 2 ? sheet.getRange(2, 2, lastRow - 1, 1) : null;
    const rawValues = range ? range.getValues() : [];
    const displayValues = range ? range.getDisplayValues() : [];
    let firstEmpty = null;
    for (let index = 0; index < rawValues.length; index += 1) {
      const rawValue = rawValues[index][0];
      const displayValue = displayValues[index][0];
      if (isBlankTimeValue_(rawValue, displayValue)) {
        if (firstEmpty === null) firstEmpty = index + 2;
        continue;
      }
      const current = normalizeTime_(rawValue, displayValue);
      if (!current) {
        throw new Error('invalid_schedule_time_row_' + (index + 2) + '_value_' + String(displayValue || rawValue).slice(0, 40));
      }
      if (current === value) return {ok: true, status: 'exists'};
    }
    sheet.getRange(firstEmpty || Math.max(2, lastRow + 1), 2).setValue(value);
    return {ok: true, status: 'added'};
  } finally {
    lock.releaseLock();
  }
}


function deleteScheduleTime_(properties, rawTime) {
  const value = requiredTime_(rawTime);
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('busy');
  try {
    const sheet = scheduleSheet_(properties);
    const lastRow = sheet.getLastRow();
    if (lastRow < 2) return {ok: true, status: 'not_found'};
    const range = sheet.getRange(2, 2, lastRow - 1, 1);
    const rawValues = range.getValues();
    const displayValues = range.getDisplayValues();
    const rows = [];
    rawValues.forEach(function(row, index) {
      const rawValue = row[0];
      const displayValue = displayValues[index][0];
      if (isBlankTimeValue_(rawValue, displayValue)) return;
      const current = normalizeTime_(rawValue, displayValue);
      if (!current) {
        throw new Error('invalid_schedule_time_row_' + (index + 2) + '_value_' + String(displayValue || rawValue).slice(0, 40));
      }
      if (current === value) rows.push(index + 2);
    });
    if (!rows.length) return {ok: true, status: 'not_found'};
    rows.forEach(function(rowNumber) { sheet.getRange(rowNumber, 2).clearContent(); });
    return {ok: true, status: 'deleted'};
  } finally {
    lock.releaseLock();
  }
}


function requiredTime_(value) {
  const normalized = normalizeTime_(value, value);
  if (!normalized) throw new Error('invalid_schedule_time');
  return normalized;
}


function normalizeTime_(rawValue, displayValue) {
  const text = String(displayValue === undefined ? rawValue : displayValue || '').trim();
  const match = text.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if (match && Number(match[1]) <= 23 && Number(match[2]) <= 59 && Number(match[3] || 0) <= 59) {
    return String(Number(match[1])).padStart(2, '0') + ':' + match[2];
  }
  if (Object.prototype.toString.call(rawValue) === '[object Date]' && !isNaN(rawValue.getTime())) {
    return Utilities.formatDate(rawValue, 'Asia/Yekaterinburg', 'HH:mm');
  }
  if (typeof rawValue === 'number' && isFinite(rawValue) && rawValue >= 0 && rawValue < 1) {
    const totalMinutes = Math.round(rawValue * 24 * 60) % (24 * 60);
    return String(Math.floor(totalMinutes / 60)).padStart(2, '0') + ':' +
      String(totalMinutes % 60).padStart(2, '0');
  }
  return '';
}


function isBlankTimeValue_(rawValue, displayValue) {
  return (rawValue === '' || rawValue === null) && String(displayValue || '').trim() === '';
}


function requiredEmail_(value) {
  const email = normalizeEmail_(value);
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    throw new Error('invalid_email');
  }
  return email;
}


function normalizeEmail_(value) {
  return String(value || '').trim().toLowerCase();
}


function cell_(value) {
  return String(value === null || value === undefined ? '' : value).trim();
}


function publicError_(error) {
  const message = String(error && error.message ? error.message : error);
  const allowed = [
    'missing_request', 'invalid_json', 'invalid_request', 'unknown_action',
    'missing_v3_api_key', 'unauthorized', 'missing_v3_spreadsheet_id',
    'invalid_start', 'invalid_end', 'invalid_interval', 'duplicate_analysis_id',
    'invalid_settings_header', 'invalid_schedule_header', 'invalid_email',
    'invalid_schedule_time', 'busy'
  ];
  if (allowed.indexOf(message) !== -1) return message;
  if (/^(required_sheet_missing_|missing_headers_|missing_header_)/.test(message)) {
    return message;
  }
  if (/^invalid_schedule_time_row_\d+_value_/.test(message)) return message;
  return 'internal_error';
}


function jsonResponse_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

