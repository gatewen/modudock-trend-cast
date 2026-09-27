from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.daily_store import DailyStore
from back.daily_sources import CALENDAR,INSTITUTIONAL,MARGIN,FinmindClient,parse_finmind,integer
from back.daily_sync import years,synchronize,TWSE_LIMITER,no_jev
from back.daily_replay import (DailyReplay,HORIZONS,label,threshold_for_returns,development_thresholds)
from back.fugle import FugleClient,CandleBatch
from back.http_client import ClientError,RateLimiter,_LIMITERS
from back.jevcast import JevClient
from back.store import Store
from back.twse import CorpBatch,TwseClient,parse_events,roc_day
from tests.daily_fixture import seed_daily
from tests.helpers import FakeServer,FakeClock,KEY
from scripts.check_daily import inventory


class DailyReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=DailyStore(':memory:');cls.days=seed_daily(cls.base)
    @classmethod
    def tearDownClass(cls):cls.base.close()
    def setUp(self):
        self.store=DailyStore(':memory:');self.base.db.backup(self.store.db)
        self.addCleanup(self.store.close);self.engine=DailyReplay(self.store)

    def test_sixty_day_warmup_and_missing_history_day_not_weekend(self):
        d=self.days
        self.assertFalse(self.engine.prepare(d[59]).predictable)
        p=self.engine.prepare(d[60]);self.assertTrue(p.predictable)
        state=json.loads(p.input_json)
        self.assertEqual(len(state['daily']),60)
        self.assertIsNone(state['daily'][0][-1])
        self.assertIsNotNone(state['daily'][19][-1])
        self.assertNotIn('2330',p.input_json);self.assertNotIn('2010-',p.input_json)
        self.assertEqual(self.engine.prepare('2010-04-04').reason,'not_trading_day')
        self.store.db.execute('DELETE FROM d_bars WHERE day=?',(d[58],));self.store.db.commit()
        self.assertEqual(self.engine.prepare(d[60]).reason,'missing_history_day')

    def test_future_daily_chips_corporate_randomization_cannot_change_features_or_eligibility(self):
        t=self.days[75];before=self.engine.prepare(t)
        self.assertTrue(before.predictable)
        rng=random.Random(451)
        with self.store.transaction():
            for day in self.days[76:]:
                value=str(rng.randrange(1,100000))
                self.store.db.execute('UPDATE d_bars SET open=?,high=?,low=?,close=?,volume=? WHERE day=?',(*[value]*5,day))
                self.store.db.execute('UPDATE d_institutional SET buy=?,sell=? WHERE day=?',(rng.randrange(999999),rng.randrange(999999),day))
                self.store.db.execute('UPDATE d_margin SET margin_balance=? WHERE day=?',(rng.randrange(999999),day))
                self.store.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',('2330',day,'999','1','TWSE:TWT49U'))
        self.assertEqual(self.engine.prepare(t),before)
        self.store.db.execute('DELETE FROM d_corp_events WHERE day>?',(t,))
        self.store.db.execute('DELETE FROM d_bars WHERE day>?',(t,));self.store.db.commit()
        self.assertEqual(self.engine.prepare(t),before)
        self.assertFalse(self.engine.outcome(t,3).scorable)

    def test_same_day_chip_not_available_and_missing_chip_is_none_not_zero(self):
        day=self.days[75]; before=self.engine.prepare(day)
        self.assertEqual(json.loads(before.input_json)['chips_previous_sessions'][-1],[100,100,10074])
        with self.store.transaction():
            self.store.db.execute('UPDATE d_margin SET margin_balance=999999 WHERE day>=?',(day,))
            self.store.db.execute('UPDATE d_institutional SET buy=999999 WHERE day>=?',(day,))
        self.assertEqual(self.engine.prepare(day),before)
        with self.store.transaction():
            self.store.db.execute('DELETE FROM d_institutional WHERE day=?',(self.days[74],))
            self.store.db.execute('DELETE FROM d_margin WHERE day=?',(self.days[74],))
        after=self.engine.prepare(day);self.assertTrue(after.predictable)
        self.assertEqual(json.loads(after.input_json)['chips_previous_sessions'][-1],[None,None,None])

    def test_foreign_category_change_sums_foreign_dealer_and_refuses_missing_category(self):
        store=DailyStore(':memory:');self.addCleanup(store.close)
        days=seed_daily(store,start=date(2017,10,1));engine=DailyReplay(store,start=days[0])
        day='2017-12-18'; next_day='2017-12-19'
        self.assertIsNone(engine.chips(next_day)[-1][0])
        store.db.execute('INSERT INTO d_institutional VALUES (?,?,?,?,?)',('2330',day,'Foreign_Dealer_Self',30,10));store.db.commit()
        self.assertEqual(engine.chips(next_day)[-1][0],120)

    def test_real_20100706_ex_dividend_reference_and_exact_compounded_return(self):
        payload=json.loads((Path(__file__).parent/'fixtures/twt49u_2010_2330.json').read_text())
        event=parse_events(payload,'2330','2010-01-04','2010-12-31')[0]
        self.assertEqual((event['day'],event['prev_close'],event['ref_price']),('2010-07-06','61.4','58.4'))
        self.assertEqual(roc_day('99年07月06日'),'2010-07-06')
        self.assertEqual(roc_day('099年07月06日'),'2010-07-06')
        raw=json.loads((Path(__file__).parent/'fixtures/fugle_daily_20100705_08.json').read_text())['rows']
        days=[row['day'] for row in raw]
        with self.store.transaction():
            for row in raw:
                d=row['day']
                self.store.db.execute('INSERT OR IGNORE INTO d_calendar VALUES (?,?)',(d,'fixture'))
                self.store.db.execute('INSERT OR REPLACE INTO d_bars VALUES (?,?,?,?,?,?,?)',
                    tuple(row[k] for k in ('symbol','day','open','high','low','close','volume')))
            self.store.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',tuple(event[k] for k in ('symbol','day','prev_close','ref_price','source')))
        # Real raw closes: 61.4, 59.9, 59.5, 60.4; the ex-date ref is 58.4.
        self.store.write_corp(CorpBatch('2330',days[0],days[-1],[event]))
        self.assertEqual(self.engine.reference(days[1],self.engine.bar(days[0])),Fraction('58.4'))
        out=self.engine.outcome(days[0],3)
        self.assertTrue(out.scorable)
        self.assertEqual(out.adjusted_return,Fraction('60.40')/Fraction('58.40')-1)
        self.assertNotEqual(out.adjusted_return,Fraction('60.40')/Fraction('61.40')-1)

    def test_calendar_defines_endpoint_gaps_and_unknown_corp_refuse_scoring(self):
        t=self.days[65]
        for H in HORIZONS:
            out=self.engine.outcome(t,H);self.assertTrue(out.scorable);self.assertEqual(out.end_day,self.days[65+H])
        self.store.db.execute('DELETE FROM d_bars WHERE day=?',(self.days[67],));self.store.db.commit()
        self.assertEqual(self.engine.outcome(t,3).reason,'missing_interval_day')
        self.assertTrue(self.engine.prepare(t).predictable)
        self.base.db.backup(self.store.db)
        self.store.db.execute('UPDATE d_corp_coverage SET end_day=?',(self.days[66],));self.store.db.commit()
        self.assertEqual(self.engine.outcome(t,3).reason,'unknown_corporate_action')
        self.assertEqual(self.engine.prepare(self.days[68]).reason,'unknown_corporate_action')
        self.assertEqual(self.engine.outcome(self.days[-1],14).reason,'endpoint_unavailable')

    def test_calendar_omission_with_present_candle_cannot_shorten_horizon_or_history(self):
        self.store.db.execute('DELETE FROM d_calendar WHERE day=?',(self.days[67],));self.store.db.commit()
        self.assertEqual(self.engine.outcome(self.days[65],3).reason,'interval_calendar_mismatch')
        self.assertEqual(self.engine.prepare(self.days[70]).reason,'history_calendar_mismatch')

    def test_fraction_label_threshold_boundaries_and_population_half_up(self):
        k=Fraction(3,200);epsilon=Fraction(1,10**50)
        for value,expected in ((k,'up'),(-k,'down'),(k-epsilon,'flat'),(-k+epsilon,'flat'),(k+epsilon,'up'),(-k-epsilon,'down')):
            self.assertEqual(label(value,k),expected)
        self.assertEqual(threshold_for_returns([Fraction(-1,100),Fraction(1,100)]),Fraction(1,200))
        # Population: sigma .004 -> k .002 rounds to zero; sample sigma would
        # incorrectly round up. This makes ddof=0 observable at the final tick.
        with self.assertRaisesRegex(DataError,'zero_daily_threshold'):
            threshold_for_returns([Fraction(-1,250),Fraction(1,250)])
        # Population sigma=.005 -> half sigma=.0025, exactly halfway, rounds UP.
        self.assertEqual(threshold_for_returns([Fraction(-1,200),Fraction(1,200)]),Fraction(1,200))
        with self.assertRaisesRegex(DataError,'zero_daily_threshold'):
            threshold_for_returns([-Fraction(1,200)+epsilon,Fraction(1,200)-epsilon])
        with self.assertRaises(DataError):label(Fraction(0),Fraction(0))
        with self.assertRaises(DataError):label(0.1,k)

    def test_relative_features_scale_invariant_and_volume_mean_excludes_today(self):
        day=self.days[75];before=self.engine.prepare(day)
        rows=json.loads(before.input_json)['daily']
        expected=Fraction(1075)/ (sum(Fraction(1000+i) for i in range(55,75))/20)
        self.assertAlmostEqual(rows[-1][-1],float(expected))
        with self.store.transaction():
            self.store.db.execute('UPDATE d_bars SET open=open*10,high=high*10,low=low*10,close=close*10')
        self.assertEqual(self.engine.prepare(day),before)

    def test_development_boundary_never_reads_holdout_prices_or_chips(self):
        store=DailyStore(':memory:');self.addCleanup(store.close)
        days=seed_daily(store,count=150,start=date(2021,9,1));engine=DailyReplay(store,start=days[0])
        original=engine.bar
        def guarded(day):
            self.assertLessEqual(day,'2021-12-31')
            return original(day)
        with patch.object(engine,'bar',guarded):
            result=development_thresholds(engine)
        self.assertTrue(all(v['last_point']<'2021-12-31' for v in result.values()))
        with store.transaction():
            store.db.execute("UPDATE d_bars SET close='PRIVATE-HOLDOUT' WHERE day>='2022-01-03'")
            store.db.execute("UPDATE d_margin SET margin_balance=99999 WHERE day>='2022-01-03'")
            store.db.execute("UPDATE d_institutional SET buy=99999 WHERE day>='2022-01-03'")
        self.assertEqual(development_thresholds(engine),result)

    def test_development_thresholds_never_use_endpoint_after_split_and_future_changes(self):
        first=self.days.index('2010-04-01');end=self.days[95];start=self.days[first]
        baseline=development_thresholds(self.engine,dev_start=start,dev_end=end)
        for H in HORIZONS:
            self.assertEqual(baseline[H]['n'],96-first-H)
            values=[self.engine.outcome(d,H).adjusted_return for d in self.days[first:96-H]]
            self.assertEqual(Fraction(baseline[H]['k_numerator'],baseline[H]['k_denominator']),threshold_for_returns(values))
        with self.store.transaction():
            self.store.db.execute('UPDATE d_bars SET close=999999,volume=888888 WHERE day>?',(end,))
            self.store.db.execute('UPDATE d_margin SET margin_balance=777777 WHERE day>?',(end,))
            self.store.db.execute('UPDATE d_institutional SET buy=666666 WHERE day>?',(end,))
        self.assertEqual(development_thresholds(self.engine,dev_start=start,dev_end=end),baseline)
        for end in ('2022-01-03','2024-07-25','2026-09-24'):
            with self.assertRaisesRegex(DataError,'daily_threshold_outside_development'):
                development_thresholds(self.engine,dev_end=end)


class DailySourceStoreTests(unittest.TestCase):
    def test_finmind_free_symbol_full_range_no_auth_and_quota_status_not_coverage(self):
        server=FakeServer();self.addCleanup(server.close);clock=FakeClock()
        raw={'status':200,'msg':'success','data':[{'date':'2012-05-02','stock_id':'2330','name':'Foreign_Investor','buy':100,'sell':200}]}
        server.queue(raw)
        client=FinmindClient(**clock.transport(server));batch=client.fetch(INSTITUTIONAL,'2010-01-04','2026-09-24')
        self.assertEqual(len(batch.rows),1)
        url,headers,_=server.original_requests[0]
        self.assertIn('data_id=2330',url);self.assertIn('start_date=2010-01-04',url);self.assertIn('end_date=2026-09-24',url)
        self.assertNotIn('Authorization',dict(headers));self.assertNotIn('token',url)
        self.assertEqual(_LIMITERS['api.finmindtrade.com'].interval,12)
        server.queue({'status':402,'msg':'quota','data':[]})
        with self.assertRaisesRegex(ClientError,'finmind_quota_exhausted'):client.fetch(MARGIN,'2010-01-04','2026-09-24')
        for change in (dict(date='2009-12-31'),dict(stock_id='9999'),dict(name='new_unknown'),dict(buy=-1),dict(buy=.5)):
            bad=dict(raw,data=[dict(raw['data'][0],**change)])
            with self.assertRaises(DataError):parse_finmind(bad,INSTITUTIONAL,'2330','2010-01-04','2026-09-24')
        with self.assertRaises(DataError):parse_finmind(dict(raw,data=raw['data']*2),INSTITUTIONAL,'2330','2010-01-04','2026-09-24')
        for value in (True,'NaN','Inf',-.1):
            with self.assertRaises(DataError):integer(value)

    def test_annual_queries_raw_fugle_and_three_second_twse_retry_limiter(self):
        self.assertEqual(years('2010-01-04','2011-05-06'),(('2010-01-04','2010-12-31'),('2011-01-01','2011-05-06')))
        server=FakeServer();self.addCleanup(server.close);clock=FakeClock()
        server.queue({'symbol':'2330','timeframe':'D','adjusted':False,'data':[]})
        with patch.dict('os.environ',{'FUGLE_API_KEY':KEY}):
            FugleClient(**clock.transport(server)).candles('2330','2010-01-04','2010-12-31','D')
        url=server.original_requests[0][0]
        self.assertIn('adjusted=false',url);self.assertIn('timeframe=D',url)
        fixture=json.loads((Path(__file__).parent/'fixtures/twt49u_2010_2330.json').read_text())
        server.queue({},status=429);server.queue(fixture);server.queue(fixture)
        limiter=RateLimiter(3,clock=clock.clock,sleep=clock.sleep)
        client=TwseClient(opener=server,limiter=limiter,sleep=clock.sleep,clock=clock.clock)
        self.assertEqual(TWSE_LIMITER.interval,3)
        client.events('2330','2010-01-04','2010-12-31');client.events('2330','2010-01-04','2010-12-31')
        self.assertEqual(clock.now,6)

    def test_new_tables_only_atomic_range_replacement_and_three_states(self):
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'both.sqlite3'
            with Store(path) as legacy:
                legacy.db.execute("INSERT INTO daily VALUES ('2330','2010-01-04','1','1','1','1','1')");legacy.db.commit()
                original={r[0]:[tuple(v) for v in legacy.db.execute('SELECT * FROM '+r[0])] for r in legacy.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            with DailyStore(path) as store:
                def no_old_writes(action,table,*_):
                    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE) and table in original else sqlite3.SQLITE_OK
                store.db.set_authorizer(no_old_writes)
                days=seed_daily(store)
                self.assertEqual(store.corp_state(days[0])['state'],'none')
                self.assertEqual(store.corp_state('2009-12-31')['state'],'unknown')
                event=dict(symbol='2330',day=days[2],prev_close='100',ref_price='97',source='TWSE:TWT49U')
                store.write_corp(CorpBatch('2330',days[0],days[-1],[event]))
                self.assertEqual(store.corp_state(days[2])['state'],'event')
                store.db.execute("CREATE TRIGGER bad_write BEFORE INSERT ON d_corp_coverage BEGIN SELECT RAISE(ABORT,'fixture'); END");store.db.commit()
                with self.assertRaises(sqlite3.DatabaseError):store.write_corp(CorpBatch('2330',days[0],days[-1],[]))
                self.assertEqual(store.corp_state(days[2])['state'],'event')
                for table,rows in original.items():self.assertEqual([tuple(v) for v in store.db.execute('SELECT * FROM '+table)],rows)

    def test_invalid_or_empty_bar_batch_never_erases_data_and_mid_write_rolls_back(self):
        store=DailyStore(':memory:');self.addCleanup(store.close);days=seed_daily(store)
        before=[tuple(r) for r in store.db.execute('SELECT * FROM d_bars')]
        for status in (200,404):
            with self.assertRaisesRegex(DataError,'unconfirmed_daily_candles'):
                store.write_bars(CandleBatch('2330',days[0],days[-1],'D',status,[]))
        rows=[dict(r) for r in store.db.execute('SELECT * FROM d_bars')]
        store.db.execute("CREATE TRIGGER bad_bar BEFORE INSERT ON d_bars BEGIN SELECT RAISE(ABORT,'fixture'); END");store.db.commit()
        with self.assertRaises(sqlite3.DatabaseError):store.write_bars(CandleBatch('2330',days[0],days[-1],'D',200,rows))
        self.assertEqual([tuple(r) for r in store.db.execute('SELECT * FROM d_bars')],before)

    def test_sync_actual_wiring_annual_ranges_and_corp_limiter(self):
        store=DailyStore(':memory:');self.addCleanup(store.close)
        calls=[]
        class FakeFinmind:
            def fetch(self,dataset,start,end,symbol):
                return parse_finmind({'status':200,'msg':'success','data':[{'date':start}] if dataset==CALENDAR else []},dataset,symbol,start,end)
        class FakeFugle:
            def candles(self,symbol,start,end,timeframe):
                calls.append(('bars',symbol,start,end,timeframe))
                row=dict(symbol=symbol,day=start,open='100',high='101',low='99',close='100',volume='1')
                return CandleBatch(symbol,start,end,timeframe,200,[row])
        class FakeTwse:
            def events(self,symbol,start,end):
                calls.append(('corp',symbol,start,end))
                return CorpBatch(symbol,start,end,[])
        with patch('back.daily_sync.TwseClient',return_value=FakeTwse()) as factory:
            result=synchronize(store,start='2010-01-04',end='2011-01-31',fugle=FakeFugle(),finmind=FakeFinmind())
        self.assertEqual(factory.call_args.kwargs['limiter'].interval,3)
        self.assertEqual(result['written'],7)
        self.assertEqual(calls,[('bars','2330','2010-01-04','2010-12-31','D'),('corp','2330','2010-01-04','2010-12-31'),
                                ('bars','2330','2011-01-01','2011-01-31','D'),('corp','2330','2011-01-01','2011-01-31')])

    def test_sync_skip_ranges_annual_calls_no_jev_and_independent_calendar_missing(self):
        store=DailyStore(':memory:');self.addCleanup(store.close);days=seed_daily(store)
        class Offline:
            def __getattr__(self,name):raise AssertionError('unexpected request')
        # Fixture range exactly matches the requested year prefix.
        result=synchronize(store,start=days[0],end=days[-1],fugle=Offline(),twse=Offline(),finmind=Offline())
        self.assertEqual(result,{'written':0,'skipped':5,'jev_http_calls':0})
        for invoke in (lambda:JevClient().predict(None),lambda:JevClient()._request(b'',KEY)):
            with no_jev(),self.assertRaisesRegex(DataError,'jev_forbidden'):invoke()
        self.store=store
        store.db.execute('DELETE FROM d_bars WHERE day=?',(days[75],));store.db.commit()
        report=inventory(store,days[0],days[-1])
        self.assertEqual(report['missing_days'],[days[75]])
        self.assertNotIn('labels',report)
