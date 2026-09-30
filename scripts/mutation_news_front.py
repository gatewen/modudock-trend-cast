#!/usr/local/bin/python3
from scripts import mutation_daily_front as base
T='news status absent publisher';E='news status rejects late'
MUTATIONS=[
 ('news_request_removed','daily.js',[("send('daily_forward'); send('news_status');", "send('daily_forward');")],T),
 ('news_push_ignored','front.js',[("isDaily && body.op === 'news_changed'","false && body.op === 'news_changed'")],T),
 ('news_timestamp_replaced_with_empty','daily.js',[("if(at===null)","if(true)")],T),
 ('news_timestamp_render_unchecked','daily.js',[("else if(typeof at==='string'&&/^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?\\+08:00$/.test(at)&&Number.isFinite(Date.parse(at)))","else if(true)")],T),
 ('news_epoch_guard_removed','front.js',[("if (!match || (body.op !== 'error' && body.op !== match.op && !(!isDaily && body.op === 'status'))) { pumpReads(); return; }","if (!match) { receive?.({...body, request_id: 5}); return; }")],E),
]
if __name__=='__main__':
    base.MUTATIONS=MUTATIONS
    raise SystemExit(base.main())
