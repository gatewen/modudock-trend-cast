from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from back.data import DataError
from back.db_writer import DBWriter
from back.experiment import ActivityGate, load_plan
from back.jevcast import JevAnswer, JevClient, JevRunner, _AUTH_DISABLED
from back.replay import CLOCKS, Replay
from back.store import Store
from tests.experiment_fixture import frozen_experiment, seed_experiment
from tests.helpers import KEY
from tests.replay_fixture import at
from tests.test_jev_client import response


class ControlledClient:
    def __init__(self, *, blocked=False, fail=False):
        self.condition = threading.Condition()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.calls, self.active, self.maximum = [], 0, 0
        self.fail = fail

    def predict(self, point, **kwargs):
        with self.condition:
            self.calls.append(point)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.condition.notify_all()
        try:
            if not self.release.wait(5):
                raise RuntimeError('fixture deadline')
            if self.fail:
                raise RuntimeError(KEY)
            return JevAnswer('flat', {'up': .2, 'flat': .6, 'down': .2}, None)
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()

    def wait_calls(self, n):
        with self.condition:
            return self.condition.wait_for(lambda: len(self.calls) >= n, timeout=3)

    def wait_idle(self):
        with self.condition:
            return self.condition.wait_for(lambda: self.active == 0, timeout=3)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'runner.sqlite3'
        with Store(self.path) as store:
            self.days = seed_experiment(store)
            self.row = frozen_experiment(store, self.days)
        self.gate = ActivityGate()
        self.writer = DBWriter(self.path)
        self.writer.ready.result(3)
        self.runners = []
        self.addCleanup(self.shutdown)

    def shutdown(self):
        for runner in self.runners:
            if isinstance(runner.client, ControlledClient):
                runner.client.release.set()
            runner.close().result(3)
            for worker in runner.workers:
                worker.join(3)
        self.writer.close()
        self.writer.thread.join(3)

    def runner(self, client=None, **kwargs):
        result = JevRunner(self.writer, self.gate, client=client or ControlledClient(), **kwargs)
        self.runners.append(result)
        return result

    def read(self, query, args=()):
        with Store(self.path, readonly=True) as store:
            return [dict(r) for r in store.db.execute(query, args)]

    def start(self, runner, times=None, **kwargs):
        return runner.start(self.row['id'], times=times or [at(self.days[25])], **kwargs)

    def test_persist_resume_and_duplicate_times_no_repeat_http(self):
        client = ControlledClient()
        progress = []
        def committed(snapshot):
            count = self.read('SELECT count(*) n FROM predictions')[0]['n']
            progress.append((snapshot['n_ok'], count))
        runner = self.runner(client, on_progress=committed)
        points = [at(self.days[25], clock) for clock in CLOCKS[:2]]
        first = self.start(runner, points + points).done.result(5)
        self.assertEqual((first['status'], first['n_ok'], first['n_fail']), ('complete', 2, 0))
        self.assertEqual(self.read('SELECT full_split FROM run_scopes WHERE run_id=?', (first['run_id'],)),
                         [{'full_split': 0}])
        self.assertTrue(all(reported == persisted for reported, persisted in progress))
        self.assertEqual(len(client.calls), 2)
        rows = self.read('SELECT * FROM predictions ORDER BY t')
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['run_id'] for r in rows}, {first['run_id']})
        self.assertEqual(rows[0]['input_json'], client.calls[0].input_json)
        self.assertEqual(rows[0]['input_hash'], client.calls[0].input_hash)
        self.assertIsNone(rows[0]['model_reported'])
        self.assertNotIn(KEY, json.dumps(rows))
        # New client/runner simulates restart; DB, not an in-memory set, deduplicates.
        runner.close().result(3)
        next_client = ControlledClient()
        second_runner = self.runner(next_client)
        resumed = self.start(second_runner, points + [at(self.days[25], CLOCKS[2])]).done.result(5)
        self.assertEqual((resumed['n_ok'], resumed['skipped']), (1, 2))
        self.assertEqual(len(next_client.calls), 1)
        self.assertEqual(len(self.read('SELECT * FROM predictions')), 3)
        self.assertEqual(self.read('SELECT n_ok FROM runs WHERE id=?', (first['run_id'],))[0]['n_ok'], 2)

    def test_six_worker_bound_and_all_threads_daemon(self):
        client = ControlledClient(blocked=True)
        runner = self.runner(client)
        points = [at(self.days[25], clock) for clock in CLOCKS]
        handle = self.start(runner, points)
        try:
            self.assertTrue(client.wait_calls(6))
            self.assertEqual(len(client.calls), 6)
            self.assertEqual(client.maximum, 6)
            self.assertEqual(len(runner.workers), 6)
            self.assertTrue(all(thread.daemon for thread in [self.writer.thread, *runner.workers]))
            with self.assertRaisesRegex(DataError, 'busy'):
                self.start(runner)
            with self.assertRaisesRegex(DataError, 'busy'):
                self.writer.submit(lambda store: frozen_experiment(store, self.days, self.gate)).result(3)
        finally:
            client.release.set()
        self.assertEqual(handle.done.result(5)['n_ok'], 8)
        self.assertLessEqual(client.maximum, 6)

    def test_cancel_old_late_responses_discard_and_new_generation_still_commits(self):
        sync_token = self.gate.claim('sync')
        client = ControlledClient(blocked=True)
        runner = self.runner(client)
        points = [at(self.days[25], clock) for clock in CLOCKS]
        old = self.start(runner, points)
        self.assertTrue(client.wait_calls(6))
        runner.cancel().result(3)
        self.assertEqual(old.done.result(3)['status'], 'cancelled')
        with self.assertRaisesRegex(DataError, 'busy'):
            self.gate.claim('sync')  # cancel did not release sync's reservation.
        self.gate.release('sync', sync_token)
        new = self.start(runner, [points[-1]])
        self.assertGreater(new.generation, old.generation)
        client.release.set()
        self.assertEqual(new.done.result(5)['n_ok'], 1)
        self.assertTrue(client.wait_idle())
        self.writer.submit(lambda store: None).result(3)
        self.assertEqual(len(client.calls), 7)  # No old point #7 or #8 dispatched.
        rows = self.read('SELECT t,run_id FROM predictions')
        self.assertEqual(rows, [{'t': points[-1].isoformat(), 'run_id': new.run_id}])
        self.assertEqual(self.read('SELECT n_ok,n_fail FROM runs WHERE id=?', (old.run_id,)),
                         [{'n_ok': 0, 'n_fail': 0}])

    def test_cancel_and_close_return_without_waiting_for_locked_database(self):
        client = ControlledClient(blocked=True)
        runner = self.runner(client)
        handle = self.start(runner)
        self.assertTrue(client.wait_calls(1))
        lock = sqlite3.connect(self.path, timeout=1)
        self.addCleanup(lock.close)
        lock.execute('BEGIN IMMEDIATE')
        beginning = threading.Event()
        self.writer.submit(lambda store: store.db.set_trace_callback(
            lambda sql: beginning.set() if sql == 'BEGIN IMMEDIATE' else None)).result(3)
        client.release.set()
        self.assertTrue(beginning.wait(3))
        # Writer may now be waiting in BEGIN IMMEDIATE. Both controls stay local.
        started = time.monotonic()
        cancelled = runner.cancel()
        self.assertLess(time.monotonic() - started, .2)
        lock.rollback()
        cancelled.result(3)
        self.writer.submit(lambda store: None).result(3)
        self.assertEqual(handle.done.result(3)['n_ok'], 0)
        self.assertEqual(self.read('SELECT * FROM predictions'), [])
        self.writer.submit(lambda store: store.db.set_trace_callback(None)).result(3)
        lock.execute('BEGIN IMMEDIATE')
        blocker = self.writer.submit(lambda store: store.db.execute("UPDATE runs SET status=status"))
        started = time.monotonic()
        runner.close()
        self.assertLess(time.monotonic() - started, .2)
        lock.rollback()
        blocker.result(3)
        self.writer.submit(lambda store: store.db.commit()).result(3)

    def test_insert_failure_rolls_back_prediction_count_and_can_retry(self):
        def trigger(store):
            store.db.execute('''CREATE TRIGGER reject_count BEFORE UPDATE OF n_ok ON runs
                WHEN new.n_ok>old.n_ok BEGIN SELECT RAISE(ABORT,'fixture'); END''')
        self.writer.submit(trigger).result(3)
        client = ControlledClient()
        progress = []
        runner = self.runner(client, on_progress=progress.append)
        failed = self.start(runner).done.result(5)
        self.assertEqual((failed['status'], failed['n_ok']), ('failed', 0))
        self.assertEqual(self.read('SELECT * FROM predictions'), [])
        self.assertTrue(all(p['n_ok'] == 0 for p in progress))
        self.writer.submit(lambda store: store.db.execute('DROP TRIGGER reject_count')).result(3)
        resumed = self.start(runner).done.result(5)
        self.assertEqual(resumed['n_ok'], 1)
        self.assertEqual(len(client.calls), 2)

    def test_cancel_status_write_failure_does_not_leave_replay_gate_stuck(self):
        client = ControlledClient(blocked=True)
        runner = self.runner(client)
        handle = self.start(runner)
        self.assertTrue(client.wait_calls(1))
        def reject_cancel(store):
            store.db.execute('''CREATE TRIGGER reject_cancel BEFORE UPDATE OF status ON runs
                WHEN new.status='cancelled' BEGIN SELECT RAISE(ABORT,'fixture'); END''')
        self.writer.submit(reject_cancel).result(3)
        with self.assertRaisesRegex(DataError, 'database_operation_failed'):
            runner.cancel().result(3)
        with self.assertRaisesRegex(DataError, 'database_operation_failed'):
            handle.done.result(3)
        with self.gate.reservation('replay'):
            pass
        client.release.set()
        self.assertTrue(client.wait_idle())
        self.writer.submit(lambda store: None).result(3)
        self.assertEqual(self.read('SELECT * FROM predictions'), [])

    def test_actual_commit_failure_not_counted_or_retained(self):
        # A deferred FK violates only at COMMIT, after INSERT and counter UPDATE
        # have both succeeded. This distinguishes commit from execute success.
        def install(store):
            store.db.executescript('''CREATE TABLE commit_guard (
                id INTEGER REFERENCES experiments(id) DEFERRABLE INITIALLY DEFERRED);
                CREATE TRIGGER defer_failure AFTER INSERT ON predictions BEGIN
                    INSERT INTO commit_guard VALUES (-999); END;''')
        self.writer.submit(install).result(3)
        runner = self.runner()
        result = self.start(runner).done.result(5)
        self.assertEqual((result['status'], result['n_ok']), ('failed', 0))
        self.assertEqual(self.read('SELECT * FROM predictions'), [])
        self.assertEqual(self.read('SELECT n_ok FROM runs'), [{'n_ok': 0}])

    def test_invalid_responses_count_failures_without_storing_error_or_predictions(self):
        client = ControlledClient(fail=True)
        runner = self.runner(client)
        result = self.start(runner).done.result(5)
        self.assertEqual((result['n_ok'], result['n_fail']), (0, 1))
        self.assertEqual(self.read('SELECT * FROM predictions'), [])
        rows = self.read('SELECT * FROM runs')
        self.assertEqual(rows[0]['n_fail'], 1)
        self.assertNotIn(KEY, json.dumps(rows))

    def test_run_bound_to_experiment_and_split_and_tampered_snapshot_refused(self):
        client = ControlledClient()
        runner = self.runner(client)
        bad = self.start(runner, [at(self.row['hold_start'])])
        with self.assertRaisesRegex(DataError, 'point_outside_run_split'):
            bad.done.result(5)
        self.assertEqual(client.calls, [])
        self.writer.submit(lambda store: store.db.executescript(
            "UPDATE daily SET close='1000.1' WHERE day='" + self.days[0] + "';")).result(3)
        bad = self.start(runner)
        with self.assertRaisesRegex(DataError, 'frozen_data_changed'):
            bad.done.result(5)
        self.assertEqual(client.calls, [])

    def test_unpredictable_points_never_dispatched(self):
        # A valid finalized day may have too-old bars at this particular clock.
        with Store(self.path) as store:
            with store.transaction():
                store.db.execute('DELETE FROM bars WHERE day=? AND bar_end>?',
                                 (self.days[25], self.days[25] + 'T09:20:00+08:00'))
            row = frozen_experiment(store, self.days)
        runner = self.runner()
        result = runner.start(row['id'], times=[at(self.days[25])]).done.result(5)
        self.assertEqual((result['n_ok'], result['unpredictable']), (0, 1))
        self.assertEqual(runner.client.calls, [])


class GlobalConcurrencyTests(unittest.TestCase):
    def test_multiple_clients_share_six_http_slots(self):
        _AUTH_DISABLED.clear()
        entered = threading.Condition()
        release = threading.Event()
        active = maximum = calls = 0
        class Reply(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout):
                nonlocal active, maximum, calls
                with entered:
                    active += 1
                    calls += 1
                    maximum = max(maximum, active)
                    entered.notify_all()
                release.wait(3)
                with entered:
                    active -= 1
                return Reply(json.dumps(response()).encode())
        with tempfile.TemporaryDirectory() as temp, Store(Path(temp) / 'db') as store:
            days = seed_experiment(store)
            row = frozen_experiment(store, days)
            point = Replay(store, load_plan(store, row['id'])).prepare(at(days[25]))
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY}), ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(JevClient(opener=Opener()).predict, point) for _ in range(10)]
            try:
                with entered:
                    self.assertTrue(entered.wait_for(lambda: calls >= 6, timeout=3))
                # Give an accidentally unbounded 7th worker a chance to enter.
                time.sleep(.05)
                self.assertEqual(calls, 6)
            finally:
                release.set()
            self.assertEqual([f.result(3).choice for f in futures], ['flat'] * 10)
        self.assertEqual(maximum, 6)
