// Development-only browser acceptance, using the shell workspace's Playwright.
// Opens only trend-cast. Never sends run/sync/new_experiment/reveal.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const modulePath = process.env.PLAYWRIGHT_MODULE || new URL('../../../modudock/web/node_modules/playwright/index.mjs', import.meta.url).href;
const {chromium} = await import(modulePath);
const output = new URL('../docs/verification/', import.meta.url);
const url = process.argv[2];
if (!url || !/^http:\/\/127\.0\.0\.1:\d+$/.test(url)) throw new Error('explicit loopback shell URL required');
const browser = await chromium.launch({headless: true});
const page = await browser.newPage({viewport: {width: 1440, height: 2100}, locale: 'zh-TW'});
const evidence = {url, catalog: null, states: [], requests: [], page_errors: [], themes: {}};
page.on('pageerror', error => evidence.page_errors.push(error.message));
page.on('websocket', socket => {
  socket.on('framereceived', frame => {
    const packet = JSON.parse(frame.payload.toString());
    if (packet.t === 'catalog') evidence.catalog = packet.modules.filter(m => m.id === 'trend-cast');
    if (packet.mod === 'trend-cast' && packet.t === 'state') evidence.states.push(packet.state);
    if (packet.mod === 'trend-cast' && packet.t === 'msg' && packet.body.op === 'report') {
      assert.equal(packet.body.holdout.state, 'locked');
      assert.deepEqual(Object.keys(packet.body.holdout).sort(), ['message', 'state']);
      evidence.development = {n: packet.body.dev.comparison.n, statement: packet.body.dev.comparison.statement,
        baseline: packet.body.dev.comparison.baseline, brier_difference: packet.body.dev.comparison.brier_difference};
    }
  });
  socket.on('framesent', frame => {
    const packet = JSON.parse(frame.payload.toString());
    if (packet.mod === 'trend-cast' && packet.t === 'msg') {
      const op = packet.body.op;
      assert.ok(['status', 'report', 'day'].includes(op), 'read-only acceptance commands');
      evidence.requests.push(op);
    }
  });
});

try {
  await page.goto(url);
  await page.locator('[data-mod="trend-cast"] .load').click();
  await page.locator('.tc-point').first().waitFor();
  await page.getByText('jev 比 majority 差', {exact: true}).waitFor({timeout: 30000});
  assert.equal(evidence.catalog.length, 1);
  assert.ok(evidence.states.includes('running'));
  assert.equal(await page.locator('.tc-point').count(), 8);
  assert.equal(await page.locator('.tc-price').count(), 1);
  assert.equal(await page.locator('.tc tr[data-method="jev"]').count(), 1);
  evidence.initial_day = await page.getByLabel('交易日期').inputValue();
  await page.getByRole('button', {name: '後一天 →', exact: true}).click();
  await page.locator('.tc-point').first().waitFor();
  const nextDay = await page.getByLabel('交易日期').inputValue();
  assert.notEqual(nextDay, evidence.initial_day);
  await page.getByRole('button', {name: '← 前一天', exact: true}).click();
  await page.locator('.tc-point').first().waitFor();
  assert.equal(await page.getByLabel('交易日期').inputValue(), evidence.initial_day);
  await page.locator('.tc-point').first().focus();
  assert.match(await page.locator('.tc-tooltip').innerText(), /機率/);
  await page.getByLabel('交易日期').focus();
  for (const mode of ['light', 'dark']) {
    await page.getByLabel('主題模式', {exact: true}).selectOption(mode);
    assert.equal(await page.locator('html').getAttribute('data-theme'), mode);
    evidence.themes[mode] = await page.locator('.tc').evaluate(element => ({
      color: getComputedStyle(element).color, background: getComputedStyle(element).backgroundColor,
      width: element.clientWidth, scrollWidth: element.scrollWidth,
      chart_points: element.querySelectorAll('.tc-point').length,
      chart_visible: !element.querySelector('svg').hasAttribute('hidden'),
      method_rows: element.querySelectorAll('tr[data-method="jev"],tr[data-method="always_flat"],tr[data-method="majority"],tr[data-method="momentum"],tr[data-method="reversal"]').length,
    }));
    assert.ok(evidence.themes[mode].scrollWidth <= evidence.themes[mode].width);
    await page.locator('.tc').screenshot({path: fileURLToPath(new URL(`block-5-${mode}.png`, output))});
    if (mode === 'light') await page.screenshot({path: fileURLToPath(new URL('block-5-shell-loaded.png', output)), fullPage: true});
  }
  assert.notEqual(evidence.themes.light.background, evidence.themes.dark.background);
  for (const path of ['data/trendcast.sqlite3', 'back/trendcast.py']) {
    const response = await page.request.get(`${url}/modules/trend-cast/${path}`);
    assert.equal(response.status(), 404);
  }
  evidence.private_routes = '404'; evidence.points = await page.locator('.tc-point').count();
  evidence.reveal_sent = false;
  assert.deepEqual(evidence.page_errors, []);
  await writeFile(new URL('block-5-shell-evidence.json', output), JSON.stringify(evidence, null, 2) + '\n');
  console.log(JSON.stringify({catalog: evidence.catalog.map(m => m.id), running: true, development: evidence.development,
    points: evidence.points, themes: Object.keys(evidence.themes), read_only_commands: true, page_errors: evidence.page_errors}));
} finally {
  await browser.close();
}
