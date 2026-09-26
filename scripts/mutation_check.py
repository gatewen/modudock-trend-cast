#!/usr/local/bin/python3
"""Run deliberate guard removals in throwaway copies, never the working source."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Each mutation must turn its named previously-green behavior test red.
MUTATIONS = [
    ('throttle_removed', 'back/http_client.py', [('self._limiter.wait()', 'pass')],
     'tests.test_clients.ClientTests.test_rate_limit_shared_across_requests'),
    ('429_retry_removed', 'back/http_client.py', [('response.status == 429 and attempt < 3', 'False')],
     'tests.test_clients.ClientTests.test_429_backoff_and_retry_limit'),
    ('auth_latch_removed', 'back/http_client.py', [('_DISABLED.add(self.host)', 'pass')],
     'tests.test_clients.ClientTests.test_401_403_disable_process_including_new_clients'),
    ('redirect_guard_removed', 'back/http_client.py',
     [('        return None\n', '        return super().redirect_request(req, fp, code, msg, headers, newurl)\n')],
     'tests.test_clients.ClientTests.test_redirect_not_followed_or_key_forwarded'),
    ('body_cap_doubled', 'back/http_client.py', [('MAX_BYTES = 16 * 1024 * 1024', 'MAX_BYTES = 32 * 1024 * 1024')],
     'tests.test_clients.ClientTests.test_oversize_declared_and_streamed_body'),
    ('secret_sanitizer_removed', 'back/http_client.py',
     [("except Exception as exc:\n            error = ClientError(transport_error_code(exc))",
       'except Exception as exc:\n            error = ClientError(str(exc))')],
     'tests.test_clients.ClientTests.test_errors_never_leak_key_or_upstream_exception_context'),
    ('rollback_replaced_with_commit', 'back/store.py', [('self.db.rollback()', 'self.db.commit()')],
     'tests.test_store.StoreTests.test_mid_transaction_failure_rolls_back_deletes_inserts_and_log'),
    ('frozen_guard_removed', 'back/store.py', [('return any(start <= day <= end for start, end in ranges)', 'return False')],
     'tests.test_store.StoreTests.test_frozen_bars_protect_updates_deletes_and_insertions'),
    ('frozen_daily_guard_removed', 'back/store.py', [('return any(start <= day <= end for start, end in ranges)', 'return False')],
     'tests.test_store.StoreTests.test_frozen_warmup_daily_corp_and_coverage'),
    ('bar_end_replaced_with_raw', 'back/store.py', [('AND bar_end <= ?', 'AND ts_raw <= ?')],
     'tests.test_store.StoreTests.test_available_bars_boundary_raw_at_t_excluded_end_at_t_included'),
    ('cutoff_plus_60_seconds', 'back/store.py',
     [('AND bar_end <= ?', 'AND bar_end < ?'),
      ("cutoff.isoformat(timespec='microseconds')", "(cutoff + timedelta(seconds=60)).isoformat(timespec='microseconds')")],
     'tests.test_store.StoreTests.test_available_bars_midminute_catches_plus_60_seconds_mutation'),
    ('auction_exception_removed', 'back/data.py',
     [('stamp if stamp.time() == time(13, 30) else stamp + timedelta(minutes=1)', 'stamp + timedelta(minutes=1)')],
     'tests.test_store.StoreTests.test_auction_timezone_and_day_isolation'),
    ('unknown_treated_as_none', 'back/store.py',
     [("return {'state': 'unknown', 'event': None}", "return {'state': 'none', 'event': None}")],
     'tests.test_store.StoreTests.test_corp_three_states_and_no_stale_event_after_replacement'),
    ('twse_range_check_removed', 'back/twse.py',
     [("if payload.get('strDate') != start.replace('-', '') or payload.get('endDate') != end.replace('-', ''):", 'if False:')],
     'tests.test_clients.ClientTests.test_twse_fixture_fields_and_three_state_prerequisites'),
    ('current_month_refresh_removed', 'back/store.py',
     [('if first >= today.replace(day=1):\n            return True', 'if first >= today.replace(day=1):\n            return False')],
     'tests.test_store.StoreTests.test_finalized_month_skipped_current_and_recent_seven_refetched'),
    ('dev_labels_include_holdout', 'scripts/check_data.py',
     [("_labels(store, experiment['id'], experiment['dev_start'], experiment['dev_end'])",
       "_labels(store, experiment['id'], experiment['dev_start'], experiment['hold_end'])")],
     'tests.test_check_data.CheckDataTests.test_dev_only_then_explicit_reveal_persists_before_report'),
]


def main():
    root = ROOT / 'data'
    root.mkdir(exist_ok=True)
    killed = 0
    for name, filename, replacements, test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix='mutation-', dir=root) as work:
            work = Path(work)
            for directory in ('back', 'scripts', 'tests'):
                shutil.copytree(ROOT / directory, work / directory,
                                ignore=shutil.ignore_patterns('__pycache__'))
            path = work / filename
            source = path.read_text()
            for old, new in replacements:
                if source.count(old) != 1:
                    raise RuntimeError('mutation anchor is not unique: ' + name)
                source = source.replace(old, new)
            path.write_text(source)
            result = subprocess.run([sys.executable, '-B', '-m', 'unittest', '-v', test],
                                    cwd=work, text=True, capture_output=True, timeout=30)
            # Import/syntax errors do not count as a killed behavior mutation.
            method = test.rsplit('.', 1)[-1]
            red = (result.returncode != 0
                   and any(prefix + method in result.stderr for prefix in ('FAIL: ', 'ERROR: '))
                   and not any(error in result.stderr for error in
                               ('ImportError', 'ModuleNotFoundError', 'SyntaxError')))
            killed += int(red)
            print(f'{name}: {"KILLED" if red else "SURVIVED/INVALID"} -> {test}')
            if not red:
                print(result.stdout + result.stderr)
    print(f'{killed}/{len(MUTATIONS)} behavior mutations killed')
    return 0 if killed == len(MUTATIONS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
