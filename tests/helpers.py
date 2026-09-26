from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import urllib.parse
import urllib.request

from back.data import candles
from back.fugle import CandleBatch
from back.http_client import NoRedirect, RateLimiter
from back.twse import CorpBatch, SOURCE

SYMBOL = '2330'
KEY = 'fixture-secret-DO-NOT-LOG'


def candle(stamp='2024-09-12T09:00:00.000+08:00', close='100.3'):
    return dict(date=stamp, open='100', high='101', low='99', close=close, volume='10')


def batch(rows=None, start='2024-09-01', end='2024-09-30', timeframe='1', status=200):
    rows = [candle()] if rows is None else rows
    return CandleBatch(SYMBOL, start, end, timeframe, status, candles(rows, SYMBOL, start, end, timeframe))


def corp(start='2024-09-01', end='2024-09-30', event_day='2024-09-12'):
    events = [] if event_day is None else [dict(symbol=SYMBOL, day=event_day,
        prev_close='901', ref_price='896.99', source=SOURCE)]
    return CorpBatch(SYMBOL, start, end, events)


def experiment(store, *, warmup='2024-09-01', dev_start='2024-09-12', dev_end='2024-09-12',
               hold_start='2024-09-13', hold_end='2024-09-30'):
    with store.transaction():
        cursor = store.db.execute('''INSERT INTO experiments
            (created_at,data_digest,dev_start,dev_end,hold_start,hold_end,threshold_permille,
             feature_version,prompt_version,model,config_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            ('2026-09-26', 'test-digest', dev_start, dev_end, hold_start, hold_end,
             3, 'f1', 'p1', 'jev-1.13.0', json.dumps({'symbol': SYMBOL, 'warmup_start': warmup})))
    return cursor.lastrowid


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def transport(self, opener):
        return dict(opener=opener, limiter=RateLimiter(clock=self.clock, sleep=self.sleep),
                    clock=self.clock, sleep=self.sleep)


class FakeServer:
    def __init__(self):
        self.responses = []
        self.requests = []
        self.bodies = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                owner.requests.append((self.path, dict(self.headers)))
                status, headers, body = owner.responses.pop(0) if owner.responses else (500, {}, b'')
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                owner.bodies.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.do_GET()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.local = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.original_requests = []

    def open(self, req, timeout):
        self.original_requests.append((req.full_url, req.header_items(), timeout))
        parsed = urllib.parse.urlsplit(req.full_url)
        url = f'http://127.0.0.1:{self.server.server_port}' + parsed.path + '?' + parsed.query
        return self.local.open(urllib.request.Request(url, data=req.data, method=req.get_method(),
                               headers=dict(req.header_items())), timeout=timeout)

    def queue(self, payload=None, *, status=200, headers=None, raw=None):
        self.responses.append((status, headers or {}, raw if raw is not None else json.dumps(payload).encode()))

    def reset(self):
        self.responses.clear()
        self.requests.clear()
        self.bodies.clear()
        self.original_requests.clear()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
