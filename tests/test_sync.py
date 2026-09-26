from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.fugle import FugleClient
from back import http_client
from back.store import Store
from back.sync import history_start, month_ranges, sync_history
from back.twse import TwseClient
from tests.helpers import FakeClock, FakeServer, KEY, candle


class SyncTests(unittest.TestCase):
    def test_calendar_month_ranges_and_history_start(self):
        self.assertEqual(history_start(date(2026, 9, 26)), date(2024, 7, 26))
        self.assertEqual(history_start(date(2025, 1, 31)), date(2023, 5, 23))
        self.assertEqual(month_ranges('2024-01-31', '2024-03-01'),
                         [('2024-01-31', '2024-01-31'), ('2024-02-01', '2024-02-29'),
                          ('2024-03-01', '2024-03-01')])

    def test_middle_404_keeps_older_month_and_unfinalized_is_refetched(self):
        server, clock = FakeServer(), FakeClock()
        self.addCleanup(server.close)
        http_client._DISABLED.clear()
        self.addCleanup(http_client._DISABLED.clear)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'db.sqlite3'
        fixture = json.loads((Path(__file__).parent / 'fixtures/twt49u.json').read_text())
        with patch.dict(os.environ, {'FUGLE_API_KEY': KEY}), Store(path) as store:
            fugle = FugleClient(**clock.transport(server))
            twse = TwseClient(**clock.transport(server))
            def queue_round():
                for month in ('03', '02', '01'):
                    end = {'03': '15', '02': '29', '01': '31'}[month]
                    day = '2024-' + month + '-12'
                    server.queue({'symbol': '2330', 'timeframe': 'D', 'data': [candle(day)]})
                    server.queue(dict(fixture, strDate='2024' + month + '01', endDate='2024' + month + end, data=[]))
                    if month == '02':
                        server.queue(status=404, raw=KEY.encode())
                    else:
                        server.queue({'symbol': '2330', 'timeframe': '1',
                                      'data': [candle(day + 'T09:00:00+08:00')]})
            queue_round()
            sync_history(store, fugle, twse, start='2024-01-01', today=date(2024, 3, 15))
            self.assertEqual([r[0] for r in store.db.execute('SELECT DISTINCT day FROM bars ORDER BY day')],
                             ['2024-01-12', '2024-03-12'])
            self.assertEqual(store.db.execute("SELECT http_status FROM fetch_log WHERE month='2024-02' AND kind='bars'").fetchone()[0], 404)
            self.assertEqual(len(server.requests), 9)
            queue_round()
            sync_history(store, fugle, twse, start='2024-01-01', today=date(2024, 3, 15))
            self.assertEqual(len(server.requests), 18)
            self.assertEqual(store.db.execute('SELECT count(*) FROM bars').fetchone()[0], 2)
            self.assertNotIn(KEY, '\n'.join(store.db.iterdump()))
        self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_failure_does_not_create_corp_coverage(self):
        server, clock = FakeServer(), FakeClock()
        self.addCleanup(server.close)
        http_client._DISABLED.clear()
        self.addCleanup(http_client._DISABLED.clear)
        with patch.dict(os.environ, {'FUGLE_API_KEY': KEY}), Store(':memory:') as store:
            server.queue({'symbol': '2330', 'timeframe': 'D', 'data': [candle('2024-09-12')]})
            server.queue({'stat': 'unknown failure echoed ' + KEY})
            server.queue({'symbol': '2330', 'timeframe': '1', 'data': [candle()]})
            sync_history(store, FugleClient(**clock.transport(server)), TwseClient(**clock.transport(server)),
                         start='2024-09-01', today=date(2024, 9, 30))
            self.assertEqual(store.corp_state('2330', '2024-09-12')['state'], 'unknown')
            self.assertNotIn(KEY, '\n'.join(store.db.iterdump()))
