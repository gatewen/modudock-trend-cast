from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from back.data import DataError, MAPPING
from back.experiment import (ActivityGate, BLOCK2_EXPOSURE, create_experiment, data_digest,
    day_access, digest_settings, exposed_days, holdout_overlap, load_plan, record_exposure,
    reveal_holdout)
from back.store import Store
from tests.experiment_fixture import TODAY, frozen_experiment, seed_experiment


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'experiments.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.days = seed_experiment(self.store)
        self.gate = ActivityGate()

    def create(self, **kwargs):
        return frozen_experiment(self.store, self.days, self.gate, **kwargs)

    def assertEmpty(self):
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM experiments').fetchone()[0], 0)

    def test_freezes_warmup_split_configuration_and_persists(self):
        row = self.create()
        self.assertEqual(json.loads(row['config_json']),
            dict(symbol='2330', warmup_start=self.days[0], mapping=MAPPING))
        self.assertEqual((row['dev_start'], row['dev_end'], row['hold_start'], row['hold_end']),
                         (self.days[25], self.days[45], self.days[46], self.days[55]))
        self.assertEqual(len(row['data_digest']), 64)
        self.assertEqual(self.store.frozen_ranges('2330'), [(self.days[0], self.days[55])])
        with Store(self.path, readonly=True) as reader:
            plan = load_plan(reader, row['id'])
            self.assertEqual(len(plan.prior_days(plan.dev_start)), 25)
            self.assertEqual(plan.experiment_id, row['id'])

    def test_sync_and_replay_each_refuse_creation_and_keep_separate_generations(self):
        for kind in ('sync', 'replay'):
            with self.subTest(kind=kind), self.gate.reservation(kind):
                with self.assertRaisesRegex(DataError, '^busy$'):
                    self.create()
                self.assertEmpty()
        sync = self.gate.claim('sync')
        replay = self.gate.claim('replay')
        self.gate.release('replay', replay)
        with self.assertRaisesRegex(DataError, '^busy$'):
            self.gate.claim('sync')
        self.gate.release('sync', sync)
        self.assertGreater(self.gate.claim('replay'), replay)

    def test_unfinalized_current_recent_and_warmup_months_rejected(self):
        # Each checks an independent reason a final=1 flag is insufficient.
        for mode in ('not_final', 'current', 'recent'):
            with self.subTest(mode=mode):
                self.store.db.execute('SAVEPOINT case_data')
                if mode == 'not_final':
                    month = self.days[55][:7]
                    self.store.db.execute('UPDATE fetch_log SET final=0 WHERE month=?', (month,))
                elif mode == 'recent':
                    self.store.db.execute('DELETE FROM daily WHERE day>?', (self.days[55],))
                self.store.db.execute('RELEASE case_data')
                when = date(2024, 3, 29) if mode == 'current' else TODAY
                with self.assertRaisesRegex(DataError, '^unfinalized_month$'):
                    create_experiment(self.store, self.gate, start=self.days[25],
                                      end=self.days[55], today=when)
                self.assertEmpty()
                if mode == 'not_final':
                    self.store.db.execute('UPDATE fetch_log SET final=1')
                    self.store.db.commit()

    def test_warmup_month_must_be_final(self):
        self.store.db.execute('UPDATE fetch_log SET final=0 WHERE month=?', (self.days[0][:7],))
        self.store.db.commit()
        with self.assertRaisesRegex(DataError, '^unfinalized_month$'):
            self.create()
        self.assertEmpty()

    def test_missing_warmup_bars_daily_or_unknown_corp_each_rejected(self):
        with self.assertRaisesRegex(DataError, '^missing_warmup$'):
            self.create(start=self.days[24])
        for table, column, reason in (('bars', 'day', 'incomplete_trading_day'),
                ('daily', 'day', 'missing_warmup'),
                ('corp_coverage', 'start_day', 'unknown_corporate_action')):
            with self.subTest(table=table):
                rows = [tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} WHERE {column}=?',
                                                               (self.days[0],))]
                with self.store.transaction():
                    self.store.db.execute(f'DELETE FROM {table} WHERE {column}=?', (self.days[0],))
                with self.assertRaisesRegex(DataError, '^' + reason + '$'):
                    self.create()
                self.assertEmpty()
                with self.store.transaction():
                    self.store.db.executemany(f'INSERT INTO {table} VALUES ({",".join("?" * len(rows[0]))})', rows)

    def test_missing_evaluation_daily_is_not_silently_removed_from_calendar(self):
        with self.store.transaction():
            self.store.db.execute('DELETE FROM daily WHERE day=?', (self.days[35],))
        with self.assertRaisesRegex(DataError, '^incomplete_trading_day$'):
            self.create()

    def test_digest_covers_each_dataset_settings_and_ignores_new_future_data(self):
        row = self.create()
        settings = digest_settings(row)
        original = row['data_digest']
        queries = [
            ('bars', "UPDATE bars SET volume='987654' WHERE day=?"),
            ('daily', "UPDATE daily SET close='1000.25' WHERE day=?"),
            ('corp_events', "INSERT INTO corp_events VALUES ('2330',?,'1000','997','fixture')"),
            ('corp_coverage', "UPDATE corp_coverage SET fetched_at='changed' WHERE start_day=?")]
        for name, sql in queries:
            with self.subTest(dataset=name):
                self.store.db.execute('BEGIN IMMEDIATE')
                self.store.db.execute(sql, (self.days[0],))
                self.assertNotEqual(data_digest(self.store, settings), original)
                self.store.db.rollback()
        for key, value in [('threshold_permille', 5), ('prompt_version', 'p2'),
                           ('model', 'other'), ('feature_version', 'f2'), ('dev_end', self.days[44])]:
            self.assertNotEqual(data_digest(self.store, {**settings, key: value}), original)
        changed = json.loads(settings['config_json'])
        changed['mapping'] = 'other'
        self.assertNotEqual(data_digest(self.store, {**settings, 'config_json': json.dumps(changed)}), original)
        with self.store.transaction():
            self.store.db.execute("UPDATE daily SET close='9999' WHERE day>?", (row['hold_end'],))
        self.assertEqual(data_digest(self.store, settings), original)

    def test_digest_independent_of_database_insertion_order(self):
        row = self.create()
        saved = [tuple(r) for r in self.store.db.execute('SELECT * FROM corp_coverage ORDER BY start_day DESC')]
        with self.store.transaction():
            self.store.db.execute('DELETE FROM corp_coverage')
            self.store.db.executemany('INSERT INTO corp_coverage VALUES (?,?,?,?,?)', saved)
        self.assertEqual(data_digest(self.store, digest_settings(row)), row['data_digest'])

    def test_creation_check_hash_and_insert_are_one_atomic_reservation(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def blocked_digest(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test deadline')
            return data_digest(*args)
        def create_on_owner():
            with Store(self.path) as store:
                results.append(frozen_experiment(store, self.days, self.gate))
        with patch('back.experiment.data_digest', side_effect=blocked_digest):
            thread = threading.Thread(target=create_on_owner, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(3))
                for kind in ('sync', 'replay', 'create'):
                    with self.assertRaisesRegex(DataError, '^busy$'):
                        self.gate.claim(kind)
                self.store.db.execute('PRAGMA busy_timeout=1')
                with self.assertRaises(sqlite3.OperationalError):
                    self.store.db.execute('BEGIN IMMEDIATE')
                self.assertEmpty()  # No half-built row is visible to another connection.
            finally:
                release.set()
                thread.join(3)
        self.assertEqual(len(results), 1)

    def test_failed_creation_rolls_back_experiment_and_exposure_together(self):
        self.store.db.execute('''CREATE TRIGGER refuse_exposure BEFORE INSERT ON prior_exposures
            BEGIN SELECT RAISE(ABORT,'fixture'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.create()
        self.assertEmpty()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM prior_exposures').fetchone()[0], 0)
        with self.gate.reservation('sync'):
            pass  # A failed transaction did not leave the gate busy.

    def test_prior_block2_exposure_is_seeded_once_and_survives_reopen(self):
        self.create()
        self.create(threshold_permille=5)
        with Store(self.path, readonly=True) as reader:
            rows = reader.db.execute('SELECT symbol,first_day,last_day,source FROM prior_exposures').fetchall()
            self.assertEqual([tuple(r) for r in rows], [BLOCK2_EXPOSURE])
            self.assertEqual(exposed_days(reader, '2330', ['2024-07-26', '2026-01-27', '2026-01-28']),
                             ('2024-07-26', '2026-01-27'))

    def test_other_experiments_development_and_reveals_are_permanent_exposure(self):
        a = self.create(end=self.days[60])
        self.assertEqual(holdout_overlap(self.store, a['id']), 0)
        b = self.create(end=self.days[40])
        self.assertEqual(holdout_overlap(self.store, b['id']), 5)
        self.assertEqual(day_access(self.store, b['id'], b['hold_start']), 'holdout_locked')
        reveal_holdout(self.store, a['id'])
        c = self.create(end=self.days[60], threshold_permille=5)
        self.assertEqual(holdout_overlap(self.store, c['id']), 11)
        # Repeated revelations and historical ranges cannot double-count days.
        reveal_holdout(self.store, a['id'])
        with self.store.transaction():
            record_exposure(self.store, '2330', c['hold_start'], c['hold_end'], 'test-audit')
            record_exposure(self.store, '9999', self.days[0], self.days[-1], 'other-symbol')
        self.assertEqual(holdout_overlap(self.store, c['id']), 11)
        self.assertEqual(day_access(self.store, c['id'], c['hold_start']), 'holdout_locked')
        self.assertEqual(day_access(self.store, a['id'], a['hold_start']), 'allowed')
        self.assertEqual(day_access(self.store, None, a['dev_start']), 'experiment_required')
        self.assertEqual(day_access(self.store, a['id'], a['dev_start']), 'allowed')
        self.assertEqual(day_access(self.store, a['id'], self.days[0]), 'day_outside_experiment')

    def test_reveal_requires_successful_commit_before_access_changes(self):
        row = self.create()
        self.store.db.execute('''CREATE TRIGGER reject_reveal BEFORE INSERT ON reveals
            BEGIN SELECT RAISE(ABORT,'fixture'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            reveal_holdout(self.store, row['id'])
        self.assertEqual(day_access(self.store, row['id'], row['hold_start']), 'holdout_locked')
