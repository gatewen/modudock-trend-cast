import styles from './style.js';

const METHODS = ['jev', 'always_flat', 'majority', 'momentum', 'reversal'];
const LABELS = {up: '漲', flat: '盤整', down: '跌'};
const PHASES = {idle: '待命', running: '進行中', complete: '完成', partial: '部分完成',
  cancelling: '停止中', cancelled: '已停止', failed: '失敗'};
const SYNC_PHASES = {...PHASES, partial: '部分失敗', failed: '全部失敗'};
const SYNC_REASONS = {tls_certificate_error: '無法驗證 TLS 憑證', tls_error: 'TLS 連線失敗',
  auth_disabled: '金鑰無效', invalid_key: '金鑰無效', missing_key: '未設定金鑰',
  network_error: '連線失敗', request_timeout: '連線逾時', http_status: '資料服務回應失敗',
  invalid_response: '資料格式不符', database_operation_failed: '資料庫寫入失敗', operation_failed: '同步作業失敗'};
const KEYS = {available: '已設定', missing: '未設定', invalid: '金鑰無效'};
const ERRORS = {busy: '目前有工作進行中，請稍後再試。', missing_key: '尚未設定所需的金鑰。',
  holdout_experiment_forbidden: '期末考只允許實驗 1（p1），其他實驗的保留段不開放。',
  auth_disabled: '金鑰無效，此次執行已停用對應功能。', stale_experiment: '實驗已變更，請重新選擇。',
  confirmation_required: '請先確認此操作。', insufficient_warmup: '定稿資料不足，尚不能建立實驗。',
  unfinalized_month: '資料含未定稿月份，請先完成同步。', unknown_corporate_action: '除權息資料尚未確認。',
  packet_too_large: '資料超出單則訊息上限，未顯示。', frozen_data_changed: '凍結資料與實驗不符，回放已停止。'};
const text = value => typeof value === 'string' ? value.slice(0, 300) : '';
const numeric = value => typeof value === 'number' && Number.isFinite(value);
const number = (value, digits = 4) => numeric(value) ? value.toFixed(digits) : '—';
const percent = value => numeric(value) ? `${(value * 100).toFixed(2)}%` : '—';
const count = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('en-US') : '—';
const interval = value => Array.isArray(value) && value.length === 2 ? `${percent(value[0])}～${percent(value[1])}` : '—';
const isDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
const minute = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(value)
  ? Number(value.slice(11, 13)) * 60 + Number(value.slice(14, 16)) : NaN;

export default function mount(ctx) {
  const doc = ctx.container.ownerDocument;
  const root = doc.createElement('section'); root.className = 'tc';
  const style = doc.createElement('style'); style.textContent = styles;
  root.append(style); ctx.container.append(root);
  let live = false, disposed = false, serial = 0, metadata = {}, selected = '', days = [], dialogState = null;
  let lastCompletion = '', pendingAction = false;
  const requests = {}, removers = [];
  function element(tag, value = '', className = '') {
    const node = doc.createElement(tag); node.textContent = value; if (className) node.className = className;
    return node;
  }
  function on(node, event, callback) {
    node.addEventListener(event, callback); removers.push(() => node.removeEventListener(event, callback));
  }
  function button(label, action) {
    const node = element('button', label); node.type = 'button'; node.disabled = true;
    on(node, 'click', () => { if (live && !disposed && !node.disabled) action(); });
    return node;
  }
  function send(op, fields = {}) {
    if (!live || disposed) return;
    const request_id = ++serial; requests[op] = request_id;
    ctx.channel.send({op, ...fields, request_id});
    return request_id;
  }
  const head = element('header', '', 'tc-head');
  const title = element('div'); title.append(element('p', 'TREND CAST / HISTORICAL REPLAY', 'tc-kicker'),
    element('h1', '走勢推演'), element('p', '2330 台積電 · 30 分鐘後，漲、盤整或跌？', 'tc-sub'));
  const tag = element('span', '等待實驗', 'tc-tag'); head.append(title, tag);
  const status = element('div', '', 'tc-status'); status.setAttribute('role', 'status');
  const keys = element('span'), range = element('span'), experiment = element('span'), work = element('span'); status.append(keys, range, experiment, work);
  const errors = element('p', '', 'tc-error'); errors.setAttribute('role', 'alert'); errors.hidden = true;
  const toolbar = element('div', '', 'tc-toolbar');
  const sync = button('重新同步', () => send('sync'));
  const method = element('select'); method.setAttribute('aria-label', '預測方法');
  for (const name of METHODS) { const option = element('option', name); option.value = name; method.append(option); }
  function run(split) {
    if (!metadata.experiment_id || pendingAction) return;
    pendingAction = true; updateButtons();
    send('run', {method: method.value, split, experiment_id: metadata.experiment_id});
  }
  const runDev = button('跑開發段', () => run('dev')); runDev.className = 'tc-primary';
  const runHold = button('跑保留段', () => run('holdout'));
  const stop = button('停止', () => send('cancel'));
  const refresh = button('更新報告', () => send('report'));
  const newExperiment = button('開新實驗', () => confirm('new_experiment'));
  toolbar.append(sync, method, runDev, runHold, stop, refresh, newExperiment);
  on(method, 'change', updateButtons);

  const dialog = element('section', '', 'tc-dialog'); dialog.hidden = true; dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-label', '確認操作');
  const dialogTitle = element('h2'), dialogText = element('p');
  const thresholdLabel = element('label', '盤整門檻（‰）');
  const threshold = element('input'); threshold.type = 'number'; threshold.min = '1'; threshold.max = '999'; threshold.step = '1';
  threshold.setAttribute('aria-label', '盤整門檻'); thresholdLabel.append(threshold);
  const accept = button('確認', () => {
    if (!dialogState) return;
    if (dialogState.experiment_id !== metadata.experiment_id) { closeDialog(); return; }
    const state = dialogState;
    const fields = {confirmed: true, experiment_id: state.experiment_id};
    if (state.op === 'new_experiment') {
      const value = Number(threshold.value);
      if (!Number.isInteger(value) || value < 1 || value >= 1000) return;
      fields.threshold_permille = value;
    }
    closeDialog(); pendingAction = true; selected = '';
    requests.day = ++serial; requests.report = ++serial;
    clearDay('正在更新實驗…'); clearReport('正在更新實驗…'); updateButtons();
    send(state.op, fields);
  });
  const dismiss = button('取消', closeDialog);
  const actions = element('div', '', 'tc-dialog-actions'); actions.append(dismiss, accept);
  dialog.append(dialogTitle, dialogText, thresholdLabel, actions);
  function confirm(op) {
    dialogState = {op, experiment_id: metadata.experiment_id};
    dialogTitle.textContent = op === 'reveal' ? '確認解鎖保留段？' : '確認建立新實驗？';
    dialogText.textContent = op === 'reveal'
      ? '解鎖後會顯示走勢、標籤與成績，並永久記錄為已使用。開新實驗也不會清除已曝光日期。'
      : '使用目前已定稿的資料建立新切分。開發段可能增加；已曝光日期會跨實驗保留，原實驗不會被刪除。';
    thresholdLabel.hidden = op !== 'new_experiment'; threshold.value = String(metadata.threshold_permille || 3);
    accept.textContent = op === 'reveal' ? '確認解鎖' : '確認建立';
    dialog.hidden = false; updateButtons(); dismiss.focus();
  }
  function closeDialog() { dialogState = null; dialog.hidden = true; updateButtons(); }
  on(dialog, 'keydown', event => { if (event.key === 'Escape') closeDialog(); });

  const chartCard = element('section', '', 'tc-card');
  const chartHead = element('div', '', 'tc-chart-head');
  const chartHeading = element('div'); chartHeading.append(element('h2', '當日走勢'), element('p', '1 分 K 收盤價 · 箭頭表示 jev 的預測方向', 'tc-sub'));
  const dateNav = element('div', '', 'tc-date');
  const previous = button('← 前一天', () => requestDay(days[days.indexOf(selected) - 1]));
  const next = button('後一天 →', () => requestDay(days[days.indexOf(selected) + 1]));
  const date = element('input'); date.type = 'date'; date.setAttribute('aria-label', '交易日期');
  dateNav.append(previous, date, next); chartHead.append(chartHeading, dateNav);
  const chart = doc.createElementNS('http://www.w3.org/2000/svg', 'svg'); chart.classList.add('tc-chart');
  chart.setAttribute('viewBox', '0 0 1000 285'); chart.setAttribute('role', 'img'); chart.setAttribute('aria-label', '當日分 K 收盤走勢與預測點');
  const empty = element('p', '正在載入實驗…', 'tc-empty');
  const tooltip = element('p', '', 'tc-tooltip');
  const legend = element('div', '', 'tc-legend');
  legend.append(element('span', '● 預測正確', 'tc-good'), element('span', '● 預測錯誤', 'tc-bad'), element('span', '● 尚無有效結果'), element('span', '▲ 漲　▬ 盤整　▼ 跌'));
  chartCard.append(chartHead, empty, chart, tooltip, legend); chart.setAttribute('hidden', '');
  function clearDay(message) { chart.replaceChildren(); chart.setAttribute('hidden', ''); empty.hidden = false; empty.textContent = message; tooltip.textContent = ''; }
  function requestDay(value) {
    if (!isDate(value) || !days.includes(value)) return;
    selected = value; date.value = value; clearDay('載入當日走勢…'); updateButtons(); send('day', {date: value});
  }
  on(date, 'change', () => { if (live && !disposed) { if (days.includes(date.value)) requestDay(date.value); else date.value = selected; } });
  function svg(tag, attributes, value = '') {
    const node = doc.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    node.textContent = value; return node;
  }
  function renderDay(body) {
    if (body.status !== 'ok') { clearDay(body.status === 'holdout_locked' ? '保留段未解鎖' : text(body.message) || '目前沒有可顯示的行情'); return; }
    if (body.day !== selected || body.experiment_id !== metadata.experiment_id) return;
    const bars = Array.isArray(body.bars) ? body.bars.filter(b => b && Number.isFinite(Number(b.close)) && Number(b.close) > 0
      && minute(b.bar_end) >= 540 && minute(b.bar_end) <= 810) : [];
    if (!bars.length) { clearDay('當天沒有可用的分 K 資料'); return; }
    const prices = bars.map(b => Number(b.close));
    const low = Math.min(...prices), high = Math.max(...prices), padding = Math.max((high - low) * .15, .5);
    const bottom = low - padding, top = high + padding;
    const x = m => 65 + (m - 540) / 270 * 910, y = price => 235 - (price - bottom) / (top - bottom) * 205;
    chart.replaceChildren(); chart.removeAttribute('hidden'); empty.hidden = true;
    for (let i = 0; i <= 4; i++) {
      const value = bottom + (top - bottom) * i / 4, yy = y(value);
      chart.append(svg('line', {x1: 65, x2: 975, y1: yy, y2: yy, class: 'tc-gridline'}),
        svg('text', {x: 54, y: yy + 4, 'text-anchor': 'end'}, number(value, 1)));
    }
    for (const m of [540, 600, 660, 720, 780, 810]) {
      chart.append(svg('text', {x: x(m), y: 269, 'text-anchor': 'middle'}, `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`));
    }
    chart.append(svg('polyline', {class: 'tc-price', points: bars.map(b => `${x(minute(b.bar_end))},${y(Number(b.close))}`).join(' ')}));
    const points = Array.isArray(body.points) ? body.points : [];
    for (const p of points.slice(0, 8)) {
      if (!p || !Number.isFinite(minute(p.t))) continue;
      const answer = p.predictions?.jev, choice = answer?.answer;
      const valid = p.predictable === true && p.scorable === true && Object.hasOwn(LABELS, choice);
      const result = valid && answer.correct === true ? 'correct' : valid && answer.correct === false ? 'incorrect' : 'unknown';
      const price = Number(p.close_t), yy = Number.isFinite(price) && price > 0 ? y(price) : 235;
      const change = Number(p.close_t) > 0 && Number(p.close_end) > 0 && p.scorable === true ? (Number(p.close_end) / Number(p.close_t) - 1) : null;
      const probabilities = answer?.probabilities || {};
      const detail = `${text(p.t).slice(11, 16)} · jev：${LABELS[choice] || '尚無答案'} · ${result === 'correct' ? '正確' : result === 'incorrect' ? '錯誤' : '尚無有效結果'}\n`
        + `機率：漲 ${percent(probabilities.up)}／盤整 ${percent(probabilities.flat)}／跌 ${percent(probabilities.down)} · 30 分鐘實際漲跌 ${percent(change)}`;
      const group = svg('g', {class: 'tc-point', 'data-result': result, tabindex: 0, role: 'img', 'aria-label': detail});
      group.dataset.detail = detail;
      group.append(svg('title', {}, detail), svg('circle', {cx: x(minute(p.t)), cy: yy, r: 12}),
        svg('text', {x: x(minute(p.t)), y: yy}, {up: '▲', flat: '▬', down: '▼'}[choice] || '·'));
      chart.append(group);
    }
    tooltip.textContent = '滑過或以 Tab 選取預測點，可查看機率與實際漲跌幅。';
  }
  const showDetail = event => { const point = event.target.closest?.('.tc-point'); if (point) tooltip.textContent = point.dataset.detail; };
  on(chart, 'mouseover', showDetail); on(chart, 'focusin', showDetail);

  const reportCard = element('section', '', 'tc-card'); reportCard.append(element('h2', '開發段成績'));
  const reportContent = element('div'); reportCard.append(reportContent);
  function clearReport(message) { reportContent.replaceChildren(element('p', message, 'tc-note')); }
  clearReport('等待計分報告…');
  function table(headers, rows) {
    const wrapper = element('div', '', 'tc-scroll'), node = element('table'), head = element('thead'), heading = element('tr'), body = element('tbody');
    for (const label of headers) heading.append(element('th', label));
    head.append(heading);
    for (const row of rows) { const tr = element('tr'); tr.dataset.method = row[0]; for (const cell of row) tr.append(element('td', cell)); body.append(tr); }
    node.append(head, body); wrapper.append(node); return wrapper;
  }
  function renderReport(body) {
    if (body.status !== 'ok' || !body.dev) { clearReport(text(body.message) || '尚未建立實驗'); return; }
    if (body.experiment_id !== metadata.experiment_id) return;
    reportContent.replaceChildren();
    for (const [split, title] of [['dev', '開發段'], ['holdout', '保留段']]) {
      const data = body[split]; if (!data || data.state === 'locked') continue;
      if (split === 'holdout' && metadata.holdout?.state !== 'revealed') continue;
      const comparison = data.comparison || {}, boot = comparison.bootstrap || {};
      if (split === 'holdout') reportContent.append(element('h2', title));
      const summary = element('div', '', 'tc-summary');
      for (const [label, value] of [['共同交集', count(comparison.n)], ['交集覆蓋率', percent(comparison.coverage)], ['最佳基準', text(comparison.baseline) || '—']]) {
        const item = element('div'); item.append(element('div', value, 'tc-value'), element('div', label, 'tc-label')); summary.append(item);
      }
      const conclusion = element('div', '', 'tc-conclusion');
      conclusion.append(element('strong', text(comparison.statement)), element('p', `Brier 差（jev − 基準）${number(comparison.brier_difference, 6)} · 95% 區間 ${Array.isArray(boot.brier_difference_ci95) ? boot.brier_difference_ci95.map(v => number(v, 6)).join(' ～ ') : '—'}`, 'tc-sub'));
      reportContent.append(summary, conclusion, table(['方法', '樣本', '覆蓋率', '準確率', 'Wilson 95%', 'Brier', '缺答／失敗'], METHODS.map(name => {
        const m = data.methods?.[name] || {};
        return [name, count(m.n), percent(m.coverage), percent(m.accuracy), interval(m.accuracy_wilson95), number(m.brier, 6), `${count(m.eligible_missing)}／${count(m.failure_attempts)}`];
      })));
      reportContent.append(element('p', `交易日配對 bootstrap ${count(boot.repetitions)} 次；主要指標為 Brier，愈低愈好。缺答是目前缺少的有效答案；失敗保留歷次紀錄。`, 'tc-note'));
      const groups = data.jev_description?.classes;
      if (groups) {
        reportContent.append(element('p', 'jev 描述性分布（不影響結論）', 'tc-note'),
          table(['choice', '命中率', '預測占比', '真實占比'], Object.entries(LABELS).map(([key, label]) => [label, percent(groups[key]?.hit_rate), percent(groups[key]?.choice_share), percent(groups[key]?.true_share)])));
      }
    }
    if (Array.isArray(body.frozen_warnings) && body.frozen_warnings.length) reportContent.append(element('p', '凍結資料重抓時發現差異，原始資料未被覆寫。', 'tc-note'));
  }
  const lockCard = element('section', '', 'tc-card tc-lock');
  const lockText = element('p', '保留段未解鎖；行情與成績維持隱藏。');
  const reveal = button('看保留段結果', () => confirm('reveal')); lockCard.append(lockText, reveal);
  root.append(head, status, toolbar, errors, dialog, chartCard, reportCard, lockCard);

  function updateButtons() {
    const active = live && !disposed, hasExperiment = Number.isSafeInteger(metadata.experiment_id);
    const busy = metadata.busy || {}, keyAvailable = method.value !== 'jev' || metadata.keys?.typesafe === 'available';
    method.disabled = !active || Boolean(busy.replay) || pendingAction;
    sync.disabled = !active || metadata.keys?.fugle !== 'available' || Boolean(busy.sync);
    runDev.disabled = runHold.disabled = !active || !hasExperiment || !keyAvailable || Boolean(busy.replay) || pendingAction;
    stop.disabled = !active || !busy.replay;
    refresh.disabled = !active || !hasExperiment;
    newExperiment.disabled = !active || pendingAction || Boolean(busy.replay || busy.sync);
    reveal.disabled = !active || pendingAction || !hasExperiment || metadata.holdout?.state === 'revealed';
    previous.disabled = !active || days.indexOf(selected) <= 0;
    next.disabled = !active || days.indexOf(selected) < 0 || days.indexOf(selected) >= days.length - 1;
    date.disabled = !active || !days.length;
    accept.disabled = dismiss.disabled = !active || !dialogState;
  }
  function receive(body) {
    if (disposed || !body || typeof body !== 'object') return;
    const op = body.op;
    if ((op === 'day' || op === 'report') && body.request_id != null && body.request_id !== requests[op]) return;
    if (op === 'status') {
      const old = metadata.experiment_id;
      metadata = body; pendingAction = false;
      keys.textContent = `富果：${KEYS[body.keys?.fugle] || '未設定'} · TypeSafe：${KEYS[body.keys?.typesafe] || '未設定'}`;
      range.textContent = isDate(body.data_range?.first_day) && isDate(body.data_range?.last_day)
        ? `資料 ${body.data_range.first_day}～${body.data_range.last_day}` : '';
      tag.textContent = body.experiment_id ? `實驗 ${count(body.experiment_id)} · 門檻 ${number((body.threshold_permille || 3) / 10, 1)}%` : '尚未建立實驗';
      experiment.textContent = isDate(body.dev_start) && isDate(body.dev_end) ? `開發段 ${body.dev_start}～${body.dev_end}` : '等待定稿資料';
      const syncReasons = Array.isArray(body.sync?.reasons)
        ? [...new Set(body.sync.reasons.filter(code => Object.hasOwn(SYNC_REASONS, code)).map(code => SYNC_REASONS[code]))] : [];
      const syncReason = ['failed', 'partial'].includes(body.sync?.status)
        ? `（${syncReasons.join('、') || '同步作業失敗'}）` : '';
      work.textContent = `同步：${SYNC_PHASES[body.sync?.status] || '待命'}${syncReason} · 回放：${PHASES[body.replay?.status] || '待命'}`;
      if ((body.replay?.split === 'dev' || body.holdout?.state === 'revealed') && Number.isSafeInteger(body.replay?.n_ok)) {
        work.textContent += `（完成 ${count(body.replay.n_ok)}／失敗 ${count(body.replay.n_fail)}／略過 ${count(body.replay.skipped)}）`;
      }
      lockText.textContent = body.holdout?.state === 'revealed' ? '保留段已解鎖，已永久記錄為使用過。' : `${text(body.holdout?.message) || '保留段未解鎖'}；行情與成績維持隱藏。`;
      days = Array.isArray(body.days) ? body.days.filter(isDate) : [];
      if (days.length) { date.min = days[0]; date.max = days.at(-1); }
      if (old !== body.experiment_id) {
        closeDialog(); selected = ''; requests.day = ++serial; requests.report = ++serial;
        clearDay('等待行情…'); clearReport('等待計分報告…'); if (body.experiment_id) send('report');
      }
      if (!selected && days.length) requestDay(days[0]);
      if (body.status === 'experiment_required') { clearDay('尚未建立實驗'); clearReport('尚未建立實驗'); }
      const completed = body.replay?.status === 'complete' ? `${body.replay.generation}:${body.replay.method}` : '';
      if (completed && completed !== lastCompletion) { lastCompletion = completed; send('report'); if (selected) requestDay(selected); }
      updateButtons();
    } else if (op === 'day') renderDay(body);
    else if (op === 'report') renderReport(body);
    else if (op === 'error') {
      errors.textContent = ERRORS[body.code] || '操作未完成，請更新狀態後重試。'; errors.hidden = false;
      pendingAction = false; updateButtons();
      if (body.request_id != null && (body.request_id === requests.reveal || body.request_id === requests.new_experiment)) {
        send('status'); send('report');
      }
    }
  }
  ctx.channel.onMessage(receive);
  ctx.onUp(() => { if (disposed || live) return; live = true; updateButtons(); send('status'); send('report'); });
  updateButtons(); ctx.report('ready');
  return {unmount() { if (disposed) return; disposed = true; live = false; for (const remove of removers) remove(); root.remove(); }};
}
