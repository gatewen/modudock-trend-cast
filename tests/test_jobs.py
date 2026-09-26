from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from back.data import DataError
from back.db_writer import DBWriter
from back.experiment import ActivityGate
from back.jobs import BaselineRunner, SyncRunner
from back.http_client import ClientError
from back.replay import Replay
from back.store import Store
from tests.experiment_fixture import frozen_experiment, seed_experiment
from tests.helpers import batch, candle, corp


class GateFugle:
    def __init__(self, blocked=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        if not blocked:
            self.release.set()

    def candles(self, symbol, start, end, timeframe):
        self.calls.append((start, end, timeframe, threading.current_thread().name))
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError('fixture timeout')
        row = candle(start + 'T09:00:00+08:00') if timeframe == '1' else dict(date=start, open='100', high='101', low='99', close='100.3', volume='10000')
        return batch([row], start, end, timeframe)


class FakeTwse:
    def events(self, symbol, start, end):
        return corp(start, end, event_day=None)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'jobs.sqlite3'
        with Store(self.path) as store:
            self.days = seed_experiment(store)
            self.row = frozen_experiment(store, self.days, end=self.days[30])
        self.gate = ActivityGate()
        self.writer = DBWriter(self.path); self.writer.ready.result(3)
        self.runners = []
        self.addCleanup(self.close)

    def close(self):
        for runner in self.runners:
            if isinstance(runner, SyncRunner):
                runner.fugle.release.set()
            runner.close()
        self.writer.close(); self.writer.thread.join(3)

    def read(self, query, args=()):
        with Store(self.path, readonly=True) as store:
            return [dict(row) for row in store.db.execute(query, args)]

    def local(self, **kwargs):
        runner = BaselineRunner(self.writer, self.gate, **kwargs); self.runners.append(runner); return runner

    def sync(self, **kwargs):
        runner = SyncRunner(self.writer, self.gate, twse=FakeTwse(), **kwargs); self.runners.append(runner); return runner

    def test_all_baselines_persist_outcomes_after_commit_and_resume_only_missing(self):
        def progress(value):
            rows = self.read('SELECT count(*) n FROM predictions WHERE method=?', (value['method'],))
            self.assertEqual(value['n_ok'], rows[0]['n'])
            self.assertGreaterEqual(self.read('SELECT count(*) n FROM outcomes')[0]['n'], value['n_ok'])
        runner = self.local(on_progress=progress)
        for method in ('always_flat', 'majority', 'momentum', 'reversal'):
            result = runner.start(self.row['id'], method=method).done.result(8)
            self.assertEqual((result['status'], result['n_ok'], result['n_fail']), ('complete', 32, 0))
        runner.on_progress = None
        again = runner.start(self.row['id'], method='majority').done.result(8)
        self.assertEqual((again['n_ok'], again['skipped']), (0, 32))
        self.assertEqual(self.read('SELECT count(*) n FROM predictions')[0]['n'], 128)
        self.assertEqual(self.read('SELECT count(*) n FROM outcomes')[0]['n'], 32)
        self.assertTrue(all(row['full_split'] == 1 for row in self.read('SELECT * FROM run_scopes')))

    def test_holdout_majority_uses_all_eligible_development_labels_but_never_holdout_labels(self):
        runner = self.local()
        runner.start(self.row['id'], method='majority', split='holdout').done.result(8)
        rows = self.read("SELECT probs_json FROM predictions WHERE method='majority' ORDER BY t")
        self.assertEqual(len(rows), 16)
        expected = {'up': 1/35, 'flat': 33/35, 'down': 1/35}
        for row in rows:
            self.assertEqual(json.loads(row['probs_json']), expected)
        self.assertEqual(self.read('SELECT count(*) n FROM reveals')[0]['n'], 0)

    def test_cancel_during_point_discards_it_and_new_generation_resumes(self):
        entered, release = threading.Event(), threading.Event()
        original = Replay.prepare
        count = 0
        def blocked(engine, t):
            nonlocal count
            count += 1
            if count == 2:
                entered.set(); release.wait(4)
            return original(engine, t)
        runner = self.local()
        with patch.object(Replay, 'prepare', blocked):
            handle = runner.start(self.row['id'], method='always_flat')
            self.assertTrue(entered.wait(3))
            started = time.monotonic(); barrier = runner.cancel()
            self.assertLess(time.monotonic() - started, .1)
            release.set(); barrier.result(3)
        self.assertEqual(handle.done.result(3)['status'], 'cancelled')
        self.assertEqual(self.read('SELECT count(*) n FROM predictions')[0]['n'], 1)
        again = runner.start(self.row['id'], method='always_flat').done.result(8)
        self.assertGreater(again['generation'], handle.generation)
        self.assertEqual((again['n_ok'], again['skipped']), (31, 1))

    def test_outcome_write_failure_rolls_back_prediction_and_success_counter(self):
        def trigger(store):
            store.db.execute("CREATE TRIGGER reject_outcome BEFORE INSERT ON outcomes BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        self.writer.submit(trigger).result(3)
        handle = self.local().start(self.row['id'], method='always_flat')
        with self.assertRaises(DataError): handle.done.result(5)
        self.assertEqual(self.read('SELECT count(*) n FROM predictions')[0]['n'], 0)
        self.assertEqual(self.read('SELECT n_ok,status FROM runs')[0], {'n_ok': 0, 'status': 'failed'})

    def test_sync_and_replay_have_independent_generations_cancel_only_replay_and_busy_creation(self):
        fugle = GateFugle(blocked=True)
        sync = self.sync(fugle=fugle)
        syncing = sync.start(start='2024-07-01', today=date(2024, 7, 2))
        self.assertTrue(fugle.entered.wait(3))
        with self.assertRaisesRegex(DataError, 'busy'): sync.start()
        runner = self.local()
        first = runner.start(self.row['id'], method='always_flat')
        with self.assertRaisesRegex(DataError, 'busy'): self.gate.claim('create')
        runner.cancel().result(3)
        self.assertTrue(self.gate.busy('sync')); self.assertFalse(syncing.cancel_event.is_set())
        first.done.result(3)
        second = runner.start(self.row['id'], method='always_flat')
        self.assertGreater(second.generation, first.generation)
        self.assertEqual(syncing.generation, 1)
        second.done.result(8)
        fugle.release.set(); self.assertEqual(syncing.done.result(5)['status'], 'complete')

    def test_sync_network_off_writer_commits_on_writer_and_late_cancelled_result_discarded(self):
        fugle = GateFugle(blocked=True)
        sync = self.sync(fugle=fugle)
        handle = sync.start(start='2024-07-01', today=date(2024, 7, 2))
        self.assertTrue(fugle.entered.wait(3)); sync.close(); fugle.release.set()
        self.assertEqual(handle.done.result(4)['status'], 'cancelled')
        self.assertEqual(self.read("SELECT count(*) n FROM daily WHERE day='2024-07-01'")[0]['n'], 0)
        self.assertTrue(all(call[3] == 'sync-worker' for call in fugle.calls))
        owners = []
        original = Store.write_candles
        def record(store, batch, **kwargs):
            owners.append(threading.current_thread().name)
            return original(store, batch, **kwargs)
        fresh = self.sync(fugle=GateFugle())
        with patch.object(Store, 'write_candles', record):
            fresh.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
        self.assertEqual(owners, ['db-writer', 'db-writer'])
        self.assertEqual(self.read("SELECT count(*) n FROM daily WHERE day='2024-07-01'")[0]['n'], 1)

    def test_sync_progress_is_after_commit_and_failed_commit_not_reported_success(self):
        calls = []
        def progress(value):
            if value['status'] == 'running':
                calls.append(self.read("SELECT count(*) n FROM daily WHERE day='2024-07-01'")[0]['n'])
        runner = self.sync(fugle=GateFugle(), on_progress=progress)
        runner.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
        self.assertEqual(calls, [1, 1, 1])
        self.writer.submit(lambda store: store.db.execute("CREATE TRIGGER reject_bar BEFORE INSERT ON bars BEGIN SELECT RAISE(ABORT, 'fixture'); END")).result(3)
        second = runner.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
        self.assertEqual(second['status'], 'partial')
        self.assertEqual(calls, [1, 1, 1, 1, 1])
        self.assertFalse(self.gate.busy('sync'))

    def test_sync_all_failed_partial_complete_and_skips_do_not_count_as_success(self):
        fugle = GateFugle()
        runner = self.sync(fugle=fugle)
        progress = []; runner.on_progress = progress.append
        for code in ('tls_certificate_error', 'network_error', 'auth_disabled'):
            with self.subTest(code=code), \
                 patch.object(fugle, 'candles', side_effect=ClientError(code)), \
                 patch.object(runner.twse, 'events', side_effect=ClientError(code)), \
                 patch.object(Store, 'should_fetch', side_effect=lambda symbol, month, **kw: month == '2024-08'):
                result = runner.start(start='2024-07-01', today=date(2024, 8, 2)).done.result(5)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['n_ok'], 0)
            self.assertEqual(result['n_fail'], 1 if code == 'auth_disabled' else 3)
            self.assertEqual(result['reasons'], [code])
            self.assertEqual(progress[-1], result)
        with patch.object(fugle, 'candles', side_effect=ClientError('network_error')):
            result = runner.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
        self.assertEqual((result['status'], result['n_ok'], result['n_fail']), ('partial', 1, 2))
        result = runner.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
        self.assertEqual((result['status'], result['n_ok'], result['n_fail'], result['reasons']), ('complete', 3, 0, []))
        with patch.object(Store, 'should_fetch', return_value=False):
            result = runner.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
        self.assertEqual((result['status'], result['n_ok'], result['n_fail']), ('complete', 0, 0))

    def test_sync_error_reasons_never_reflect_unknown_exception_text(self):
        fugle = GateFugle(); runner = self.sync(fugle=fugle)
        for error in (ClientError('fixture-secret'), DataError('fixture-secret'), RuntimeError('fixture-secret')):
            with patch.object(fugle, 'candles', side_effect=error):
                result = runner.start(start='2024-07-01', today=date(2024, 7, 2)).done.result(5)
            self.assertNotIn('fixture-secret', json.dumps(result))
            self.assertIn(result['status'], ('failed', 'partial'))
