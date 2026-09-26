// Real sync acceptance against an already-running local shell. Requires keys in
// the shell's environment, with SSL_CERT_FILE unset. Never runs/reveals forecasts.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const url = process.argv[2];
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(url || '') || !process.argv.includes('--execute')) {
  throw new Error('requires explicit loopback shell URL and --execute (real data sync)');
}
const modulePath = process.env.PLAYWRIGHT_MODULE || new URL('../../../modudock/web/node_modules/playwright/index.mjs', import.meta.url).href;
const {chromium} = await import(modulePath);
const output = new URL('../docs/verification/', import.meta.url);
const evidence = {url, catalog: [], states: [], sync: [], requests: [], page_errors: []};
const browser = await chromium.launch({headless: true});
const page = await browser.newPage({viewport: {width: 1440, height: 1600}, locale: 'zh-TW'});
page.on('pageerror', () => evidence.page_errors.push('page_error'));
page.on('websocket', socket => {
  socket.on('framereceived', frame => {
    const p = JSON.parse(frame.payload.toString());
    if (p.t === 'catalog') evidence.catalog = p.modules.filter(m => m.id === 'trend-cast');
    if (p.mod !== 'trend-cast') return;
    if (p.t === 'state') evidence.states.push(p.state);
    if (p.t === 'msg' && p.body.op === 'status') {
      assert.notEqual(p.body.holdout?.state, 'revealed');
      evidence.keys = p.body.keys; // availability only, never credentials
      if (p.body.sync?.generation) {
        const sync = p.body.sync;
        if (JSON.stringify(sync) !== JSON.stringify(evidence.sync.at(-1))) evidence.sync.push(sync);
      }
    }
  });
  socket.on('framesent', frame => {
    const p = JSON.parse(frame.payload.toString());
    if (p.mod === 'trend-cast' && p.t === 'msg') {
      assert.ok(['status', 'report', 'day', 'sync'].includes(p.body.op));
      evidence.requests.push(p.body.op);
    }
  });
});

async function waitSync(generation) {
  const deadline = Date.now() + 300000;
  while (Date.now() < deadline) {
    const final = evidence.sync.find(s => s.generation === generation && ['complete', 'partial', 'failed'].includes(s.status));
    if (final) {
      assert.equal(final.status, 'complete');
      assert.ok(final.n_ok > 0);
      assert.equal(final.n_fail, 0);
      assert.deepEqual(final.reasons, []);
      return final;
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('sync completion timeout');
}

try {
  await page.goto(url);
  await page.locator('[data-mod="trend-cast"] .load').click();
  evidence.automatic = await waitSync(1);
  await page.getByRole('button', {name: '重新同步', exact: true}).click();
  evidence.manual = await waitSync(2);
  assert.deepEqual(evidence.catalog[0].backend.command, ['python3', 'back/trendcast.py']);
  assert.ok(evidence.states.includes('running'));
  assert.equal(evidence.keys.fugle, 'available');
  assert.equal(evidence.keys.typesafe, 'available');
  evidence.visible_status = await page.locator('.tc-status').innerText();
  assert.match(evidence.visible_status, /同步：完成/);
  assert.deepEqual(evidence.page_errors, []);
  await page.locator('.tc').screenshot({path: fileURLToPath(new URL('block-5-fix-sync.png', output))});
  console.log(JSON.stringify({automatic: evidence.automatic, manual: evidence.manual, page_errors: evidence.page_errors}));
} finally {
  await writeFile(new URL('block-5-fix-sync-shell.json', output), JSON.stringify(evidence, null, 2) + '\n');
  await browser.close();
}
