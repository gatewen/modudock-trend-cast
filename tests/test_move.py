from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.experiment import MODEL, canonical, load_plan
from back.http_client import ClientError
from back.jevcast import JevClient, JevAnswer, validate_response, request_payload, _AUTH_DISABLED
from back.move import (SEED, check_settings, ensure_experiment, freeze_sample, population_times,
                       matured_counts, convert, prepare_sample, run_stage_a, stage_a_report)
from back.prompts import P2_INSTRUCTIONS
from back.replay import Replay
from back.score import paired_bootstrap
from back.store import Store
from scripts.run_dev import CallBudget, DevClient
from scripts.run_move_stage_a import main
from tests.experiment_fixture import frozen_experiment
from tests.score_fixture import scored_fixture
from tests.helpers import FakeServer, KEY


def response(choice='move', probabilities=None):
    return {'model': MODEL, 'answers': {'move': {'choice': choice,
        'probabilities': probabilities if probabilities is not None else {'move': .8, 'still': .2}}}}


class FakeClient:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
    def predict(self, point, **kwargs):
        self.calls += 1
        assert kwargs == {'prompt_version': 'p3'}
        assert point.experiment_id == 3
        if self.fail:
            raise ClientError('network_error')
        return validate_response(response(), prompt_version='p3')


class MoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Store(':memory:')
        cls.ref, cls.days = scored_fixture(cls.base, populate_holdout=True)
        frozen_experiment(cls.base, cls.days, end=cls.days[30], prompt_version='p2')
        with cls.base.transaction():
            cls.base.db.execute('''INSERT INTO predictions SELECT experiment_id,'vol_prior',t,run_id,
                input_hash,input_json,answer,probs_json,model_reported,created_at FROM predictions
                WHERE method='majority' AND substr(t,1,10)<=?''', (cls.ref['dev_end'],))
    @classmethod
    def tearDownClass(cls): cls.base.close()
    def setUp(self):
        self.store = Store(':memory:'); self.base.db.backup(self.store.db)
        self.addCleanup(self.store.close)
        _AUTH_DISABLED.clear(); self.addCleanup(_AUTH_DISABLED.clear)
        env = patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY}); env.start(); self.addCleanup(env.stop)

    def test_p3_actual_http_only_binary_glossary_and_no_private_fields(self):
        ensure_experiment(self.store)
        point = Replay(self.store, load_plan(self.store, 3)).prepare(self.ref['dev_start']+'T09:30:00+08:00')
        server = FakeServer(); self.addCleanup(server.close)
        for k in (3, 7):
            server.queue(response())
            JevClient(opener=server).predict(replace(point, threshold_permille=k), prompt_version='p3')
            raw = server.bodies[-1]; payload = json.loads(raw)
            self.assertEqual(set(payload), {'state','model','questions'})
            self.assertEqual(payload['state'], point.state)
            self.assertEqual(set(payload['questions']), {'move'})
            question = payload['questions']['move']
            self.assertEqual(question['type'], 'choice')
            self.assertEqual(question['criteria'], {'move': f'上漲或下跌 {k}‰ 或更多（不論方向）', 'still': f'漲跌都小於 {k}‰'})
            self.assertEqual(question['instructions'], P2_INSTRUCTIONS.split('\n\n')[0]+'\n\n這檔股票從現在到 30 分鐘後，價格變化的幅度會落在哪一類？')
            for marker in (b'2330', point.t.date().isoformat().encode(), b'51%', b'25%', b'close_t', b'input_hash', KEY.encode()):
                self.assertNotIn(marker, raw)
            for row in self.store.db.execute('SELECT open,high,low,close FROM bars WHERE day=?',(self.ref['dev_start'],)):
                for price in row: self.assertNotIn(price.encode(), raw)

    def test_binary_validation_shape_sum_max_model_all_invalid(self):
        bad = [response('up'), response(True), response(probabilities={'move':1}),
               response(probabilities={'move':.8,'still':.2,'extra':0}),
               response(probabilities={'move':.8,'still':.8}), response('still'),
               response(probabilities={'move':True,'still':0}), response(probabilities={'move':float('nan'),'still':.2}),
               response(probabilities={'move':-1,'still':2}), response(probabilities={'move':'0.8','still':.2}),
               dict(response(), model='wrong'), {'answers':{'direction':{'choice':'move','probabilities':{'move':1,'still':0}}}}]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ClientError):
                validate_response(item, prompt_version='p3')
        for value in ('0.99','1.00'):
            self.assertEqual(validate_response(response(probabilities={'move':Decimal(value),'still':0}),prompt_version='p3').choice,'move')
        with self.assertRaises(ClientError):
            validate_response(response(probabilities={'move':Decimal('.989999'),'still':0}),prompt_version='p3')

    def test_maturity_inclusive_smoothing_flat_exclusion_future_random_and_ties(self):
        t = '2024-02-06T10:00:00+08:00'
        records = [('2024-02-06T09:00:00+08:00','up'), ('2024-02-06T09:30:00+08:00','down'),
                   ('2024-02-06T09:30:00+08:00','flat'), ('2024-02-06T09:30:01+08:00','up')]
        self.assertEqual(matured_counts(t,records),(1,1))
        base = convert(validate_response(response(),prompt_version='p3'),*matured_counts(t,records))
        self.assertEqual(base.probabilities,{'flat':.2,'up':.4,'down':.4})
        self.assertEqual(base.choice,'up')
        for _ in range(20):
            changed=records[:3]+[(records[-1][0],random.choice(['up','down','flat']))]*100
            self.assertEqual(convert(validate_response(response(),prompt_version='p3'),*matured_counts(t,changed)),base)
        answer = validate_response(response(probabilities={'move':.6,'still':.4}),prompt_version='p3')
        self.assertEqual(convert(answer,2,0).probabilities, {'flat':.4,'up':.44999999999999996,'down':.15})
        self.assertEqual(convert(answer,0,0).choice,'flat')
        self.assertEqual(convert(validate_response(response(probabilities={'move':1,'still':0}),prompt_version='p3'),0,0).choice,'up')
        tie=JevAnswer('move', {'move':.5,'still':.5},MODEL)
        self.assertEqual(convert(tie,0,0).choice,'flat')

    def test_timestamp_only_fixed_sample_and_future_labels_do_not_change_it(self):
        ensure_experiment(self.store)
        def guard(action,table,column,*args):
            if action==sqlite3.SQLITE_READ and table in ('outcomes','predictions') and column not in ('t','method','experiment_id'):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.db.set_authorizer(guard)
        chosen=freeze_sample(self.store,size=8)
        population=population_times(self.store,self.ref)
        self.store.db.set_authorizer(None)
        self.assertEqual(chosen,tuple(sorted(random.Random(20260927).sample(population,8))))
        self.assertTrue(all(self.ref['dev_start']<=t[:10]<=self.ref['dev_end'] for t in chosen))
        self.store.db.execute("UPDATE outcomes SET label='RANDOM'"); self.store.db.commit()
        self.assertEqual(freeze_sample(self.store,size=8),chosen)
        self.store.db.execute("UPDATE move_samples SET times_json='[]'"); self.store.db.commit()
        with self.assertRaisesRegex(DataError,'stage_a_sample_changed'): freeze_sample(self.store,size=8)

    def test_exact_settings_before_http_and_sample_not_enough(self):
        row=ensure_experiment(self.store)
        self.assertEqual(row['prompt_version'],'p3')
        for key in ('dev_start','dev_end','hold_start','hold_end','config_json'):
            self.assertEqual(row[key],self.ref[key])
        with self.assertRaisesRegex(DataError,'insufficient_stage_a_population'): freeze_sample(self.store)
        self.store.db.execute("UPDATE experiments SET dev_end=hold_end WHERE id=3"); self.store.db.commit()
        client=FakeClient()
        with self.assertRaisesRegex(DataError,'move_settings_changed'): run_stage_a(self.store,client,execute=True,size=8)
        self.assertEqual(client.calls,0)

    def test_no_execute_no_http_no_writes_and_cli_no_db_needed(self):
        client=FakeClient(); changes=self.store.db.total_changes
        self.assertEqual(run_stage_a(self.store,client)['http_calls'],0)
        self.assertEqual(client.calls,0); self.assertEqual(self.store.db.total_changes,changes)
        with redirect_stdout(io.StringIO()), patch('scripts.run_move_stage_a.DevClient',side_effect=AssertionError('network')):
            self.assertEqual(main(['--db','/does-not-exist']),0)

    def test_atomic_dev_only_no_reveal_rerun_zero_http_and_report_pairing(self):
        before={table:[tuple(r) for r in self.store.db.execute('SELECT * FROM '+table)] for table in ('predictions','outcomes','reveals','forward_days')}
        client=FakeClient()
        result=run_stage_a(self.store,client,execute=True,size=8)
        self.assertEqual((client.calls,result['missing']),(8,0))
        self.assertEqual(result['report']['methods']['jev_move']['n'],8)
        self.assertEqual(result['report']['methods']['vol_prior']['n'],8)
        report=result['report']; a,b=report['methods']['jev_move'],report['methods']['vol_prior']
        self.assertAlmostEqual(report['brier_difference'],a['brier']-b['brier'])
        self.assertEqual(report['bootstrap']['repetitions'],2000)
        self.assertEqual(report['bootstrap']['seed'],20260927)
        self.assertEqual(report['statement'],'沒有改善')
        self.assertFalse(report['stage_b_criterion_met'])
        for table in before:
            query='SELECT * FROM '+table+(' WHERE experiment_id<>3' if table in ('predictions','outcomes') else '')
            self.assertEqual([tuple(r) for r in self.store.db.execute(query)],before[table])
        self.assertEqual({tuple(r) for r in self.store.db.execute('SELECT method,split FROM runs WHERE experiment_id=3')},{('jev_move','dev')})
        again=FakeClient(); repeat=run_stage_a(self.store,again,execute=True,size=8)
        self.assertEqual(again.calls,0); self.assertEqual(repeat['report'],report)
        self.store.db.execute("UPDATE move_answers SET up_count=999"); self.store.db.commit()
        with self.assertRaisesRegex(DataError,'move_audit_conflict'): run_stage_a(self.store,again,execute=True,size=8)

    def test_failure_no_selective_report_missing_baseline_abort_and_write_rollback(self):
        result=run_stage_a(self.store,FakeClient(fail=True),execute=True,size=8)
        self.assertEqual(result['missing'],8); self.assertNotIn('report',result)
        times=freeze_sample(self.store,size=8)
        with self.assertRaisesRegex(DataError,'stage_a_incomplete'): stage_a_report(self.store,times)
        self.store.db.execute("CREATE TRIGGER no_audit BEFORE INSERT ON move_answers BEGIN SELECT RAISE(ABORT,'fixture'); END"); self.store.db.commit()
        with self.assertRaises(sqlite3.DatabaseError): run_stage_a(self.store,FakeClient(),execute=True,size=8)
        for table in ('predictions','outcomes','move_answers'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM '+table+' WHERE experiment_id=3').fetchone()[0],0)
        self.store.db.execute("DELETE FROM predictions WHERE method='vol_prior'"); self.store.db.commit()
        with self.assertRaisesRegex(DataError,'stage_a_baseline_missing'): prepare_sample(self.store,times)
        with self.assertRaisesRegex(DataError,'stage_a_point_outside_development'):
            prepare_sample(self.store,[self.ref['hold_start']+'T09:30:00+08:00'])

    def test_max_calls_retries_campaign_and_resume(self):
        server=FakeServer(); self.addCleanup(server.close)
        for _ in range(8): server.queue(response())
        budget=CallBudget(2); client=DevClient(budget,opener=server)
        result=run_stage_a(self.store,client,execute=True,size=8)
        self.assertEqual((budget.calls,len(server.requests),result['missing']),(2,2,6))
        self.assertNotIn('report',result)
        budget=CallBudget(10); client=DevClient(budget,opener=server)
        result=run_stage_a(self.store,client,execute=True,size=8)
        self.assertEqual((budget.calls,result['missing']),(6,0))
