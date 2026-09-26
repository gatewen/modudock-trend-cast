from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import ssl
import traceback
import unittest
from unittest.mock import patch
import urllib.request

from back.data import DataError
from back.fugle import FugleClient
from back import http_client
from back.http_client import ClientError, JsonClient, secure_opener
from back.twse import TwseClient, parse_events
from tests.helpers import FakeClock, FakeServer, KEY, candle


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        self.server.reset()
        http_client._DISABLED.clear()
        self.env = patch.dict(os.environ, {'FUGLE_API_KEY': KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(http_client._DISABLED.clear)
        self.clock = FakeClock()
        self.client = FugleClient(**self.clock.transport(self.server))

    def payload(self, rows=None, timeframe='1'):
        return {'symbol': '2330', 'timeframe': timeframe, 'data': [candle()] if rows is None else rows}

    def fetch(self, client=None, timeframe='1'):
        return (client or self.client).candles('2330', '2024-09-01', '2024-09-30', timeframe)

    def test_month_and_daily_query_decimal_precision(self):
        self.server.queue(raw=b'{"symbol":"2330","timeframe":"1","data":[{"date":"2024-09-12T09:00:00.000+08:00","open":100.0000000001,"high":101,"low":99,"close":100.3,"volume":10}]}')
        result = self.fetch()
        self.assertEqual(result.rows[0]['open'], '100.0000000001')
        self.assertEqual(result.rows[0]['bar_end'], '2024-09-12T09:01:00+08:00')
        url, headers, timeout = self.server.original_requests[0]
        self.assertTrue(url.startswith('https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/2330?'))
        self.assertNotIn(KEY, url)
        self.assertEqual(dict(headers)['X-api-key'], KEY)
        self.assertEqual(timeout, 20)
        self.server.queue(self.payload([candle('2024-09-12')], 'D'))
        self.fetch(timeframe='D')
        self.assertIn('adjusted=false', self.server.requests[-1][0])

    def test_404_and_empty_data(self):
        self.server.queue(status=404, raw=KEY.encode())
        self.assertEqual((self.fetch().status), 404)
        self.server.queue(self.payload([]))
        self.assertEqual(self.fetch().rows, [])

    def test_rate_limit_shared_across_requests(self):
        times = []
        for _ in range(3):
            self.server.queue(self.payload([]))
            self.fetch()
            times.append(self.clock.now)
        self.assertEqual(times, [0, 2, 4])

    def test_default_limiter_shared_across_clients(self):
        first, second = FugleClient(), FugleClient()
        self.assertIs(first._http._limiter, second._http._limiter)

    def test_429_backoff_and_retry_limit(self):
        for _ in range(3):
            self.server.queue(status=429)
        self.server.queue(self.payload())
        self.assertEqual(len(self.fetch().rows), 1)
        self.assertEqual(self.clock.sleeps, [2, 4, 8])
        self.server.reset()
        for _ in range(5):
            self.server.queue(status=429)
        with self.assertRaises(ClientError) as caught:
            self.fetch()
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(self.server.requests), 4)
        self.assertEqual(len(self.server.responses), 1)

    def test_401_403_disable_process_including_new_clients(self):
        for status in (401, 403):
            with self.subTest(status=status):
                http_client._DISABLED.clear()
                self.server.reset()
                self.server.queue(status=status, raw=KEY.encode())
                with self.assertRaisesRegex(ClientError, '^auth_disabled$'):
                    self.fetch()
                other = FugleClient(**self.clock.transport(self.server))
                with self.assertRaisesRegex(ClientError, '^auth_disabled$'):
                    self.fetch(other)
                self.assertEqual(len(self.server.requests), 1)

    def test_redirect_not_followed_or_key_forwarded(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.server.reset()
                self.server.queue(status=status, headers={'Location': '/stolen'})
                self.server.queue(self.payload())
                with self.assertRaises(ClientError) as caught:
                    self.fetch()
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(len(self.server.requests), 1)

    def test_oversize_declared_and_streamed_body(self):
        # Independent specification oracle: do not import the implementation's cap.
        spec_max_bytes = 16 * 1024 * 1024
        oversized_json = json.dumps(dict(self.payload([]), padding='x' * spec_max_bytes)).encode()
        for headers, body in (({'Content-Length': str(spec_max_bytes + 1)}, b''),
                              ({}, oversized_json)):
            with self.subTest(declared=bool(headers)):
                self.server.queue(headers=headers, raw=body)
                with self.assertRaisesRegex(ClientError, '^response_too_large$'):
                    self.fetch()

    def test_truncated_body_rejected(self):
        self.server.queue(raw=b'{}', headers={'Content-Length': '5'})
        with self.assertRaisesRegex(ClientError, '^truncated_response$'):
            self.fetch()

    def test_bad_json_duplicate_keys_nonfinite_and_encoding(self):
        for body in (b'{', b'\xff', b'{"x":1,"x":2}', b'{"x":NaN}'):
            with self.subTest(body=body):
                self.server.queue(raw=body)
                with self.assertRaisesRegex(ClientError, '^invalid_json$'):
                    self.fetch()
        self.server.queue(raw=b'{}', headers={'Content-Encoding': 'gzip'})
        with self.assertRaisesRegex(ClientError, '^unsupported_encoding$'):
            self.fetch()

    def test_envelope_and_candle_validation(self):
        samples = [[], {'symbol': '9999', 'timeframe': '1', 'data': []},
                   dict(self.payload(), adjusted=True), self.payload([candle(), candle()]),
                   self.payload([dict(candle(), close='999')]),
                   self.payload([dict(candle(), date='2024-10-01T09:00:00+08:00')]),
                   self.payload([dict(candle(), date='2024-09-12T09:00:00')])]
        for payload in samples:
            with self.subTest(payload=payload):
                self.server.queue(payload)
                with self.assertRaises(DataError):
                    self.fetch()

    def test_missing_key_and_invalid_query_do_not_connect(self):
        with patch.dict(os.environ, {'FUGLE_API_KEY': ''}):
            with self.assertRaisesRegex(ClientError, '^missing_key$'):
                self.fetch()
        with self.assertRaises(DataError):
            self.client.candles('../steal', '2024-09-01', '2024-09-30')
        self.assertEqual(self.server.requests, [])

    def test_calendar_year_limit_accepts_documented_leap_year_example(self):
        self.server.queue(self.payload([]))
        self.assertEqual(self.client.candles('2330', '2024-01-01', '2024-12-31').rows, [])
        with self.assertRaises(DataError):
            self.client.candles('2330', '2023-12-31', '2024-12-31')
        self.assertEqual(len(self.server.requests), 1)

    def test_errors_never_leak_key_or_upstream_exception_context(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            for status, body in ((500, KEY.encode()), (200, ('bad ' + KEY).encode())):
                self.server.queue(status=status, raw=body)
                try:
                    self.fetch()
                except ClientError as exc:
                    self.assertNotIn(KEY, str(exc))
                    self.assertNotIn(KEY, repr(exc))
                    self.assertNotIn(KEY, traceback.format_exc())
                    self.assertIsNone(exc.__context__)
                else:
                    self.fail('request unexpectedly accepted')
            with patch.object(self.server, 'open', side_effect=OSError(KEY)):
                try:
                    self.fetch()
                except ClientError as exc:
                    self.assertIsNone(exc.__context__)
                    self.assertNotIn(KEY, traceback.format_exc())
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')

    def test_fixed_hosts_no_proxies_and_verified_tls(self):
        with self.assertRaisesRegex(ClientError, '^host_not_allowed$'):
            JsonClient('attacker.example')
        client = JsonClient('api.fugle.tw', **self.clock.transport(self.server))
        with self.assertRaisesRegex(ClientError, '^invalid_path$'):
            client.get('//attacker.example/path', {})
        with patch.dict(os.environ, {'https_proxy': 'http://127.0.0.1:9'}):
            with patch('urllib.request.build_opener') as build:
                secure_opener()
                handlers = build.call_args.args
        proxy = next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler))
        https = next(h for h in handlers if isinstance(h, urllib.request.HTTPSHandler))
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(https._context.check_hostname)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)

    def test_tls_setup_failure_refuses_connection(self):
        with patch('ssl.create_default_context', side_effect=ssl.SSLError(KEY)):
            with patch('urllib.request.build_opener') as build:
                with self.assertRaisesRegex(ClientError, '^tls_error$') as caught:
                    self.fetch(FugleClient(limiter=self.clock.transport(self.server)['limiter']))
                build.assert_not_called()
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(self.server.requests, [])

    def test_twse_fixture_fields_and_three_state_prerequisites(self):
        payload = json.loads((Path(__file__).parent / 'fixtures/twt49u.json').read_text())
        self.server.queue(payload)
        twse = TwseClient(**self.clock.transport(self.server))
        result = twse.events('2330', '2024-09-01', '2024-09-30')
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]['ref_price'], '896.99')  # Not auction base 897.
        self.assertEqual(result.events[0]['day'], '2024-09-12')
        self.assertNotIn('X-api-key', dict(self.server.original_requests[-1][1]))
        payload['data'] = []
        self.assertEqual(parse_events(payload, '2330', '2024-09-01', '2024-09-30'), [])
        for broken in ({'stat': '很抱歉，沒有符合條件的資料!'},
                       dict(payload, strDate='20240902'), dict(payload, fields=[]),
                       dict(payload, data=[['bad']])):
            with self.assertRaises(DataError):
                parse_events(broken, '2330', '2024-09-01', '2024-09-30')


if __name__ == '__main__':
    unittest.main()
