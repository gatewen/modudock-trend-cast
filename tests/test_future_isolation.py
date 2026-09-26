from dataclasses import replace
from datetime import timedelta
import random
import unittest

from back.baselines import Baselines, METHODS
from back.replay import LabelObservation, Replay
from back.store import Store
from tests.replay_fixture import at, plan_for, seed_replay


class FutureIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Store(':memory:')
        cls.days = seed_replay(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.base.close()

    def setUp(self):
        self.store = Store(':memory:')
        self.base.db.backup(self.store.db)
        self.addCleanup(self.store.close)
        self.replay = Replay(self.store, plan_for(self.days))
        self.day = self.days[25]

    def snapshot(self, t, records):
        point = self.replay.prepare(t)
        baseline = Baselines(self.replay, records)
        answers = tuple(baseline.predict(method, point) for method in METHODS)
        return point.predictable, point.reason, point.input_json, point.input_hash, answers

    def test_random_future_data_today_daily_and_unmatured_labels_never_change_predictions(self):
        all_times = [*self.replay.candidates('dev'), *self.replay.candidates('holdout')]
        records = tuple(LabelObservation(t, True, True, ('flat', 'up', 'down')[i % 3])
                        for i, t in enumerate(all_times))
        points = list(self.replay.candidates('dev'))[:8]
        for seed in (17, 91):
            for t in points:
                with self.subTest(seed=seed, clock=t.strftime('%H:%M')):
                    before = self.snapshot(t, records)
                    rng = random.Random(seed)
                    self.store.db.execute('SAVEPOINT future_perturbation')
                    try:
                        keys = [r[0] for r in self.store.db.execute(
                            'SELECT bar_end FROM bars WHERE bar_end>?', (t.isoformat(),))]
                        rows = [(str(rng.randrange(600, 1900)), '2000', '500',
                                 str(rng.randrange(600, 1900)), str(rng.randrange(1, 50000)), key)
                                for key in keys]
                        self.store.db.executemany('''UPDATE bars SET open=?,high=?,low=?,close=?,volume=?
                            WHERE bar_end=?''', rows)
                        keys = [r[0] for r in self.store.db.execute('SELECT day FROM daily WHERE day>=?', (self.day,))]
                        self.store.db.executemany('''UPDATE daily SET open=?,high='3000',low='200',close=?,volume=?
                            WHERE day=?''', [(str(rng.randrange(600, 1900)), str(rng.randrange(600, 1900)),
                                             str(rng.randrange(10000, 500000)), day) for day in keys])
                        self.store.db.executemany('INSERT OR REPLACE INTO corp_events VALUES (?,?,?,?,?)',
                            [('2330', day, '1500', str(rng.randrange(500, 1900)), 'TWSE:TWT49U')
                             for day in keys if day > self.day])
                        changed = tuple(replace(record, label=rng.choice(('up', 'flat', 'down')),
                                                predictable=bool(rng.randrange(2)), scorable=bool(rng.randrange(2)))
                                        if record.t + timedelta(minutes=30) > t else record for record in records)
                        self.assertEqual(self.snapshot(t, changed), before)
                    finally:
                        self.store.db.execute('ROLLBACK TO future_perturbation')
                        self.store.db.execute('RELEASE future_perturbation')

    def test_deleting_future_bars_and_today_daily_does_not_change_input_or_baselines(self):
        t = at(self.day, '10:30')
        before = self.snapshot(t, ())
        with self.store.transaction():
            self.store.db.execute('DELETE FROM bars WHERE bar_end>?', (t.isoformat(),))
            self.store.db.execute('DELETE FROM daily WHERE day>=?', (self.day,))
            self.store.db.execute('DELETE FROM corp_coverage WHERE start_day>?', (self.day,))
        self.assertEqual(self.snapshot(t, ()), before)
        self.assertFalse(self.replay.outcome(self.replay.prepare(t)).scorable)

    def test_raw_at_t_is_invisible_but_bar_ending_at_t_changes_input_and_hash(self):
        t = at(self.day)
        before = self.snapshot(t, ())
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET high='1100',close='1100' WHERE ts_raw=?",
                                 (t.isoformat(),))
        self.assertEqual(self.snapshot(t, ()), before)
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET high='1100',close='1100' WHERE bar_end=?",
                                 (t.isoformat(),))
        after = self.snapshot(t, ())
        self.assertNotEqual(after[2], before[2])
        self.assertNotEqual(after[3], before[3])
        self.assertNotEqual(after[4], before[4])

    def test_available_bars_fractional_cutoff_rejects_plus_60_seconds_mutation(self):
        # At exact minute boundaries '< t+60s' is equivalent for minute-aligned
        # bars. A non-aligned cutoff distinguishes this defective shared helper.
        rows = self.store.available_bars(self.day, at(self.day) + timedelta(seconds=30))
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[-1]['bar_end'], at(self.day).isoformat())
