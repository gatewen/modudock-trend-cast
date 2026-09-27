#!/usr/local/bin/python3
"""Prompt compatibility, routing, privacy and pre-HTTP comparison mutations."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

P = 'tests.test_prompts.PromptTests.'
R = 'tests.test_run_dev.RunDevTests.'
MUTATIONS = [
    ('default_p1_changed_to_p2', 'back/prompts.py', [("PROMPT_VERSION = 'p1'", "PROMPT_VERSION = 'p2'")],
     P + 'test_p1_actual_http_body_byte_for_byte_matches_pre_p2_golden'),
    ('p2_field_explanation_removed', 'back/prompts.py', [('clock 現在時刻；', '')],
     P + 'test_p2_http_payload_only_changes_instructions_and_preserves_privacy_and_threshold'),
    ('p2_prior_flat_wrong', 'back/prompts.py', [("'flat': 51", "'flat': 50")],
     P + 'test_p2_http_payload_only_changes_instructions_and_preserves_privacy_and_threshold'),
    ('p2_private_symbol_added', 'back/jevcast.py',
     [("        'instructions': instructions(prompt_version),", "        'instructions': instructions(prompt_version) + point.symbol,")],
     P + 'test_p2_http_payload_only_changes_instructions_and_preserves_privacy_and_threshold'),
    ('unknown_prompt_falls_back', 'back/prompts.py', [('if prompt_version not in PROMPT_VERSIONS:', 'if False:')],
     P + 'test_unsupported_prompt_refused_before_http_and_experiment_write'),
    ('frozen_prompt_ignores_requested_version', 'back/experiment.py',
     [('feature_version=FEATURE_VERSION, prompt_version=prompt_version,', 'feature_version=FEATURE_VERSION, prompt_version=PROMPT_VERSION,')],
     P + 'test_prompt_frozen_into_digest_with_identical_split_and_f1_inputs'),
    ('runner_routes_p2_as_p1', 'back/jevcast.py', [('prompt_version=handle.prompt_version)', "prompt_version='p1')")],
     R + 'test_cli_selected_p2_experiment_routes_prompt_and_checks_reference_before_http'),
    ('client_ignores_prompt_version', 'back/jevcast.py',
     [('request_payload(point, model, prompt_version=prompt_version)', 'request_payload(point, model)')],
     P + 'test_p2_http_payload_only_changes_instructions_and_preserves_privacy_and_threshold'),
    ('cli_ignores_selected_experiment', 'scripts/run_dev.py', [('experiment_id=args.experiment,', 'experiment_id=1,')],
     R + 'test_cli_selected_p2_experiment_routes_prompt_and_checks_reference_before_http'),
    ('reference_split_guard_removed', 'scripts/run_dev.py',
     [('if experiment_id == reference_id or any(row[key] != reference[key] for key in fields):', 'if False:')],
     R + 'test_reference_split_mismatch_stops_before_preparation_or_http'),
    ('reference_baseline_guard_removed', 'scripts/run_dev.py', [('if not current or current != prior:', 'if False:')],
     R + 'test_reference_baseline_mismatch_stops_before_any_http'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS = MUTATIONS
    raise SystemExit(mutation_check.main())
