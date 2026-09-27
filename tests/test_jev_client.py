from dataclasses import replace
from decimal import Decimal
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch
import urllib.request

from back.data import DataError
from back.experiment import MODEL
from back.http_client import ClientError, NoRedirect
from back.jevcast import (JevClient, URL, _AUTH_DISABLED,
                         request_payload, validate_response)
from back.replay import Replay
from back.store import Store
from tests.helpers import FakeClock, FakeServer, KEY
from tests.replay_fixture import at, plan_for, seed_replay


def response(choice='flat', probabilities=None, **extra):
    return dict(model=MODEL, answers={'direction': {'type': 'choice', 'choice': choice,
        'probabilities': probabilities if probabilities is not None else {'up': .2, 'flat': .6, 'down': .2}}},
        **extra)


class JevClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer()
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / 'data.sqlite3')
        cls.days = seed_replay(cls.store)
        cls.point = Replay(cls.store, plan_for(cls.days)).prepare(at(cls.days[25]))

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.temp.cleanup()
        cls.server.close()

    def setUp(self):
        _AUTH_DISABLED.clear()
        self.addCleanup(_AUTH_DISABLED.clear)
        self.server.reset()
        self.env = patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.clock = FakeClock()
        self.client = JevClient(opener=self.server, sleep=self.clock.sleep, clock=self.clock.clock)

    def test_actual_http_post_single_point_dynamic_criteria_and_private_payload(self):
        for k in (3, 7):
            self.server.queue(response())
            point = replace(self.point, threshold_permille=k)
            self.client.predict(point)
            body = self.server.bodies[-1]
            payload = json.loads(body)
            self.assertEqual(set(payload), {'state', 'model', 'questions'})
            self.assertEqual(payload['state'], point.state)
            self.assertEqual(set(payload['questions']), {'direction'})
            self.assertEqual(payload['questions']['direction']['criteria'],
                {'up': f'上漲 {k}‰ 或更多', 'flat': f'漲跌都小於 {k}‰', 'down': f'下跌 {k}‰ 或更多'})
            self.assertEqual(payload['model'], MODEL)
            numbers = []
            def collect(value):
                if isinstance(value, dict):
                    for item in value.values():
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
                elif isinstance(value, (int, float)):
                    numbers.append(Decimal(str(value)))
            collect(payload)
            # Compare with *all* same-day OHLC, not only close_t or ref.
            for row in self.store.db.execute('SELECT * FROM bars WHERE day=?', (self.days[25],)):
                for field in ('open', 'high', 'low', 'close'):
                    self.assertNotIn(Decimal(row[field]), numbers)
                    self.assertNotIn(row[field].encode(), body)
            for private in (b'2330', self.days[25].encode(), b'2024-', KEY.encode(), b'close_t', b'input_hash'):
                self.assertNotIn(private, body)
            original_url, headers, timeout = self.server.original_requests[-1]
            self.assertEqual((original_url, timeout), (URL, 15))
            self.assertEqual(dict(headers)['Authorization'], 'Bearer ' + KEY)
            self.assertEqual(self.server.requests[-1][0], '/v1/systemone?')

    def test_feature_hash_and_envelope_guard(self):
        with self.assertRaisesRegex(DataError, 'point_not_predictable'):
            request_payload(replace(self.point, predictable=False))
        with self.assertRaisesRegex(DataError, 'invalid_feature_input'):
            request_payload(replace(self.point, input_hash='incorrect'))
        text = json.dumps({**self.point.state, 'symbol': '2330'})
        with self.assertRaisesRegex(DataError, 'invalid_feature_input'):
            request_payload(replace(self.point, input_json=text, input_hash=hashlib.sha256(text.encode()).hexdigest()))

    def test_response_each_invalid_probability_shape_sum_and_choice(self):
        bad = [[], {}, {'answers': {}}, response(choice='other'), response(choice=True),
            response(probabilities={'up': 0, 'flat': 0, 'down': 0}),
            response(probabilities={'up': .4, 'flat': .4, 'down': .4}),
            response(probabilities={'up': .2, 'flat': .6, 'down': .2, 'extra': 0}),
            response(probabilities={'flat': 1}),
            response(probabilities={'up': float('nan'), 'flat': .6, 'down': .2}),
            response(probabilities={'up': float('inf'), 'flat': .6, 'down': .2}),
            response(probabilities={'up': True, 'flat': 0, 'down': 0}),
            response(probabilities={'up': '0.2', 'flat': .6, 'down': .2}),
            response(probabilities={'up': -.1, 'flat': .9, 'down': .2}),
            response(probabilities={'up': 0, 'flat': 1.1, 'down': 0}),
            response(choice='up')]
        for index, payload in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(ClientError):
                validate_response(payload)
        for total in ('0.989999', '1.010001'):
            with self.assertRaisesRegex(ClientError, 'invalid_probability_sum'):
                validate_response(response(probabilities={'up': 0, 'flat': Decimal(total), 'down': 0})
                    if Decimal(total) <= 1 else response(probabilities={'up': Decimal(total) - 1, 'flat': 1, 'down': 0}))

    def test_response_sum_tolerance_boundaries_ties_and_model_field(self):
        for probabilities in ({'up': 0, 'flat': Decimal('.99'), 'down': 0},
                              {'up': Decimal('.01'), 'flat': 1, 'down': 0},
                              {'up': .5, 'flat': .5, 'down': 0}):
            self.assertEqual(validate_response(response(probabilities=probabilities)).choice, 'flat')
        self.assertEqual(validate_response(response()).model_reported, MODEL)
        without_model = response()
        del without_model['model']
        self.assertIsNone(validate_response(without_model).model_reported)
        for wrong in ('jev-1.12.0', None, True, {'name': MODEL}):
            payload = response()
            payload['model'] = wrong
            with self.assertRaisesRegex(ClientError, 'model_mismatch'):
                validate_response(payload)

    def test_429_529_backoff_and_exact_retry_limit(self):
        for status in (429, 529):
            with self.subTest(status=status):
                self.server.reset()
                self.clock.sleeps.clear()
                for _ in range(2):
                    self.server.queue(status=status)
                self.server.queue(response())
                self.assertEqual(self.client.predict(self.point).choice, 'flat')
                self.assertEqual(self.clock.sleeps, [.5, 1])
                self.assertEqual(len(self.server.bodies), 3)
                self.server.reset()
                for _ in range(3):
                    self.server.queue(status=status)
                with self.assertRaisesRegex(ClientError, 'http_status'):
                    self.client.predict(self.point)
                self.assertEqual(len(self.server.bodies), 3)

    def test_401_403_latch_shared_by_new_clients_and_environment_changes(self):
        for status in (401, 403):
            with self.subTest(status=status):
                _AUTH_DISABLED.clear()
                self.server.reset()
                self.server.queue(status=status)
                with self.assertRaisesRegex(ClientError, 'auth_disabled'):
                    self.client.predict(self.point)
                with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'replacement-fixture-key'}):
                    other = JevClient(opener=self.server)
                    with self.assertRaisesRegex(ClientError, 'auth_disabled'):
                        other.predict(self.point)
                    self.assertFalse(other.enabled())
                self.assertEqual(len(self.server.requests), 1)

    def test_cancel_prevents_initial_request_and_retry_after_backoff(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(ClientError, 'cancelled'):
            self.client.predict(self.point, cancel=cancel)
        self.assertEqual(self.server.requests, [])
        cancel.clear()
        self.server.queue(status=429)
        client = JevClient(opener=self.server, sleep=lambda _: cancel.set())
        with self.assertRaisesRegex(ClientError, 'cancelled'):
            client.predict(self.point, cancel=cancel)
        self.assertEqual(len(self.server.requests), 1)

    def test_redirect_never_followed(self):
        self.server.queue(status=302, headers={'Location': '/stolen-key'})
        self.server.queue(response())
        with self.assertRaisesRegex(ClientError, 'http_status'):
            self.client.predict(self.point)
        self.assertEqual(len(self.server.requests), 1)

    def test_size_cap_declared_streamed_encoding_truncation_and_json(self):
        # Fixed spec limit (not an imported implementation constant), and valid
        # JSON: a removed size guard cannot hide behind a JSON parse failure.
        oversized = json.dumps(response()).encode().ljust(1024 * 1024 + 1, b' ')
        for headers in ({}, {'Content-Length': str(len(oversized))}):
            self.server.queue(raw=oversized, headers=headers)
            with self.assertRaisesRegex(ClientError, '^response_too_large$'):
                self.client.predict(self.point)
        cases = [dict(headers={'Content-Length': 'invalid'}),
            dict(headers={'Content-Encoding': 'gzip'}), dict(raw=b'not JSON'),
            dict(raw=b'\xff'), dict(raw=b'{"a":1,"a":2}'), dict(raw=b'{"a":NaN}'),
            dict(raw=b'{}', headers={'Content-Length': '4'})]
        for kwargs in cases:
            with self.subTest(kwargs={k: len(v) if isinstance(v, bytes) else v for k, v in kwargs.items()}):
                self.server.queue(**kwargs)
                with self.assertRaises(ClientError):
                    self.client.predict(self.point)

    def test_key_missing_invalid_and_transport_exception_not_retained_or_leaked(self):
        for key, code in (('', 'missing_key'), ('bad\nkey', 'invalid_key')):
            with patch.dict(os.environ, {'TYPESAFE_API_KEY': key}):
                with self.assertRaisesRegex(ClientError, code):
                    self.client.predict(self.point)
        self.assertEqual(self.server.requests, [])
        class BrokenOpener:
            def open(self, request, timeout):
                raise RuntimeError('transport says ' + request.get_header('Authorization'))
        try:
            JevClient(opener=BrokenOpener()).predict(self.point)
        except ClientError as exc:
            self.assertEqual(str(exc), 'network_error')
            self.assertIsNone(exc.__context__)
            self.assertIsNone(exc.__cause__)
            self.assertNotIn(KEY, ''.join(traceback.format_exception(exc)))
        else:
            self.fail('transport failure was accepted')

    def test_secure_defaults_require_ca_and_disable_proxy(self):
        budget = Path(self.temp.name) / 'mock-budget.sqlite3'
        with patch('back.http_client.ssl.create_default_context', side_effect=OSError(KEY)):
            with self.assertRaisesRegex(ClientError, '^network_error$'):
                JevClient(campaign_budget=budget).predict(self.point)
        fake_opener = self.server
        with patch('back.http_client.ssl.create_default_context') as context, \
             patch('back.http_client.urllib.request.build_opener', return_value=fake_opener) as build:
            self.server.queue(response())
            JevClient(campaign_budget=budget).predict(self.point)
            handlers = build.call_args.args
            self.assertEqual(handlers[0].proxies, {})
            self.assertIsInstance(handlers[1], NoRedirect)
            self.assertIsInstance(handlers[2], urllib.request.HTTPSHandler)
            context.assert_called_once_with()

    def test_wall_deadline_includes_streamed_response(self):
        clock = self.clock
        class SlowReply(io.BytesIO):
            status = 200
            headers = {}
            def read(self, size):
                clock.now += 16
                return super().read(size)
        class Opener:
            def open(self, request, timeout):
                return SlowReply(json.dumps(response()).encode())
        client = JevClient(opener=Opener(), clock=clock.clock)
        with self.assertRaisesRegex(ClientError, 'request_timeout'):
            client.predict(self.point)

    def test_invalid_http_json_response_does_not_become_a_valid_answer(self):
        for payload in (response(probabilities={'up': 0, 'flat': 0, 'down': 0}),
                        response(choice='up'), {**response(), 'model': 'other'}):
            self.server.queue(payload)
            with self.assertRaises(ClientError):
                self.client.predict(self.point)
