#!/usr/local/bin/python3
from scripts import mutation_daily_front as base
MUTATIONS=[
 ('news_prediction_hidden','prospective.js',[("'jev_ind','jev_news'","'jev_ind'")],'news predictions show'),
 ('no_snapshot_says_missing','prospective.js',[("absent?'該日無新聞資料':'缺答'","'缺答'")],'news enabled reception'),
 ('enabled_status_ignored','daily.js',[("typeof body.jev_news_enabled!=='boolean'","body.jev_news_enabled!==false")],'news enabled reception'),
 ('comparison_hidden','prospective.js',[("for(const baseline of ['jev_ind','majority'])","for(const baseline of ['majority'])")],'news predictions show'),
 ('sixty_day_caveat_removed','prospective.js',[("p.small_sample?'；未滿 60 個到期日。':''","''")],'news predictions show'),
]
if __name__=='__main__':
    base.MUTATIONS=MUTATIONS
    raise SystemExit(base.main())
