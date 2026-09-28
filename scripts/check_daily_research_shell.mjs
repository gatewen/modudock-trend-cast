import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE);
const [url,out]=process.argv.slice(2);
assert.match(url,/^http:\/\/127\.0\.0\.1:\d+$/);
const browser=await chromium.launch({headless:true});
const evidence={url,states:[],requests:[],responses:[],page_errors:[],horizons:{},themes:{},rapid_sequence:['3','7','14','3']};
const image=(name)=>out.replace('shell-browser.json',`${name}.png`);
try {
  const page=await browser.newPage({viewport:{width:1440,height:1100},locale:'zh-TW'});
  page.on('pageerror',e=>evidence.page_errors.push(e.message));
  page.on('websocket',s=>{
    s.on('framereceived',f=>{const p=JSON.parse(f.payload.toString());if(p.mod==='trend-cast'&&p.t==='state')evidence.states.push(p.state);
      if(p.mod==='trend-cast'&&p.t==='msg')evidence.responses.push({op:p.body.op,status:p.body.status,H:p.body.H,code:p.body.code});});
    s.on('framesent',f=>{const p=JSON.parse(f.payload.toString());if(p.mod==='trend-cast'&&p.t==='msg'){
      assert.ok(['daily_status','daily_chart','daily_report','daily_holdout','daily_indicators','daily_forward','news_status'].includes(p.body.op));
      evidence.requests.push(p.body.op);
    }});
  });
  await page.goto(url);await page.locator('[data-mod="trend-cast"] .load').click();
  // Deliberately switch before development/holdout/forward reads have returned.
  // There is no wait for a response or a loaded card between these changes.
  for(const H of evidence.rapid_sequence)await page.getByLabel('預測天期').selectOption(H);
  evidence.immediately_after_rapid=await page.locator('.tc-daily').evaluate(n=>({
    title:n.querySelector('h1').textContent,development_rows:n.querySelectorAll('.tc-daily-scores tbody tr').length,
    forward_still_loading:n.querySelector('.tc-forward-latest').textContent.includes('正在讀取'),
  }));
  assert.equal(evidence.immediately_after_rapid.development_rows,0);
  assert.equal(evidence.immediately_after_rapid.forward_still_loading,true);
  for(const H of ['3','7','14','3']) {
    if(await page.getByLabel('預測天期').inputValue()!==H)await page.getByLabel('預測天期').selectOption(H);
    await page.waitForFunction(()=>document.querySelectorAll('.tc-daily-scores tbody tr').length===25
      &&document.querySelectorAll('.tc-daily-holdout details tbody tr').length===24
      &&document.querySelectorAll('.tc-daily-indicators .tc-indicator').length===11
      &&!document.querySelector('.tc-forward-latest')?.textContent.includes('正在讀取'),{},{timeout:40000});
    const text=await page.locator('.tc-daily-holdout').innerText();
    for(const s of ['已使用','組合預測','（ens_avg）','大環境組合模型','（mkt_logit）','猜最常見答案','（majority）','−0.00008','−0.00405','+0.00592','0.075'])assert.ok(text.includes(s),s);
    assert.equal(await page.locator('.tc-daily-holdout button').count(),0);
    assert.equal(await page.locator('.tc-daily-holdout details').getAttribute('open'),null);
    const dev=await page.locator('.tc-daily-scores').innerText();
    for(const m of ['ens_avg','ind_mkt_trend','ind_mkt_ret5','ind_adr_premium','ind_sox_ret1','ind_sox_trend','mkt_logit'])assert.ok(dev.includes(m));
    const shortlisted=await page.locator('.tc-daily-scores tbody tr').evaluateAll(rows=>rows.filter(r=>r.lastElementChild.textContent.startsWith('入圍')).map(r=>r.dataset.method));
    assert.deepEqual(shortlisted,H==='3'?['ens_avg','mkt_logit']:H==='7'?['ens_avg']:[]);
    evidence.horizons[H]={shortlisted,holdout:text,development_rows:25,descriptive_rows:24};
    assert.match(await page.locator('.tc-daily h1').innerText(),new RegExp(`^${H} 個交易日後`));
    const logit=await page.locator('.tc-daily-scores tr[data-method="mkt_logit"]').innerText();
    if(H==='3'){assert.match(logit,/2,891/);assert.match(logit,/0.660868/);}
    assert.equal(await page.locator('.tc-daily-scores tbody tr > td:first-child small').count(),25);
    assert.ok((await page.getByLabel('指標日期').inputValue())<='2021-12-31');
    assert.equal(await page.locator('.tc-error:visible').count(),0);
  }
  for(const mode of ['light','dark']) {
    await page.setViewportSize({width:1440,height:1100});
    await page.getByLabel('主題模式',{exact:true}).selectOption(mode);
    await page.waitForFunction(m=>document.documentElement.dataset.theme===m,mode);
    evidence.themes[mode]=await page.locator('.tc-daily-research').evaluate(n=>{
      const s=getComputedStyle(n);return {color:s.color,background:s.backgroundColor,colorScheme:s.colorScheme};
    });
    assert.equal(evidence.themes[mode].colorScheme,mode);
    await page.locator('.tc-horizon').scrollIntoViewIfNeeded();
    await page.screenshot({path:image(`${mode}-summary`)});
    await page.locator('.tc-daily-holdout summary').click();
    assert.notEqual(await page.locator('.tc-daily-holdout details').getAttribute('open'),null);
    // Let the actual shell pane fit a whole card before locator capture; implicit
    // screenshot resizing would otherwise clip content inside the scroll pane.
    await page.setViewportSize({width:1440,height:3000});
    await page.locator('.tc-daily-holdout').scrollIntoViewIfNeeded();
    await page.locator('.tc-daily-holdout').screenshot({path:image(`${mode}-holdout`)});
    await page.locator('.tc-daily-holdout summary').click();
    await page.locator('.tc-daily-scores').scrollIntoViewIfNeeded();
    assert.ok(await page.locator('.tc-daily-scores .tc-scroll').evaluate(n=>n.scrollWidth<=n.clientWidth+1),'development table fits the shell pane');
    await page.locator('.tc-daily-scores').screenshot({path:image(`${mode}-development`)});
    assert.equal(await page.locator('.tc-error:visible').count(),0);
  }
  assert.notEqual(evidence.themes.light.background,evidence.themes.dark.background);
  for(const file of ['data/trendcast.sqlite3','back/daily_hold_run.py']) {
    const response=await page.request.get(`${url}/modules/trend-cast/${file}`);assert.equal(response.status(),404);
  }
  assert.ok(evidence.states.includes('running'));assert.deepEqual(evidence.page_errors,[]);
  assert.deepEqual(evidence.responses.filter(r=>r.op==='error'),[]);
  evidence.private_routes='404';await writeFile(out,JSON.stringify(evidence,null,2)+'\n');
}finally{await writeFile(out,JSON.stringify(evidence,null,2)+'\n');await browser.close();}
