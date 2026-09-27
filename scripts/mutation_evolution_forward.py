#!/usr/local/bin/python3
"""Frozen forward inference and two prespecified comparisons, synthetic only."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
F='tests.test_evolution_forward.FrozenVolTests.'
R='tests.test_evolution_forward.EvolutionForwardTests.'
C='tests.test_evolution_forward.ForwardComparisonTests.'
M='back/evolution_forward.py'
MUTATIONS=[
 ('fit_all_labels_including_hidden',M,[('WalkForward(development.engine, development.records).matured(SimpleNamespace(t=cutoff))','development.records')],F+'test_fit_only_matured_eligible_dev_and_includes_last_point'),
 ('last_dev_outcome_excluded',M,[("plan.dev_end + 'T13:30:00'","plan.dev_end + 'T13:29:59'")],F+'test_fit_only_matured_eligible_dev_and_includes_last_point'),
 ('frozen_terciles_wrong',M,[('(percentile(values, 1/3), percentile(values, 2/3))','(percentile(values, 1/4), percentile(values, 3/4))')],F+'test_fit_only_matured_eligible_dev_and_includes_last_point'),
 ('frozen_ties_go_higher',M,[('from bisect import bisect_left','from bisect import bisect_right as bisect_left')],F+'test_frozen_groups_30_threshold_smoothing_lower_ties_and_fallback'),
 ('frozen_group_min_29',M,[('if sum(group) >= MIN_SAMPLES:', 'if sum(group) >= MIN_SAMPLES-1:')],F+'test_frozen_groups_30_threshold_smoothing_lower_ties_and_fallback'),
 ('frozen_group_min_31',M,[('if sum(group) >= MIN_SAMPLES:', 'if sum(group) > MIN_SAMPLES:')],F+'test_frozen_groups_30_threshold_smoothing_lower_ties_and_fallback'),
 ('global_counts_instead_of_group',M,[('counts = group','counts = self.majority')],F+'test_frozen_groups_30_threshold_smoothing_lower_ties_and_fallback'),
 ('fallback_uses_first_group',M,[('counts = self.majority','counts = self.groups[0]')],F+'test_frozen_groups_30_threshold_smoothing_lower_ties_and_fallback'),
 ('source_digest_not_frozen',M,[("saved['source_digest'] != development.source_digest",'False')],R+'test_frozen_parameters_cannot_be_replaced_or_refit_to_new_day'),
 ('parameters_not_frozen',M,[("saved['parameters_json'] != model.serialize()",'False')],R+'test_frozen_parameters_cannot_be_replaced_or_refit_to_new_day'),
 ('forward_truth_read_during_inference',M,[('point = engine.prepare(t)','point = engine.prepare(t)\n            engine.outcome(point)')],R+'test_offline_forward_never_reads_forward_truth_or_calls_network_and_no_reveal'),
 ('inference_auto_reveals',M,[('        check()\n        return dict(points=', "        store.db.execute(\"INSERT INTO reveals VALUES (1,'fixture','2000-01-01','2099-12-31','all','forward')\")\n        check()\n        return dict(points=")],R+'test_offline_forward_never_reads_forward_truth_or_calls_network_and_no_reveal'),
 ('cancel_guard_removed',M,[('if cancel is not None and cancel.is_set():','if False:')],R+'test_cancel_rolls_back_new_model_and_predictions_and_other_experiment_refused'),
 ('progress_counts_any_method',M,[('set(FORWARD_METHODS) <= values','bool(values)')],R+'test_locked_status_only_explicit_metadata_and_never_scores'),
 ('vol_excluded_from_reveal','back/forward.py',[("for method in FORWARD_METHODS:\n            record", "for method in FORWARD_METHODS[:-1]:\n            record")],R+'test_missing_or_wrong_vol_answer_blocks_reveal_and_both_comparisons_required'),
 ('wrong_but_valid_vol_answer_allowed','back/forward.py',[("if method == 'vol_prior':","if False:")],R+'test_missing_or_wrong_vol_answer_blocks_reveal_and_both_comparisons_required'),
 ('vol_excluded_from_score','back/score.py',[("methods_in_split = FORWARD_METHODS if split == 'forward' else ALL_METHODS",'methods_in_split = ALL_METHODS')],R+'test_missing_or_wrong_vol_answer_blocks_reveal_and_both_comparisons_required'),
 ('unrevealed_report_built','back/report.py',[('            if forward_days:', '            if True:')],R+'test_locked_status_only_explicit_metadata_and_never_scores'),
 ('vol_comparison_sign_reversed','back/score.py',[("common['vol_prior'][t].brier - common['majority'][t].brier","common['majority'][t].brier - common['vol_prior'][t].brier")],C+'test_same_six_method_intersection_both_results_report_even_if_opposite'),
 ('vol_bootstrap_uses_jev','back/score.py',[("boot = paired_bootstrap(common['vol_prior'], common['majority'])","boot = paired_bootstrap({t:data.scored['jev'][t] for t in data.common}, common['majority'])")],C+'test_same_six_method_intersection_both_results_report_even_if_opposite'),
 ('unfavorable_comparison_hidden','back/score.py',[("result['comparisons'] = comparisons","result['comparisons'] = {k:v for k,v in comparisons.items() if v['brier_difference'] < 0}")],C+'test_same_six_method_intersection_both_results_report_even_if_opposite'),
 ('evolution_view_writes','back/evolution_run.py',[('    data = read_development(store, experiment_id)\n    for point, outcome, _ in data.points:',"    store.db.execute(\"INSERT INTO runs(experiment_id,method,split,started_at,status) VALUES (1,'vol_prior','dev','fixture','complete')\")\n    data = read_development(store, experiment_id)\n    for point, outcome, _ in data.points:")],R+'test_development_view_is_readonly_and_matches_round2_report'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
