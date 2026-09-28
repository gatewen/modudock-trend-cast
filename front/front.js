import styles from './style.js';
import mountIntraday from './intraday.js';
import mountDaily from './daily.js';

const READS = new Set(['status','day','report','daily_status','daily_chart','daily_report',
  'daily_holdout','daily_indicators','daily_forward','news_status']);
// Leave room in the backend's eight-slot queue for its own status/change reads.
const MAX_READS = 6;

// Each mounted view owns its callbacks; wire IDs never repeat after switching.
export default function mount(ctx) {
  const doc = ctx.container.ownerDocument;
  const root = doc.createElement('section'); root.className = 'tc tc-shell';
  const style = doc.createElement('style'); style.textContent = styles; root.append(style);
  const nav = doc.createElement('label'); nav.className = 'tc-horizon'; nav.textContent = '預測天期';
  const select = doc.createElement('select'); select.setAttribute('aria-label', '預測天期');
  for (const [value, text] of [['30m', '30 分鐘'], ['3', '3 日'], ['7', '7 日'], ['14', '14 日']]) {
    const option = doc.createElement('option'); option.value = value; option.textContent = text; select.append(option);
  }
  select.value = '7'; select.disabled = true; nav.append(select);
  const slot = doc.createElement('div'); root.append(nav, slot); ctx.container.append(root);
  let live = false, disposed = false, serial = 0, epoch = 0, child, receive, up;
  const pending = new Map();
  const activeReads = new Map(), queuedReads = new Map();
  function transmit(item) {
    const request_id = ++serial;
    pending.set(request_id, {local: item.body.request_id, op: item.body.op});
    if (pending.size > 128) pending.delete(pending.keys().next().value);
    if (READS.has(item.body.op)) activeReads.set(request_id,item.body.op);
    ctx.channel.send({...item.body,request_id});
  }
  function pumpReads() {
    if (!live || disposed) return;
    while (activeReads.size < MAX_READS && queuedReads.size) {
      const [op,item] = queuedReads.entries().next().value;
      queuedReads.delete(op);
      if (item.generation === epoch) transmit(item);
    }
  }
  function show() {
    child?.unmount(); epoch++; pending.clear(); queuedReads.clear(); receive = up = undefined;
    const generation = epoch;
    const local = {container: slot, H: Number(select.value), report() {},
      channel: {
        onMessage(fn) { receive = fn; },
        send(body) {
          if (!live || disposed || generation !== epoch) return;
          const item = {body,generation};
          if (READS.has(body.op)) {
            // Keep only the latest unsent date/range/refresh for each operation.
            queuedReads.set(body.op,item); pumpReads();
          } else transmit(item);
        },
      }, onUp(fn) { up = fn; }};
    child = (select.value === '30m' ? mountIntraday : mountDaily)(local);
    if (live) up?.();
  }
  function change() { if (live && !disposed) show(); }
  select.addEventListener('change', change);
  show();
  ctx.channel.onMessage(body => {
    if (disposed || !body || typeof body !== 'object') return;
    const isDaily = select.value !== '30m';
    if (body.request_id != null) {
      const active = activeReads.get(body.request_id);
      if (active && (body.op === active || body.op === 'error')) activeReads.delete(body.request_id);
      const match = pending.get(body.request_id);
      if (!match || (body.op !== 'error' && body.op !== match.op && !(!isDaily && body.op === 'status'))) { pumpReads(); return; }
      pending.delete(body.request_id); receive?.({...body, request_id: match.local});
      pumpReads();
    } else if (isDaily && body.op === 'daily_forward_changed') receive?.(body);
    else if (isDaily && body.op === 'news_changed') receive?.(body);
    else if (!isDaily && ['status', 'error'].includes(body.op)) receive?.(body);
  });
  ctx.onUp(() => { if (live || disposed) return; live = true; select.disabled = false; up?.(); });
  ctx.report('ready');
  return {unmount() { if (disposed) return; disposed = true; child?.unmount(); pending.clear(); queuedReads.clear(); activeReads.clear();
    select.removeEventListener('change', change); root.remove(); }};
}
