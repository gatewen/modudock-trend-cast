from dataclasses import replace
from fractions import Fraction
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.daily_config import INDICATORS, settings
from back.daily_indicators import Frame
from back.daily_prompt import (sample_dates, request_payload, payload_bytes, questions,
    threshold_text, bias_states, COMMON_INSTRUCTIONS, ROUND_CALL_LIMIT)
from back.daily_replay import DailyReplay
from back.daily_store import DailyStore
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.experiment import MODEL, canonical
from scripts.preview_daily_prompt import main, render_prompt
from tests.daily_fixture import seed_daily


def config(days):
    result=settings()
    result['thresholds']={str(h):dict(numerator=1,denominator=d) for h,d in ((3,100),(7,50),(14,40))}
    result['first_prediction']=next(d for i,d in enumerate(days) if i>=60 and d>=result['dev_start'])
    return result


class DailyPromptTests(unittest.TestCase):
    def setUp(self):
        self.store=DailyStore(':memory:');self.addCleanup(self.store.close)
        self.days=seed_daily(self.store,340);self.config=config(self.days)
    def test_sample_calendar_only_fixed_grid_and_common_endpoints(self):
        def authorize(action,table,*_):
            if action==sqlite3.SQLITE_READ and table!='d_calendar':return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.db.set_authorizer(authorize)
        sample=sample_dates(self.store,self.config)
        self.store.db.set_authorizer(None)
        origin=self.days.index(self.config['first_prediction'])
        indices=[self.days.index(d) for d in sample['days']]
        self.assertEqual(indices,list(range(origin,len(self.days)-14,5)))
        self.assertEqual(sample['grid_count'],len(range(origin,len(self.days),5)))
        self.assertEqual(sample['step'],5);self.assertTrue(all(i+14<len(self.days) for i in indices))
        with self.store.transaction():
            self.store.db.execute('DELETE FROM d_bars');self.store.db.execute('DELETE FROM d_institutional')
        self.assertEqual(sample_dates(self.store,self.config),sample)
        with self.assertRaises(DataError):sample_dates(self.store,dict(self.config,dev_end='2022-01-03'))
    def test_three_questions_thresholds_exact_inclusive_from_config(self):
        q=questions(self.config)
        self.assertEqual(tuple(q),('direction_3d','direction_7d','direction_14d'))
        for H,k in ((3,'1'),(7,'2'),(14,'2.5')):
            question=q[f'direction_{H}d'];self.assertEqual(question['type'],'choice')
            self.assertEqual(question['criteria'],dict(up=f'R_H ≥ +{k}%（上漲 {k}% 或更多）',
                flat=f'−{k}% < R_H < +{k}%（漲跌幅皆未達 {k}%）',down=f'R_H ≤ −{k}%（下跌 {k}% 或更多）'))
            self.assertTrue(question['instructions'].startswith(COMMON_INSTRUCTIONS))
            self.assertIn(f'本題 H={H}',question['instructions'])
        self.config['thresholds']['7']=dict(numerator=3,denominator=100)
        self.assertIn('+3%',questions(self.config)['direction_7d']['criteria']['up'])
        self.assertEqual(threshold_text(Fraction(1,10)),'10')
        for k in (Fraction(0),Fraction(-1,100),Fraction(1,3)):
            with self.assertRaises(DataError):threshold_text(k)
    def test_payload_bytes_whitelist_no_dates_symbol_absolute_prices(self):
        day=self.days[200];raw=payload_bytes(self.store,self.config,day);p=json.loads(raw)
        self.assertEqual(set(p),{'state','model','questions'});self.assertEqual(p['model'],MODEL)
        self.assertEqual(set(p['state']),{'daily','indicators'})
        self.assertEqual(set(p['state']['indicators']),set(INDICATORS))
        self.assertEqual(len(p['state']['daily']),60)
        self.assertTrue(all(len(row)==5 for row in p['state']['daily']))
        text=raw.decode();self.assertNotRegex(text,r'\b\d{4}-\d{2}-\d{2}\b')
        for key in ('symbol','date','day','input_hash','experiment_id','close_t','ref_price','outcomes','probabilities','chips_previous_sessions'):
            self.assertNotIn('"'+key+'"',text)
        self.assertNotIn('2330',text);self.assertNotIn('台積電',text)
        self.assertEqual(raw,canonical(p).encode())
        # Multiplying every absolute price cannot affect the request.
        with self.store.transaction():
            self.store.db.execute('UPDATE d_bars SET open=open*10,high=high*10,low=low*10,close=close*10')
        self.assertEqual(raw,payload_bytes(self.store,self.config,day))
    def test_future_prices_corp_labels_and_same_day_chips_do_not_change_bytes(self):
        day=self.days[200];before=payload_bytes(self.store,self.config,day)
        with self.store.transaction():
            self.store.db.execute("UPDATE d_bars SET open='800',high='999',low='799',close='900',volume='90000' WHERE day>?",(day,))
            self.store.db.execute('UPDATE d_institutional SET buy=987654321 WHERE day>=?',(day,))
            self.store.db.execute('UPDATE d_margin SET margin_balance=987654321 WHERE day>=?',(day,))
            self.store.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',('2330',self.days[201],'10','9','fixture'))
            self.store.db.execute('UPDATE d_corp_coverage SET end_day=?',(day,))
            self.store.db.execute('CREATE TABLE d_outcomes(day TEXT,label TEXT)')
            self.store.db.execute("INSERT INTO d_outcomes VALUES (?,'future-poison')",(self.days[201],))
        def authorize(action,table,*_):
            if action==sqlite3.SQLITE_READ and table in ('d_predictions','d_outcomes','predictions','outcomes'):return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.db.set_authorizer(authorize)
        with patch.object(DailyReplay,'outcome',side_effect=AssertionError('must not calculate returns')):
            self.assertEqual(before,payload_bytes(self.store,self.config,day))
    def test_bias_cutoffs_per_horizon_maturity_boundary_and_future_invariance(self):
        def f(i,value):return Frame(self.days[i],i,True,dict(bias20=value),{})
        p=f(100,60);history=[f(93,0),f(94,50),f(97,100),f(98,999)]
        self.assertEqual(bias_states(history,p),{3:'middle',7:'high',14:'missing'})
        self.assertEqual(bias_states(history[:-1]+[f(98,-999),f(101,999)],p),bias_states(history,p))
        self.assertEqual(bias_states([replace(history[0],predictable=False)],p),{3:'missing',7:'missing',14:'missing'})
    def test_reuses_frozen_indicator_text_and_missing_is_not_zero(self):
        from back.daily_indicators import frames
        day=self.days[200];f=frames(self.store,end=day)[-1]
        p=request_payload(self.store,self.config,day)['state'];text=p['indicators']
        self.assertIn(f'K={f.values["kd_k"]:.6f}',text['kd'])
        self.assertIn('黃金交叉',text['ma_cross'])
        self.assertRegex(text['foreign_net'],r'^-?\d+\.\d{2}%$')
        for H in (3,7,14):self.assertIn(f'{H}日狀態=',text['bias20'])
        with self.store.transaction():self.store.db.execute('DELETE FROM d_institutional')
        p=request_payload(self.store,self.config,day)['state']
        self.assertEqual('本期無此資料',p['indicators']['foreign_net'])
        self.assertIn('均量不含該列當日',COMMON_INSTRUCTIONS)
        self.assertIn('含當日 20 日均量',COMMON_INSTRUCTIONS)
    def test_fail_closed_for_unpredictable_and_outside_development(self):
        for day in ('2022-01-03','2024-07-26','2010-01-05'):
            with self.assertRaises(DataError):payload_bytes(self.store,self.config,day)
        with self.store.transaction():self.store.db.execute('DELETE FROM d_bars WHERE day=?',(self.days[190],))
        with self.assertRaises(DataError):payload_bytes(self.store,self.config,self.days[200])
    def test_preview_no_network_readonly_canonical_body_and_exact_document(self):
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'db.sqlite3';out=Path(work)/'review';doc=Path(work)/'prompt.md'
            db=sqlite3.connect(path);self.store.db.backup(db);db.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest();guard=ForbiddenClient()
            with guard.guard(),patch('scripts.preview_daily_prompt.load_experiment',return_value=dict(config=self.config,data_digest='fixture')),patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(main(['--db',str(path),'--out',str(out),'--prompt-doc',str(doc)]),0)
            self.assertEqual(guard.calls,0);self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
            review=json.loads((out/'evolve-7-preflight.json').read_text());body=(out/'evolve-7-example-payload.json').read_bytes()
            self.assertEqual(review['example_body_sha256'],hashlib.sha256(body).hexdigest())
            self.assertEqual(review['round_max_calls'],650);self.assertTrue(review['approved_to_run'])
            self.assertEqual(review['network_attempts'],0)
            self.assertEqual(doc.read_text(),render_prompt(self.config,sample_dates(self.store,self.config)))
            for q in json.loads(body)['questions'].values():self.assertIn(q['instructions'],doc.read_text())
            with patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):main(['--execute'])

    def test_chips_only_percent_missing_units_and_previous_20_volume(self):
        day=self.days[200]
        with self.store.transaction():self.store.db.execute("UPDATE d_bars SET volume='100000'")
        p=request_payload(self.store,self.config,day)['state']['indicators']
        # 3 * 100 institutional shares / 100000; 5 margin lots * 1000 shares.
        self.assertEqual(p['foreign_net'],'0.30%');self.assertEqual(p['trust_net'],'0.30%')
        self.assertEqual(p['margin_chg'],'5.00%')
        with self.store.transaction():self.store.db.execute("UPDATE d_bars SET volume='99999999' WHERE day>=?",(day,))
        after=request_payload(self.store,self.config,day)['state']['indicators']
        for name in ('foreign_net','trust_net','margin_chg'):
            self.assertEqual(p[name],after[name]);self.assertRegex(after[name],r'^-?\d+\.\d{2}%$')
        with self.store.transaction():self.store.db.execute('DELETE FROM d_margin')
        missing=request_payload(self.store,self.config,day)['state']['indicators']
        self.assertEqual(missing['margin_chg'],'本期無此資料')

if __name__=='__main__':unittest.main()
