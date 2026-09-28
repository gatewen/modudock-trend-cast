"""One-time holdout freeze, no-label prediction phase and reveal boundaries."""
from scripts import mutation_check
R='back/daily_hold_run.py';M='back/daily_hold_model.py';D='back/daily_hold_store.py';V='back/daily_reveal.py'
T='tests.test_daily_holdout.HoldoutTests.'
MUTATIONS=[
 ('progress_leaks_held_count',R,[("progress(dict(stage='predicting'))","progress(dict(stage='predicting',total=len(points)))")],T+'test_all_horizons_before_first_outcome_and_reveal'),
 ('labels_read_during_prediction',R,[("written=0","store.db.execute('SELECT label FROM d_hold_outcomes').fetchall()\n    written=0")],T+'test_held_labels_do_not_affect_frozen_predictions'),
 ('holdout_outcome_in_features',R,[("history=frames(store,start=row['config']['start'],end=HOLD_END)","DailyReplay(store).outcome(HOLD_START,3,end_limit=HOLD_END)\n    history=frames(store,start=row['config']['start'],end=HOLD_END)")],T+'test_real_bounded_input_reads_exclude_exposed_suffix'),
 ('use_first_logit_fit',M,[("ORDER BY day DESC LIMIT 1","ORDER BY day ASC LIMIT 1")],T+'test_development_last_fit_and_full_frequency_no_refit'),
 ('refit_ind_logit',M,[("from fractions import Fraction","from fractions import Fraction\nfrom dataclasses import asdict"),("ind_logit=last_fit(store,'d_fits',H)","ind_logit=asdict(Logistic.fit(records,points[-1]))")],T+'test_development_last_fit_and_full_frequency_no_refit'),
 ('refit_mkt_logit',M,[("from fractions import Fraction","from fractions import Fraction\nfrom dataclasses import asdict"),("mkt_logit=last_fit(store,'market_fits',H)","mkt_logit=asdict(MarketLogistic.fit(records,points[-1]))")],T+'test_development_last_fit_and_full_frequency_no_refit'),
 ('frequency_only_last_fit_training',M,[("majority=probabilities(r.truth for r in records)","majority=probabilities(r.truth for r in records[:250])")],T+'test_development_last_fit_and_full_frequency_no_refit'),
 ('ens_only_majority',M,[("out['ens_avg']=average_current({name:out[name] for name in COMPONENTS})","out['ens_avg']=prediction('ens_avg',majority)")],T+'test_ensemble_is_frozen_current_mean_and_models_dont_mutate'),
 ('exposed_date_allowed',M,[("HOLD_START<=point.day<=HOLD_END","HOLD_START<=point.day")],T+'test_ensemble_is_frozen_current_mean_and_models_dont_mutate'),
 ('omit_fourteen_day',R,[("for H in HORIZONS:\n                    for row", "for H in (3,7):\n                    for row")],T+'test_all_horizons_before_first_outcome_and_reveal'),
 ('ignore_missing_prediction_and_seal',R,[("if actual!=expected or features!=expected_features:","if features!=expected_features:"),("if saved is None or any(saved[k]!=v for k,v in complete.items()):", "if False:")],T+'test_missing_any_method_any_horizon_blocks_all_outcomes'),
 ('no_completion_check',R,[("if saved is None or any(saved[k]!=v for k,v in complete.items()):", "if False:")],T+'test_completion_seal_required_even_when_predictions_are_complete'),
 ('report_before_reveal',R,[("require_revealed(store)  # Before every feature, outcome or score read.","pass  # Removed reveal gate.")],T+'test_locked_report_cli_and_existing_views_never_read_held_results'),
 ('missing_report_seal',R,[("sha(outcomes)!=seal['outcome_digest']","False")],T+'test_held_labels_do_not_affect_frozen_predictions'),
 ('ignore_reveal_record',V,[("return bool(store.db.execute(","return True or bool(store.db.execute(")],T+'test_all_horizons_before_first_outcome_and_reveal'),
 ('forget_used_status',V,[("if revealed(store):return dict(state='used',message='已使用（一次性保留段考試已揭露）')","if False:return dict(state='used',message='已使用（一次性保留段考試已揭露）')")],T+'test_all_horizons_before_first_outcome_and_reveal'),
 ('four_primary_tests',M,[("PRIMARY=((3,'ens_avg'),(7,'ens_avg'),(3,'mkt_logit'))","PRIMARY=((3,'ens_avg'),(7,'ens_avg'),(3,'mkt_logit'),(7,'mkt_logit'))")],T+'test_four_verdicts_and_exactly_three_comparisons'),
 ('wrong_lucky_count',M,[("expected_lucky=.075","expected_lucky=.025")],T+'test_four_verdicts_and_exactly_three_comparisons'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
