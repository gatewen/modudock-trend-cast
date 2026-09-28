import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE);
const [url,out]=process.argv.slice(2);
assert.match(url,/^http:\/\/127\.0\.0\.1:\d+$/);
const browser=await chromium.launch({headless:true});
const evidence={url,states:[],requests:[],page_errors:[],horizons:{}};
try {
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  page.on('pageerror',e=>evidence.page_errors.push(e.message));
  page.on('websocket',s=>{
    s.on('framereceived',f=>{const p=JSON.parse(f.payload.toString());if(p.mod==='trend-cast'&&p.t==='state')evidence.states.push(p.state);});
    s.on('framesent',f=>{const p=JSON.parse(f.payload.toString());if(p.mod==='trend-cast'&&p.t==='msg'){
      assert.ok(['daily_status','daily_chart','daily_report','daily_indicators','daily_forward','news_status'].includes(p.body.op));
      evidence.requests.push(p.body.op);
    }});
  });
  await page.goto(url);await page.locator('[data-mod="trend-cast"] .load').click();
  for(const H of ['7','3','14']) {
    await page.getByLabel('預測天期').selectOption(H);
    await page.waitForFunction(()=>document.querySelector('.tc-daily-holdout')?.textContent.includes('已使用（一次性保留段考試已揭露）'));
    evidence.horizons[H]=await page.locator('.tc-daily-holdout').innerText();
    assert.equal(await page.locator('.tc-daily-holdout button').count(),0);
  }
  for(const file of ['data/trendcast.sqlite3','back/daily_hold_run.py']) {
    const response=await page.request.get(`${url}/modules/trend-cast/${file}`);assert.equal(response.status(),404);
  }
  await page.locator('.tc-daily-holdout').screenshot({path:out.replace('.json','.png')});
  assert.ok(evidence.states.includes('running'));assert.deepEqual(evidence.page_errors,[]);
  evidence.private_routes='404';await writeFile(out,JSON.stringify(evidence,null,2)+'\n');
}finally{await browser.close();}
