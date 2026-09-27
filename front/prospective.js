// Dedicated forward-only renderer. Development date guards remain in daily.js.
const timing = {ontime:'準時',backfill:'補記',unconfirmed:'準時待確認'};
const labels = {up:'漲',flat:'盤整',down:'跌'};
const methods = ['majority','ind_logit','vol_prior_d','jev_ind','jev_news'];
const EMPTY = '尚無前瞻預測，下一個交易日收盤後自動產生';
const DISCLAIMER = '這是方法的機率判斷，不是投資建議；過去在開發段沒有勝過簡單方法';
const numeric = n => typeof n==='number' && Number.isFinite(n);
const percent = n => numeric(n)?`${(n*100).toFixed(2)}%`:'—';
const number = n => numeric(n)?n.toFixed(6):'—';
const safe = s => typeof s==='string'?s.slice(0,200):'';
const day = s => typeof s==='string' && /^\d{4}-\d{2}-\d{2}$/.test(s);
export default function prospective(doc,H) {
  const el=(tag,text='',cls='')=>{const n=doc.createElement(tag);n.textContent=text;n.className=cls;return n;};
  const root=el('section','','tc-prospective');
  function card(title,cls){const n=el('section','',`tc-card ${cls}`);n.append(el('h2',title));root.append(n);return n;}
  const latest=card('最新預測','tc-forward-latest'),latestBody=el('div');
  latest.append(latestBody,el('p','前瞻紀錄自 2026-09-29 起按實際交易日累積；資料尚未可得時不產生紀錄。','tc-note tc-forward-origin'),el('p',DISCLAIMER,'tc-note'));latestBody.append(el('p','正在讀取前瞻紀錄…','tc-empty'));
  const scores=card(`${H} 日前瞻成績`,'tc-forward-scores'),scoreBody=el('div');
  scores.append(el('p','以準時成績為主；補記另外列示，待確認不納入主要比較。','tc-note'),scoreBody);
  const pending=card('待到期清單','tc-forward-pending'),pendingBody=el('div');pending.append(pendingBody);
  function table(headers,rows){const wrap=el('div','','tc-scroll'),t=el('table'),head=el('thead'),tr=el('tr'),body=el('tbody');
    headers.forEach(v=>tr.append(el('th',v)));head.append(tr);
    rows.forEach(row=>{const r=el('tr');row.forEach(v=>r.append(el('td',String(v))));body.append(r);});
    t.append(head,body);wrap.append(t);return wrap;}
  function render(v){
    if(v.H!==H||v.experiment_id!==4||v.split!=='forward'||v.status!=='ok'||!day(v.frozen_day)||v.frozen_day<'2026-09-27')return;
    // The only allowed recent dates are this frozen boundary and strictly later dates.
    const dates=JSON.stringify(v).match(/\d{4}-\d{2}-\d{2}/g)||[];
    if(dates.some(d=>d<v.frozen_day))return;
    if(v.latest && (!day(v.latest.day)||v.latest.day<=v.frozen_day))return;
    if((v.pending||[]).some(p=>!day(p.day)||p.day<=v.frozen_day||(p.end_day && p.end_day<=p.day)))return;
    latestBody.replaceChildren();scoreBody.replaceChildren();pendingBody.replaceChildren();
    if(!v.latest){latestBody.append(el('p',EMPTY,'tc-empty'));scoreBody.append(el('p','尚無已到期的前瞻樣本。','tc-note'));pendingBody.append(el('p','目前沒有待到期紀錄。','tc-note'));}
    else {
      latestBody.append(el('p',`${v.latest.day} · 已累積 ${Number.isSafeInteger(v.recorded_days)?v.recorded_days:'—'} 個交易日`,'tc-sub'));
      for(const h of [3,7,14]){
        latestBody.append(el('h3',`${h} 個交易日`));
        const rows=methods.map(m=>{const p=(v.latest.predictions||[]).find(p=>p.H===h&&p.method===m);
          const absent=m==='jev_news'&&v.latest.news_state==='no_news';
          return p?[m,labels[p.choice]||'—',percent(p.probabilities?.up),percent(p.probabilities?.flat),percent(p.probabilities?.down),timing[p.timing]||'—',safe(p.recorded_at)]:[m,absent?'今日無新聞資料':'缺答','—','—','—',absent?'未發請求':'尚未完成','—'];});
        latestBody.append(table(['方法','答案','漲','盤整','跌','紀錄','記錄時間'],rows));
      }
      for(const key of ['ontime','backfill','unconfirmed']){
        const c=(v.cohorts||[]).find(c=>c.timing===key);if(!c)continue;
        scoreBody.append(el('h3',timing[key]));
        scoreBody.append(table(['方法','到期樣本','準確率','Wilson 95%','Brier','覆蓋率','缺答'],(c.methods||[]).filter(m=>methods.includes(m.method)).map(m=>[
          m.method,m.n,percent(m.accuracy),Array.isArray(m.accuracy_wilson95)?m.accuracy_wilson95.map(percent).join(' ～ '):'—',number(m.brier),percent(m.coverage),m.missing])));
        const cmp=c.comparison||{};
        scoreBody.append(el('p',`jev_ind − majority：${number(cmp.difference)}；95% 區間 ${Array.isArray(cmp.ci95)?cmp.ci95.map(number).join(' ～ '):'—'}；交集 ${cmp.n??0}。${safe(cmp.verdict)}${cmp.small_sample?'；樣本太少，持續累積。':''}`,'tc-note'));
        for(const baseline of ['jev_ind','majority']){
          const p=c.news_comparisons?.[baseline];if(!p)continue;
          scoreBody.append(el('p',`jev_news − ${baseline}：${number(p.difference)}；95% 區間 ${Array.isArray(p.ci95)?p.ci95.map(number).join(' ～ '):'—'}；交集 ${p.n??0}。${safe(p.verdict)}${p.small_sample?'；未滿 60 個到期日。':''}`,'tc-note'));
        }
      }
      scoreBody.append(el('p','區間：20 交易日區塊 bootstrap，2,000 次、固定種子。','tc-note'));
      const pendingRows=(v.pending||[]).map(p=>[p.day,`${p.H} 日`,p.end_day||`尚待 ${p.remaining} 個交易日資料`]);
      pendingBody.append(pendingRows.length?table(['起點','天期','到期／進度'],pendingRows):el('p','目前沒有待到期紀錄。','tc-note'));
      if(v.pending_total>pendingRows.length)pendingBody.append(el('p',`共 ${v.pending_total} 筆，顯示最近 ${pendingRows.length} 筆。`,'tc-note'));
      if(v.missing_total)pendingBody.append(el('p',`jev 尚有 ${v.missing_total} 日未完成；僅 429／529 可在截止前重試，跨重啟最多三次。`,'tc-note'));
    }
    if(v.service?.state==='error')latestBody.append(el('p','前瞻更新暫未完成；保留既有紀錄，稍後同步再檢查。','tc-error'));
  }
  return {root,render};
}
