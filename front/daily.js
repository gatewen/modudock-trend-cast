import prospective from './prospective.js';
import {writeMethods,plainMethods} from './method_names.js';
const CAP = '2021-12-31';
const allowedDay = d => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d) && d >= '2010-04-01' && d <= CAP;
const forbiddenDate = value => (JSON.stringify(value).match(/\d{4}-\d{2}-\d{2}/g) || []).some(d => d > CAP);
const safe = s => typeof s === 'string' ? s.slice(0, 350) : '';
const numeric = v => typeof v === 'number' && Number.isFinite(v);
const number = (v, n = 4) => numeric(v) ? v.toFixed(n) : '—';
const percent = v => numeric(v) ? `${(v * 100).toFixed(2)}%` : '—';
const count = v => Number.isSafeInteger(v) && v >= 0 ? v.toLocaleString('en-US') : '—';
const signed = v => numeric(v) ? `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(5)}` : '—';
const LABELS = {up: '漲', flat: '盤整', down: '跌'};
const NAMES = {ma_cross:'5／20 日均線', ma_trend:'60 日均線', rsi14:'RSI', kd:'KD', macd:'MACD', bollinger:'布林通道',
  bias20:'20 日乖離', vol_price:'價量', foreign_net:'外資近 3 日', trust_net:'投信近 3 日', margin_chg:'融資 5 日變化'};
const METHODS = ['always_flat', 'majority', 'momentum_H', 'reversal_H', 'vol_prior_d', ...Object.keys(NAMES).map(n => `ind_${n}`), 'ind_logit', 'jev_ind',
  'ens_avg', 'ind_mkt_trend', 'ind_mkt_ret5', 'ind_adr_premium', 'ind_sox_ret1', 'ind_sox_trend', 'mkt_logit'];
const PRIMARY = [[3,'ens_avg'],[7,'ens_avg'],[3,'mkt_logit']];
const ERRORS = {daily_experiment_missing:'尚未建立多日實驗。', daily_view_dev_only:'只開放開發段資料。',
  daily_view_invalid_date:'請選擇開發段的交易日期。', daily_view_incomplete:'開發段資料不完整。',
  busy:'讀取工作進行中，請稍後重新整理。', packet_too_large:'資料超出訊息上限。'};

export default function mountDaily(ctx) {
  const doc = ctx.container.ownerDocument, H = ctx.H;
  let live = false, disposed = false, serial = 0, selected = '', days = [], claims = [];
  const requests = {}, listeners = [];
  const el = (tag, text = '', cls = '') => { const n = doc.createElement(tag); writeMethods(n,text); n.className = cls; return n; };
  const on = (node, event, fn) => { node.addEventListener(event, fn); listeners.push(() => node.removeEventListener(event, fn)); };
  function send(op, fields = {}) {
    if (!live || disposed) return;
    const request_id = ++serial; requests[op] = request_id;
    ctx.channel.send({op, H, experiment_id:4, ...fields, request_id});
  }
  function button(text, fn) { const n = el('button', text); n.type = 'button'; n.disabled = true;
    on(n, 'click', () => { if (live && !disposed && !n.disabled) fn(); }); return n; }
  const root = el('section', '', 'tc tc-daily'); ctx.container.append(root);
  const head = el('header', '', 'tc-head'), title = el('div');
  title.append(el('p', 'TREND CAST / DAILY RESEARCH', 'tc-kicker'), el('h1', `${H} 個交易日後，漲、盤整或跌？`),
    el('p', '2330 台積電 · 多日預測（3／7／14 個交易日）', 'tc-sub'));
  const refresh = button('重新整理', () => load()); head.append(title, refresh);
  const status = el('div', '正在讀取多日實驗…', 'tc-status'); status.setAttribute('role', 'status');
  const news = el('p', '新聞廣播：未收到', 'tc-news tc-sub'); news.setAttribute('role', 'status');
  const error = el('p', '', 'tc-error'); error.hidden = true;
  const card = (heading, cls) => { const n = el('section', '', `tc-card ${cls}`); n.append(el('h2', heading)); return n; };
  const research = card('研究結論', 'tc-daily-research');
  const researchHold = el('p','保留段結論讀取中…');
  research.append(el('p','開發段有 3 組小幅入圍：ens_avg 3／7 日、mkt_logit 3 日；ens_avg 3 日屬邊緣。'),researchHold,
    el('p','jev_ind 在開發段顯著較差。前瞻紀錄自 2026-09-29 起累積；jev_news 仍在前瞻中。'),
    el('p','已測：11 個技術／籌碼指標、5 個大環境指標、2 個組合模型、ens_avg、jev_ind；另有 jev_news（前瞻中）。','tc-note'),
    el('p','這不是投資建議','tc-note'));
  const chartCard = card('日線走勢與抽樣預測', 'tc-daily-chart');
  const chartToolbar = el('div', '', 'tc-chart-head');
  const span = el('select'); span.setAttribute('aria-label', '走勢範圍'); span.disabled = true;
  for (const [v,t] of [['6m','近 6 個月'],['1y','近 1 年'],['all','全部開發段']]) { const n = el('option',t); n.value=v; span.append(n); }
  const chartRange = el('p', '', 'tc-sub'); chartToolbar.append(chartRange, span);
  const svg = (tag, attrs = {}, value = '') => { const n=doc.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v)); n.textContent=value; return n; };
  const chart = svg('svg', {viewBox:'0 0 1000 310',role:'img','aria-label':'開發段調整收盤指數與兩方法抽樣預測',class:'tc-chart'});
  const empty = el('p', '正在載入走勢…', 'tc-empty'); chart.hidden = true;
  const detail = el('p', '', 'tc-tooltip');
  const legend = el('div', '', 'tc-legend');
  legend.append(el('span','● 預測正確','tc-good'),el('span','● 預測錯誤','tc-bad'),el('span','● jev_ind　■ ind_logit'),el('span','預測點可點選日期；滑過看方向與對錯'));
  const chartNote = el('p', '調整收盤指數以開發段首日＝100；不含保留段或已曝光段。', 'tc-note');
  chartCard.append(chartToolbar, empty, chart, detail, legend, chartNote);
  const indicatorsCard = card('當日指標狀態', 'tc-daily-indicators');
  const dateNav = el('div', '', 'tc-date tc-toolbar');
  const previous = button('← 前一天', () => choose(days[days.indexOf(selected)-1]));
  const next = button('後一天 →', () => choose(days[days.indexOf(selected)+1]));
  const date = el('input'); date.type='date'; date.max=CAP; date.disabled=true; date.setAttribute('aria-label','指標日期');
  dateNav.append(previous,date,next);
  const indicators = el('div', '', 'tc-indicator-grid');
  indicatorsCard.append(dateNav, indicators, el('p','指標是當時可見的訊號，並非預測結論。籌碼只用前一交易日以前。','tc-note'));
  const reportCard = card(`${H} 日開發段成績`, 'tc-daily-scores'), report = el('div');
  const reportNote = el('p','開發段入圍只代表值得再驗證，不代表未來有效。jev_ind 在 3／7／14 日的 Brier 顯著較差；共同 576 日抽樣，與其他方法的完整開發段分列。','tc-note');
  reportCard.append(reportNote, report);
  const claimsCard = card('網路說法 vs 實際', 'tc-daily-claims');
  claimsCard.append(el('p','常見說法是待驗假說；下列為開發段實際頻率，未平滑，不代表未來勝率。','tc-note'));
  const claimSelect = el('select'); claimSelect.setAttribute('aria-label','檢視指標說法');
  for (const [k,v] of [['all','全部指標'],...Object.entries(NAMES)]) { const n=el('option',v); n.value=k; claimSelect.append(n); }
  claimSelect.value='kd'; const claimTable=el('div'); claimsCard.append(claimSelect,claimTable);
  const hold = card('多日保留段', 'tc-daily-holdout');
  const holdText=el('p','正在讀取保留段狀態…','tc-note'); hold.append(holdText);
  const holdResults=el('div'); hold.append(holdResults);
  const forward = prospective(doc,H);
  root.append(head,status,news,error,research,hold,forward.root,chartCard,indicatorsCard,reportCard,claimsCard);
  function controls() {
    refresh.disabled = !live; span.disabled = !live;
    date.disabled = !live || !days.length; previous.disabled = !live || days.indexOf(selected)<=0;
    next.disabled = !live || days.indexOf(selected)<0 || days.indexOf(selected)>=days.length-1;
  }
  function clearChart() { chart.replaceChildren(); chart.setAttribute('hidden',''); empty.hidden=false; empty.textContent='正在載入走勢…'; detail.textContent=''; }
  function choose(value) {
    if (!allowedDay(value) || !days.includes(value)) { date.value=selected; return; }
    selected=value; date.value=value; indicators.replaceChildren(el('p','載入指標…','tc-note'));
    controls(); send('daily_indicators',{date:value});
  }
  function load() {
    error.hidden=true; clearChart(); report.replaceChildren(el('p','正在計算開發段成績與區間…','tc-note'));
    send('daily_status'); send('daily_chart',{range:span.value}); send('daily_report'); send('daily_holdout'); send('daily_forward'); send('news_status');
  }
  on(date,'change',()=>choose(date.value));
  on(span,'change',()=>{clearChart();send('daily_chart',{range:span.value});});
  on(claimSelect,'change',()=>renderClaims());
  function table(headers, rows) {
    const wrap=el('div','','tc-scroll'), table=el('table'), head=el('thead'), tr=el('tr'), body=el('tbody');
    headers.forEach(v=>tr.append(el('th',v)));head.append(tr);
    rows.forEach(row=>{const tr=el('tr');tr.dataset.method=row[0];row.forEach(v=>tr.append(el('td',v)));body.append(tr);});
    table.append(head,body);wrap.append(table);return wrap;
  }
  function renderChart(body) {
    if (body.range!==span.value) return;
    const prices = Array.isArray(body.prices) ? body.prices.filter(p=>allowedDay(p.day)&&numeric(p.close)&&p.close>0) : [];
    chart.replaceChildren(); detail.textContent='';
    if (!prices.length) { empty.hidden=false;empty.textContent='沒有可顯示的開發段走勢。';chart.setAttribute('hidden','');return; }
    empty.hidden=true;chart.removeAttribute('hidden');
    chartRange.textContent=`${prices[0].day}～${prices.at(-1).day} · ${count(prices.length)} 個交易日`;
    const values=prices.map(p=>p.close), low=Math.min(...values),high=Math.max(...values),pad=Math.max((high-low)*.1,1);
    const x=i=>65+i/Math.max(prices.length-1,1)*910,y=v=>240-(v-low+pad)/(high-low+2*pad)*205;
    for(let i=0;i<=4;i++){const v=low-pad+(high-low+2*pad)*i/4;
      chart.append(svg('line',{x1:65,x2:975,y1:y(v),y2:y(v),class:'tc-gridline'}),svg('text',{x:54,y:y(v)+4,'text-anchor':'end'},number(v,1)));}
    const indices=[...new Set([0,Math.floor((prices.length-1)/3),Math.floor((prices.length-1)*2/3),prices.length-1])];
    indices.forEach(i=>chart.append(svg('text',{x:x(i),y:285,'text-anchor':i===0?'start':i===prices.length-1?'end':'middle'},prices[i].day)));
    chart.append(svg('polyline',{class:'tc-price',points:prices.map((p,i)=>`${x(i)},${y(p.close)}`).join(' ')}));
    const map=new Map(prices.map((p,i)=>[p.day,{...p,i}]));
    for(const p of Array.isArray(body.points)?body.points:[]) {
      if(!allowedDay(p.day)||!map.has(p.day)||!['jev_ind','ind_logit'].includes(p.method)||!Object.hasOwn(LABELS,p.choice))continue;
      const value=map.get(p.day),correct=p.correct===true?'correct':p.correct===false?'incorrect':'unknown';
      const info=plainMethods(`${p.day} · ${p.method} 猜${LABELS[p.choice]} · ${correct==='correct'?'正確':correct==='incorrect'?'錯誤':'尚無有效結果'}`);
      const g=svg('g',{class:'tc-daily-point','data-result':correct,'data-day':p.day,tabindex:0,role:'button','aria-label':info});
      g.dataset.detail=info;
      const xx=x(value.i),yy=y(value.close)+(p.method==='jev_ind'?-7:7),r=span.value==='all'?2.8:5;
      g.append(svg('title',{},info),p.method==='jev_ind'?svg('circle',{cx:xx,cy:yy,r}):svg('rect',{x:xx-r,y:yy-r,width:r*2,height:r*2,rx:1}));chart.append(g);
    }
    detail.textContent='滑過預測點可查看方向與對錯；點選即可切換指標日期。';
  }
  const showPoint=e=>{const p=e.target.closest?.('.tc-daily-point');if(p)detail.textContent=p.dataset.detail;};
  on(chart,'mouseover',showPoint);on(chart,'focusin',showPoint);
  on(chart,'click',e=>{const p=e.target.closest?.('.tc-daily-point');if(p)choose(p.dataset.day);});
  on(chart,'keydown',e=>{if(['Enter',' '].includes(e.key)){const p=e.target.closest?.('.tc-daily-point');if(p){e.preventDefault();choose(p.dataset.day);}}});
  function renderClaims() {
    const chosen=claims.filter(r=>claimSelect.value==='all'||r.indicator===claimSelect.value);
    claimTable.replaceChildren(table(['指標／訊號','常見說法','樣本','實際漲','盤整','跌','提醒'],chosen.map(r=>[
      `${NAMES[r.indicator]} · ${safe(r.description)}`,safe(r.claim),count(r.n),percent(r.frequencies?.up),percent(r.frequencies?.flat),percent(r.frequencies?.down),r.n<30?'樣本太少':'描述性頻率'])));
  }
  function renderHoldout(body) {
    if(body.H!==H||body.experiment_id!==4||body.split!=='holdout'||body.status!=='ok'||body.request_id!==requests.daily_holdout)return;
    if(body.state==='locked') {
      if(!holdResults.children.length) {
        holdText.textContent='尚未揭露（不顯示保留段成績）';
        researchHold.textContent='保留段尚未揭露，暫不顯示結論。';
      }
      return;
    }
    if(body.state!=='used'||body.first_day!=='2022-01-03'||body.last_day!=='2024-07-25')return;
    const rows=PRIMARY.map(([h,m])=>body.primary_comparisons?.find(r=>r.H===h&&r.method===m));
    if(rows.some(r=>!r)||body.primary_comparisons.length!==3)return;
    holdText.textContent='已使用（一次性保留段考試已揭露）';
    researchHold.textContent='保留段（2022-01～2024-07）只能用一次、已使用：三者都沒有證據比「猜最常見答案」好。';
    holdResults.replaceChildren(el('p','2022-01～2024-07 · 所有方法固定使用開發段最後狀態。以下 3 個主要比較跨天期同時列出。','tc-note'),
      table(['主要比較','樣本','Brier 差','95% 區間','結論'],rows.map(r=>[
        `${r.method} ${r.H} 日 − majority`,count(r.n),signed(r.difference),
        Array.isArray(r.ci95)?r.ci95.map(signed).join(' ～ '):'—',safe(r.verdict)])));
    holdResults.append(el('p','差為考生 − majority，負值較好；20 交易日區塊 bootstrap 2,000 次、固定種子。','tc-note'),
      el('p',safe(body.multiplicity),'tc-note'));
    const details=el('details'),description=el('summary',`展開 ${H} 日全部方法的描述性成績`);
    const descriptive=Array.isArray(body.descriptive)?body.descriptive.filter(r=>METHODS.includes(r.method)&&r.method!=='jev_ind'):[];
    details.append(description,el('p','只描述歷史成績，不作優劣結論，不從此表再篩選方法。','tc-note'),
      table(['方法','樣本','準確率','Brier（越低越好）','覆蓋率','缺答'],descriptive.map(r=>[
        r.method,count(r.n),percent(r.accuracy),number(r.brier,6),percent(r.coverage),count(r.missing)])));
    holdResults.append(details);
  }
  ctx.channel.onMessage(body=>{
    if(disposed||!body||typeof body!=='object')return;
    if(body.op==='news_changed'){send('news_status');return;}
    if(body.op==='news_status') {
      if(body.request_id!==requests.news_status||body.status!=='ok'||typeof body.jev_news_enabled!=='boolean')return;
      const at=body.received_at;
      if(at===null)news.textContent='新聞廣播：未收到';
      else if(typeof at==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+08:00$/.test(at)&&Number.isFinite(Date.parse(at)))
        writeMethods(news,`新聞廣播：最後收到時間 ${at.slice(0,19).replace('T',' ')}（台北） · jev_news ${body.jev_news_enabled?'已啟用（需當日快照）':'未啟用'}`);
      return;
    }
    if(body.op==='daily_forward_changed'){send('daily_forward');return;}
    if(body.op==='daily_forward'){if(body.request_id===requests.daily_forward)forward.render(body);return;}
    if(body.op==='daily_holdout'){renderHoldout(body);return;}
    if(forbiddenDate(body))return;
    if(body.op==='error') {
      if(!Object.values(requests).includes(body.request_id))return;
      error.textContent=ERRORS[body.code]||'多日資料讀取失敗，請重新整理。';error.hidden=false;return;
    }
    if(body.H!==H||body.experiment_id!==4||body.split!=='dev'||body.status!=='ok'||body.request_id!==requests[body.op])return;
    if(['daily_status','daily_report'].includes(body.op)&&body.holdout?.state==='used')holdText.textContent='已使用（一次性保留段考試已揭露）';
    if(body.op==='daily_status') {
      if(!allowedDay(body.dev_start)||!allowedDay(body.dev_end))return;
      days=Array.isArray(body.days)?body.days.filter(allowedDay):[];
      status.textContent=`實驗 4 · 開發段 ${body.dev_start}～${body.dev_end} · 盤整門檻 ±${percent(body.threshold)} · 唯讀檢視`;
      date.min=days[0]||'2010-04-01';date.max=days.at(-1)||CAP;
      if(days.length)choose(days.includes(selected)?selected:days.at(-1));controls();
    } else if(body.op==='daily_chart')renderChart(body);
    else if(body.op==='daily_indicators') {
      if(!allowedDay(body.day)||body.day!==selected)return;
      indicators.replaceChildren();
      for(const item of Array.isArray(body.indicators)?body.indicators:[]) {
        if(!Object.hasOwn(NAMES,item.name))continue;
        const cell=el('article','','tc-indicator');cell.dataset.indicator=item.name;
        cell.append(el('h3',NAMES[item.name]),el('strong',safe(item.description)),el('p',safe(item.detail),'tc-sub'));indicators.append(cell);
      }
    } else if(body.op==='daily_report') {
      const rows=Array.isArray(body.methods)?body.methods.filter(r=>METHODS.includes(r.method)):[];
      report.replaceChildren(table(['方法','樣本','準確率','Brier','差（−基準）','95% 區間','入圍'],rows.map(r=>[
        r.method,count(r.n),percent(r.accuracy),number(r.brier,6),number(r.difference,6),
        Array.isArray(r.ci95)?r.ci95.map(v=>number(v,6)).join(' ～ '):'—',safe(r.verdict)])));
      report.append(el('p',safe(body.comparison_note),'tc-note'),el('p',safe(body.multiplicity),'tc-note'));
      claims=Array.isArray(body.claims)?body.claims.filter(r=>Object.hasOwn(NAMES,r.indicator)):[];renderClaims();
    }
  });
  ctx.onUp(()=>{if(live||disposed)return;live=true;controls();load();});
  ctx.report('ready');
  return {unmount(){if(disposed)return;disposed=true;live=false;listeners.forEach(fn=>fn());root.remove();}};
}
