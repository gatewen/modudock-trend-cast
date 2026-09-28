from scripts import mutation_daily_front as runner

runner.MUTATIONS=[
 ('locked_scores_leak','daily.js',[("if(body.state==='locked')", "if(false)"), ("body.state!=='used'||", "")],'holdout results require'),
 ('holdout_scope_removed','daily.js',[("body.split!=='holdout'||", "")],'holdout results require'),
 ('holdout_horizon_removed','daily.js',[("if(body.H!==H||body.experiment_id!==4||body.split!=='holdout'", "if(body.experiment_id!==4||body.split!=='holdout'")],'holdout results require'),
 ('holdout_exposed_end_allowed','daily.js',[("||body.last_day!=='2024-07-25'", "")],'holdout results require'),
 ('holdout_difference_swapped','daily.js',[("signed(r.difference)", "signed(-r.difference)")],'revealed holdout shows'),
 ('holdout_interval_sign_lost','daily.js',[("r.ci95.map(signed)", "r.ci95.map(v=>signed(Math.abs(v)))")],'revealed holdout shows'),
 ('descriptive_method_lost','daily.js',[("METHODS.includes(r.method)&&r.method!=='jev_ind'", "METHODS.includes(r.method)&&!['jev_ind','mkt_logit'].includes(r.method)")],'revealed holdout shows'),
 ('research_conclusion_lost','daily.js',[("三者都沒有證據比「猜最常見答案」好", "三者已證明有效")],'revealed holdout shows'),
]
if __name__=='__main__':raise SystemExit(runner.main())
