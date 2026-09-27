from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError, TAIPEI
from back.db_writer import DBWriter
from back.evolution_budget import consume
from back.experiment import ActivityGate, exposed_days, record_exposure
from back.forward import (LOCKED, active_days, discover, enroll, plan_for,
                          reveal_forward, revealed_days)
from back.forward_runner import ForwardRunner
from back.http_client import ClientError
from back.jevcast import JevClient, _AUTH_DISABLED
from back.replay import Replay
from back.report import build_report, day_view, status_view
from back.runtime import Application
from back.store import Store
from scripts.check_data import build_report as data_report
from tests.helpers import FakeServer, KEY
from tests.score_fixture import scored_fixture
from tests.test_jev_client import response
from tests.test_jev_runner import ControlledClient
from tests.test_runtime import Sink


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'forward.sqlite3'
        self.store = Store(self.path); self.addCleanup(self.store.close)
        self.row, self.days = scored_fixture(self.store)
        self.future = self.days[31:33]
        self.extra = self.days[33]
        self.saved = {table: [tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} WHERE day=?', (self.extra,))]
                      for table in ('bars', 'daily')}
        with self.store.transaction():
            for table in ('bars', 'daily', 'corp_events'):
                self.store.db.execute(f'DELETE FROM {table} WHERE day>?', (self.days[32],))
        self.writer = DBWriter(self.path); self.writer.ready.result(3)
        self.runners = []
        self.addCleanup(self.cleanup)
        self.env = patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY}); self.env.start(); self.addCleanup(self.env.stop)
        _AUTH_DISABLED.clear(); self.addCleanup(_AUTH_DISABLED.clear)

    def cleanup(self):
        for runner in self.runners:
            if isinstance(runner.client, ControlledClient): runner.client.release.set()
            runner.close()
            if runner.thread: runner.thread.join(4)
        self.writer.close(); self.writer.thread.join(4)

    def run_all(self, client=None):
        runner = ForwardRunner(self.writer, ActivityGate(), client=client or ControlledClient())
        self.runners.append(runner)
        result = runner.start().done.result(8)
        self.assertEqual(result['status'], 'complete')
        return runner

    def add_day(self):
        with self.store.transaction():
            for table, rows in self.saved.items():
                self.store.db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in rows[0])})', rows)

    def test_dates_only_excludes_exposed_and_holdout_without_reading_prices(self):
        with self.store.transaction():
            record_exposure(self.store, '2330', self.future[0], self.future[0], 'fixture-used')
        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and (column in ('open', 'high', 'low', 'close', 'volume', 'label', 'answer', 'probs_json')
                    or table in ('outcomes', 'predictions')):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.db.set_authorizer(authorize)
        try: preview = discover(self.store)
        finally: self.store.db.set_authorizer(None)
        self.assertEqual(preview, {'dates': [self.future[1]], 'excluded_exposed_dates': [self.future[0]]})
        with self.store.transaction(): enroll(self.store)
        self.assertEqual(active_days(self.store), (self.future[1],))
        self.assertEqual(self.store.db.execute('SELECT day FROM forward_exclusions').fetchone()[0], self.future[0])

    def test_only_closed_days_but_intraday_gaps_use_existing_predictability_rules(self):
        day = self.future[0]
        self.store.db.execute("DELETE FROM bars WHERE day=? AND substr(ts_raw,12,5)='10:01'", (day,)); self.store.db.commit()
        self.assertIn(day, discover(self.store)['dates'])
        before_close = datetime.fromisoformat(day + 'T13:29:59').replace(tzinfo=TAIPEI)
        self.assertNotIn(day, discover(self.store, now=before_close)['dates'])
        self.store.db.execute("UPDATE fetch_log SET final=0")
        self.store.db.execute("DELETE FROM bars WHERE day=? AND substr(ts_raw,12,5)='13:30'", (day,)); self.store.db.commit()
        self.assertNotIn(day, discover(self.store)['dates'])

    def test_zero_answers_refuses_reveal_and_all_unrevealed_exits_are_constant(self):
        with self.store.transaction(): enroll(self.store)
        def views():
            return (build_report(self.store), status_view(self.store), day_view(self.store, self.future[0]), data_report(self.store))
        before = views()
        self.assertEqual(before[0]['forward'], LOCKED)
        self.assertEqual(before[1]['forward'], LOCKED)
        self.assertEqual(before[2], {'status': 'forward_locked', 'message': '前瞻段未揭露'})
        for day in self.future: self.assertNotIn(day, json.dumps(before))
        from back.score import load_split
        with self.assertRaisesRegex(DataError, 'forward_locked'):
            load_split(self.store, self.row, 'forward')
        with self.assertRaisesRegex(DataError, 'forward_incomplete'): reveal_forward(self.store)
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET open='998877',high='998878',low='998876',close='998877' WHERE day>?", (self.row['hold_end'],))
            self.store.db.execute("UPDATE daily SET open='998877',high='998878',low='998876',close='998877',volume='998877' WHERE day>?", (self.row['hold_end'],))
        self.assertEqual(before, views())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)

    def test_five_methods_p1_payload_majority_frozen_and_explicit_reveal(self):
        server = FakeServer(); self.addCleanup(server.close)
        for _ in range(16): server.queue(response())
        ledger = Path(self.tmp.name) / 'budget.sqlite3'
        client = JevClient(opener=server, campaign_budget=ledger)
        before = [tuple(r) for r in self.store.db.execute('SELECT * FROM predictions ORDER BY rowid')]
        self.run_all(client)
        self.assertEqual(len(server.bodies), 16)
        self.assertEqual(build_report(self.store)['forward'], LOCKED)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)
        for body in server.bodies:
            self.assertEqual(json.loads(body)['questions']['direction']['instructions'],
                '根據 state，這檔股票從現在到 30 分鐘後，價格變化最可能落在哪一類？')
            for secret in ('2330', *self.future): self.assertNotIn(secret, body.decode())
        probs = [json.loads(r[0]) for r in self.store.db.execute("SELECT probs_json FROM predictions WHERE method='majority' AND substr(t,1,10)>?", (self.row['hold_end'],))]
        # 32 mature development flat labels, additive-one smoothing, no holdout/forward labels.
        self.assertEqual(probs, [{'up': 1/35, 'flat': 33/35, 'down': 1/35}] * 16)
        after = [tuple(r) for r in self.store.db.execute('SELECT * FROM predictions WHERE substr(t,1,10)<=? ORDER BY rowid', (self.row['dev_end'],))]
        self.assertEqual(before, after)
        reveal_forward(self.store)
        report = build_report(self.store)['forward']
        self.assertEqual((report['state'], report['cumulative_days'], report['predictable_and_scorable']), ('revealed', 2, 16))
        self.assertEqual(revealed_days(self.store), self.future)
        self.assertEqual(exposed_days(self.store, '2330', self.future), self.future)
        self.assertTrue(all(r[0] == 'forward' for r in self.store.db.execute('SELECT segment FROM reveals')))
        self.assertEqual(day_view(self.store, self.future[0])['status'], 'ok')
        self.run_all(client)
        self.assertEqual(len(server.bodies), 16)
        reveal_forward(self.store)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 2)

    def test_new_day_extends_cohort_but_unopened_work_does_not_change_revealed_report(self):
        self.run_all(); reveal_forward(self.store)
        before = build_report(self.store)['forward']
        self.add_day()
        self.assertEqual(discover(self.store)['dates'], [self.extra])
        with self.store.transaction(): enroll(self.store)
        self.assertEqual(active_days(self.store), (*self.future, self.extra))
        with self.assertRaisesRegex(DataError, 'forward_incomplete'): reveal_forward(self.store)
        # Counters/failed attempts from a new, unrevealed day must also stay private.
        self.run_all()
        with self.store.transaction():
            run_id = self.store.db.execute("SELECT max(id) FROM runs WHERE method='jev' AND split='forward'").fetchone()[0]
            self.store.db.execute('UPDATE forward_attempts SET failed=887766 WHERE run_id=? AND day=?', (run_id, self.extra))
        self.assertEqual(before, build_report(self.store)['forward'])
        reveal_forward(self.store)
        after = build_report(self.store)['forward']
        self.assertEqual((after['cumulative_days'], after['predictable_and_scorable']), (3, 24))

    def test_settings_and_data_are_frozen_before_replay(self):
        with self.store.transaction(): enroll(self.store)
        with self.assertRaises(DataError): discover(self.store, 2)
        for column, value in (('prompt_version', 'p2'), ('threshold_permille', 5), ('model', 'different'), ('feature_version', 'f2')):
            old = self.row[column]
            self.store.db.execute(f'UPDATE experiments SET {column}=? WHERE id=1', (value,)); self.store.db.commit()
            with self.assertRaisesRegex(DataError, 'forward_settings_changed'): plan_for(self.store, verify=True)
            self.store.db.execute(f'UPDATE experiments SET {column}=? WHERE id=1', (old,)); self.store.db.commit()
        self.assertTrue(all(self.store._frozen(day, self.store.frozen_ranges('2330')) for day in self.future))
        self.store.db.execute('UPDATE daily SET close=? WHERE day=?', ('998877', self.future[0])); self.store.db.commit()
        with self.assertRaisesRegex(DataError, 'frozen_data_changed'): plan_for(self.store, verify=True)

    def test_exposure_after_admission_prevents_reveal_and_is_recorded_on_next_run(self):
        self.run_all()
        with self.store.transaction(): record_exposure(self.store, '2330', self.future[0], self.future[0], 'external-audit')
        with self.assertRaisesRegex(DataError, 'forward_exposure_changed'): reveal_forward(self.store)
        with self.store.transaction(): enroll(self.store)
        self.assertEqual(active_days(self.store), (self.future[1],))
        self.run_all(); reveal_forward(self.store)
        self.assertEqual(revealed_days(self.store), (self.future[1],))

    def test_incomplete_development_selection_suppresses_forward_conclusion(self):
        self.run_all(); reveal_forward(self.store)
        self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND substr(t,1,10)=?", (self.row['dev_start'],))
        self.store.db.commit()
        self.assertEqual(build_report(self.store)['forward']['comparison']['statement'], '結果不完整，不下結論')

    def test_missing_or_invalid_answer_and_unfinished_run_do_not_reveal(self):
        self.run_all()
        for sql in ("UPDATE predictions SET probs_json='{}' WHERE substr(t,1,10)>'" + self.row['hold_end'] + "' AND method='jev'",
                    "DELETE FROM outcomes WHERE substr(t,1,10)>'" + self.row['hold_end'] + "'",
                    "UPDATE runs SET status='running' WHERE split='forward' AND method='jev'"):
            snapshot = sqlite3.connect(':memory:'); self.store.db.backup(snapshot)
            try:
                self.store.db.execute(sql); self.store.db.commit()
                with self.assertRaisesRegex(DataError, 'forward_incomplete'): reveal_forward(self.store)
                self.assertEqual(revealed_days(self.store), ())
            finally: snapshot.backup(self.store.db); snapshot.close()
        self.store.db.execute("CREATE TRIGGER fail_reveal BEFORE INSERT ON reveals BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError): reveal_forward(self.store)
        self.assertEqual(revealed_days(self.store), ())

    def test_runtime_requires_confirmation_rejects_overrides_and_hides_forward_progress(self):
        sink, client = Sink(), ControlledClient()
        with patch('back.runtime.key_state', return_value={'fugle': 'missing', 'typesafe': 'available'}):
            app = Application(self.writer, sink, 1, jev_client=client)
            try:
                app.start(); sink.wait(lambda b: b.get('experiment_id') == 1)
                for op in ('run_forward', 'reveal_forward'):
                    app.request({'op': op, 'experiment_id': 1})
                    sink.wait(lambda b: b.get('code') == 'confirmation_required')
                    for extra in ({'method': 'majority'}, {'threshold_permille': 5}, {'prompt_version': 'p2'}):
                        app.request({'op': op, 'experiment_id': 1, 'confirmed': True, **extra})
                        sink.wait(lambda b: b.get('code') == 'forward_settings_changed')
                self.assertEqual(client.calls, [])
                app.request({'op': 'run_forward', 'experiment_id': 1, 'confirmed': True})
                sink.wait(lambda b: b.get('replay', {}).get('status') == 'complete', timeout=8)
                for packet in sink.values:
                    if packet.get('replay', {}).get('split') == 'forward':
                        self.assertEqual(set(packet['replay']), {'status', 'method', 'split'})
                self.assertEqual(revealed_days(self.store), ())
                app.request({'op': 'reveal_forward', 'experiment_id': 1, 'confirmed': True})
                sink.wait(lambda b: b.get('forward', {}).get('state') == 'revealed', timeout=8)
                self.assertEqual(revealed_days(self.store), self.future)
                app.request({'op': 'status', 'request_id': 999})
                final = sink.wait(lambda b: b.get('request_id') == 999)
                self.assertIn(self.future[-1], final['days'])
                app._on_replay({'generation': 999, 'status': 'running', 'method': 'jev',
                    'split': 'forward', 'n_ok': 998877, 'n_fail': 887766, 'skipped': 776655})
                self.assertEqual(set(sink.values[-1]['replay']), {'status', 'method', 'split'})
            finally:
                app.close()
                if app.forward.thread: app.forward.thread.join(4)
                app.reader.join(4)

    def test_budget_is_durable_atomic_and_counts_retries_before_http(self):
        ledger = Path(self.tmp.name) / 'budget.sqlite3'
        server = FakeServer(); self.addCleanup(server.close)
        with self.store.transaction(): enroll(self.store)
        engine = Replay(self.store, plan_for(self.store))
        point = engine.prepare(next(engine.candidates('forward')))
        server.queue({}, status=429); server.queue(response())
        with patch('back.evolution_budget.LIMIT', 2):
            client = JevClient(opener=server, campaign_budget=ledger, sleep=lambda _: None)
            client.predict(point)
            with self.assertRaisesRegex(ClientError, 'evolution_budget_exhausted'):
                JevClient(opener=server, campaign_budget=ledger).predict(point)
        self.assertEqual(len(server.bodies), 2)
        with sqlite3.connect(ledger) as db: self.assertEqual(db.execute('SELECT used FROM budget').fetchone()[0], 2)
        concurrent = Path(self.tmp.name) / 'concurrent.sqlite3'
        def attempt(_):
            try: consume(concurrent); return True
            except ClientError: return False
        with patch('back.evolution_budget.LIMIT', 3), ThreadPoolExecutor(6) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 3)

    def test_default_transport_budget_cannot_be_bypassed_by_existing_cli_hook(self):
        server = FakeServer(); self.addCleanup(server.close)
        server.queue(response())
        ledger = Path(self.tmp.name) / 'real-path-budget.sqlite3'
        with self.store.transaction(): enroll(self.store)
        engine = Replay(self.store, plan_for(self.store))
        point = engine.prepare(next(engine.candidates('forward')))
        with patch('back.jevcast.secure_opener', return_value=server), \
             patch('back.evolution_budget.consume', side_effect=lambda: consume(ledger)) as budget, \
             patch('back.evolution_budget.LIMIT', 1):
            hooks = []
            client = JevClient(before_request=lambda: hooks.append('called'))
            client.predict(point)
            with self.assertRaisesRegex(ClientError, 'evolution_budget_exhausted'): client.predict(point)
            self.assertEqual(budget.call_count, 2)
        self.assertEqual(len(server.bodies), 1)

    def test_cancel_reserves_whole_workflow_and_discards_late_results(self):
        client, gate = ControlledClient(blocked=True), ActivityGate()
        runner = ForwardRunner(self.writer, gate, client=client); self.runners.append(runner)
        handle = runner.start()
        self.assertTrue(client.wait_calls(6))
        with self.assertRaisesRegex(DataError, 'busy'): gate.claim('create')
        with self.assertRaisesRegex(DataError, 'busy'): runner.start()
        runner.cancel(); client.release.set()
        self.assertEqual(handle.done.result(6)['status'], 'cancelled')
        self.assertFalse(gate.busy())
        self.assertFalse(self.store.db.execute("SELECT 1 FROM predictions WHERE method='jev' AND substr(t,1,10)>?", (self.row['hold_end'],)).fetchone())
        self.assertEqual(revealed_days(self.store), ())

    def test_legacy_reveals_migrate_without_changing_prior_access(self):
        legacy = Path(self.tmp.name) / 'legacy.sqlite3'
        with sqlite3.connect(legacy) as db:
            db.execute('CREATE TABLE reveals (experiment_id INTEGER, revealed_at TEXT, first_day TEXT, last_day TEXT, what TEXT)')
            db.execute("INSERT INTO reveals VALUES (1,'fixture','2024-01-01','2024-01-02','all')")
        with Store(legacy) as migrated:
            record = dict(migrated.db.execute('SELECT * FROM reveals').fetchone())
            self.assertEqual(record, {'experiment_id': 1, 'revealed_at': 'fixture',
                'first_day': '2024-01-01', 'last_day': '2024-01-02', 'what': 'all', 'segment': 'holdout'})
