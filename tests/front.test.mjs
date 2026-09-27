import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
import mount from '../front/intraday.js';

function setup(t) {
  const window = new Window(), container = window.document.createElement('div');
  window.document.body.append(container);
  const sent = [], reports = []; let message, up;
  const handle = mount({container,
    channel: {send(body) { sent.push(body); }, onMessage(fn) { message = fn; }},
    onUp(fn) { up = fn; }, report(status) {
      assert.equal(typeof message, 'function'); assert.equal(typeof up, 'function');
      assert.ok(container.querySelector('svg')); reports.push(status);
    }});
  t.after(async () => { try { handle.unmount(); } finally { await window.happyDOM.close(); } });
  return {window, container, sent, reports, handle, message, up,
    button(name) { return [...container.querySelectorAll('button')].find(b => b.textContent === name); },
    latest(op) { return sent.filter(b => b.op === op).at(-1); }};
}
const status = (extra = {}) => ({op: 'status', status: 'ok', experiment_id: 1,
  threshold_permille: 3, dev_start: '2024-02-06', dev_end: '2024-02-07',
  days: ['2024-02-06', '2024-02-07'], keys: {fugle: 'available', typesafe: 'available'},
  busy: {sync: false, replay: false}, sync: {status: 'idle'}, replay: {status: 'idle'},
  holdout: {state: 'locked', message: '保留段未解鎖'}, ...extra});
function day(date = '2024-02-06', extra = {}) {
  const stamp = clock => `${date}T${clock}:00+08:00`;
  return {op: 'day', status: 'ok', experiment_id: 1, day: date,
    bars: [{bar_end: stamp('09:01'), close: '100'}, {bar_end: stamp('10:00'), close: '101'}, {bar_end: stamp('13:30'), close: '100.3'}],
    points: Array.from({length: 8}, (_, i) => ({t: stamp(`${String(9 + Math.floor((i + 1)/2)).padStart(2, '0')}:${i % 2 ? '00' : '30'}`),
      predictable: i !== 6, scorable: i !== 7, close_t: '100', close_end: '100.3',
      predictions: {jev: {answer: ['up', 'flat', 'down'][i % 3], probabilities: {up: .2, flat: .6, down: .2}, correct: i % 2 === 0}}})), ...extra};
}
function report(extra = {}) {
  const m = {n: 100, accuracy: .44, accuracy_wilson95: [.35, .54], brier: .67, coverage: 1, eligible_missing: 0, failure_attempts: 1};
  return {op: 'report', status: 'ok', experiment_id: 1, frozen_warnings: [],
    dev: {methods: Object.fromEntries(['jev','always_flat','majority','momentum','reversal'].map(k => [k, m])),
      comparison: {n: 100, coverage: 1, baseline: 'majority', statement: 'jev 比 majority 差', brier_difference: .04,
        bootstrap: {repetitions: 2000, brier_difference_ci95: [.03, .05]}},
      jev_description: {classes: {down: {hit_rate: .3, choice_share: .35, true_share: .25}}}},
    holdout: {state: 'locked'}, ...extra};
}
function ready(h, extra = {}) { h.up(); h.message(status(extra)); }

test('mount synchronous; ready after registration; no sending before up', t => {
  const h = setup(t); assert.deepEqual(h.reports, ['ready']); assert.equal(h.handle.then, undefined);
  for (const b of h.container.querySelectorAll('button')) { assert.equal(b.disabled, true); b.dispatchEvent(new h.window.Event('click')); }
  assert.deepEqual(h.sent, []); h.up();
  assert.deepEqual(h.sent.map(b => b.op), ['status', 'report']); h.up(); assert.equal(h.sent.length, 2);
});

test('status initializes authorized date and renders eight SVG predictions with outcome colors', t => {
  const h = setup(t); ready(h); assert.equal(h.latest('day').date, '2024-02-06');
  h.message({...day(), request_id: h.latest('day').request_id});
  assert.ok(h.container.querySelector('.tc-price').getAttribute('points').includes(','));
  const points = [...h.container.querySelectorAll('.tc-point')]; assert.equal(points.length, 8);
  assert.deepEqual(points.map(p => p.dataset.result), ['correct','incorrect','correct','incorrect','correct','incorrect','unknown','unknown']);
  assert.deepEqual(points.slice(0, 3).map(p => p.querySelector('text').textContent), ['▲','▬','▼']);
  assert.equal(h.container.querySelector('svg').hasAttribute('hidden'), false);
});

test('hover and keyboard focus show probabilities and realized return', t => {
  const h = setup(t); ready(h); h.message(day());
  h.container.querySelector('.tc-point').dispatchEvent(new h.window.Event('focusin', {bubbles: true}));
  const detail = h.container.querySelector('.tc-tooltip').textContent;
  assert.match(detail, /漲 20.00%／盤整 60.00%／跌 20.00%/); assert.match(detail, /0.30%/);
});

test('unpredictable unscorable missing or nonboolean correctness never gets green/red', t => {
  const h = setup(t); ready(h);
  const data = day(); data.points[0].predictions = {}; data.points[1].predictions.jev.correct = 'false';
  h.message(data); const points = [...h.container.querySelectorAll('.tc-point')];
  assert.equal(points[0].dataset.result, 'unknown'); assert.equal(points[1].dataset.result, 'unknown');
  assert.equal(points[6].dataset.result, 'unknown'); assert.equal(points[7].dataset.result, 'unknown');
});

test('day navigation clears old plot immediately and rejects out-of-order day response', t => {
  const h = setup(t); ready(h); const first = h.latest('day'); h.message({...day(), request_id: first.request_id});
  assert.equal(h.button('← 前一天').disabled, true); h.button('後一天 →').click();
  assert.equal(h.container.querySelectorAll('.tc-point').length, 0);
  assert.equal(h.latest('day').date, '2024-02-07');
  h.message({...day(), request_id: first.request_id}); assert.equal(h.container.querySelectorAll('.tc-point').length, 0);
  h.message({...day('2024-02-07'), request_id: h.latest('day').request_id});
  assert.equal(h.container.querySelectorAll('.tc-point').length, 8); assert.equal(h.button('後一天 →').disabled, true);
});

test('date picker refuses unknown and locked days without requests', t => {
  const h = setup(t); ready(h); const input = h.container.querySelector('input[type=date]'); const before = h.sent.length;
  input.value = '2024-02-08'; input.dispatchEvent(new h.window.Event('change'));
  assert.equal(h.sent.length, before); assert.equal(input.value, '2024-02-06');
  assert.equal(input.max, '2024-02-07');
});

test('older report for the same experiment cannot overwrite a later refresh', t => {
  const h = setup(t); ready(h); const old = h.latest('report').request_id;
  h.button('更新報告').click(); const current = h.latest('report').request_id;
  const fresh = report({request_id: current}); fresh.dev.comparison.statement = 'FRESH'; h.message(fresh);
  const stale = report({request_id: old}); stale.dev.comparison.statement = 'STALE'; h.message(stale);
  assert.match(h.container.textContent, /FRESH/); assert.doesNotMatch(h.container.textContent, /STALE/);
});

test('locked day discards any supplied data and clears a previous visible chart', t => {
  const h = setup(t); ready(h); h.message(day());
  h.message({...day(), status: 'holdout_locked', message: '保留段未解鎖', secret: 'PRIVATE-HOLDOUT'});
  assert.equal(h.container.querySelectorAll('.tc-point,.tc-price').length, 0);
  assert.equal(h.container.querySelector('svg').hasAttribute('hidden'), true);
  assert.match(h.container.textContent, /保留段未解鎖/); assert.doesNotMatch(h.container.textContent, /PRIVATE-HOLDOUT/);
});

test('report table includes raw metrics Wilson Brier coverage failures and fourth conclusion', t => {
  const h = setup(t); ready(h); h.message(report());
  assert.equal(h.container.querySelectorAll('tr[data-method=jev]').length, 1);
  assert.match(h.container.textContent, /jev 比 majority 差/); assert.match(h.container.textContent, /35.00%～54.00%/);
  assert.match(h.container.textContent, /0.670000/); assert.match(h.container.textContent, /0／1/);
  assert.match(h.container.textContent, /描述性分布（不影響結論）/);
});

test('locked metadata refuses even an accidentally supplied revealed holdout report', t => {
  const h = setup(t); ready(h); const hidden = structuredClone(report().dev);
  hidden.state = 'revealed'; hidden.comparison.statement = 'PRIVATE-HOLDOUT'; hidden.comparison.n = 998877;
  h.message(report({holdout: hidden})); assert.doesNotMatch(h.container.textContent, /PRIVATE-HOLDOUT|998877/);
});

test('development progress can show counts but locked holdout progress cannot', t => {
  const h = setup(t); ready(h, {replay: {status: 'running', split: 'dev', n_ok: 12, n_fail: 1, skipped: 2}});
  assert.match(h.container.textContent, /完成 12／失敗 1／略過 2/);
  h.message(status({replay: {status: 'running', split: 'holdout', n_ok: 998877, n_fail: 887766, skipped: 776655}}));
  assert.doesNotMatch(h.container.textContent, /998,877|887,766|776,655/);
});

test('untrusted strings become text, never HTML or handlers', t => {
  const h = setup(t); ready(h, {holdout: {state: 'locked', message: '<img src=x onerror=alert(1)>'}});
  const data = report(); data.dev.comparison.statement = '<script>attack()</script>'; h.message(data);
  assert.equal(h.container.querySelectorAll('img,script').length, 0);
  assert.match(h.container.textContent, /<script>attack\(\)<\/script>/);
  assert.doesNotMatch(readFileSync(new URL('../front/intraday.js', import.meta.url), 'utf8'), /\.innerHTML\s*=/);
});

test('reveal requires second explicit confirmation and cancel sends nothing', t => {
  const h = setup(t); ready(h); const before = h.sent.length;
  h.button('看保留段結果').click(); assert.equal(h.sent.length, before);
  assert.equal(h.container.querySelector('[role=dialog]').hidden, false);
  h.button('取消').click(); assert.equal(h.sent.length, before);
  h.button('看保留段結果').click(); h.button('確認解鎖').click();
  assert.deepEqual(h.latest('reveal'), {op: 'reveal', confirmed: true, experiment_id: 1, request_id: h.latest('reveal').request_id});
  assert.equal(h.container.querySelector('[role=dialog]').hidden, true);
});

test('new experiment uses a second confirmation and validates threshold', t => {
  const h = setup(t); ready(h); h.button('開新實驗').click(); assert.equal(h.latest('new_experiment'), undefined);
  const threshold = h.container.querySelector('[aria-label=盤整門檻]'); threshold.value = '0'; h.button('確認建立').click();
  assert.equal(h.latest('new_experiment'), undefined);
  threshold.value = '5'; h.button('確認建立').click(); assert.equal(h.latest('new_experiment').threshold_permille, 5);
  assert.equal(h.latest('new_experiment').confirmed, true);
});

test('successful reveal refreshes the cleared day and failed confirmation can recover', t => {
  const h = setup(t); ready(h); h.message(day());
  h.button('看保留段結果').click(); h.button('確認解鎖').click();
  const old = h.latest('day').request_id;
  h.message(status({holdout: {state: 'revealed'}}));
  assert.ok(h.latest('day').request_id > old);
  h.message({...day(), request_id: h.latest('day').request_id}); assert.equal(h.container.querySelectorAll('.tc-point').length, 8);
  h.button('開新實驗').click(); h.button('確認建立').click();
  h.message({op: 'error', code: 'busy', request_id: h.latest('new_experiment').request_id});
  assert.equal(h.sent.at(-1).op, 'report'); assert.equal(h.sent.at(-2).op, 'status');
});

test('experiment change invalidates old report and pending reveal confirmation', t => {
  const h = setup(t); ready(h); const previous = h.latest('report'); h.button('看保留段結果').click();
  h.message(status({experiment_id: 2})); assert.equal(h.container.querySelector('[role=dialog]').hidden, true);
  h.message(report({request_id: previous.request_id})); assert.doesNotMatch(h.container.textContent, /jev 比 majority 差/);
  assert.equal(h.latest('reveal'), undefined);
});

test('missing key disables jev but baseline replay stays usable', t => {
  const h = setup(t); ready(h, {keys: {fugle: 'missing', typesafe: 'missing'}});
  assert.equal(h.button('重新同步').disabled, true); assert.equal(h.button('跑開發段').disabled, true);
  const select = h.container.querySelector('select'); select.value = 'always_flat'; select.dispatchEvent(new h.window.Event('change'));
  assert.equal(h.button('跑開發段').disabled, false); h.button('跑開發段').click();
  assert.equal(h.latest('run').method, 'always_flat'); assert.equal(h.latest('run').split, 'dev');
  const before = h.sent.length; h.button('跑開發段').dispatchEvent(new h.window.Event('click')); assert.equal(h.sent.length, before);
});

test('busy flags keep sync independent of replay and prevent experiment creation', t => {
  const h = setup(t); ready(h, {busy: {sync: true, replay: false}});
  assert.equal(h.button('重新同步').disabled, true); assert.equal(h.button('跑開發段').disabled, false);
  assert.equal(h.button('開新實驗').disabled, true);
  h.message(status({busy: {sync: false, replay: true}}));
  assert.equal(h.button('重新同步').disabled, false); assert.equal(h.button('跑開發段').disabled, true);
  assert.equal(h.button('停止').disabled, false); h.button('停止').click(); assert.equal(h.sent.at(-1).op, 'cancel');
});

test('no experiment shows no chart or score and late unmount callbacks do nothing', t => {
  const h = setup(t); h.up(); h.message({op: 'status', status: 'experiment_required', keys: {}, days: []});
  assert.match(h.container.textContent, /尚未建立實驗/); assert.equal(h.container.querySelectorAll('.tc-price').length, 0);
  const old = h.button('開新實驗'), before = h.sent.length; h.handle.unmount(); h.handle.unmount();
  old.dispatchEvent(new h.window.Event('click')); h.message(status()); h.message(day()); h.up();
  assert.equal(h.sent.length, before); assert.equal(h.container.childNodes.length, 0);
});

test('error renders local explanation and never reflects upstream secret text', t => {
  const h = setup(t); ready(h); h.message({op: 'error', code: 'fixture-secret'});
  assert.doesNotMatch(h.container.textContent, /fixture-secret/);
  h.message({op: 'error', code: 'auth_disabled'}); assert.match(h.container.textContent, /金鑰無效/);
});

test('sync distinguishes total and partial failure with safe reasons and clears them after success', t => {
  const h = setup(t); ready(h);
  for (const [phase, code, expected] of [
    ['failed', 'tls_certificate_error', '同步：全部失敗（無法驗證 TLS 憑證）'],
    ['failed', 'auth_disabled', '同步：全部失敗（金鑰無效）'],
    ['partial', 'network_error', '同步：部分失敗（連線失敗）']]) {
    h.message(status({sync: {status: phase, reasons: [code, 'fixture-secret', 'toString']}}));
    assert.ok(h.container.textContent.includes(expected));
    assert.doesNotMatch(h.container.textContent, /fixture-secret|function toString/);
  }
  h.message(status({sync: {status: 'complete', reasons: []}}));
  assert.match(h.container.textContent, /同步：完成/);
  assert.doesNotMatch(h.container.textContent, /無法驗證|連線失敗|部分失敗|全部失敗/);
});

test('theme rules inherit shell tokens and define both color schemes', t => {
  const h = setup(t), css = h.container.querySelector('style').textContent;
  assert.match(css, /var\(--md-bg/); assert.match(css, /var\(--md-fg/);
  assert.match(css, /data-theme="dark"/); assert.match(css, /data-theme="light"/);
  assert.match(css, /\.tc-point\[data-result="correct"\]/); assert.match(css, /\.tc-point\[data-result="incorrect"\]/);
});

test('forward run and reveal each require confirmation and send fixed experiment only', t => {
  const h = setup(t); ready(h);
  for (const [label, accept, op] of [['跑前瞻段', '確認執行', 'run_forward'], ['看前瞻段結果', '確認揭露', 'reveal_forward']]) {
    const before = h.sent.length;
    h.button(label).click(); assert.equal(h.sent.length, before);
    h.button('取消').click(); assert.equal(h.sent.length, before);
    h.button(label).click(); h.button(accept).click();
    assert.deepEqual(h.latest(op), {op, confirmed: true, experiment_id: 1, request_id: h.latest(op).request_id});
    h.message(status());
  }
  h.message(status({experiment_id: 2}));
  assert.equal(h.button('跑前瞻段').disabled, true);
  assert.equal(h.button('看前瞻段結果').disabled, true);
});

test('locked forward report and growing forward progress hide all counts even with revealed holdout', t => {
  const h = setup(t); ready(h, {holdout: {state: 'revealed'}, forward: {state: 'locked'},
    replay: {status: 'running', split: 'forward', n_ok: 998877, n_fail: 887766, skipped: 776655}});
  const hidden = structuredClone(report().dev);
  hidden.state = 'revealed'; hidden.cumulative_days = 112233; hidden.predictable_and_scorable = 223344;
  hidden.comparison.statement = 'PRIVATE-FORWARD'; hidden.comparison.n = 998877;
  h.message(report({forward: hidden}));
  assert.match(h.container.textContent, /前瞻段未揭露/);
  assert.doesNotMatch(h.container.textContent, /PRIVATE-FORWARD|998,877|887,766|776,655|112,233|223,344/);
  h.message(status({forward: {state: 'revealed'}, holdout: {state: 'revealed'},
    replay: {status: 'running', split: 'forward', n_ok: 998877, n_fail: 887766, skipped: 776655}}));
  assert.doesNotMatch(h.container.textContent, /998,877|887,766|776,655/);
});

test('revealed forward shows cumulative days points and permanent used-holdout label', t => {
  const h = setup(t); ready(h, {forward: {state: 'revealed'}, holdout: {state: 'revealed'}});
  const forward = {...structuredClone(report().dev), state: 'revealed', cumulative_days: 2, predictable_and_scorable: 16};
  h.message(report({forward}));
  assert.match(h.container.textContent, /累積 2 日／16 點/);
  assert.match(h.container.textContent, /保留段已使用/);
  assert.equal(h.container.querySelectorAll('tr[data-method=jev]').length, 2);
});

test('each forward rerun refreshes its cleared report without exposing generation counts', t => {
  const h = setup(t); ready(h);
  for (let i = 0; i < 2; i++) {
    h.button('跑前瞻段').click(); h.button('確認執行').click();
    h.message(status({replay: {status: 'running', split: 'forward', method: 'all'}}));
    const before = h.sent.filter(v => v.op === 'report').length;
    h.message(status({replay: {status: 'complete', split: 'forward', method: 'all'}}));
    assert.equal(h.sent.filter(v => v.op === 'report').length, before + 1);
  }
});

test('evolution development table shows signed Brier intervals and honest interpretation', t => {
  const h=setup(t);ready(h);
  const names=['clock_prior','vol_prior','jev_calibrated'];
  const evo={state:'ready',methods:Object.fromEntries(names.map(name=>[name,{accuracy:.51,brier:.61}])),
    comparisons:Object.fromEntries(names.map(name=>[name,{brier_difference:-.013,
      bootstrap:{brier_difference_ci95:[-.02,-.006]},statement:name==='jev_calibrated'?'沒有改善':'值得前瞻驗證'}]))};
  h.message(report({evolution_dev:evo}));
  const section=h.container.querySelector('.tc-evolution');
  assert.ok(section);
  assert.match(section.textContent,/進化新方法（開發段）/);
  assert.match(section.textContent,/開發段勝出＝值得前瞻驗證，不是證明有效/);
  assert.equal(section.querySelectorAll('tbody tr').length,3);
  for(const name of names) assert.match(section.textContent,new RegExp(name));
  assert.match(section.textContent,/51\.00%/);assert.match(section.textContent,/0\.610000/);
  assert.match(section.textContent,/-0\.013000/);assert.match(section.textContent,/-0\.020000 ～ -0\.006000/);
  assert.match(section.textContent,/沒有改善/);
});

test('forward participants and committed points are metadata only while scores remain locked', t => {
  const h=setup(t);ready(h,{forward:{state:'locked',run_points:144},holdout:{state:'revealed'}});
  const hidden={...structuredClone(report().dev),state:'revealed',comparisons:{
    vol_prior:{brier_difference:987.654321,statement:'PRIVATE-VOL',bootstrap:{brier_difference_ci95:[888888.888,999999.999]}}}};
  h.message(report({forward:hidden}));
  assert.match(h.container.textContent,/考生：jev p1、vol_prior、always_flat、majority、momentum、reversal/);
  assert.match(h.container.textContent,/已跑 144 點（六方法共同完成）/);
  assert.match(h.container.textContent,/前瞻段未揭露/);
  assert.doesNotMatch(h.container.textContent,/PRIVATE-VOL|987\.654321|888888\.888|999999\.999/);
});

test('revealed forward renders both prespecified comparisons including unfavorable result', t => {
  const h=setup(t);ready(h,{forward:{state:'revealed',run_points:16}});
  const forward={...structuredClone(report().dev),state:'revealed',cumulative_days:2,predictable_and_scorable:16,
    comparisons:{jev:{brier_difference:.1,bootstrap:{brier_difference_ci95:[.05,.15]},statement:'jev 比 majority 差'},
      vol_prior:{brier_difference:-.02,bootstrap:{brier_difference_ci95:[-.03,-.01]},statement:'vol_prior 比 majority 好'}}};
  h.message(report({forward}));
  assert.match(h.container.textContent,/jev p1 − majority：0\.100000/);
  assert.match(h.container.textContent,/vol_prior − majority：-0\.020000/);
  assert.match(h.container.textContent,/jev 比 majority 差/);
  assert.match(h.container.textContent,/vol_prior 比 majority 好/);
});

test('used holdout heading stays visible even when current status is locked', t => {
  const h=setup(t);ready(h,{holdout:{state:'locked'}});h.message(report());
  assert.ok(h.container.querySelector('.tc-lock h2'));
  assert.equal(h.container.querySelector('.tc-lock h2').textContent,'保留段已使用');
});

test('another experiment cannot inherit evolution table or forward point count', t => {
  const h=setup(t);ready(h,{experiment_id:2,forward:{state:'locked',run_points:144}});
  const data=report({experiment_id:2,evolution_dev:{state:'ready',methods:{},comparisons:{}}});
  h.message(data);
  assert.equal(h.container.querySelectorAll('.tc-evolution tbody tr').length,0);
  assert.doesNotMatch(h.container.textContent,/已跑 144/);
});

test('four independent cards have one heading each and buttons stay with their segment', t => {
  const h=setup(t); ready(h,{holdout:{state:'revealed'},forward:{state:'locked',run_points:144,unrevealed_days:18}});
  const holdout={...structuredClone(report().dev),state:'revealed'};
  h.message(report({holdout}));
  const root=h.container.querySelector('.tc'), dev=root.querySelector('.tc-dev'), evo=root.querySelector('.tc-evolution');
  assert.ok(dev); assert.ok(evo);
  assert.ok(dev.parentElement===root); assert.ok(evo.parentElement===root);
  assert.ok(dev.nextElementSibling===evo);
  assert.equal(dev.querySelectorAll('tr[data-method=jev]').length,1);
  assert.equal(dev.querySelector('.tc-evolution'),null);
  for (const title of ['開發段成績','進化新方法（開發段）','保留段已使用','前瞻段'])
    assert.equal([...root.querySelectorAll('h2')].filter(h=>h.textContent===title).length,1,title);
  assert.equal(root.querySelector('.tc-lock').querySelectorAll('tr[data-method=jev]').length,1);
  for (const name of ['跑保留段','看保留段結果']) assert.ok(h.button(name).closest('.tc-card')===root.querySelector('.tc-lock'));
  for (const name of ['跑前瞻段','看前瞻段結果']) assert.ok(h.button(name).closest('.tc-card')===root.querySelector('.tc-forward'));
  assert.equal(root.querySelector('.tc-forward').querySelectorAll('tbody').length,0);
});

test('status explains unrevealed days with safe metadata and clears stale annotations', t => {
  const h=setup(t); ready(h,{data_range:{first_day:'2024-08-01',last_day:'2026-08-31'},
    forward:{state:'locked',run_points:144,unrevealed_days:18}});
  const bar=h.container.querySelector('.tc-status');
  assert.match(bar.textContent,/資料 2024-08-01～2026-08-31/);
  assert.match(bar.textContent,/另有前瞻段 18 日未揭露/);
  for (const value of [0,-1,1.5,'18', '<script>secret</script>',null]) {
    h.message(status({forward:{state:'locked',unrevealed_days:value}}));
    assert.doesNotMatch(bar.textContent,/另有前瞻段|secret/);
  }
  h.message(status({experiment_id:2,forward:{state:'locked',unrevealed_days:18}}));
  assert.doesNotMatch(bar.textContent,/另有前瞻段/);
});

test('changing experiment clears all separate score containers immediately', t => {
  const h=setup(t);ready(h,{holdout:{state:'revealed'},forward:{state:'revealed'}});
  const exposed={...structuredClone(report().dev),state:'revealed'};
  exposed.comparison.statement='OLD-SCORE';
  h.message(report({holdout:exposed,forward:exposed}));
  assert.match(h.container.textContent,/OLD-SCORE/);
  h.message(status({experiment_id:3}));
  assert.doesNotMatch(h.container.textContent,/OLD-SCORE/);
  assert.equal(h.container.querySelectorAll('tbody tr').length,0);
});
