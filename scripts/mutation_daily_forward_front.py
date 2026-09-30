#!/usr/local/bin/python3
from scripts import mutation_daily_front as base
MUTATIONS=[
 ('forward_historical_guard_removed','prospective.js',[("if(dates.some(d=>d<v.frozen_day))return false;", "")], 'forward rejects historical'),
 ('forward_freeze_floor_removed','prospective.js',[("||v.frozen_day<'2026-09-27'", "")], 'forward rejects historical'),
 ('forward_horizon_missing','prospective.js',[("const HORIZONS = [3,7,14];", "const HORIZONS = [3,7];")], 'forward displays all'),
 ('forward_cohort_merged','prospective.js',[("['ontime','backfill','unconfirmed']", "['ontime','backfill']")], 'forward displays all'),
 ('probabilities_swapped','prospective.js',[("percent(p.probabilities?.up)", "percent(p.probabilities?.flat)")], 'forward displays all'),
 ('disclaimer_removed','prospective.js',[("el('p',DISCLAIMER,'tc-note')", "el('p','','tc-note')")], 'forward empty latest'),
 ('forward_push_ignored','front.js',[("isDaily && body.op === 'daily_forward_changed'", "false && body.op === 'daily_forward_changed'")], 'forward unsolicited refresh'),
 ('forward_request_removed','daily.js',[("send('daily_forward'); send('news_status');\n  }", "send('news_status');\n  }")], 'daily default seven'),
]
if __name__=='__main__':
    base.MUTATIONS=MUTATIONS
    raise SystemExit(base.main())
