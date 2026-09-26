from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.experiment import data_digest, digest_settings, load_plan
from back.jevcast import JevClient, _AUTH_DISABLED, request_payload
from back.prompts import P2_BASE_PERCENT
from back.replay import Replay
from back.store import Store
from tests.experiment_fixture import frozen_experiment, seed_experiment
from tests.helpers import FakeServer, KEY
from tests.replay_fixture import at, plan_for
from tests.test_jev_client import response


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'p2.sqlite3'); self.addCleanup(self.store.close)
        self.days = seed_experiment(self.store)
        self.point = Replay(self.store, plan_for(self.days)).prepare(at(self.days[25]))
        self.server = FakeServer(); self.addCleanup(self.server.close)
        env = patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY}); env.start(); self.addCleanup(env.stop)
        _AUTH_DISABLED.clear(); self.addCleanup(_AUTH_DISABLED.clear)

    def test_p1_actual_http_body_byte_for_byte_matches_pre_p2_golden(self):
        # Captured BEFORE implementation from commit 4c9030f09df1f21ad09aa53ceff4668f2e5f70dd.
        golden = (Path(__file__).parent / 'fixtures/p1_http_payload.json').read_bytes()
        self.assertEqual(hashlib.sha256(golden).hexdigest(),
            '3afbedead181b90fe0b245ccbbd3a6abf1a9192d7b008d4877a24a338a84da0b')
        for kwargs in ({}, {'prompt_version': 'p1'}):
            self.server.queue(response())
            JevClient(opener=self.server).predict(self.point, **kwargs)
            self.assertEqual(self.server.bodies[-1], golden)

    def test_p2_http_payload_only_changes_instructions_and_preserves_privacy_and_threshold(self):
        for k in (3, 7):
            point = replace(self.point, threshold_permille=k)
            self.server.queue(response())
            JevClient(opener=self.server).predict(point, prompt_version='p2')
            raw = self.server.bodies[-1]
            actual, p1 = json.loads(raw), request_payload(point)
            instructions = actual['questions']['direction']['instructions']
            self.assertEqual(len(instructions.split('\n\n')), 3)
            for phrase in ('clock 現在時刻', 'minutes_to_close 距 13:30',
                'today 今日開／高／低／目前價相對參考價的千分比',
                'recent 今日最近至多 60 根', '自 09:00 起的分鐘序號',
                '收盤相對目前價的千分比', '過去 20 日同一分鐘平均的倍數',
                'null＝樣本不足', 'prev_days 前 5 個交易日',
                '日量相對其前 20 日均量的倍數', '由遠到近',
                '51% 屬於 flat', 'up 與 down 各約 25%',
                '機率反映你真正的把握程度', '沒有明確訊號時，機率應接近上述基準比例'):
                self.assertIn(phrase, instructions)
            actual['questions']['direction']['instructions'] = p1['questions']['direction']['instructions']
            self.assertEqual(actual, p1)  # No additional state, metadata or fields.
            self.assertEqual(actual['state'], json.loads(point.input_json))
            self.assertEqual(actual['questions']['direction']['criteria']['up'], f'上漲 {k}‰ 或更多')
            numbers = []
            def collect(value):
                if isinstance(value, dict):
                    for child in value.values(): collect(child)
                elif isinstance(value, list):
                    for child in value: collect(child)
                elif isinstance(value, (int, float)): numbers.append(Decimal(str(value)))
            collect(json.loads(raw))
            for row in self.store.db.execute('SELECT * FROM bars WHERE day=?', (self.point.t.date().isoformat(),)):
                for field in ('open', 'high', 'low', 'close'):
                    self.assertNotIn(Decimal(row[field]), numbers)
                    self.assertNotIn(row[field].encode(), raw)
            for private in (b'2330', self.point.t.date().isoformat().encode(), b'2024-', KEY.encode(),
                            b'close_t', b'input_hash', b'experiment_id', b'prompt_version'):
                self.assertNotIn(private, raw)

    def test_p2_percent_constants_are_frozen_rounded_source_outcomes(self):
        counts = {'flat': 1361, 'up': 662, 'down': 665}
        rounded = {k: int((Decimal(v) * 100 / 2688).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                   for k, v in counts.items()}
        self.assertEqual(P2_BASE_PERCENT, rounded)

    def test_unsupported_prompt_refused_before_http_and_experiment_write(self):
        for version in ('p3', None, 2):
            with self.assertRaisesRegex(DataError, 'unsupported_prompt_version'):
                JevClient(opener=self.server).predict(self.point, prompt_version=version)
            with self.assertRaisesRegex(DataError, 'unsupported_prompt_version'):
                frozen_experiment(self.store, self.days, prompt_version=version)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM experiments').fetchone()[0], 0)

    def test_prompt_frozen_into_digest_with_identical_split_and_f1_inputs(self):
        days = self.days
        p1 = frozen_experiment(self.store, days)
        p2 = frozen_experiment(self.store, days, prompt_version='p2')
        self.assertEqual((p1['prompt_version'], p2['prompt_version']), ('p1', 'p2'))
        for key in ('dev_start', 'dev_end', 'hold_start', 'hold_end', 'config_json', 'model',
                    'feature_version', 'threshold_permille'):
            self.assertEqual(p1[key], p2[key])
        self.assertNotEqual(p1['data_digest'], p2['data_digest'])
        settings = dict(digest_settings(p2), prompt_version='p1')
        self.assertEqual(data_digest(self.store, settings), p1['data_digest'])
        before, after = [Replay(self.store, load_plan(self.store, row['id'])).prepare(at(row['dev_start']))
                         for row in (p1, p2)]
        self.assertEqual((before.input_json, before.input_hash), (after.input_json, after.input_hash))
