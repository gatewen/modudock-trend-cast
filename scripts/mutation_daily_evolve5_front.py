from scripts import mutation_daily_front as runner

runner.MUTATIONS=[
 ('read_limit_removed','front.js',[("const MAX_READS = 6;", "const MAX_READS = 99;")],'rapid horizons 3 7 14 3'),
 ('retired_reply_does_not_drain_queue','front.js',[("{ pumpReads(); return; }", "{ return; }")],'rapid horizons 3 7 14 3'),
 ('old_generation_routed','front.js',[("epoch++; pending.clear(); queuedReads.clear();", "epoch++; queuedReads.clear();")],'holdout late reply cannot cross'),
 ('error_does_not_release_read','front.js',[("body.op === active || body.op === 'error'", "body.op === active")],'queued reads release on errors'),
 ('name_mapping_wrong','method_names.js',[("ens_avg:'組合預測'", "ens_avg:'錯誤名稱'")],'all daily methods have plain'),
 ('code_not_small','method_names.js',[("doc.createElement('small')", "doc.createElement('span')")],'all daily methods have plain'),
]
if __name__=='__main__':raise SystemExit(runner.main())
