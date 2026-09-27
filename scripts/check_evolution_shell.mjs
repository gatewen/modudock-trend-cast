// Real shell acceptance: status/report/dev-day only, never run or reveal.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || new URL('../../../modudock/web/node_modules/playwright/index.mjs',import.meta.url).href);
const url=process.argv[2];
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(url || ''))throw new Error('explicit loopback shell URL required');
const out=new URL('../docs/verification/',import.meta.url);
const evidence={url,catalog:[],states:[],requests:[],page_errors:[],themes:{},reveal_sent:false};
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1560,height:1900},locale:'zh-TW'});
await page.addInitScript(()=>{
  const send=WebSocket.prototype.send;
  WebSocket.prototype.send=function(raw){
    const p=JSON.parse(raw);
    if(p.mod==='trend-cast'&&p.t==='msg'&&!['status','report','day'].includes(p.body.op))throw new Error('acceptance_readonly');
    return send.call(this,raw);
  };
});
page.on('pageerror',()=>evidence.page_errors.push('page_error'));
page.on('websocket',socket=>{
  socket.on('framesent',frame=>{
    const p=JSON.parse(frame.payload.toString());
    if(p.mod==='trend-cast'&&p.t==='msg')evidence.requests.push(p.body.op);
  });
  socket.on('framereceived',frame=>{
    const p=JSON.parse(frame.payload.toString());
    if(p.t==='catalog')evidence.catalog=p.modules.filter(m=>m.id==='trend-cast').map(m=>m.id);
    if(p.mod!=='trend-cast')return;
    if(p.t==='state')evidence.states.push(p.state);
    if(p.t==='msg'&&p.body.op==='report'&&p.body.experiment_id===1){
      assert.deepEqual(p.body.forward,{state:'locked',message:'前瞻段未揭露'});
      assert.equal(p.body.evolution_dev.state,'ready');
      evidence.forward_report_locked=true;
    }
    if(p.t==='msg'&&p.body.op==='status'&&p.body.experiment_id===1&&p.body.forward){
      assert.equal(p.body.forward.state,'locked');
      evidence.forward={state:p.body.forward.state,run_points:p.body.forward.run_points,participants:p.body.forward.participants};
    }
  });
});
try{
  await page.goto(url);
  await page.locator('[data-mod="trend-cast"] .load').click();
  await page.locator('.tc-evolution tbody tr').first().waitFor({timeout:90000});
  assert.equal(await page.locator('.tc-evolution tbody tr').count(),3);
  assert.equal(await page.locator('.tc-point').count(),8);
  assert.match(await page.locator('.tc-evolution').innerText(),/開發段勝出＝值得前瞻驗證，不是證明有效/);
  assert.match(await page.locator('.tc').innerText(),/考生：jev p1、vol_prior、always_flat、majority、momentum、reversal/);
  assert.match(await page.locator('.tc').innerText(),/已跑 144 點（六方法共同完成）/);
  assert.match(await page.locator('.tc-lock').innerText(),/保留段已使用/);
  assert.equal(evidence.forward.state,'locked');
  assert.equal(evidence.forward.run_points,144);
  assert.ok(evidence.catalog.includes('trend-cast'));assert.ok(evidence.states.includes('running'));
  // The shell scrolls its main pane independently. Give it enough height so
  // element screenshots include the whole module rather than clipped blanks.
  const contentHeight=await page.locator('.tc').evaluate(el=>el.scrollHeight);
  await page.setViewportSize({width:1560,height:Math.ceil(contentHeight*1.8+400)});
  for(const mode of ['light','dark']){
    await page.getByLabel('主題模式',{exact:true}).selectOption(mode);
    assert.equal(await page.locator('html').getAttribute('data-theme'),mode);
    evidence.themes[mode]=await page.locator('.tc').evaluate(el=>({
      color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
      width:el.clientWidth,scrollWidth:el.scrollWidth,evolution_rows:el.querySelectorAll('.tc-evolution tbody tr').length,
    }));
    assert.ok(evidence.themes[mode].scrollWidth<=evidence.themes[mode].width);
    await page.locator('.tc').screenshot({path:fileURLToPath(new URL(`evolve-3-${mode}.png`,out))});
  }
  assert.notEqual(evidence.themes.light.background,evidence.themes.dark.background);
  assert.deepEqual(evidence.page_errors,[]);
  assert.ok(evidence.requests.every(op=>['status','report','day'].includes(op)));
  await writeFile(new URL('evolve-3-shell-evidence.json',out),JSON.stringify(evidence,null,2)+'\n');
  console.log(JSON.stringify({catalog:evidence.catalog,running:true,forward:evidence.forward,
    evolution_rows:3,themes:Object.keys(evidence.themes),page_errors:evidence.page_errors,reveal_sent:false}));
}finally{await browser.close();}
