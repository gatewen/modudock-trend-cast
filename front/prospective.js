// Dedicated forward-only renderer. Development date guards remain in daily.js.
import {writeMethods} from './method_names.js';
const timing = {ontime:'準時',backfill:'補記',unconfirmed:'準時待確認'};
const cohortTitles = {ontime:'事先記錄（準時）',backfill:'事後補記（另計）',unconfirmed:'記錄是否準時尚待確認'};
const labels = {up:'漲',flat:'盤整',down:'跌'};
const methods = ['majority','ind_logit','vol_prior_d','jev_ind','jev_news'];
const HORIZONS = [3,7,14];
const SERVICE = {running:'正在更新資料與預測。',idle:'等待下一次更新：開著模組時會在啟動、同步後與每 15 分鐘檢查一次。'};
const EMPTY = '還沒有預測紀錄。每天收盤後，行情和籌碼都到齊就會自動記錄一筆。';
const DISCLAIMER = '這是方法的機率判斷，不是投資建議；入圍方法在最終考試中沒有足夠證據勝過簡單方法';
const numeric = n => typeof n==='number' && Number.isFinite(n);
const percent = n => numeric(n)?`${(n*100).toFixed(2)}%`:'—';
const number = n => numeric(n)?n.toFixed(6):'—';
const safe = s => typeof s==='string'?s.slice(0,200):'';
const day = s => typeof s==='string' && /^\d{4}-\d{2}-\d{2}$/.test(s);
const interval = (v,f) => Array.isArray(v)?v.map(f).join(' ～ '):'—';
export default function prospective(doc,H) {
  const el=(tag,text='',cls='')=>{const n=doc.createElement(tag);writeMethods(n,text);n.className=cls;return n;};
  const root=el('section','','tc-prospective');
  function card(title,cls){const n=el('section','',`tc-card ${cls}`);n.append(el('h2',title));root.append(n);return n;}
  const latest=card('最新一筆預測','tc-forward-latest'),latestBody=el('div');
  latest.append(latestBody,el('p','前瞻紀錄自 2026-09-29 起按實際交易日累積；資料尚未可得時不產生紀錄。','tc-note tc-forward-origin'),el('p',DISCLAIMER,'tc-note'));latestBody.append(el('p','正在讀取前瞻紀錄…','tc-empty'));
  const scores=card('對答案進度','tc-forward-scores'),scoreBody=el('div');
  scores.append(el('p','主要成績只算事先記錄（準時）的預測。何時能下結論由統計規則決定：jev_ind 的比較至少要兩個 20 交易日區塊，jev_news 的比較要滿 60 個共同到期日；未達前一律標示「不下結論」。','tc-note'),scoreBody);
  const pending=el('details','','tc-forward-pending'),pendingBody=el('div');
  pending.append(el('summary','等待揭曉的預測清單'),pendingBody);scores.append(pending);
  function table(headers,rows){const wrap=el('div','','tc-scroll'),t=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');
    headers.forEach(v=>tr.append(el('th',v)));head.append(tr);
    rows.forEach(row=>{const r=el('tr');if(String(row[0]).startsWith('majority'))r.className='tc-base';
      row.forEach(v=>{if(typeof v==='object'&&v)r.append(v);else r.append(el('td',String(v)));});body.append(r);});
    t.append(head,body);wrap.append(t);return wrap;}
  // One bar per method: widths are the three probabilities, so the likeliest answer is visible at a glance.
  function bar(p){const td=el('td'),b=el('span','','tc-prob');b.setAttribute('aria-hidden','true');
    for(const k of ['up','flat','down']){const i=el('i','',`tc-prob-${k}`);i.style.width=`${Math.max(0,Math.min(1,p[k]))*100}%`;b.append(i);}
    td.append(b);return td;}
  function horizonTable(v,h){
    const rows=methods.map(m=>{const p=(v.latest.predictions||[]).find(p=>p.H===h&&p.method===m);
      const absent=m==='jev_news'&&v.latest.news_state==='no_news';
      const name=m==='majority'?'majority（比較基準）':m;
      return p?[name,labels[p.choice]||'—',p.probabilities&&['up','flat','down'].every(k=>numeric(p.probabilities[k]))?bar(p.probabilities):'—',
        percent(p.probabilities?.up),percent(p.probabilities?.flat),percent(p.probabilities?.down),timing[p.timing]||'—']
        :[name,absent?'該日無新聞資料':'缺答','—','—','—','—',absent?'未發請求':'尚未完成'];});
    return table(['方法','最可能','機率','漲','盤整','跌','紀錄'],rows);
  }
  function reveal(v){
    const p=(v.pending||[]).find(p=>p.day===v.latest.day&&p.H===H);
    if(!p)return '已揭曉';
    return day(p.end_day)?`預計 ${p.end_day} 揭曉`:`還要 ${Number.isSafeInteger(p.remaining)?p.remaining:'—'} 個交易日才揭曉`;
  }
  function stat(value,label){const n=el('div','',typeof value==='number'?'tc-stat':'tc-stat tc-stat-text');n.append(el('div',String(value),'tc-stat-value'),el('div',label,'tc-stat-label'));return n;}
  function cohortTable(c){
    return table(['方法','已揭曉','猜對率','猜對率 95% 範圍','機率誤差（越低越好）','有作答比例','缺答'],(c.methods||[]).filter(m=>methods.includes(m.method)).map(m=>[
      m.method==='majority'?'majority（比較基準）':m.method,m.n,percent(m.accuracy),interval(m.accuracy_wilson95,percent),number(m.brier),percent(m.coverage),m.missing]));
  }
  function comparisons(c,into){
    const cmp=c.comparison||{};
    into.append(el('p',`jev_ind 比 majority 的機率誤差差距：${number(cmp.difference)}（負＝較好）；95% 區間 ${interval(cmp.ci95,number)}；共同樣本 ${cmp.n??0}。${safe(cmp.verdict)}${cmp.small_sample?'；樣本太少，持續累積。':''}`,'tc-note'));
    for(const baseline of ['jev_ind','majority']){
      const p=c.news_comparisons?.[baseline];if(!p)continue;
      into.append(el('p',`jev_news − ${baseline}：${number(p.difference)}；95% 區間 ${interval(p.ci95,number)}；共同樣本 ${p.n??0}。${safe(p.verdict)}${p.small_sample?'；未滿 60 個到期日。':''}`,'tc-note'));
    }
  }
  function render(v){
    if(v.H!==H||v.experiment_id!==4||v.split!=='forward'||v.status!=='ok'||!day(v.frozen_day)||v.frozen_day<'2026-09-27')return false;
    // The only allowed recent dates are this frozen boundary and strictly later dates.
    const dates=JSON.stringify(v).match(/\d{4}-\d{2}-\d{2}/g)||[];
    if(dates.some(d=>d<v.frozen_day))return false;
    if(v.latest && (!day(v.latest.day)||v.latest.day<=v.frozen_day))return false;
    if((v.pending||[]).some(p=>!day(p.day)||p.day<=v.frozen_day||(p.end_day && p.end_day<=p.day)))return false;
    latestBody.replaceChildren();scoreBody.replaceChildren();pendingBody.replaceChildren();
    const counted=v.pending_counts?.[String(H)];
    const waiting=Number.isSafeInteger(counted)&&counted>=0?counted:0;
    const ontime=(v.cohorts||[]).find(c=>c.timing==='ontime');
    const revealed=ontime?.methods?.find(m=>m.method==='majority')?.n;
    const done=Number.isSafeInteger(revealed)&&revealed>0?revealed:0;
    const stats=el('div','','tc-stats');
    stats.append(stat(done,'已揭曉（事先記錄）'),stat(waiting,'等待揭曉（含補記）'),stat(safe(ontime?.comparison?.verdict)||'不下結論','jev_ind 比 majority 目前結論'));
    scoreBody.append(stats);
    if(!v.latest){
      latestBody.append(el('p',EMPTY,'tc-empty tc-empty-compact'));
      if(SERVICE[v.service?.state])latestBody.append(el('p',`更新狀態：${SERVICE[v.service.state]}`,'tc-note'));
      pendingBody.append(el('p','目前沒有待到期紀錄。','tc-note'));
    } else {
      latestBody.append(el('p',`預測起點 ${v.latest.day}（只用當天收盤以前的資料） · ${reveal(v)} · 已累積 ${Number.isSafeInteger(v.recorded_days)?v.recorded_days:'—'} 個交易日`,'tc-sub'));
      latestBody.append(el('h3',`${H} 個交易日`),horizonTable(v,H));
      const others=el('details','','tc-forward-others');
      others.append(el('summary',`看其他天期（${HORIZONS.filter(h=>h!==H).join('／')} 日）`));
      for(const h of HORIZONS.filter(h=>h!==H))others.append(el('h3',`${h} 個交易日`),horizonTable(v,h));
      latestBody.append(others);
      const legend=el('p','','tc-legend');
      legend.append(el('span','■ 漲','tc-good'),el('span','■ 盤整'),el('span','■ 跌','tc-bad'),el('span','灰底列是比較基準'));
      latestBody.append(legend);
      const extra=el('details','','tc-forward-cohorts');extra.append(el('summary',done>0?'事後補記與待確認的紀錄':'成績明細（尚無已揭曉的預測）'));
      for(const key of ['ontime','backfill','unconfirmed']){
        const c=(v.cohorts||[]).find(c=>c.timing===key);if(!c)continue;
        const into=key==='ontime'&&done>0?scoreBody:extra;
        into.append(el('h3',cohortTitles[key]),cohortTable(c));comparisons(c,into);
      }
      if(extra.children.length>1)scoreBody.append(extra);
      scoreBody.append(el('p','區間：20 交易日區塊 bootstrap，2,000 次、固定種子。','tc-note'));
      const pendingRows=(v.pending||[]).map(p=>[p.day,`${p.H} 日`,p.end_day||`尚待 ${p.remaining} 個交易日資料`]);
      pendingBody.append(pendingRows.length?table(['記錄日','天期','揭曉日／進度'],pendingRows):el('p','目前沒有待到期紀錄。','tc-note'));
      if(v.pending_total>pendingRows.length)pendingBody.append(el('p',`共 ${v.pending_total} 筆，顯示最近 ${pendingRows.length} 筆。`,'tc-note'));
      if(v.missing_total)pendingBody.append(el('p',`jev 尚有 ${v.missing_total} 日未完成；僅 429／529 可在截止前重試，跨重啟最多三次。`,'tc-note'));
    }
    if(v.service?.state==='error')latestBody.append(el('p','前瞻更新暫未完成；保留既有紀錄，稍後同步再檢查。','tc-error'));
    return true;
  }
  // A failed read must not leave the loading placeholder up forever.
  function fail(message){latestBody.replaceChildren(el('p',message,'tc-empty'));scoreBody.replaceChildren();pendingBody.replaceChildren();}
  return {root,render,fail};
}
