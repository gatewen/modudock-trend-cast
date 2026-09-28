from scripts import mutation_daily_front as runner

runner.MUTATIONS=[
 ('used_holdout_hidden','daily.js',[("holdText.textContent='已使用（一次性保留段考試已揭露）'","holdText.textContent='未使用'")],'daily holdout used status'),
 ('unadmitted_holdout_status','daily.js',[("body.split!=='dev'||","")],'daily holdout used status'),
]
if __name__=='__main__':raise SystemExit(runner.main())
