from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.experiment import (ActivityGate, create_experiment, holdout_overlap, reveal_holdout)
from back.report import build_report, day_view, markdown_report, status_view
from back.replay import Replay
from back.score import (ALL_METHODS, INCOMPLETE, NO_EVIDENCE, best_development_baseline, load_split)
from back.store import Store
from scripts.check_data import build_report as data_report
from scripts.report import main
from tests.experiment_fixture import TODAY, frozen_experiment
from tests.helpers import batch, candle
from tests.score_fixture import change_answers, scored_fixture


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'score.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.row, self.days = scored_fixture(self.store, populate_holdout=True)
        self.dev_stamps = [r[0] for r in self.store.db.execute('''SELECT t FROM outcomes
            WHERE substr(t,1,10) BETWEEN ? AND ? ORDER BY t''', (self.row['dev_start'], self.row['dev_end']))]

    def report(self, **kwargs):
        return build_report(self.store, experiment_id=self.row['id'], **kwargs)

    def test_own_metrics_coverage_and_comparison_use_different_correct_denominators(self):
        with self.store.transaction():
            self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND t=?", (self.dev_stamps[0],))
            self.store.db.execute("DELETE FROM predictions WHERE method='momentum' AND t=?", (self.dev_stamps[1],))
        result = self.report()['dev']
        self.assertEqual(result['predictable_and_scorable'], 32)
        self.assertEqual(result['methods']['jev']['n'], 31)
        self.assertEqual(result['methods']['jev']['coverage'], 31/32)
        self.assertEqual(result['methods']['always_flat']['n'], 32)
        self.assertEqual(result['methods']['always_flat']['coverage'], 1)
        self.assertEqual(result['comparison']['n'], 30)
        self.assertEqual(result['comparison']['coverage'], 30/32)
        self.assertEqual({m['n'] for m in result['comparison']['intersection_metrics'].values()}, {30})
        self.assertEqual(result['comparison']['statement'], INCOMPLETE)
        self.assertTrue(result['comparison']['replay_complete'])
        for method in result['methods'].values():
            self.assertLessEqual(method['coverage'], 1)

    def test_unpredictable_and_unscorable_answers_cannot_inflate_coverage(self):
        prepare, outcome = Replay.prepare, Replay.outcome
        def unavailable(engine, t):
            value = prepare(engine, t)
            return replace(value, predictable=False) if t.isoformat() == self.dev_stamps[0] else value
        def missing_end(engine, point):
            value = outcome(engine, point)
            return replace(value, scorable=False, label=None, close_end=None) if point.t.isoformat() == self.dev_stamps[1] else value
        with self.store.transaction():
            self.store.db.execute('DELETE FROM outcomes WHERE t=?', (self.dev_stamps[1],))
        with patch.object(Replay, 'prepare', unavailable), patch.object(Replay, 'outcome', missing_end):
            data = self.report()['dev']
        self.assertEqual((data['candidates'], data['predictable'], data['scorable'],
                          data['predictable_and_scorable']), (32, 31, 31, 30))
        self.assertEqual(data['comparison']['n'], 30)
        self.assertEqual(data['jev_description']['n'], 30)
        for method in data['methods'].values():
            self.assertEqual((method['n'], method['coverage'], method['eligible_missing']), (30, 1, 0))

    def test_score_reader_itself_rejects_locked_holdout_before_loading_plan(self):
        with patch('back.score.load_plan', side_effect=AssertionError('must reject first')):
            with self.assertRaisesRegex(DataError, '^holdout_locked$'):
                load_split(self.store, self.row, 'holdout')

    def test_partial_smoke_not_complete_even_above_95_percent(self):
        with self.store.transaction():
            self.store.db.execute("UPDATE run_scopes SET full_split=0 WHERE run_id IN (SELECT id FROM runs WHERE method='jev')")
            self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND t=?", (self.dev_stamps[0],))
        result = self.report()['dev']
        self.assertGreaterEqual(result['comparison']['coverage'], .95)
        self.assertFalse(result['methods']['jev']['replay_complete'])
        self.assertEqual(result['comparison']['statement'], INCOMPLETE)

    def test_running_or_cancelled_latest_run_suppresses_conclusion_despite_answers(self):
        for status in ('running', 'cancelled', 'failed', 'call_limit'):
            with self.subTest(status=status):
                with self.store.transaction():
                    self.store.db.execute("UPDATE runs SET status=? WHERE method='jev' AND split='dev'", (status,))
                result = self.report()['dev']
                self.assertEqual(result['comparison']['coverage'], 1)
                self.assertEqual(result['comparison']['statement'], INCOMPLETE)

    def test_finished_failures_and_historical_retries_are_not_missing_counts(self):
        with self.store.transaction():
            self.store.db.execute("UPDATE runs SET n_fail=3 WHERE method='jev' AND split='dev'")
            self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND t=?", (self.dev_stamps[0],))
        result = self.report()['dev']
        self.assertEqual(result['methods']['jev']['failure_attempts'], 3)
        self.assertEqual(result['methods']['jev']['eligible_missing'], 1)
        self.assertTrue(result['methods']['jev']['replay_complete'])
        self.assertEqual(result['comparison']['statement'], 'jev 比 always_flat 差')

    def test_legacy_database_full_answers_prove_completion_without_scope_metadata(self):
        self.store.db.execute('DROP TABLE run_scopes')
        result = self.report()['dev']
        self.assertTrue(result['comparison']['replay_complete'])
        with self.store.transaction():
            self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND t=?", (self.dev_stamps[0],))
        self.assertFalse(self.report()['dev']['comparison']['replay_complete'])

    def test_corrupt_input_probabilities_model_or_outcomes_do_not_inflate_results(self):
        original = dict(self.store.db.execute("SELECT * FROM predictions WHERE method='jev' AND t=?", (self.dev_stamps[0],)).fetchone())
        for field, value in (('input_hash', 'wrong'), ('input_json', '{}'),
                ('probs_json', '{"up":0,"flat":0,"down":0}'), ('model_reported', 'other-model')):
            with self.subTest(field=field):
                with self.store.transaction():
                    self.store.db.execute(f"UPDATE predictions SET {field}=? WHERE method='jev' AND t=?", (value, self.dev_stamps[0]))
                result = self.report()['dev']
                self.assertEqual(result['methods']['jev']['invalid_predictions'], 1)
                self.assertEqual(result['methods']['jev']['n'], 31)
                self.assertEqual(result['comparison']['statement'], INCOMPLETE)
                with self.store.transaction():
                    self.store.db.execute(f"UPDATE predictions SET {field}=? WHERE method='jev' AND t=?", (original[field], self.dev_stamps[0]))
        with self.store.transaction():
            self.store.db.execute("UPDATE outcomes SET label='down' WHERE t=?", (self.dev_stamps[0],))
        with self.assertRaisesRegex(DataError, '^outcome_mismatch$'):
            self.report()

    def test_best_baseline_uses_brier_not_accuracy_fixed_tie_order(self):
        # All answers remain flat/correct, but majority's probabilities are worse.
        change_answers(self.store, 'always_flat', 'flat', self.row['dev_start'], self.row['dev_end'],
                       probabilities={'up': .1, 'flat': .8, 'down': .1})
        change_answers(self.store, 'majority', 'flat', self.row['dev_start'], self.row['dev_end'],
                       probabilities={'up': .2, 'flat': .6, 'down': .2})
        result = self.report()['dev']
        self.assertEqual(result['comparison']['baseline'], 'momentum')
        self.assertEqual(result['comparison']['accuracy_difference'], 0)
        self.assertAlmostEqual(result['comparison']['brier_difference'], .24)

    def test_baseline_selection_uses_same_development_intersection(self):
        for method in ('majority', 'momentum', 'reversal'):
            change_answers(self.store, method, 'flat', self.row['dev_start'], self.row['dev_end'],
                           probabilities={'up': .1, 'flat': .8, 'down': .1})
        with self.store.transaction():
            # always_flat's sole mistake is excluded from the five-way comparison.
            self.store.db.execute("UPDATE predictions SET answer='up',probs_json=? WHERE method='always_flat' AND t=?",
                                  ('{"up":1,"flat":0,"down":0}', self.dev_stamps[0]))
            self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND t=?", (self.dev_stamps[0],))
        result = self.report()['dev']
        self.assertGreater(result['methods']['always_flat']['brier'], result['methods']['majority']['brier'])
        self.assertEqual(result['comparison']['baseline'], 'always_flat')

    def test_holdout_keeps_development_winner_even_if_another_is_perfect_there(self):
        for method in ('always_flat', 'momentum', 'reversal'):
            change_answers(self.store, method, 'up', self.row['dev_start'], self.row['dev_end'])
        # Dev winner is majority; on holdout it is wrong, while always_flat wins.
        change_answers(self.store, 'majority', 'up', self.row['hold_start'], self.row['hold_end'])
        reveal_holdout(self.store, self.row['id'])
        result = self.report()
        self.assertEqual(result['dev']['comparison']['baseline'], 'majority')
        self.assertEqual(result['holdout']['comparison']['baseline'], 'majority')
        self.assertLess(result['holdout']['comparison']['brier_difference'], 0)
        self.assertEqual(result['holdout']['comparison']['statement'], 'jev 比 majority 好')

    def test_unfinished_development_blocks_holdout_conclusion_after_reveal(self):
        with self.store.transaction():
            self.store.db.execute("UPDATE runs SET status='running' WHERE method='jev' AND split='dev'")
        reveal_holdout(self.store, self.row['id'])
        result = self.report()
        self.assertTrue(result['holdout']['comparison']['replay_complete'])
        self.assertEqual(result['holdout']['comparison']['statement'], INCOMPLETE)

    def test_locked_report_and_day_do_not_even_evaluate_holdout_and_views_do_not_reveal(self):
        changes = self.store.db.total_changes
        original = load_split
        def guarded(store, row, split):
            self.assertEqual(split, 'dev')
            return original(store, row, split)
        with patch('back.report.load_split', side_effect=guarded):
            report = self.report()
            self.assertEqual(report['holdout'], {'state': 'locked', 'message': '保留段未解鎖'})
        with patch('back.report.Replay', side_effect=AssertionError('must reject before replay')):
            day = day_view(self.store, self.row['hold_start'], experiment_id=self.row['id'])
        self.assertEqual(day, {'status': 'holdout_locked', 'message': '保留段未解鎖'})
        status = status_view(self.store, experiment_id=self.row['id'])
        self.assertEqual(status['holdout']['state'], 'locked')
        self.assertNotIn('holdout_runs', status)
        for value in (report, day, status):
            text = json.dumps(value)
            self.assertNotIn(self.row['hold_start'], text)
            self.assertNotIn(self.row['hold_end'], text)
        self.assertEqual(self.store.db.total_changes, changes)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)

    def test_all_locked_exits_insensitive_to_hidden_predictions_labels_prices_and_run_counts(self):
        def views():
            return (self.report(), day_view(self.store, self.row['hold_start']), status_view(self.store), data_report(self.store))
        before = views()
        with self.store.transaction():
            self.store.db.execute("UPDATE outcomes SET label='down',close_end='998877.66' WHERE substr(t,1,10)>=?", (self.row['hold_start'],))
            self.store.db.execute("UPDATE predictions SET answer='down',probs_json='PRIVATE-HOLDOUT' WHERE substr(t,1,10)>=?", (self.row['hold_start'],))
            self.store.db.execute("UPDATE runs SET n_ok=987654,n_fail=876543 WHERE split='holdout'")
            self.store.db.execute("UPDATE bars SET open='998877',high='998878',low='998876',close='998877' WHERE day>=?", (self.row['hold_start'],))
            self.store.db.execute("UPDATE daily SET open='998877',high='998878',low='998876',close='998877' WHERE day>=?", (self.row['hold_start'],))
        after = views()
        self.assertEqual(before, after)
        for output in after:
            serialized = json.dumps(output)
            for private in ('PRIVATE-HOLDOUT', '998877', '987654', '876543'):
                self.assertNotIn(private, serialized)

    def test_hidden_only_fetch_warning_in_same_month_does_not_affect_report_or_status(self):
        before = self.report(), status_view(self.store)
        with self.store.transaction():
            self.store.db.execute('''INSERT INTO fetch_log
                (symbol,month,fetched_at,http_status,n_bars,final,note,kind,range_start,range_end)
                VALUES ('2330',?,'fixture',200,998877,1,'frozen_difference','bars',?,?)''',
                (self.row['hold_start'][:7], self.row['hold_start'], self.row['hold_end']))
        self.assertEqual(before, (self.report(), status_view(self.store)))

    def test_explicit_reveal_persists_then_unlocks_report_and_chart_only_this_experiment(self):
        reveal_holdout(self.store, self.row['id'])
        with Store(self.path, readonly=True) as reader:
            result = build_report(reader, experiment_id=self.row['id'])
            self.assertEqual(result['holdout']['state'], 'revealed')
            self.assertEqual(result['holdout']['methods']['jev']['n'], 16)
            view = day_view(reader, self.row['hold_start'], experiment_id=self.row['id'])
            self.assertEqual(view['status'], 'ok')
            self.assertEqual(len(view['bars']), 266)
            self.assertEqual(len(view['points']), 8)
            self.assertGreater(status_view(reader)['holdout']['overlap_days'], 0)
        other = frozen_experiment(self.store, self.days, end=self.days[30], threshold_permille=5)
        status = status_view(self.store, experiment_id=other['id'])
        self.assertTrue(status['holdout']['used'])
        self.assertEqual(status['holdout']['overlap_days'], 2)
        self.assertEqual(day_view(self.store, other['hold_start'], experiment_id=other['id'])['status'], 'holdout_locked')

    def test_other_experiments_development_marks_new_holdout_used_without_revealing(self):
        newer = frozen_experiment(self.store, self.days, end=self.days[28])
        status = status_view(self.store, experiment_id=newer['id'])
        self.assertEqual(status['holdout']['message'], '保留段已使用（2 日重疊）')
        self.assertEqual(status['holdout']['state'], 'locked')
        self.assertEqual(holdout_overlap(self.store, newer['id']), 2)

    def test_labels_only_reveal_does_not_unlock_report_or_chart(self):
        data_report(self.store, include_holdout=True)
        self.assertEqual(self.report()['holdout']['state'], 'locked')
        self.assertEqual(day_view(self.store, self.row['hold_start'])['status'], 'holdout_locked')
        self.assertTrue(status_view(self.store)['holdout']['used'])

    def test_no_experiment_all_views_refuse_market_data_and_check_data_is_quality_only(self):
        with Store(':memory:') as empty:
            empty.write_candles(batch([candle()]))
            for output in (build_report(empty), day_view(empty, '2024-09-12'), status_view(empty)):
                self.assertEqual(output, {'status': 'experiment_required', 'message': '尚未建立實驗'})
            self.assertEqual(len(data_report(empty)), 6)

    def test_frozen_difference_warning_without_overwriting_or_leaking_prices(self):
        day = self.row['dev_start']
        original = self.store.db.execute('SELECT close FROM bars WHERE day=? ORDER BY bar_end LIMIT 1', (day,)).fetchone()[0]
        changed = dict(candle(day + 'T09:00:00+08:00', close='998877'), open='998877', high='998878', low='998876')
        self.store.write_candles(batch([changed], start=day, end=day))
        report = self.report()
        self.assertEqual(report['frozen_warnings'], [{'kind': 'bars'}])
        self.assertNotIn('998877', json.dumps(report))
        self.assertEqual(self.store.db.execute('SELECT close FROM bars WHERE day=? ORDER BY bar_end LIMIT 1', (day,)).fetchone()[0], original)
        self.assertIn('未覆寫', markdown_report(report))

    def test_cli_is_readonly_has_no_reveal_switch_and_renders_both_formats(self):
        changes = self.store.db.total_changes
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--db', str(self.path)]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result['score_version'], 's2')
        self.assertEqual(result['holdout']['state'], 'locked')
        markdown = Path(self.temp.name) / 'report.md'
        self.assertEqual(main(['--db', str(self.path), '--format', 'markdown', '--output', str(markdown)]), 0)
        self.assertIn('Wilson 95%', markdown.read_text())
        self.assertIn('jev 描述性分布', markdown.read_text())
        self.assertNotIn(self.row['hold_start'], markdown.read_text())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['--db', str(self.path), '--reveal'])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--db', str(self.path), '--output', str(self.path)]), 2)
        self.assertEqual(self.store.db.total_changes, changes)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)
