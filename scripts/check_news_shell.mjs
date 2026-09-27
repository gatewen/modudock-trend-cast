import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE);
const [url,out]=process.argv.slice(2);
assert.match(url,/^http:\/\/127\.0\.0\.1:\d+$/);
const browser=await chromium.launch({headless:true});
const evidence={url,catalog:[],running:[],received_events:[],news_changed:0,page_errors:[]};
try {
  const page=await browser.newPage();
  page.on('pageerror',e=>evidence.page_errors.push(e.message));
  page.on('websocket',s=>s.on('framereceived',frame=>{
    const p=JSON.parse(frame.payload.toString());
    if(p.t==='catalog')evidence.catalog=p.modules.map(m=>m.id);
    if(p.t==='state'&&p.state==='running')evidence.running.push(p.mod);
    if(p.t==='event')evidence.received_events.push({mod:p.mod,topic:p.topic,body:p.body});
    if(p.body?.op==='news_changed')evidence.news_changed++;
  }));
  await page.goto(url);
  await page.locator('[data-mod="trend-cast"] .load').click();
  await page.waitForFunction(()=>document.querySelector('[aria-label="預測天期"]')?.disabled===false);
  assert.equal(await page.locator('.tc-news').innerText(),'新聞廣播：未收到');
  await page.locator('[data-mod="news-digest-fixture"] .load').click();
  await page.waitForFunction(()=>document.querySelector('.tc-news')?.textContent.includes('2026-09-29 13:29:00'));
  evidence.news_status_text=await page.locator('.tc-news').innerText();
  assert.match(evidence.news_status_text,/已啟用（需當日快照）/);
  assert.deepEqual(evidence.catalog.sort(),['news-digest-fixture','trend-cast']);
  assert.deepEqual(evidence.running.sort(),['news-digest-fixture','trend-cast']);
  assert.ok(evidence.news_changed>=1);
  assert.ok(evidence.received_events.some(p=>p.mod==='trend-cast'&&p.topic==='news.market_digest'));
  assert.deepEqual(evidence.page_errors,[]);
  await writeFile(out,JSON.stringify(evidence,null,2)+'\n');
}finally {await browser.close();}
