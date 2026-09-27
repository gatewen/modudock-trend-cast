import test from 'node:test';
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
import mount from '../front/front.js';

function setup(t) {
  const window=new Window(),container=window.document.createElement('div');window.document.body.append(container);
  const sent=[],reports=[];let message,up;
  const handle=mount({container,channel:{send(b){sent.push(b);},onMessage(fn){message=fn;}},onUp(fn){up=fn;},report(s){assert.equal(typeof up,'function');assert.equal(typeof message,'function');reports.push(s);}});
  t.after(async()=>{handle.unmount();await window.happyDOM.close();});
  const find=label=>container.querySelector(`[aria-label="${label}"]`);
  const h={window,container,sent,reports,handle,message,up,find,
    latest(op){return sent.filter(b=>b.op===op).at(-1);},
    change(label,value){const e=find(label);e.value=value;e.dispatchEvent(new window.Event('change'));},
    answer(op,body={}){const r=this.latest(op);message({op,H:r.H,experiment_id:4,split:'dev',status:'ok',request_id:r.request_id,...body});}};
  return h;
}
const days=['2021-12-01','2021-12-02','2021-12-03'];
function ready(h) {h.up();h.answer('daily_status',{dev_start:'2010-04-01',dev_end:'2021-12-31',threshold:.02,days});}
function chart(h, extra={}){h.answer('daily_chart',{range:'6m',prices:days.map((day,i)=>({day,close:100+i})),
  points:[{day:days[0],method:'jev_ind',choice:'up',correct:true},{day:days[0],method:'ind_logit',choice:'flat',correct:false},
    {day:days[1],method:'jev_ind',choice:'down',correct:'true'}],...extra});}
const names=['ma_cross','ma_trend','rsi14','kd','macd','bollinger','bias20','vol_price','foreign_net','trust_net','margin_chg'];
const methods=['always_flat','majority','momentum_H','reversal_H','vol_prior_d',...names.map(n=>`ind_${n}`),'ind_logit','jev_ind'];
function report(h,extra={}) {h.answer('daily_report',{methods:methods.map(method=>({method,n:method==='jev_ind'?576:2890,accuracy:.4,brier:.65,difference:.01,ci95:[-.02,.03],verdict:'未入圍'})),
  comparison_note:'各列與 majority 同日期比較；jev_ind 僅 576 天。',multiplicity:'11×2.5%＝0.275',
  claims:[{indicator:'kd',description:'低檔黃金交叉',claim:'偏多',n:29,frequencies:{up:.5,flat:.3,down:.2}},
    {indicator:'kd',description:'高檔',claim:'無預設方向',n:30,frequencies:{up:.4,flat:.3,down:.3}}],...extra});}

test('daily default seven days registers before ready and sends only reads after up',t=>{
  const h=setup(t);assert.deepEqual(h.reports,['ready']);assert.equal(h.find('預測天期').value,'7');
  assert.equal(h.sent.length,0);h.up();h.up();
  assert.deepEqual(h.sent.map(b=>b.op),['daily_status','daily_chart','daily_report','daily_forward','news_status']);
  assert.ok(h.sent.every(b=>b.H===7));assert.equal(h.container.querySelectorAll('.tc-forward').length,0);
  assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/未使用（沒有入圍者，保留給未來）/);
  assert.equal(h.container.querySelector('.tc-daily-holdout button'),null);
});

test('news status absent publisher remains usable and timestamp refresh is text only',t=>{
  const h=setup(t);ready(h);chart(h);
  assert.ok(h.latest('news_status'));
  assert.equal(h.container.querySelector('.tc-news').textContent,'新聞廣播：未收到');
  h.answer('news_status',{received_at:null,jev_news_enabled:false});
  assert.ok(h.container.querySelector('.tc-price'));
  h.message({op:'news_changed'});
  h.answer('news_status',{received_at:'2026-09-29T13:29:00.000000+08:00',jev_news_enabled:false});
  const line=h.container.querySelector('.tc-news');
  assert.match(line.textContent,/最後收到時間 2026-09-29 13:29:00（台北）.*jev_news 未啟用/);
  assert.doesNotMatch(h.container.querySelector('.tc-daily-chart').textContent,/2026-09-29/);
  const saved=line.textContent;
  h.message({op:'news_changed'});h.answer('news_status',{received_at:'<img src=x onerror=alert(1)>',jev_news_enabled:false});
  assert.equal(line.textContent,saved);assert.equal(line.querySelector('img'),null);
  assert.ok(h.sent.every(p=>!p.op.startsWith('run')));
});

test('news status rejects late epoch replies and mismatched operation',t=>{
  const h=setup(t);ready(h);const old=h.latest('news_status');
  h.change('預測天期','3');const current=h.latest('news_status');
  const body={op:'news_status',status:'ok',received_at:'2026-09-29T13:29:00+08:00',jev_news_enabled:false};
  h.message({...body,request_id:old.request_id});
  assert.equal(h.container.querySelector('.tc-news').textContent,'新聞廣播：未收到');
  h.message({...body,request_id:h.latest('daily_chart').request_id});
  assert.equal(h.container.querySelector('.tc-news').textContent,'新聞廣播：未收到');
  h.message({...body,request_id:current.request_id});
  assert.match(h.container.querySelector('.tc-news').textContent,/最後收到時間/);
  h.change('預測天期','30m');const n=h.sent.length;h.message({op:'news_changed'});assert.equal(h.sent.length,n);
});

test('daily chart shape and exact boolean colors show both sampled methods',t=>{
  const h=setup(t);ready(h);chart(h);
  const points=[...h.container.querySelectorAll('.tc-daily-point')];assert.equal(points.length,3);
  assert.deepEqual(points.map(p=>p.dataset.result),['correct','incorrect','unknown']);
  assert.ok(points[0].querySelector('circle'));assert.ok(points[1].querySelector('rect'));
  points[0].dispatchEvent(new h.window.Event('focusin',{bubbles:true}));
  assert.match(h.container.querySelector('.tc-tooltip').textContent,/2021-12-01 · jev_ind 猜漲 · 正確/);
  points[0].dispatchEvent(new h.window.MouseEvent('click',{bubbles:true}));
  assert.equal(h.latest('daily_indicators').date,days[0]);
});

test('daily bounded date controls eleven plain states and invalid dates never request',t=>{
  const h=setup(t);ready(h);assert.equal(h.latest('daily_indicators').date,days.at(-1));
  assert.ok(h.find('指標日期').max<='2021-12-31');
  h.answer('daily_indicators',{day:days.at(-1),indicators:names.map(name=>({name,description:name==='kd'?'高檔':'中性',detail:name==='kd'?'K 94.0 · D 90.0':'0.00%'}))});
  assert.equal(h.container.querySelectorAll('.tc-indicator').length,11);assert.match(h.container.querySelector('[data-indicator=kd]').textContent,/高檔K 94.0/);
  const n=h.sent.length;h.change('指標日期','2022-01-03');h.change('指標日期','2024-07-26');
  assert.equal(h.sent.length,n);assert.equal(h.find('指標日期').value,days.at(-1));
  assert.doesNotMatch(h.container.textContent,/2022-01-03|2024-07-26/);
});

test('daily all methods sample sizes comparison intervals and n under thirty warning',t=>{
  const h=setup(t);ready(h);report(h);
  assert.equal(h.container.querySelectorAll('.tc-daily-scores tbody tr').length,18);
  assert.match(h.container.querySelector('tr[data-method=jev_ind]').textContent,/576.*40.00%.*0.650000.*0.010000.*-0.020000 ～ 0.030000.*未入圍/);
  const rows=h.container.querySelectorAll('.tc-daily-claims tbody tr');assert.equal(rows.length,2);
  assert.match(rows[0].textContent,/樣本太少/);assert.doesNotMatch(rows[1].textContent,/樣本太少/);
  assert.match(h.container.querySelector('.tc-daily-scores').textContent,/11×2.5%/);
});

test('daily horizon and range switching discard late replies including earlier mount epoch',t=>{
  const h=setup(t);ready(h);const old=h.latest('daily_chart');chart(h);assert.ok(h.container.querySelector('.tc-price'));
  h.change('走勢範圍','all');assert.equal(h.container.querySelector('.tc-price'),null);
  h.message({op:'daily_chart',H:7,experiment_id:4,split:'dev',status:'ok',request_id:old.request_id,range:'6m',prices:[{day:days[0],close:999}]});
  assert.equal(h.container.querySelector('.tc-price'),null);
  h.change('預測天期','3');assert.equal(h.latest('daily_report').H,3);
  h.change('預測天期','14');assert.equal(h.latest('daily_report').H,14);
  h.change('預測天期','7');
  h.message({op:'daily_report',H:7,experiment_id:4,split:'dev',status:'ok',request_id:old.request_id,methods:[{method:'jev_ind',verdict:'STALE'}]});
  assert.doesNotMatch(h.container.textContent,/STALE/);
});

test('daily rejects future dates at every outlet and ignores legacy status/report',t=>{
  const h=setup(t);ready(h);
  h.message({op:'status',experiment_id:1,days:['2026-09-24'],dev_start:'2024-09-05',dev_end:'2026-01-21'});
  chart(h,{prices:[{day:'2024-07-26',close:123}],points:[{day:'2022-01-03',method:'jev_ind',choice:'up',correct:true}]});
  assert.equal(h.container.querySelectorAll('.tc-daily-point').length,0);
  h.answer('daily_indicators',{day:'2022-01-03',indicators:[{name:'kd',description:'PRIVATE'}]});
  assert.doesNotMatch(h.container.textContent,/2022-01-03|2024-07-26|2026-09-24|PRIVATE/);
  report(h,{comparison_note:'PRIVATE 2024-07-26'});
  assert.doesNotMatch(h.container.textContent,/PRIVATE|2024-07-26/);
  report(h,{split:'holdout',methods:[{method:'jev_ind',verdict:'PRIVATE'}]});
  assert.doesNotMatch(h.container.textContent,/PRIVATE/);
});

test('thirty minute mode keeps legacy interface and switching back clears all its dates',t=>{
  const h=setup(t);ready(h);h.change('預測天期','30m');
  assert.equal(h.container.querySelectorAll('.tc-daily').length,0);assert.ok(h.container.querySelector('.tc-forward'));
  assert.equal(h.latest('report').op,'report');assert.equal(h.latest('status').op,'status');
  h.message({op:'status',status:'ok',experiment_id:1,days:['2026-01-01'],dev_start:'2024-09-05',dev_end:'2026-01-21',threshold_permille:3,holdout:{state:'locked'},keys:{},busy:{}});
  assert.match(h.container.textContent,/2026-01-21/);
  h.change('預測天期','7');assert.doesNotMatch(h.container.textContent,/2026-01-21|2024-09-05/);
  assert.equal(h.container.querySelectorAll('.tc-forward').length,0);assert.ok(h.container.querySelector('.tc-daily'));
});

test('daily strings stay text and unmount stops messages and leaves no UI',t=>{
  const h=setup(t);ready(h);report(h,{comparison_note:'<b data-injected>unsafe</b>'});
  assert.equal(h.container.querySelectorAll('[data-injected]').length,0);assert.match(h.container.textContent,/<b data-injected>/);
  h.handle.unmount();const n=h.sent.length;h.up();h.message({op:'status'});assert.equal(h.sent.length,n);assert.equal(h.container.children.length,0);
});


test('legacy action status acknowledgement survives channel routing after confirmation',t=>{
  const h=setup(t);h.up();h.change('預測天期','30m');
  const status={op:'status',status:'ok',experiment_id:1,days:['2026-01-01'],dev_start:'2024-09-05',dev_end:'2026-01-21',threshold_permille:3,holdout:{state:'locked'},keys:{},busy:{}};
  h.message(status);
  const button=name=>[...h.container.querySelectorAll('button')].find(b=>b.textContent===name);
  button('看保留段結果').click();assert.equal(h.latest('reveal'),undefined);
  button('確認解鎖').click();const request=h.latest('reveal');assert.equal(request.confirmed,true);
  h.message({...status,request_id:request.request_id,holdout:{state:'revealed'}});
  assert.match(h.container.querySelector('.tc-lock').textContent,/結果只作歷史描述/);
});

const forwardEmpty={split:'forward',frozen_day:'2026-09-27',latest:null,cohorts:[],pending:[],recorded_days:0};
function forwardReady(h,extra={}){h.answer('daily_forward',{...forwardEmpty,...extra});}
const four=['majority','ind_logit','vol_prior_d','jev_ind'];
function forwardPopulated(){return {...forwardEmpty,recorded_days:1,latest:{day:'2026-09-29',predictions:[3,7,14].flatMap(H=>four.map(method=>({H,method,choice:'flat',probabilities:{up:.2,flat:.6,down:.2},timing:'unconfirmed',recorded_at:'2026-09-29T18:00:00+08:00'})))},
 cohorts:['ontime','backfill','unconfirmed'].map(timing=>({timing,methods:four.map(method=>({method,n:0,accuracy:null,accuracy_wilson95:null,brier:null,coverage:1,missing:0})),comparison:{n:0,difference:null,ci95:null,small_sample:true,verdict:'結果不完整，不下結論'}})),pending:[{day:'2026-09-29',H:7,end_day:null,remaining:7}],pending_total:1,missing_total:0};}

test('forward empty latest comes before historical chart and keeps disclaimer',t=>{
 const h=setup(t);ready(h);forwardReady(h);
 const latest=h.container.querySelector('.tc-forward-latest');
 assert.match(latest.textContent,/尚無前瞻預測，下一個交易日收盤後自動產生/);
 assert.match(latest.textContent,/這是方法的機率判斷，不是投資建議；過去在開發段沒有勝過簡單方法/);
 const cards=[...h.container.querySelectorAll('.tc-card')];assert.ok(cards.indexOf(latest)<cards.indexOf(h.container.querySelector('.tc-daily-chart')));
 assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/未使用/);
 assert.equal(h.container.querySelector('.tc-prospective button'),null);
});
test('forward displays all three horizons four methods probabilities timing pending and cohorts',t=>{
 const h=setup(t);ready(h);forwardReady(h,forwardPopulated());
 assert.equal(h.container.querySelectorAll('.tc-forward-latest tbody tr').length,12);
 assert.match(h.container.querySelector('.tc-forward-latest').textContent,/20.00%60.00%20.00%準時待確認/);
 assert.match(h.container.querySelector('.tc-forward-pending').textContent,/2026-09-297 日尚待 7 個交易日資料/);
 assert.deepEqual([...h.container.querySelectorAll('.tc-forward-scores h3')].map(n=>n.textContent),['準時','補記','準時待確認']);
 assert.match(h.container.querySelector('.tc-forward-scores').textContent,/結果不完整，不下結論；樣本太少/);
});
function forwardDataText(h){const copy=h.container.cloneNode(true);copy.querySelectorAll('.tc-forward-origin').forEach(n=>n.remove());return copy.textContent;}
test('forward rejects historical injection and wrong frozen origin without weakening dev exits',t=>{
 const h=setup(t);ready(h);forwardReady(h,{...forwardPopulated(),latest:{day:'2024-07-26',predictions:[]}});
 assert.doesNotMatch(forwardDataText(h),/2024-07-26/);
 h.container.querySelector('button').click();forwardReady(h,{...forwardPopulated(),pending:[{day:'2022-01-03',H:7}]});
 assert.doesNotMatch(forwardDataText(h),/2022-01-03|2026-09-29/);
 h.container.querySelector('button').click();
 const oldRecord=forwardPopulated();oldRecord.latest.predictions[0].recorded_at='2024-07-26T18:00:00+08:00';
 forwardReady(h,oldRecord);assert.doesNotMatch(forwardDataText(h),/2024-07-26|2026-09-29/);
 h.container.querySelector('button').click();forwardReady(h,{...forwardPopulated(),frozen_day:'2021-12-31'});
 assert.doesNotMatch(forwardDataText(h),/2026-09-29/);
});
test('forward unsolicited refresh is read only and stale replies cannot cross horizon',t=>{
 const h=setup(t);ready(h);const before=h.sent.length,old=h.latest('daily_forward');
 h.message({op:'daily_forward_changed'});assert.equal(h.sent.length,before+1);assert.equal(h.sent.at(-1).op,'daily_forward');
 h.change('預測天期','3');h.message({op:'daily_forward',H:7,experiment_id:4,status:'ok',request_id:old.request_id,...forwardPopulated()});
 assert.doesNotMatch(forwardDataText(h),/2026-09-29/);
 forwardReady(h);assert.match(h.container.textContent,/尚無前瞻預測/);
 h.change('預測天期','30m');const n=h.sent.length;h.message({op:'daily_forward_changed'});assert.equal(h.sent.length,n);
});

test('daily release copy distinguishes development findings and prospective origin',t=>{
 const h=setup(t);ready(h);forwardReady(h);
 assert.match(h.container.querySelector('.tc-daily-scores').textContent,/沒有方法顯著勝過 majority；jev_ind 三天期 Brier 顯著較差/);
 assert.match(h.container.querySelector('.tc-forward-origin').textContent,/2026-09-29.*實際交易日/);
 assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/未使用/);
});
