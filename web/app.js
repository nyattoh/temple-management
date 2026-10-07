'use strict';

function shiftDate(value, days) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + Number(days));
  return date.toISOString().slice(0, 10);
}
function monthDays(month) {
  const [year, number] = month.split('-').map(Number);
  return new Date(Date.UTC(year, number, 0)).getUTCDate();
}
function kanjiNumber(value) {
  const digits = '〇一二三四五六七八九';
  const number = Number(value);
  if (number < 10) return digits[number];
  if (number < 100) return `${number < 20 ? '' : digits[Math.floor(number / 10)]}十${number % 10 ? digits[number % 10] : ''}`;
  return String(number).replace(/\d/g, digit => digits[Number(digit)]);
}
function printDate(value) {
  if (!value) return '未登録';
  const [year, month, day] = value.split('-');
  return `${year.replace(/\d/g, digit => '〇一二三四五六七八九'[Number(digit)])}年${kanjiNumber(month)}月${kanjiNumber(day)}日`;
}
function normalisePostal(value) {
  const text = value.normalize('NFKC').trim().replace(/^〒\s*/, '');
  return /^[0-9]{3}-?[0-9]{4}$/.test(text) ? text.replace('-', '') : null;
}
function parseRokuyoCsv(text) {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  if (lines.shift()?.trim() !== 'date,rokuyo') throw new Error('CSVの先頭行は date,rokuyo としてください。');
  const labels = ['先勝', '友引', '先負', '仏滅', '大安', '赤口'];
  const seen = new Set();
  const rows = [];
  lines.forEach((line, index) => {
    if (!line.trim()) return;
    const fields = line.split(',').map(field => field.trim());
    const [date, label] = fields;
    if (fields.length !== 2 || !/^\d{4}-\d{2}-\d{2}$/.test(date) || date.startsWith('0000-') || !labels.includes(label)) throw new Error(`CSV ${index + 2}行目の日付または六曜が不正です。`);
    let valid = false;
    try { valid = shiftDate(date, 0) === date; } catch { /* Invalid date. */ }
    if (!valid || seen.has(date)) throw new Error(`CSV ${index + 2}行目の日付が不正または重複しています。`);
    seen.add(date);
    rows.push({date, label});
    if (rows.length > 370) throw new Error('六曜CSVは1回につき370行まで取り込めます。');
  });
  return rows;
}

// Native JavaScript checks need no browser or personal data: node web/app.js.
if (typeof document === 'undefined') {
  const check = (condition) => { if (!condition) throw new Error('Self-check failed'); };
  check(shiftDate('2024-02-27', 6) === '2024-03-04');
  check(shiftDate('2026-12-31', 1) === '2027-01-01');
  check(monthDays('2024-02') === 29 && monthDays('2026-02') === 28);
  check(printDate('2024-03-05') === '二〇二四年三月五日');
  check(printDate('2026-12-31') === '二〇二六年十二月三十一日');
  check(normalisePostal('０６０－０００１') === '0600001');
  check(normalisePostal('12345678') === null);
  check(parseRokuyoCsv('\uFEFFdate,rokuyo\r\n2026-01-01,大安\r\n')[0].label === '大安');
  let rejected = false;
  try { parseRokuyoCsv('date,rokuyo\n2026-02-30,大安'); } catch { rejected = true; }
  check(rejected);
  try { parseRokuyoCsv('date,rokuyo\n2026-01-01,大安\n\n2026-01-01,大安'); throw new Error('Expected rejection'); }
  catch (error) { check(error.message.includes('4行目')); }
  try { parseRokuyoCsv('date,rokuyo\n0000-01-01,大安'); throw new Error('Expected rejection'); }
  catch (error) { check(error.message.includes('不正')); }
  const fullYear = Array.from({length: 371}, (_, i) => `${shiftDate('2024-01-01', i)},大安`);
  check(parseRokuyoCsv(`date,rokuyo\n${fullYear.slice(0, 370).join('\n')}`).length === 370);
  try { parseRokuyoCsv(`date,rokuyo\n${fullYear.join('\n')}`); throw new Error('Expected rejection'); }
  catch (error) { check(error.message.includes('370行')); }
} else {
  const $ = id => document.getElementById(id);
  let state = {households: [], deceased: [], rules: [], events: [], rokuyo: []};
  let memorials = [];
  let reportReady = false;
  let reportBusy = false;
  let loadVersion = 0;
  let loading = false;
  let postalVersion = 0, postalTimer, postalAbort, pendingPostal, lastAutoAddress;
  const titles = {household: '檀家', deceased: '故人', event: '予定'};
  const collections = {household: 'households', deceased: 'deceased', event: 'events'};
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function showError(error) { $('error').textContent = error.message || String(error); $('error').hidden = false; }
  function clearError() { $('error').hidden = true; $('error').textContent = ''; }
  async function api(path, method = 'GET', data) {
    const response = await fetch(path, {method, headers: {'Content-Type': 'application/json'}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
    let result;
    try { result = await response.json(); } catch { throw new Error('サーバーの応答を読み取れません。起動状態を確認してください。'); }
    if (!response.ok) throw new Error(result.error || `通信に失敗しました (${response.status})。`);
    return result;
  }
  function button(text, action) {
    const element = node('button', text, 'secondary'); element.type = 'button'; element.addEventListener('click', action); return element;
  }
  function cell(row, text) { const result = node('td', text); row.append(result); return result; }
  function emptyTable(target, columns) { const row = node('tr'); const td = cell(row, '該当する記録はありません。'); td.colSpan = columns; target.append(row); }
  function householdName(id) { return state.households.find(item => String(item.id) === String(id))?.name || '未関連'; }
  function matches(item, keys, query) { return keys.some(key => String(item[key] || '').toLocaleLowerCase('ja').includes(query)); }
  function edit(kind, item) {
    if (kind === 'household') resetPostal();
    const form = $(`${kind}-form`);
    for (const element of form.elements) if (element.name) element.value = item[element.name] ?? '';
    $(`${kind}-form-title`).textContent = `${titles[kind]}を編集`;
    form.querySelector('input:not([type=hidden]),select')?.focus();
  }
  function resetPostal() {
    ++postalVersion; clearTimeout(postalTimer); postalAbort?.abort();
    pendingPostal = null; lastAutoAddress = null;
    $('postal-options-label').hidden = true; $('postal-options').replaceChildren();
    $('postal-replace').hidden = true; $('postal-lookup').disabled = false;
    $('postal-help').textContent = '7桁入力すると町域まで自動入力します。番地・建物名は続けて入力してください。';
  }
  function offerAddress(candidate) {
    const address = $('household-address');
    const detail = candidate.needs_detail ? ` 対象範囲：${candidate.label}。町名・丁目を確認してください。` : '';
    if (!address.value.trim() || address.value === lastAutoAddress) {
      address.value = candidate.address; lastAutoAddress = candidate.address; pendingPostal = null;
      $('postal-replace').hidden = true;
      $('postal-help').textContent = `住所を入力しました。番地・建物名を続けて入力してください。${detail}`;
    } else {
      pendingPostal = candidate; $('postal-replace').hidden = false;
      $('postal-help').textContent = `入力済みの住所は保持しています。候補は「${candidate.address}」です。置き換える場合は下のボタンを押してください。${detail}`;
    }
  }
  async function lookupAddress() {
    const code = normalisePostal($('postal-code').value);
    if (!code) { $('postal-help').textContent = '郵便番号は7桁で入力してください。'; return; }
    postalAbort?.abort(); const request = ++postalVersion;
    postalAbort = new AbortController();
    $('postal-lookup').disabled = true; $('postal-help').textContent = '住所を調べています。';
    try {
      const response = await fetch(`/api/postal/${code}`, {signal: postalAbort.signal});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '住所を調べられませんでした。');
      if (request !== postalVersion || normalisePostal($('postal-code').value) !== code) return;
      $('postal-code').value = `${code.slice(0, 3)}-${code.slice(3)}`;
      const candidates = result.candidates;
      if (!candidates.length) { $('postal-help').textContent = '該当する町域がありません。郵便番号を確認するか、住所を手入力してください。'; return; }
      if (candidates.length === 1) offerAddress(candidates[0]);
      else {
        const select = $('postal-options'); select.replaceChildren();
        const prompt = node('option', '住所を選んでください'); prompt.value = ''; select.append(prompt);
        candidates.forEach((candidate, index) => { const option = node('option', candidate.label); option.value = String(index); select.append(option); });
        select.onchange = () => { if (select.value !== '') offerAddress(candidates[Number(select.value)]); };
        $('postal-options-label').hidden = false;
        $('postal-help').textContent = `${candidates.length}件の住所が該当します。候補から選んでください。`;
      }
    } catch (error) {
      if (request === postalVersion && error.name !== 'AbortError') $('postal-help').textContent = `${error.message} 住所は手入力できます。`;
    } finally { if (request === postalVersion) $('postal-lookup').disabled = false; }
  }
  $('postal-code').addEventListener('input', () => {
    resetPostal();
    if (normalisePostal($('postal-code').value)) postalTimer = setTimeout(lookupAddress, 250);
  });
  $('postal-lookup').addEventListener('click', () => { clearTimeout(postalTimer); lookupAddress(); });
  $('household-address').addEventListener('input', () => { lastAutoAddress = null; });
  $('postal-replace').addEventListener('click', () => {
    if (!pendingPostal) return;
    const candidate = pendingPostal;
    $('household-address').value = ''; offerAddress(candidate); $('household-address').focus();
  });
  function renderHouseholds() {
    const query = $('household-search').value.trim().toLocaleLowerCase('ja');
    const items = state.households.filter(item => matches(item, ['name', 'kana', 'address', 'phone'], query));
    const target = $('household-list'); target.replaceChildren();
    $('household-count').textContent = `${items.length}件 / 全${state.households.length}件`;
    for (const item of items) { const row = node('tr'); cell(row, item.name); cell(row, item.address); cell(row, item.phone); cell(row).append(button('編集', () => edit('household', item))); target.append(row); }
    if (!items.length) emptyTable(target, 4);
  }
  function renderDeceased() {
    const query = $('deceased-search').value.trim().toLocaleLowerCase('ja');
    const items = state.deceased.filter(item => matches({...item, household: householdName(item.household_id)}, ['name', 'kana', 'kaimyo', 'household'], query));
    const target = $('deceased-list'); target.replaceChildren();
    $('deceased-count').textContent = `${items.length}件 / 全${state.deceased.length}件`;
    for (const item of items) {
      const row = node('tr'); const name = cell(row); name.append(node('div', item.name), node('div', item.kaimyo || '戒名未登録', 'subtext'));
      cell(row, householdName(item.household_id)); cell(row, item.death_date);
      cell(row).append(button('編集', () => edit('deceased', item)), button('七日参り', () => showMemorial(item)));
      target.append(row);
    }
    if (!items.length) emptyTable(target, 4);
  }
  async function showMemorial(item) {
    clearError();
    try {
      const rows = await api(`/api/memorials/${item.id}`);
      const target = $('memorial-detail'); target.replaceChildren(node('h3', `${item.name} — 七日参り`));
      for (const row of rows) target.append(node('p', `${row.label}　${row.date}（命日から${row.offset_days}日）`));
      target.hidden = false;
    } catch (error) { showError(error); }
  }
  function fillSelect(select, items, prompt, label) {
    const value = select.value; select.replaceChildren();
    const option = node('option', prompt); option.value = ''; select.append(option);
    for (const item of items) { const entry = node('option', label(item)); entry.value = item.id; select.append(entry); }
    select.value = value;
  }
  function renderRules() {
    const target = $('rule-fields'); target.replaceChildren();
    for (const rule of state.rules) {
      const row = node('div', undefined, 'rule-row'); row.append(node('span', rule.label));
      const label = node('label', '命日からの日数'); const input = node('input'); input.type = 'number'; input.min = '0'; input.max = '366'; input.step = '1'; input.required = true; input.value = rule.offset_days; input.dataset.id = rule.id; input.setAttribute('aria-label', `${rule.label}の命日からの日数`); label.append(input); row.append(label); target.append(row);
    }
    const years = [...new Set((state.rokuyo || []).map(row => row.date.slice(0, 4)))].sort();
    $('rokuyo-summary').textContent = `六曜 ${state.rokuyo?.length || 0}件・対象年：${years.join('、') || '未収録'}`;
  }
  function scheduleItems() {
    return [...state.events.map(item => ({...item, manual: true})), ...memorials].sort((a, b) => a.date.localeCompare(b.date));
  }
  function renderCalendar() {
    const month = $('calendar-month').value; if (!month) return;
    const target = $('calendar'); target.replaceChildren();
    for (const day of ['日', '月', '火', '水', '木', '金', '土']) target.append(node('div', day, 'calendar-heading'));
    const first = new Date(`${month}-01T00:00:00Z`).getUTCDay();
    for (let i = 0; i < first; i++) target.append(node('div', '', 'calendar-day empty'));
    const items = scheduleItems().filter(item => item.date.startsWith(month));
    const now = new Date(); const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    for (let day = 1; day <= monthDays(month); day++) {
      const date = `${month}-${String(day).padStart(2, '0')}`;
      const box = node('div', undefined, `calendar-day${date === today ? ' today' : ''}`); box.append(node('strong', String(day)));
      const rokuyo = (state.rokuyo || []).find(item => item.date === date);
      const rokuyoText = node('div', rokuyo?.label || '六曜未収録', 'calendar-note subtext'); if (rokuyo) rokuyoText.title = `出典：${rokuyo.source}`; box.append(rokuyoText);
      for (const item of items.filter(item => item.date === date)) box.append(node('div', item.title, 'calendar-note'));
      target.append(box);
    }
    const list = $('event-list'); list.replaceChildren();
    for (const item of items) { const row = node('div', undefined, 'event-row'); row.append(node('span', `${item.date}　${item.title}`)); if (item.manual) row.append(button('編集', () => edit('event', item))); if (item.notes) row.append(node('div', item.notes, 'subtext')); list.append(row); }
    if (!items.length) list.append(node('p', 'この月の予定はありません。'));
  }
  function invalidateReport() { reportReady = false; $('print-report').disabled = true; $('report-preview').replaceChildren(); $('print-area').replaceChildren(); $('report-warning').textContent = ''; }
  async function load() {
    const version = ++loadVersion;
    loading = true; invalidateReport(); $('preview-report').disabled = true;
    let next;
    try { next = await api('/api/state'); }
    catch (error) { if (version === loadVersion) { loading = false; $('preview-report').disabled = false; } throw error; }
    if (version !== loadVersion) return;
    state = next; memorials = []; const invalid = [];
    for (const item of state.deceased) {
      try {
        const rows = state.rules.map(row => ({...row, date: shiftDate(item.death_date, row.offset_days), title: `${item.name}・${row.label}`}));
        if (rows.some(row => !/^\d{4}-\d{2}-\d{2}$/.test(row.date))) throw new Error('date range');
        memorials.push(...rows);
      } catch { invalid.push(item.name); }
    }
    ++loadVersion; loading = false; $('preview-report').disabled = false;
    renderHouseholds(); renderDeceased(); renderRules();
    fillSelect($('deceased-form').elements.household_id, state.households, state.households.length ? '檀家を選択' : '先に檀家を登録してください', item => item.name);
    fillSelect($('report-deceased'), state.deceased, '故人を選択', item => `${item.name}（${householdName(item.household_id)}）`);
    $('memorial-detail').hidden = true; renderCalendar(); invalidateReport();
    if (invalid.length) showError(new Error(`法要日を計算できない記録があります。死亡日を編集してください：${invalid.join('、')}`));
  }
  for (const kind of ['household', 'deceased', 'event']) {
    const form = $(`${kind}-form`);
    form.addEventListener('reset', () => { form.elements.id.value = ''; form.elements.version.value = ''; if (kind === 'household') resetPostal(); $(`${kind}-form-title`).textContent = `${titles[kind]}を登録`; });
    form.addEventListener('submit', async event => {
      event.preventDefault(); clearError(); const submit = form.querySelector('[type=submit]'); submit.disabled = true;
      const data = Object.fromEntries(new FormData(form)); const id = data.id; delete data.id;
      if (id) data.version = Number(data.version); else delete data.version;
      if (kind === 'deceased') data.household_id = Number(data.household_id);
      let saved = false;
      try { await api(`/api/${collections[kind]}${id ? `/${id}` : ''}`, id ? 'PUT' : 'POST', data); saved = true; form.reset(); await load(); $('status').textContent = `${titles[kind]}を保存しました。`; }
      catch (error) { if (saved) error.message = `保存は完了しましたが、表示更新に失敗しました。${error.message}`; showError(error); }
      finally { submit.disabled = false; }
    });
  }
  $('rules-form').addEventListener('submit', async event => {
    event.preventDefault(); clearError(); const submit = event.target.querySelector('[type=submit]'); submit.disabled = true;
    const rules = state.rules.map(rule => ({...rule, offset_days: Number([...$('rule-fields').querySelectorAll('input')].find(input => input.dataset.id === String(rule.id)).value)}));
    try { await api('/api/rules', 'PUT', {rules}); await load(); $('status').textContent = '七日参りの日数を保存しました。'; } catch (error) { showError(error); } finally { submit.disabled = false; }
  });
  $('rokuyo-form').addEventListener('submit', async event => {
    event.preventDefault(); clearError(); const submit = event.target.querySelector('[type=submit]'); submit.disabled = true;
    try {
      const file = $('rokuyo-file').files[0]; if (!file) throw new Error('六曜CSVを選択してください。');
      const rows = parseRokuyoCsv(new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer()));
      if (!rows.length) throw new Error('CSVに日付がありません。');
      await api('/api/rokuyo', 'POST', {source: $('rokuyo-source').value.trim(), rows}); await load(); $('status').textContent = `六曜を${rows.length}件取り込みました。`;
    } catch (error) { showError(error); } finally { submit.disabled = false; }
  });
  function reportPage(title) { const page = node('article', undefined, 'report-page'); page.append(node('h2', title)); return page; }
  function deceasedPage(item, onlyKaimyo) {
    const page = reportPage(onlyKaimyo ? '戒名' : '過去帳'); const body = node('div', undefined, 'vertical-body');
    page.dataset.personName = item.name;
    body.append(node('p', item.kaimyo || '戒名未登録', 'vertical-kaimyo'));
    if (!onlyKaimyo) {
      for (const text of [`俗名　${item.name}`, `命日　${printDate(item.death_date)}`, `生年月日　${printDate(item.birth_date)}`, `檀家　${householdName(item.household_id)}`]) body.append(node('p', text));
      if ($('report-notes').checked) body.append(node('p', `備考　${item.notes || 'なし'}`));
    } else body.append(node('p', `俗名　${item.name}\n命日　${printDate(item.death_date)}`));
    page.append(body); return page;
  }
  async function prepareReport() {
    if (reportBusy || loading) return;
    reportBusy = true; clearError(); invalidateReport(); $('preview-report').disabled = true;
    const version = loadVersion; const type = $('report-type').value; const selected = $('report-deceased').value;
    try {
      const item = state.deceased.find(entry => String(entry.id) === selected);
      const pages = [];
      if (type === 'register') {
        if (!state.deceased.length) throw new Error('過去帳に登録がありません。');
        for (const entry of state.deceased) pages.push(deceasedPage(entry, false));
      } else {
        if (!item) throw new Error('帳票に表示する故人を選択してください。');
        if (type === 'kaimyo') pages.push(deceasedPage(item, true));
        else {
          const rows = await api(`/api/memorials/${item.id}`); const page = reportPage('七日参り');
          page.dataset.personName = item.name;
          const body = node('div', undefined, 'vertical-body memorial-body');
          body.append(node('p', `俗名　${item.name}\n命日　${printDate(item.death_date)}\n戒名　${item.kaimyo || '未登録'}`));
          for (const row of rows) body.append(node('p', `${row.label}　${printDate(row.date)}　命日から${kanjiNumber(row.offset_days)}日`));
          page.append(body); pages.push(page);
        }
      }
      if (version !== loadVersion || type !== $('report-type').value || selected !== $('report-deceased').value) return;
      $('report-preview').append(...pages);
      let fontMissing = false;
      try { await document.fonts.load('22px "Noto Serif JP"'); } catch { fontMissing = true; }
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (version !== loadVersion || type !== $('report-type').value || selected !== $('report-deceased').value) { invalidateReport(); return; }
      const overflowPages = pages.filter(page => page.scrollHeight > page.clientHeight || page.scrollWidth > page.clientWidth || [...page.querySelectorAll('.vertical-body')].some(body => body.scrollHeight > body.clientHeight || body.scrollWidth > body.clientWidth));
      const overflow = overflowPages.length > 0;
      for (const page of overflowPages) page.classList.add('report-overflow');
      $('report-warning').textContent = overflow ? `帳票に収まらない文字があります：${overflowPages.map(page => `${pages.indexOf(page) + 1}ページ目（${page.dataset.personName}）`).join('、')}。戒名を確認するか、備考を含める設定を外してください。印刷を停止しています。` : `${pages.length}ページ。欠字・異体字・原本との一致は未確認です。${fontMissing ? '同梱フォントを読み込めず、代替フォントを使用しています。' : ''}`;
      reportReady = !overflow; $('print-report').disabled = !reportReady;
    } catch (error) { showError(error); }
    finally { reportBusy = false; $('preview-report').disabled = false; }
  }
  $('preview-report').addEventListener('click', prepareReport);
  $('print-report').addEventListener('click', async () => {
    if (!reportReady || reportBusy) return;
    await document.fonts.ready;
    if (!reportReady || reportBusy) return;
    $('print-area').replaceChildren(...[...$('report-preview').children].map(page => page.cloneNode(true)));
    window.print();
  });
  for (const id of ['report-type', 'report-deceased', 'report-notes']) $(id).addEventListener('change', invalidateReport);
  $('household-search').addEventListener('input', renderHouseholds);
  $('deceased-search').addEventListener('input', renderDeceased);
  for (const button of document.querySelectorAll('[data-tab]')) button.addEventListener('click', () => {
    for (const panel of document.querySelectorAll('main>.panel')) panel.hidden = panel.id !== button.dataset.tab;
    for (const tab of document.querySelectorAll('[data-tab]')) tab.setAttribute('aria-pressed', String(tab === button));
  });
  $('calendar-month').addEventListener('change', renderCalendar);
  function moveMonth(delta) { const date = new Date(`${$('calendar-month').value}-01T00:00:00Z`); date.setUTCMonth(date.getUTCMonth() + delta); $('calendar-month').value = date.toISOString().slice(0, 7); renderCalendar(); }
  $('previous-month').addEventListener('click', () => moveMonth(-1)); $('next-month').addEventListener('click', () => moveMonth(1));
  const now = new Date(); $('calendar-month').value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  $('status').textContent = 'ローカルの帳簿を読み込んでいます。';
  load().then(() => { $('status').textContent = '帳簿を読み込みました。'; }).catch(error => { $('status').textContent = ''; showError(error); });
}
