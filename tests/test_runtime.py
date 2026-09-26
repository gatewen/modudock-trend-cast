from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from back.db_writer import DBWriter
from back.runtime import Application
from back.store import Store
from tests.score_fixture import scored_fixture
from tests.test_jobs import FakeTwse, GateFugle
from tests.test_jev_runner import ControlledClient


class Sink:
    def __init__(self):
        self.values = []
        self.condition = threading.Condition()

    def put(self, packet):
        with self.condition:
            self.values.append(packet['body']); self.condition.notify_all()
        return True

    def wait(self, predicate, timeout=5):
        with self.condition:
            if not self.condition.wait_for(lambda: any(predicate(v) for v in self.values), timeout):
                raise AssertionError('output deadline')
            return next(v for v in reversed(self.values) if predicate(v))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'app.sqlite3'
        with Store(self.path) as store:
            self.row, self.days = scored_fixture(store)
        self.writer = DBWriter(self.path); self.writer.ready.result(3)
        self.sink = Sink(); self.client = ControlledClient(blocked=True); self.fugle = GateFugle(blocked=True)
        self.keys = {'fugle': 'missing', 'typesafe': 'available'}
        self.keys_patch = patch('back.runtime.key_state', side_effect=lambda: dict(self.keys)); self.keys_patch.start()
        self.app = Application(self.writer, self.sink, 127, jev_client=self.client, fugle=self.fugle, twse=FakeTwse())
        self.addCleanup(self.close)
        self.app.start()
        self.sink.wait(lambda b: b.get('op') == 'status' and b.get('experiment_id') == 1)

    def close(self):
        self.app.close(); self.client.release.set(); self.fugle.release.set()
        self.writer.thread.join(3)
        for worker in self.app.jev.workers: worker.join(3)
        self.app.sync.worker.join(3); self.app.reader.join(3)
        self.keys_patch.stop()

    def query(self, sql, args=()):
        with Store(self.path, readonly=True) as store:
            return [dict(r) for r in store.db.execute(sql, args)]

    def test_start_and_reads_make_no_jev_calls_and_only_return_authorized_days(self):
        self.assertEqual(self.client.calls, [])
        view = self.sink.values[-1]
        self.assertEqual((view['days'][0], view['days'][-1]), (self.row['dev_start'], self.row['dev_end']))
        self.app.request({'op': 'day', 'date': self.row['dev_start'], 'request_id': 20})
        value = self.sink.wait(lambda b: b.get('op') == 'day' and b.get('request_id') == 20)
        self.assertEqual(len(value['bars']), 266); self.assertEqual(len(value['points']), 8)
        self.app.request({'op': 'report', 'request_id': 21})
        report = self.sink.wait(lambda b: b.get('op') == 'report' and b.get('request_id') == 21)
        self.assertEqual(report['holdout'], {'state': 'locked', 'message': '保留段未解鎖'})
        self.assertNotIn(self.row['hold_start'], json.dumps(report))
        self.assertEqual(self.client.calls, [])

    def test_holdout_run_progress_and_all_exits_hide_outcomes_until_explicit_reveal(self):
        self.app.request({'op': 'run', 'method': 'jev', 'split': 'holdout'})
        self.assertTrue(self.client.wait_calls(6))
        self.client.release.set()
        self.sink.wait(lambda b: b.get('replay', {}).get('status') == 'complete')
        self.app.request({'op': 'day', 'date': self.row['hold_start'], 'request_id': 3})
        day = self.sink.wait(lambda b: b.get('op') == 'day')
        self.assertEqual(set(day), {'op', 'status', 'message', 'request_id'})
        self.assertEqual(day['status'], 'holdout_locked')
        for value in self.sink.values:
            if value.get('replay', {}).get('split') == 'holdout':
                self.assertEqual(set(value['replay']), {'status', 'method', 'split'})
        self.assertEqual(self.query('SELECT count(*) n FROM reveals')[0]['n'], 0)

    def test_reveal_requires_confirmation_current_experiment_and_successful_commit(self):
        self.app.request({'op': 'reveal', 'experiment_id': 1})
        self.sink.wait(lambda b: b.get('code') == 'confirmation_required')
        self.app.request({'op': 'reveal', 'experiment_id': 2, 'confirmed': True})
        self.sink.wait(lambda b: b.get('code') == 'stale_experiment')
        self.assertEqual(self.query('SELECT count(*) n FROM reveals')[0]['n'], 0)
        self.writer.submit(lambda db: db.db.execute("CREATE TRIGGER deny_reveal BEFORE INSERT ON reveals BEGIN SELECT RAISE(ABORT, 'fixture'); END")).result(3)
        self.app.request({'op': 'reveal', 'experiment_id': 1, 'confirmed': True, 'request_id': 8})
        self.sink.wait(lambda b: b.get('op') == 'error' and b.get('request_id') == 8)
        self.assertEqual(self.query('SELECT count(*) n FROM reveals')[0]['n'], 0)
        self.writer.submit(lambda db: db.db.execute('DROP TRIGGER deny_reveal')).result(3)
        self.app.request({'op': 'reveal', 'experiment_id': 1, 'confirmed': True})
        value = self.sink.wait(lambda b: b.get('op') == 'status' and b.get('holdout', {}).get('state') == 'revealed')
        self.assertIn(self.row['hold_start'], value['days'])
        self.assertEqual(self.query('SELECT count(*) n FROM reveals')[0]['n'], 1)

    def test_sync_and_replay_can_coexist_cancel_preserves_sync_new_experiment_refused(self):
        self.app.request({'op': 'run', 'method': 'jev', 'split': 'holdout'})
        self.assertTrue(self.client.wait_calls(6))
        self.keys['fugle'] = 'available'
        self.app.request({'op': 'sync'})
        self.assertTrue(self.fugle.entered.wait(3))
        sync = self.app.sync._active
        self.assertTrue(self.app.gate.busy('replay')); self.assertTrue(self.app.gate.busy('sync'))
        self.app.request({'op': 'new_experiment', 'confirmed': True})
        self.sink.wait(lambda b: b.get('code') == 'busy')
        self.assertEqual(self.query('SELECT count(*) n FROM experiments')[0]['n'], 1)
        self.app.request({'op': 'cancel'})
        self.sink.wait(lambda b: b.get('replay', {}).get('status') == 'cancelled')
        self.assertFalse(sync.cancel_event.is_set()); self.assertTrue(self.app.gate.busy('sync'))

    def test_duplicate_run_and_sync_are_busy_and_cannot_start_extra_workers(self):
        self.app.request({'op': 'run', 'method': 'jev', 'split': 'holdout'})
        self.assertTrue(self.client.wait_calls(6))
        threads = threading.active_count()
        for i in range(3): self.app.request({'op': 'run', 'method': 'always_flat', 'split': 'dev', 'request_id': i})
        self.assertEqual(sum(b.get('code') == 'busy' for b in self.sink.values), 3)
        self.assertEqual(threading.active_count(), threads)

    def test_new_experiment_changes_settings_and_discards_already_read_old_report(self):
        entered, release = threading.Event(), threading.Event()
        import back.runtime
        original = back.runtime.build_report
        def blocked(store, **kwargs):
            if not entered.is_set():
                entered.set(); release.wait(4)
                return {'status': 'ok', 'dev': 'PRIVATE-OLD'}
            return original(store, **kwargs)
        with patch('back.runtime.build_report', side_effect=blocked):
            self.app.request({'op': 'report'}); self.assertTrue(entered.wait(3))
            self.app.request({'op': 'new_experiment', 'confirmed': True, 'threshold_permille': 5,
                              'start': self.row['dev_start'], 'end': self.row['hold_end']})
            self.writer.submit(lambda db: None).result(3)
            self.assertGreater(self.app._view_generation, 0)
            release.set()
            self.sink.wait(lambda b: b.get('op') == 'status' and b.get('experiment_id') == 2)
        self.assertEqual(self.query('SELECT threshold_permille FROM experiments WHERE id=2')[0]['threshold_permille'], 5)
        self.assertNotIn('PRIVATE-OLD', json.dumps(self.sink.values))

    def test_missing_and_invalid_keys_block_only_corresponding_operation(self):
        self.keys['typesafe'] = 'invalid'
        self.app.request({'op': 'run', 'method': 'jev', 'split': 'dev', 'request_id': 1})
        self.sink.wait(lambda b: b.get('code') == 'auth_disabled')
        self.app.request({'op': 'sync', 'request_id': 2})
        self.sink.wait(lambda b: b.get('code') == 'missing_key')
        self.app.request({'op': 'run', 'method': 'always_flat', 'split': 'dev'})
        self.sink.wait(lambda b: b.get('replay', {}).get('status') == 'complete')
        self.assertEqual(self.client.calls, [])

    def test_old_sync_and_replay_progress_generations_cannot_replace_current_status(self):
        self.app._on_sync({'generation': 10, 'status': 'complete'})
        self.app._on_sync({'generation': 9, 'status': 'running'})
        self.app._on_replay({'generation': 11, 'status': 'complete', 'method': 'jev', 'split': 'dev'})
        self.app._on_replay({'generation': 10, 'status': 'running', 'method': 'jev', 'split': 'dev'})
        self.assertEqual(self.app._sync, {'generation': 10, 'status': 'complete'})
        self.assertEqual(self.app._replay['generation'], 11); self.assertEqual(self.app._replay['status'], 'complete')
        self.app._on_replay({'generation': 11, 'status': 'running', 'method': 'jev', 'split': 'dev'})
        self.assertEqual(self.app._replay['status'], 'complete')

    def test_enabled_fugle_auto_syncs_on_up_and_jev_still_needs_explicit_run(self):
        self.keys['fugle'] = 'available'
        with patch.object(self.app.sync, 'start', return_value=SimpleNamespace(generation=1)) as sync, \
             patch.object(self.app.jev, 'start') as jev:
            self.app.start()
            sync.assert_called_once_with(symbol='2330'); jev.assert_not_called()

    def test_creation_reserves_gate_before_db_queue_and_rejects_duplicate_or_replay(self):
        entered, release = threading.Event(), threading.Event()
        binding_reservations = []
        changed = self.app._changed
        def bind(future, request_id):
            binding_reservations.append(self.app.gate.busy('create'))
            changed(future, request_id)
        self.app._changed = bind
        def blocked(store): entered.set(); release.wait(3)
        self.writer.submit(blocked); self.assertTrue(entered.wait(1))
        try:
            self.app.request({'op': 'new_experiment', 'confirmed': True, 'experiment_id': 1})
            self.assertTrue(self.app.gate.busy('create'))
            self.app.request({'op': 'new_experiment', 'confirmed': True, 'experiment_id': 1, 'request_id': 2})
            self.app.request({'op': 'run', 'method': 'always_flat', 'split': 'dev', 'request_id': 3})
            for request_id in (2, 3):
                self.sink.wait(lambda b: b.get('request_id') == request_id and b.get('code') == 'busy')
        finally:
            release.set()
        self.sink.wait(lambda b: b.get('op') == 'status' and b.get('experiment_id') == 2)
        self.assertEqual(self.query('SELECT count(*) n FROM experiments')[0]['n'], 2)
        self.assertEqual(binding_reservations, [True])
        self.writer.submit(lambda db: None).result(3)
        self.assertFalse(self.app.gate.busy('create'))
        self.assertEqual(self.app._replay, {'status': 'idle'})

    def test_failed_creation_releases_reservation_and_reveal_keeps_bound_experiment(self):
        self.app.request({'op': 'new_experiment', 'confirmed': True, 'start': '2099-01-01', 'request_id': 81})
        self.sink.wait(lambda b: b.get('op') == 'error' and b.get('request_id') == 81)
        self.writer.submit(lambda db: None).result(3)
        self.assertFalse(self.app.gate.busy('create'))
        self.assertEqual(self.app._experiment, 1)
        self.app.request({'op': 'reveal', 'confirmed': True, 'experiment_id': 1})
        self.sink.wait(lambda b: b.get('op') == 'status' and b.get('holdout', {}).get('state') == 'revealed')
        self.assertEqual(self.app._experiment, 1)

    def test_close_does_not_wait_on_read_worker_and_discards_its_late_output(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(*a, **k):
            entered.set(); release.wait(3); return {'status': 'ok', 'secret': 'LATE'}
        with patch('back.runtime.build_report', side_effect=blocked):
            self.app.request({'op': 'report'}); self.assertTrue(entered.wait(3))
            before = len(self.sink.values); started = time.monotonic(); self.app.close()
            self.assertLess(time.monotonic() - started, .1)
            release.set(); self.app.reader.join(3)
        self.assertEqual(len(self.sink.values), before)
