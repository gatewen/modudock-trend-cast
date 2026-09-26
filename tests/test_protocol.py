import io
import json
import os
from pathlib import Path
import queue
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from back.protocol import MAX_PACKET, Outbox
from back.store import Store
from back.trendcast import preflight
from tests.experiment_fixture import frozen_experiment, seed_experiment
from tests.helpers import KEY

ROOT = Path(__file__).resolve().parents[1]


class Child:
    def __init__(self, path, gates, *, mode='normal', drain=True):
        self.gates = gates; gates.mkdir()
        env = os.environ.copy(); env.pop('TYPESAFE_API_KEY', None); env.pop('FUGLE_API_KEY', None)
        self.proc = subprocess.Popen([sys.executable, '-B', '-m', 'tests.protocol_fixture',
            '--mode', mode, '--gates', str(gates), '--db', str(path)], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.lines = []; self.queue = queue.Queue(); self.seq = 731927
        self.reader = None
        if drain:
            self.reader = threading.Thread(target=self._read, daemon=True); self.reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line); self.queue.put(json.loads(line))

    def send(self, kind, seq=None, **kwargs):
        self.proc.stdin.write((json.dumps({'t': kind, 'seq': self.seq if seq is None else seq, **kwargs}) + '\n').encode())
        self.proc.stdin.flush()

    def get(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while True:
            packet = self.queue.get(timeout=max(.01, end - time.monotonic()))
            if predicate(packet): return packet
            if time.monotonic() > end: raise AssertionError('packet deadline')

    def ready(self):
        self.send('hello'); return self.get(lambda p: p['t'] in ('ready', 'fail'))

    def up(self):
        self.send('up')
        return self.get(lambda p: p['t'] == 'msg' and p['body'].get('op') == 'status' and p['body'].get('experiment_id') == 1)

    def mark(self, name, timeout=3):
        end = time.monotonic() + timeout
        while not (self.gates / name).exists():
            if time.monotonic() > end: raise AssertionError('missing gate: ' + name)
            time.sleep(.002)

    def finish(self, eof=False):
        started = time.monotonic()
        if eof: self.proc.stdin.close()
        else: self.send('bye')
        self.proc.wait(timeout=2)
        elapsed = time.monotonic() - started
        if self.reader: self.reader.join(1)
        return elapsed

    def close(self):
        if self.proc.poll() is None: self.proc.kill(); self.proc.wait(2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream and not stream.closed: stream.close()


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name); self.path = self.directory / 'fixture.sqlite3'
        with Store(self.path) as store:
            days = seed_experiment(store); self.row = frozen_experiment(store, days, end=days[30])
        self.children = []

    def child(self, **kwargs):
        child = Child(kwargs.pop('path', self.path), self.directory / f'gates-{len(self.children)}', **kwargs)
        self.children.append(child); self.addCleanup(child.close); return child

    def test_preflight_version_checks(self):
        self.assertIsNotNone(preflight((3, 11), (2, 6)))
        self.assertIsNotNone(preflight((3, 12), (2, 5)))
        self.assertIsNone(preflight((3, 12), (2, 6)))

    def test_real_process_seq_lifecycle_invalid_messages_and_no_business_before_up(self):
        child = self.child()
        with self.assertRaises(queue.Empty): child.queue.get(timeout=.05)
        for bad in (True, -1, 1.5, 2**53): child.send('hello', seq=bad)
        self.assertEqual(child.ready(), {'t': 'ready', 'seq': child.seq})
        child.send('up', seq=child.seq+1); child.send('msg', body={'op': 'report'})
        with self.assertRaises(queue.Empty): child.queue.get(timeout=.05)
        child.up(); child.send('hello')
        child.send('msg', body={'op': KEY})
        error = child.get(lambda p: p['t'] == 'msg' and p['body'].get('op') == 'error')
        self.assertEqual(error['body']['code'], 'unknown_operation')
        self.assertLess(child.finish(), 1)
        packets = [json.loads(line) for line in child.lines]
        self.assertTrue(all(p['seq'] == child.seq for p in packets))
        self.assertEqual(packets[-1], {'t': 'done', 'seq': child.seq})
        self.assertNotIn(KEY.encode(), b''.join(child.lines) + child.proc.stderr.read())

    def test_preflight_fail_only_after_hello_and_flush_then_exit(self):
        child = self.child(mode='preflight_fail')
        with self.assertRaises(queue.Empty): child.queue.get(timeout=.05)
        packet = child.ready(); self.assertEqual(packet['t'], 'fail'); self.assertEqual(packet['seq'], child.seq)
        child.proc.wait(2); child.reader.join(1); self.assertEqual(len(child.lines), 1)

    def test_database_open_failure_only_after_hello_exits_instead_of_hanging(self):
        child = self.child(path=self.directory)
        with self.assertRaises(queue.Empty): child.queue.get(timeout=.05)
        self.assertEqual(child.ready(), {'t': 'fail', 'seq': child.seq, 'reason': 'database_open_failed'})
        child.proc.wait(2); self.assertEqual(child.proc.returncode, 1)

    def test_eof_shutdown_done_is_last_and_under_one_second(self):
        child = self.child(); child.ready(); child.up()
        self.assertLess(child.finish(eof=True), 1)
        self.assertEqual(json.loads(child.lines[-1])['t'], 'done')

    def test_external_sqlite_write_lock_does_not_block_bye_or_done(self):
        child = self.child(); child.ready(); child.up()
        locker = sqlite3.connect(self.path); self.addCleanup(locker.close)
        locker.execute('BEGIN IMMEDIATE')
        child.send('msg', body={'op': 'run', 'method': 'always_flat', 'split': 'dev'})
        child.get(lambda p: p['t'] == 'msg' and p['body'].get('replay', {}).get('status') == 'running')
        self.assertLess(child.finish(), 1); locker.rollback()
        self.assertEqual(json.loads(child.lines[-1])['t'], 'done')

    def test_network_gate_bye_discards_late_results_and_preserves_commits_for_restart(self):
        child = self.child(mode='one_then_block'); child.ready(); child.up()
        child.send('msg', body={'op': 'run', 'method': 'jev', 'split': 'dev'})
        child.mark('client-entered')
        child.get(lambda p: p['t'] == 'msg' and p['body'].get('replay', {}).get('n_ok', 0) >= 1)
        self.assertLess(child.finish(), 1)
        with Store(self.path, readonly=True) as store:
            committed = store.db.execute('SELECT count(*) FROM predictions').fetchone()[0]
            self.assertGreater(committed, 0); self.assertLess(committed, 32)
            self.assertEqual(store.db.execute('SELECT count(*) FROM outcomes').fetchone()[0], committed)
        restart = self.child(mode='all_ok'); restart.ready(); restart.up()
        restart.send('msg', body={'op': 'run', 'method': 'jev', 'split': 'dev'})
        restart.get(lambda p: p['t'] == 'msg' and p['body'].get('replay', {}).get('status') == 'complete', timeout=10)
        self.assertLess(restart.finish(), 1)
        self.assertEqual(len((restart.gates / 'calls').read_text().splitlines()), 32-committed)
        with Store(self.path, readonly=True) as store:
            self.assertEqual(store.db.execute('SELECT count(*) FROM predictions').fetchone()[0], 32)
        self.assertEqual(json.loads(child.lines[-1])['t'], 'done')

    def test_writer_gate_terminal_drops_queued_business_and_rejects_late_producer(self):
        child = self.child(mode='writer_gate'); child.ready(); child.send('up'); child.mark('writer-entered')
        child.send('msg', body={'op': 'unknown'}); child.send('bye'); child.mark('outbox-closed')
        (child.gates / 'release-writer').touch(); child.proc.wait(1); child.reader.join(1)
        self.assertTrue((child.gates / 'late-rejected').exists())
        self.assertEqual(json.loads(child.lines[-1]), {'t': 'done', 'seq': child.seq})
        self.assertTrue(all(json.loads(line)['t'] in ('ready', 'msg', 'done') for line in child.lines))
        self.assertEqual(sum(json.loads(line)['t'] == 'msg' for line in child.lines), 1)

    def test_stdout_not_drained_still_exits_under_one_second(self):
        child = self.child(mode='flood', drain=False); child.send('hello')
        self.assertTrue(select.select([child.proc.stdout], [], [], 3)[0])
        self.assertEqual(json.loads(child.proc.stdout.readline())['t'], 'ready')
        child.send('up'); child.mark('up-handled')
        self.assertLess(child.finish(), 1)

    def test_real_day_packet_above_900_kib_is_replaced_by_safe_error(self):
        huge = {'t': 'msg', 'seq': 42, 'body': {'op': 'day', 'data': '大' * 200000}}
        self.assertGreater(len((json.dumps(huge) + '\n').encode()), MAX_PACKET)
        with self.assertRaises(ValueError): Outbox.encode(huge)
        child = self.child(mode='oversized_day'); child.ready(); child.up()
        child.send('msg', body={'op': 'day', 'date': self.row['dev_start'], 'request_id': 42})
        packet = child.get(lambda p: p['t'] == 'msg' and p['body'].get('op') == 'error')
        self.assertEqual(packet['body'], {'op': 'error', 'code': 'packet_too_large', 'request_id': 42})
        child.finish(); self.assertTrue(all(len(line) <= MAX_PACKET for line in child.lines))

    def test_outbox_concurrent_producers_never_interleave_json_or_write_after_done(self):
        stream = io.BytesIO(); out = Outbox(stream)
        def producer(i):
            for _ in range(50): out.put({'t': 'msg', 'seq': 18, 'body': {'worker': i}})
        threads = [threading.Thread(target=producer, args=(i,)) for i in range(6)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        out.close_with({'t': 'done', 'seq': 18}); self.assertTrue(out.terminal_sent.wait(1))
        self.assertFalse(out.put({'t': 'msg', 'seq': 18, 'body': {'late': True}}))
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(rows[-1], {'t': 'done', 'seq': 18}); self.assertTrue(all(row['seq'] == 18 for row in rows))

    def test_outbox_coalesces_progress_so_terminal_status_is_not_dropped(self):
        entered, release = threading.Event(), threading.Event()
        def gate(packet): entered.set(); release.wait(3)
        stream = io.BytesIO(); out = Outbox(stream, before_write=gate)
        out.put({'t': 'ready', 'seq': 17}); self.assertTrue(entered.wait(1))
        for n in range(100):
            self.assertTrue(out.put({'t': 'msg', 'seq': 17, 'body': {'op': 'status', 'progress': n}}))
        self.assertEqual(out.queue.qsize(), 1)
        release.set()
        end = time.monotonic()+1
        while out.queue.qsize() and time.monotonic() < end: time.sleep(.002)
        out.close_with({'t': 'done', 'seq': 17}); out.writer.join(1)
        packets = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([p['body']['progress'] for p in packets if p['t']=='msg'], [99])
