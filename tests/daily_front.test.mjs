import test from 'node:test';
import assert from 'node:assert/strict';
import {Window} from 'happy-dom';
import mount from '../front/front.js';

function setup(t) {
  const window=new Window(),container=window.document.createElement('div');window.document.body.append(container);
  const sent=[],reports=[],answered=new Set(),retired=new Set();let message,up;
  const handle=mount({container,channel:{send(b){sent.push(b);},onMessage(fn){message=b=>{
    const request=sent.find(r=>r.request_id===b.request_id);
    if(request&&(request.op===b.op||b.op==='error'))answered.add(b.request_id);fn(b);
  };}},onUp(fn){up=fn;},report(s){assert.equal(typeof up,'function');assert.equal(typeof message,'function');reports.push(s);}});
  t.after(async()=>{handle.unmount();await window.happyDOM.close();});
  const find=label=>container.querySelector(`[aria-label="${label}"]`);
  const h={window,container,sent,reports,handle,message,up,find,
    latest(op){return sent.filter(b=>b.op===op).at(-1);},
    change(label,value){if(label==='預測天期')sent.forEach(r=>{if(!answered.has(r.request_id))retired.add(r.request_id);});
      const e=find(label);e.value=value;e.dispatchEvent(new window.Event('change'));},
    drainRetired(){for(const r of sent.slice())if(retired.has(r.request_id)&&!answered.has(r.request_id))
      message({...r,experiment_id:4,split:'dev',status:'ok'});},
    answer(op,body={}){const r=this.latest(op);message({op,H:r.H,experiment_id:4,split:'dev',status:'ok',request_id:r.request_id,...body});}};
  return h;
}
const days=['2021-12-01','2021-12-02','2021-12-03'];
function ready(h) {h.up();h.answer('daily_status',{dev_start:'2010-04-01',dev_end:'2021-12-31',threshold:.02,days});}
function chart(h, extra={}){h.answer('daily_chart',{range:'6m',prices:days.map((day,i)=>({day,close:100+i})),
  points:[{day:days[0],method:'jev_ind',choice:'up',correct:true},{day:days[0],method:'ind_logit',choice:'flat',correct:false},
    {day:days[1],method:'jev_ind',choice:'down',correct:'true'}],...extra});}
const names=['ma_cross','ma_trend','rsi14','kd','macd','bollinger','bias20','vol_price','foreign_net','trust_net','margin_chg'];
const methods=['always_flat','majority','momentum_H','reversal_H','vol_prior_d',...names.map(n=>`ind_${n}`),'ind_logit','jev_ind',
  'ens_avg','ind_mkt_trend','ind_mkt_ret5','ind_adr_premium','ind_sox_ret1','ind_sox_trend','mkt_logit'];
function report(h,extra={}) {h.answer('daily_report',{methods:methods.map(method=>({method,n:method==='jev_ind'?576:2890,accuracy:.4,brier:.65,difference:.01,ci95:[-.02,.03],verdict:'未入圍'})),
  comparison_note:'各列與 majority 同日期比較；jev_ind 僅 576 天。',multiplicity:'11×2.5%＝0.275',
  claims:[{indicator:'kd',description:'低檔黃金交叉',claim:'偏多',n:29,frequencies:{up:.5,flat:.3,down:.2}},
    {indicator:'kd',description:'高檔',claim:'無預設方向',n:30,frequencies:{up:.4,flat:.3,down:.3}}],...extra});}

test('daily default seven days registers before ready and sends only reads after up',t=>{
  const h=setup(t);assert.deepEqual(h.reports,['ready']);assert.equal(h.find('預測天期').value,'7');
  assert.equal(h.sent.length,0);h.up();h.up();
  assert.deepEqual(h.sent.map(b=>b.op),['daily_status','daily_chart','daily_report','daily_holdout','daily_forward','news_status']);
  assert.ok(h.sent.every(b=>b.H===7));assert.equal(h.container.querySelectorAll('.tc-forward').length,0);
  assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/正在讀取保留段狀態/);
  assert.equal(h.container.querySelector('.tc-daily-holdout button'),null);
});

test('daily holdout used status is permanent and ignores unadmitted result packets',t=>{
  const h=setup(t);h.up();
  h.answer('daily_status',{split:'holdout',holdout:{state:'used'},private_score:987.123});
  assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/正在讀取保留段狀態/);
  [...h.container.querySelectorAll('button')].find(b=>b.textContent==='重新整理').click();
  h.answer('daily_status',{dev_start:'2010-04-01',dev_end:'2021-12-31',threshold:.02,days,holdout:{state:'used'}});
  assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/已使用（一次性保留段考試已揭露）/);
  report(h,{holdout:{state:'unused'}});
  assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/已使用/);
  assert.doesNotMatch(h.container.textContent,/987\.123/);
});

function held(H=7){return {split:'holdout',state:'used',first_day:'2022-01-03',last_day:'2024-07-25',
 primary_comparisons:[[3,'ens_avg',-.0000771374749074,[-.00252397142567,.00242079429091]],
   [7,'ens_avg',-.0040539704948,[-.00877372804825,.00100247671519]],
   [3,'mkt_logit',.00591719707585,[-.00210922071031,.0145347477224]]].map(([H,method,difference,ci95])=>
     ({H,method,n:619-H,difference,ci95,verdict:`沒有證據顯示 ${method} 比簡單方法好`})),
 descriptive:methods.filter(m=>m!=='jev_ind').map(method=>({method,n:619-H,accuracy:.4567,brier:.654321,coverage:1,missing:0})),
 multiplicity:'3 個事先指定比較，預期約 0.075 個因運氣顯著較好；名目估算，未作多重比較校正。'};}

test('revealed holdout shows all three comparisons exact signs and expandable descriptive horizon',t=>{
 const h=setup(t);ready(h);
 for(const H of [7,3,14]){
   if(H!==7){h.change('預測天期',String(H));h.drainRetired();}
   h.answer('daily_holdout',held(H));
   const hold=h.container.querySelector('.tc-daily-holdout'),tables=hold.querySelectorAll('table');
   assert.equal(tables[0].querySelectorAll('tbody tr').length,3);
   const rows=[...tables[0].querySelectorAll('tbody tr')].map(r=>r.textContent);
   assert.match(rows[0],/組合預測（ens_avg） 3 日.*616.*−0.00008.*−0.00252 ～ \+0.00242.*沒有證據/);
   assert.match(rows[1],/組合預測（ens_avg） 7 日.*612.*−0.00405.*−0.00877 ～ \+0.00100.*沒有證據/);
   assert.match(rows[2],/大環境組合模型（mkt_logit） 3 日.*616.*\+0.00592.*−0.00211 ～ \+0.01453.*沒有證據/);
   assert.equal(hold.querySelector('details').open,false);
   assert.match(hold.querySelector('summary').textContent,new RegExp(`${H} 日全部方法`));
   assert.equal(tables[1].querySelectorAll('tbody tr').length,24);
   assert.ok([...tables[1].querySelectorAll('tbody tr')].every(r=>r.textContent.includes(String(619-H))));
   assert.match(hold.textContent,/只描述歷史成績，不作優劣結論/);assert.match(hold.textContent,/0.075/);
   assert.match(h.container.querySelector('.tc-daily-research').textContent,/只能用一次、已使用.*沒有證據比「猜最常見答案」好/);
 }
});

test('holdout results require revealed scoped reply and never leak locked supplied numbers',t=>{
 for(const invalid of [{state:'locked'},{state:'unused'},{split:'dev'},{experiment_id:1},{H:3},{status:'error'},
     {first_day:'2021-12-31'},{last_day:'2024-07-26'},{primary_comparisons:[]}]){
   const h=setup(t);ready(h);h.answer('daily_holdout',{...held(),...invalid});
   assert.equal(h.container.querySelectorAll('.tc-daily-holdout table').length,0);
   assert.doesNotMatch(h.container.querySelector('.tc-daily-research').textContent,/三者都沒有證據/);
   assert.doesNotMatch(h.container.textContent,/0.654321|0.00592|0.075/);
 }
});

test('holdout late reply cannot cross mount and revealed results do not relax development guards',t=>{
 const h=setup(t);ready(h);const old=h.latest('daily_holdout');
 h.change('預測天期','3');h.change('預測天期','7');
 h.message({op:'daily_holdout',H:7,experiment_id:4,status:'ok',request_id:old.request_id,...held()});
 assert.equal(h.container.querySelectorAll('.tc-daily-holdout table').length,0);
 h.drainRetired();
 h.answer('daily_holdout',held());
 chart(h,{prices:[{day:'2022-01-03',close:12345}]});
 assert.equal(h.container.querySelectorAll('.tc-price').length,0);
 report(h,{split:'holdout',methods:[{method:'ens_avg',verdict:'PRIVATE'}]});
 assert.doesNotMatch(h.container.querySelector('.tc-daily-scores').textContent,/PRIVATE/);
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
  assert.match(line.textContent,/最後收到時間 2026-09-29 13:29:00（台北）.*jev 讀新聞（jev_news） 未啟用/);
  assert.doesNotMatch(h.container.querySelector('.tc-daily-chart').textContent,/2026-09-29/);
  const saved=line.textContent;
  h.message({op:'news_changed'});h.answer('news_status',{received_at:'<img src=x onerror=alert(1)>',jev_news_enabled:false});
  assert.equal(line.textContent,saved);assert.equal(line.querySelector('img'),null);
  assert.ok(h.sent.every(p=>!p.op.startsWith('run')));
});

test('news status rejects late epoch replies and mismatched operation',t=>{
  const h=setup(t);ready(h);const old=h.latest('news_status');
  h.change('預測天期','3');
  const body={op:'news_status',status:'ok',received_at:'2026-09-29T13:29:00+08:00',jev_news_enabled:false};
  h.message({...body,request_id:old.request_id});
  assert.equal(h.container.querySelector('.tc-news').textContent,'新聞廣播：未收到');
  h.drainRetired();const current=h.latest('news_status');
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
  assert.match(h.container.querySelector('.tc-tooltip').textContent,/2021-12-01 · jev 讀指標（jev_ind） 猜漲 · 正確/);
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
  assert.equal(h.container.querySelectorAll('.tc-daily-scores tbody tr').length,25);
  for(const name of methods)assert.ok(h.container.querySelector(`.tc-daily-scores tr[data-method=${name}]`));
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
  h.change('預測天期','3');h.drainRetired();assert.equal(h.latest('daily_report').H,3);
  h.change('預測天期','14');h.drainRetired();assert.equal(h.latest('daily_report').H,14);
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
  const h=setup(t);ready(h);h.change('預測天期','30m');h.drainRetired();
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
 assert.match(latest.textContent,/還沒有預測紀錄。每天收盤後，行情和籌碼都到齊就會自動記錄一筆。/);
 assert.match(latest.textContent,/這是方法的機率判斷，不是投資建議；入圍方法在最終考試中沒有足夠證據勝過簡單方法/);
 const cards=[...h.container.querySelectorAll('.tc-card')];assert.ok(cards.indexOf(latest)<cards.indexOf(h.container.querySelector('.tc-daily-chart')));
 assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/正在讀取保留段狀態/);
 assert.equal(h.container.querySelector('.tc-prospective button'),null);
});
test('forward displays all three horizons five methods probabilities timing pending and cohorts',t=>{
 const h=setup(t);ready(h);forwardReady(h,forwardPopulated());
 assert.equal(h.container.querySelectorAll('.tc-forward-latest tbody tr').length,15);
 assert.match(h.container.querySelector('.tc-forward-latest').textContent,/20.00%60.00%20.00%準時待確認/);
 assert.match(h.container.querySelector('.tc-forward-pending').textContent,/2026-09-297 日尚待 7 個交易日資料/);
 assert.deepEqual([...h.container.querySelectorAll('.tc-forward-scores h3')].map(n=>n.textContent),['事先記錄（準時）','事後補記（另計）','記錄是否準時尚待確認']);
 assert.match(h.container.querySelector('.tc-forward-scores').textContent,/結果不完整，不下結論；樣本太少/);
});
function forwardDataText(h){const copy=h.container.querySelector('.tc-prospective').cloneNode(true);copy.querySelectorAll('.tc-forward-origin').forEach(n=>n.remove());return copy.textContent;}
test('forward rejects historical injection and wrong frozen origin without weakening dev exits',t=>{
 const recorded=forwardPopulated();recorded.latest.predictions[0].recorded_at='2024-07-26T18:00:00+08:00';
 const cases=[{...forwardPopulated(),latest:{day:'2024-07-26',predictions:[]}},
   {...forwardPopulated(),pending:[{day:'2022-01-03',H:7}]},recorded,
   {...forwardPopulated(),frozen_day:'2021-12-31'}];
 for(const body of cases){
   const h=setup(t);ready(h);forwardReady(h,body);
   assert.doesNotMatch(forwardDataText(h),/2024-07-26|2022-01-03|2026-09-29/);
 }
});
test('forward unsolicited refresh is read only and stale replies cannot cross horizon',t=>{
 const h=setup(t);ready(h);forwardReady(h);const before=h.sent.length,old=h.latest('daily_forward');
 h.message({op:'daily_forward_changed'});assert.equal(h.sent.length,before+1);assert.equal(h.sent.at(-1).op,'daily_forward');
 h.change('預測天期','3');h.message({op:'daily_forward',H:7,experiment_id:4,status:'ok',request_id:old.request_id,...forwardPopulated()});
 assert.doesNotMatch(forwardDataText(h),/2026-09-29/);
 h.drainRetired();
 forwardReady(h);assert.match(h.container.textContent,/還沒有預測紀錄/);
 h.change('預測天期','30m');const n=h.sent.length;h.message({op:'daily_forward_changed'});assert.equal(h.sent.length,n);
});

test('daily release copy distinguishes development findings and prospective origin',t=>{
 const h=setup(t);ready(h);forwardReady(h);
 assert.match(h.container.querySelector('.tc-daily-scores').textContent,/入圍只代表值得再驗證.*jev 讀指標（jev_ind） 在 3／7／14 日的機率誤差顯著較差/);
 const research=h.container.querySelector('.tc-daily-research');
 assert.equal(h.container.querySelector('.tc-card'),research);
 for(const copy of ['11 個技術／籌碼指標','5 個大環境指標','2 個組合模型','ens_avg','jev 讀指標','jev 讀新聞（jev_news）（前瞻中）','3 組小幅入圍','邊緣','2026-09-29','沒有足夠證據'])assert.ok(research.textContent.includes(copy),copy);
 assert.match(h.container.querySelector('.tc-footer').textContent,/這不是投資建議/);
 assert.match(h.container.querySelector('.tc-forward-origin').textContent,/2026-09-29.*實際交易日/);
 assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/正在讀取保留段狀態/);
});


test('news enabled reception and missing snapshot render separately',t=>{
 const h=setup(t);ready(h);
 h.answer('news_status',{received_at:'2026-09-29T13:29:00+08:00',jev_news_enabled:true});
 assert.match(h.container.querySelector('.tc-news').textContent,/已啟用（需當日快照）/);
 const value=forwardPopulated();value.latest.news_state='no_news';forwardReady(h,value);
 const rows=[...h.container.querySelectorAll('.tc-forward-latest tbody tr')].filter(r=>r.querySelector('[data-method=jev_news]'));
 assert.equal(rows.length,3);rows.forEach(r=>assert.match(r.textContent,/該日無新聞資料.*未發請求/));
});
test('news predictions show three horizons and both preregistered comparisons',t=>{
 const h=setup(t);ready(h);const value=forwardPopulated();value.latest.news_state='done';
 value.latest.predictions.push(...[3,7,14].map(H=>({H,method:'jev_news',choice:'up',probabilities:{up:.7,flat:.2,down:.1},timing:'ontime',recorded_at:'2026-09-29T17:00:00+08:00'})));
 value.cohorts[0].news_comparisons=Object.fromEntries(['jev_ind','majority'].map(m=>[m,{n:1,difference:.01,ci95:[-.02,.03],small_sample:true,verdict:'結果不完整，不下結論'}]));
 forwardReady(h,value);
 const rows=[...h.container.querySelectorAll('.tc-forward-latest tbody tr')].filter(r=>r.querySelector('[data-method=jev_news]'));
 assert.equal(rows.length,3);rows.forEach(r=>assert.match(r.textContent,/漲70.00%20.00%10.00%準時/));
 assert.match(h.container.querySelector('.tc-forward-scores').textContent,/jev 讀新聞（jev_news） − jev 讀指標（jev_ind）/);
 assert.match(h.container.querySelector('.tc-forward-scores').textContent,/jev 讀新聞（jev_news） − 猜最常見答案（majority）/);
 assert.match(h.container.querySelector('.tc-forward-scores').textContent,/未滿 60 個到期日/);
});

test('rapid horizons 3 7 14 3 bound reads discard retired queues and show correct final data',t=>{
 const h=setup(t);h.up();const initial=h.sent.slice();
 for(const H of ['3','7','14','3'])h.change('預測天期',H);
 for(let i=0;i<25;i++)h.message({op:'daily_forward_changed'});
 assert.equal(h.find('預測天期').value,'3');
 assert.equal(h.sent.length,6); // No previous response has returned; no burst reaches the server.
 const done=new Set();let peak=0;
 for(let i=0;i<h.sent.length;i++){
   const r=h.sent[i];peak=Math.max(peak,h.sent.filter(x=>!done.has(x.request_id)).length);
   assert.ok(peak<=6,'backend queue must never receive an unbounded burst');
   const extra=r.op==='daily_status'?{dev_start:'2010-04-01',dev_end:'2021-12-31',threshold:.01,days}:
     r.op==='daily_report'?{methods:[{method:'mkt_logit',n:r.H===3?2891:777,accuracy:.4,brier:r.H===3?.660868:99,difference:-.004385,ci95:[-.0085,-.00029],verdict:'入圍'}]}:
     r.op==='daily_holdout'?held(r.H):r.op==='daily_forward'?forwardEmpty:
     r.op==='news_status'?{received_at:null,jev_news_enabled:false}:
     r.op==='daily_indicators'?{day:days.at(-1),indicators:[{name:'kd',description:`${r.H} 日資料`,detail:'正確'}]}:
     {range:'6m',prices:days.map(day=>({day,close:r.H===3?333:777})),points:[]};
   done.add(r.request_id);h.message({...r,experiment_id:4,split:'dev',status:'ok',...extra});
 }
 assert.equal(h.sent.filter(r=>r.op==='daily_status'&&r.H===3).length,1);
 assert.ok(h.sent.every(r=>r.H===3||initial.includes(r)));
 assert.equal(h.container.querySelectorAll('.tc-error:not([hidden])').length,0);
 assert.match(h.container.querySelector('h1').textContent,/3 個交易日後/);
 assert.match(h.container.querySelector('.tc-daily-scores').textContent,/2,891.*0.660868/);
 assert.match(h.container.querySelector('.tc-daily-indicators').textContent,/3 日資料/);
 assert.match(h.container.querySelector('.tc-forward-latest').textContent,/還沒有預測紀錄/);
 assert.match(h.container.querySelector('.tc-daily-holdout summary').textContent,/3 日/);
 assert.ok(h.container.querySelector('.tc-price'));assert.doesNotMatch(h.container.querySelector('.tc-daily-scores').textContent,/99.000000/);
 // Late duplicates cannot release a new request slot or overwrite the current view.
 const n=h.sent.length;h.message({...initial[2],experiment_id:4,split:'dev',status:'ok',methods:[{method:'mkt_logit',verdict:'STALE'}]});
 assert.equal(h.sent.length,n);assert.doesNotMatch(h.container.textContent,/STALE/);
});

test('queued reads release on errors and unmount prevents deferred transmissions',t=>{
 const h=setup(t);h.up();const old=h.sent.slice();h.change('預測天期','3');
 h.message({op:'error',code:'busy',request_id:old[0].request_id});
 assert.equal(h.sent.at(-1).H,3);assert.equal(h.container.querySelectorAll('.tc-error:not([hidden])').length,0);
 const n=h.sent.length;h.handle.unmount();old.slice(1).forEach(r=>h.message({...r,status:'ok'}));
 assert.equal(h.sent.length,n);assert.equal(h.container.children.length,0);
});

test('all daily methods have plain names with small codes and unchanged identifiers',t=>{
 const h=setup(t);ready(h);report(h);h.answer('daily_holdout',held());forwardReady(h,forwardPopulated());
 const expected={ens_avg:'組合預測',mkt_logit:'大環境組合模型',majority:'猜最常見答案',ind_kd:'KD',
   ind_mkt_trend:'大盤均線趨勢',jev_ind:'jev 讀指標',jev_news:'jev 讀新聞'};
 for(const method of methods){
   const row=h.container.querySelector(`.tc-daily-scores tr[data-method=${method}]`);
   assert.ok(row.querySelector('small'));
   assert.equal(row.querySelector('small').textContent,`（${method}）`);
   assert.notEqual(row.firstElementChild.textContent,method);
 }
 for(const [method,name] of Object.entries(expected)){
   const label=h.container.querySelector(`.tc-method[data-method=${method}]`);
   assert.equal(label.textContent,`${name}（${method}）`);assert.ok(label.querySelector('small.tc-method-code'));
 }
 assert.match(h.container.querySelector('.tc-daily-research').textContent,/組合預測（ens_avg）.*大環境組合模型（mkt_logit）/);
 assert.match(h.container.querySelector('.tc-daily-holdout').textContent,/猜最常見答案（majority）/);
 assert.ok(h.sent.every(r=>!('method' in r))); // Rendering never changes wire method identifiers.
});

test('read errors replace the forward and chart loading placeholders',t=>{
 const h=setup(t);h.up();
 for(const op of ['daily_chart','daily_forward'])h.message({op:'error',code:'daily_experiment_missing',request_id:h.latest(op).request_id});
 assert.doesNotMatch(h.container.textContent,/正在讀取前瞻紀錄|正在載入走勢/);
 assert.match(h.container.querySelector('.tc-forward-latest').textContent,/尚未建立多日實驗/);
 assert.match(h.container.querySelector('.tc-daily-chart').textContent,/尚未建立多日實驗/);
});

test('redesign: plain rule sentence, verdict first, research folded closed',t=>{
 const h=setup(t);ready(h);
 assert.match(h.container.querySelector('.tc-rule').textContent,/7 個交易日後的漲跌幅（已調整除權息）：漲 2% 以上算漲，跌 2% 以上算跌，其餘算盤整/);
 assert.doesNotMatch(h.container.textContent,/唯讀檢視|實驗 4 ·/);
 const verdict=h.container.querySelector('.tc-verdict h2');assert.match(verdict.textContent,/正在讀取結論/);
 h.answer('daily_holdout',held());
 assert.match(verdict.textContent,/最終考試中，3 組入圍方法都沒有足夠證據/);
 const folds=[...h.container.querySelectorAll('.tc-daily-details > details.tc-fold')];
 assert.equal(folds.length,6);assert.ok(folds.every(d=>!d.open));
 for(const cls of ['tc-daily-holdout','tc-daily-chart','tc-daily-indicators','tc-daily-scores','tc-daily-claims'])
   assert.ok(h.container.querySelector(`.tc-daily-details .${cls}`),cls);
 const order=[...h.container.querySelectorAll('.tc-verdict,.tc-forward-latest,.tc-forward-scores,.tc-daily-details')].map(n=>n.className.split(' ').at(-1));
 assert.deepEqual(order,['tc-verdict','tc-forward-latest','tc-forward-scores','tc-daily-details']);
});

test('redesign: locked holdout keeps a cautious verdict without holdout results',t=>{
 const h=setup(t);ready(h);
 h.answer('daily_holdout',{split:'holdout',state:'locked'});
 assert.match(h.container.querySelector('.tc-verdict h2').textContent,/還沒通過最終考試/);
 assert.doesNotMatch(h.container.querySelector('.tc-verdict').textContent,/目前沒有任何方法/);
});

test('redesign: selected horizon first, others folded, baseline marked, reveal and progress shown',t=>{
 const h=setup(t);ready(h);forwardReady(h,{...forwardPopulated(),data_through:'2026-09-30',pending_counts:{'3':0,'7':1,'14':0}});
 const latest=h.container.querySelector('.tc-forward-latest');
 const direct=[...latest.querySelectorAll('tbody tr')].filter(r=>!r.closest('.tc-forward-others'));
 assert.equal(direct.length,5);assert.equal(latest.querySelectorAll('.tc-forward-others tbody tr').length,10);
 assert.ok(direct[0].classList.contains('tc-base'));assert.equal(direct[0].querySelectorAll('.tc-prob i').length,3);
 assert.match(latest.textContent,/預測起點 2026-09-29（只用當天收盤以前的資料） · 還要 7 個交易日才揭曉/);
 assert.match(latest.querySelector('.tc-forward-others summary').textContent,/3／14 日/);
 const stats=[...h.container.querySelectorAll('.tc-forward-scores .tc-stat-value')].map(n=>n.textContent);
 assert.deepEqual(stats,['0','1','結果不完整，不下結論']);
 assert.ok(h.container.querySelector('.tc-forward-cohorts').textContent.includes('事先記錄（準時）'));
 assert.match(h.container.querySelector('.tc-head').textContent,/行情更新到 2026-09-30/);
});

test('redesign: data_through before the freeze is rejected with the whole packet',t=>{
 const h=setup(t);ready(h);forwardReady(h,{...forwardPopulated(),data_through:'2021-12-30'});
 assert.doesNotMatch(h.container.textContent,/行情更新到|2021-12-30/);
 assert.match(h.container.querySelector('.tc-forward-latest').textContent,/正在讀取前瞻紀錄/);
});

test('redesign: every loading line is replaced when its read fails',t=>{
 const h=setup(t);h.up();
 for(const op of ['daily_status','daily_holdout','daily_report'])h.message({op:'error',code:'daily_experiment_missing',request_id:h.latest(op).request_id});
 assert.doesNotMatch(h.container.textContent,/正在讀取預測設定|正在讀取結論|正在讀取保留段狀態|正在計算開發段成績/);
 assert.match(h.container.querySelector('.tc-rule').textContent,/無法讀取預測設定：尚未建立多日實驗/);
 assert.match(h.container.querySelector('.tc-verdict').textContent,/無法讀取結論.*尚未建立多日實驗/);
});

test('redesign: empty forward names the known service state and a null data day resets the header',t=>{
 const h=setup(t);ready(h);forwardReady(h,{...forwardPopulated(),data_through:'2026-09-30'});
 assert.match(h.container.querySelector('.tc-head').textContent,/行情更新到 2026-09-30/);
 h.message({op:'daily_forward_changed'});forwardReady(h,{data_through:null,service:{state:'idle'}});
 assert.doesNotMatch(h.container.querySelector('.tc-head').textContent,/行情更新到/);
 assert.match(h.container.querySelector('.tc-forward-latest').textContent,/還沒有預測紀錄.*更新狀態：等待下一次更新/);
});
