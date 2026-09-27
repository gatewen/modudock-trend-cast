// Read-only acceptance. The shell is launched separately with SSL_CERT_FILE unset.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || new URL('../../../modudock/web/node_modules/playwright/index.mjs',import.meta.url).href);
const url=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(url||''))throw new Error('explicit loopback URL required');
const out=new URL('../docs/verification/',import.meta.url);
const permitted=['daily_status','daily_chart','daily_indicators','daily_report','status','day','report'];
const evidence={url,catalog:[],states:[],requests:[],daily_packets:0,max_daily_packet_bytes:0,page_errors:[],themes:{},horizons:{},ssl_cert_file_unset:true};
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1560,height:1800},locale:'zh-TW'});
await page.addInitScript(ops=>{
  const send=WebSocket.prototype.send;
  WebSocket.prototype.send=function(raw){const p=JSON.parse(raw);
    if(p.mod==='trend-cast'&&p.t==='msg'&&!ops.includes(p.body.op))throw new Error('acceptance_readonly');
    return send.call(this,raw);};
},permitted);
page.on('pageerror',()=>evidence.page_errors.push('page_error'));
page.on('websocket',socket=>{
  socket.on('framesent',frame=>{const p=JSON.parse(frame.payload.toString());if(p.mod==='trend-cast'&&p.t==='msg')evidence.requests.push(p.body.op);});
  socket.on('framereceived',frame=>{
    const raw=frame.payload.toString(),p=JSON.parse(raw);
    if(p.t==='catalog')evidence.catalog=p.modules.filter(m=>m.id==='trend-cast').map(m=>m.id);
    if(p.mod!=='trend-cast')return;
    if(p.t==='state')evidence.states.push(p.state);
    if(p.t==='msg'&&p.body.op?.startsWith('daily_')){
      assert.ok((raw.match(/\d{4}-\d{2}-\d{2}/g)||[]).every(d=>d<='2021-12-31'));
      evidence.daily_packets++;evidence.max_daily_packet_bytes=Math.max(evidence.max_daily_packet_bytes,Buffer.byteLength(raw));
      if(p.body.op==='daily_report'){
        assert.equal(p.body.methods.length,18);assert.deepEqual(p.body.shortlist,[]);
        evidence.horizons[p.body.H]={methods:18,jev_n:p.body.methods.find(r=>r.method==='jev_ind').n,holdout:p.body.holdout.state};
      }
    }
  });
});
async function ready(H){
  await page.locator('.tc-daily-scores tbody tr').first().waitFor({timeout:90000});
  await page.locator('.tc-indicator').first().waitFor({timeout:90000});
  assert.equal(await page.locator('.tc-daily-scores tbody tr').count(),18);
  assert.equal(await page.locator('.tc-indicator').count(),11);
  assert.equal(await page.getByLabel('預測天期',{exact:true}).inputValue(),String(H));
  assert.ok(await page.locator('.tc-daily-point').count()>0);
  assert.ok((await page.getByLabel('指標日期',{exact:true}).getAttribute('max'))<='2021-12-31');
  assert.match(await page.locator('.tc-daily-holdout').innerText(),/未使用（沒有入圍者，保留給未來）/);
  assert.equal(await page.locator('.tc-daily-holdout button').count(),0);
  const text=await page.locator('.tc-shell').innerText();
  assert.ok((text.match(/\d{4}-\d{2}-\d{2}/g)||[]).every(d=>d<='2021-12-31'));
}
try{
  await page.goto(url);await page.locator('[data-mod="trend-cast"] .load').click();await ready(7);
  assert.ok(evidence.catalog.includes('trend-cast'));assert.ok(evidence.states.includes('running'));
  for(const H of [3,14,7]){await page.getByLabel('預測天期',{exact:true}).selectOption(String(H));await ready(H);}
  const ranges={};
  for(const [range,minimum] of [['all',1000],['1y',200],['6m',80]]){
    await page.getByLabel('走勢範圍',{exact:true}).selectOption(range);
    await page.waitForFunction(()=>document.querySelector('.tc-daily-chart .tc-price'));
    const count=await page.locator('.tc-daily-chart .tc-price').evaluate(el=>el.getAttribute('points').split(' ').length);
    assert.ok(count>minimum);ranges[range]=count;
  }
  evidence.chart_sessions=ranges;
  const marker=page.locator('.tc-daily-point').first();const selected=await marker.getAttribute('data-day');await marker.click();
  await page.waitForFunction(day=>document.querySelector('[aria-label="指標日期"]').value===day,selected);
  await page.locator('.tc-indicator').first().waitFor();
  evidence.selected_indicator_day=selected;
  await page.getByLabel('檢視指標說法',{exact:true}).selectOption('all');
  assert.ok(await page.locator('.tc-daily-claims tbody tr').count()>40);
  await page.getByLabel('檢視指標說法',{exact:true}).selectOption('kd');
  assert.match(await page.locator('.tc-daily-claims').innerText(),/樣本太少/);
  // Preserve and exercise the existing 30-minute view, then remove it entirely.
  await page.getByLabel('預測天期',{exact:true}).selectOption('30m');
  await page.locator('.tc-point').first().waitFor({timeout:90000});
  assert.equal(await page.locator('.tc-point').count(),8);assert.equal(await page.locator('.tc-forward').count(),1);
  evidence.intraday_unchanged=true;
  await page.getByLabel('預測天期',{exact:true}).selectOption('7');await ready(7);
  const contentHeight=await page.locator('.tc-shell').evaluate(el=>el.scrollHeight);
  await page.setViewportSize({width:1560,height:Math.ceil(contentHeight+650)});
  for(const mode of ['light','dark']){
    await page.getByLabel('主題模式',{exact:true}).selectOption(mode);
    assert.equal(await page.locator('html').getAttribute('data-theme'),mode);
    evidence.themes[mode]=await page.locator('.tc-shell').evaluate(el=>({color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,width:el.clientWidth,scrollWidth:el.scrollWidth}));
    assert.ok(evidence.themes[mode].scrollWidth<=evidence.themes[mode].width);
    await page.locator('.tc-shell').screenshot({path:fileURLToPath(new URL(`evolve-8-${mode}.png`,out))});
  }
  assert.notEqual(evidence.themes.light.background,evidence.themes.dark.background);
  assert.deepEqual(evidence.page_errors,[]);assert.ok(evidence.requests.every(op=>permitted.includes(op)));
  await writeFile(new URL('evolve-8-shell-evidence.json',out),JSON.stringify(evidence,null,2)+'\n');
  console.log(JSON.stringify(evidence));
}finally{await browser.close();}
