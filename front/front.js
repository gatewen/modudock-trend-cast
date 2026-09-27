import styles from './style.js';
import mountIntraday from './intraday.js';
import mountDaily from './daily.js';

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
  function show() {
    child?.unmount(); epoch++; pending.clear(); receive = up = undefined;
    const generation = epoch;
    const local = {container: slot, H: Number(select.value), report() {},
      channel: {
        onMessage(fn) { receive = fn; },
        send(body) {
          if (!live || disposed || generation !== epoch) return;
          const request_id = ++serial;
          pending.set(request_id, {local: body.request_id, op: body.op});
          if (pending.size > 128) pending.delete(pending.keys().next().value);
          ctx.channel.send({...body, request_id});
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
      const match = pending.get(body.request_id);
      if (!match || (body.op !== 'error' && body.op !== match.op && !(!isDaily && body.op === 'status'))) return;
      pending.delete(body.request_id); receive?.({...body, request_id: match.local});
    } else if (!isDaily && ['status', 'error'].includes(body.op)) receive?.(body);
  });
  ctx.onUp(() => { if (live || disposed) return; live = true; select.disabled = false; up?.(); });
  ctx.report('ready');
  return {unmount() { if (disposed) return; disposed = true; child?.unmount(); pending.clear();
    select.removeEventListener('change', change); root.remove(); }};
}
