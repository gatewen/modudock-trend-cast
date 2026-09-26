#!/usr/local/bin/python3
"""CA fallback and sync failure classification regressions, synthetic data only."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

T = 'tests.test_tls.TLSTests.'
J = 'tests.test_jobs.JobTests.'
MUTATIONS = [
    ('ca_fallback_removed', 'back/http_client.py', [('if path:', 'if False:')],
     T + 'test_empty_default_ca_without_env_all_three_clients_use_verified_fallback'),
    ('empty_ca_connection_guard_removed', 'back/http_client.py',
     [("if not context.cert_store_stats()['x509_ca']:", 'if False:')],
     T + 'test_no_ca_refuses_all_three_clients_before_constructing_opener'),
    ('tls_verification_disabled', 'back/http_client.py',
     [('context.verify_mode = ssl.CERT_REQUIRED', 'context.verify_mode = ssl.CERT_NONE'),
      ('context.check_hostname = True', 'context.check_hostname = False')],
     T + 'test_empty_default_ca_without_env_all_three_clients_use_verified_fallback'),
    ('tls_certificate_reason_lost', 'back/http_client.py',
     [('if isinstance(reason, ssl.SSLCertVerificationError):', 'if False:')],
     T + 'test_tls_and_network_reasons_are_classified_without_exception_context'),
    ('all_failed_reported_partial', 'back/jobs.py',
     [("('partial' if handle.n_ok else 'failed')", "'partial'")],
     J + 'test_sync_all_failed_partial_complete_and_skips_do_not_count_as_success'),
    ('unknown_exception_code_echoed', 'back/jobs.py',
     [("else 'invalid_response'", 'else exc.code')],
     J + 'test_sync_error_reasons_never_reflect_unknown_exception_text'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS = MUTATIONS
    raise SystemExit(mutation_check.main())
