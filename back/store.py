"""SQLite storage. Instantiate/write on the future db-writer thread only.

Prices and volumes are exact decimal TEXT. Readers use separate connections.
Experiment config_json may specify symbol and warmup_start; otherwise the first
25 stored trading days before dev_start are protected conservatively.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from .data import (TAIPEI, DataError, candles, day_value, decimal_value,
                   local_time, month_bounds, symbol_value)
from .twse import SOURCE

DEFAULT_DB = Path(__file__).resolve().parents[1] / 'data' / 'trendcast.sqlite3'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS bars (
 symbol TEXT NOT NULL, day TEXT NOT NULL, ts_raw TEXT NOT NULL, bar_end TEXT NOT NULL,
 open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
 volume TEXT NOT NULL, PRIMARY KEY(symbol, bar_end));
CREATE INDEX IF NOT EXISTS bars_day ON bars(symbol, day, bar_end);
CREATE TABLE IF NOT EXISTS daily (
 symbol TEXT NOT NULL, day TEXT NOT NULL, open TEXT NOT NULL, high TEXT NOT NULL,
 low TEXT NOT NULL, close TEXT NOT NULL, volume TEXT NOT NULL, PRIMARY KEY(symbol, day));
CREATE TABLE IF NOT EXISTS corp_events (
 symbol TEXT NOT NULL, day TEXT NOT NULL, prev_close TEXT NOT NULL,
 ref_price TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(symbol, day));
CREATE TABLE IF NOT EXISTS corp_coverage (
 symbol TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL,
 source TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(symbol, start_day, end_day));
CREATE TABLE IF NOT EXISTS fetch_log (
 symbol TEXT NOT NULL, month TEXT NOT NULL, fetched_at TEXT NOT NULL,
 http_status INTEGER, n_bars INTEGER NOT NULL, final INTEGER NOT NULL CHECK(final IN (0,1)),
 note TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'bars', range_start TEXT, range_end TEXT);
CREATE INDEX IF NOT EXISTS fetch_month ON fetch_log(symbol, month, kind);
CREATE TABLE IF NOT EXISTS experiments (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, data_digest TEXT NOT NULL,
 dev_start TEXT NOT NULL, dev_end TEXT NOT NULL, hold_start TEXT NOT NULL, hold_end TEXT NOT NULL,
 threshold_permille INTEGER NOT NULL, feature_version TEXT NOT NULL, prompt_version TEXT NOT NULL,
 model TEXT NOT NULL, config_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, experiment_id INTEGER NOT NULL REFERENCES experiments(id),
 method TEXT NOT NULL, split TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
 status TEXT NOT NULL, n_ok INTEGER NOT NULL DEFAULT 0, n_fail INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS run_scopes (
 run_id INTEGER PRIMARY KEY REFERENCES runs(id), full_split INTEGER NOT NULL CHECK(full_split IN (0,1)));
CREATE TABLE IF NOT EXISTS predictions (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), method TEXT NOT NULL,
 t TEXT NOT NULL, run_id INTEGER NOT NULL REFERENCES runs(id), input_hash TEXT NOT NULL,
 input_json TEXT NOT NULL, answer TEXT NOT NULL, probs_json TEXT NOT NULL,
 model_reported TEXT, created_at TEXT NOT NULL, PRIMARY KEY(experiment_id, method, t));
CREATE TABLE IF NOT EXISTS outcomes (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), t TEXT NOT NULL,
 close_t TEXT NOT NULL, close_end TEXT NOT NULL, label TEXT NOT NULL,
 PRIMARY KEY(experiment_id, t));
CREATE TABLE IF NOT EXISTS reveals (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), revealed_at TEXT NOT NULL,
 first_day TEXT NOT NULL, last_day TEXT NOT NULL, what TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reveal_context (
 experiment_id INTEGER PRIMARY KEY REFERENCES experiments(id),
 prior_overlap_days INTEGER NOT NULL CHECK(prior_overlap_days>=0));
CREATE TABLE IF NOT EXISTS prior_exposures (
 symbol TEXT NOT NULL, first_day TEXT NOT NULL, last_day TEXT NOT NULL,
 source TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(symbol, first_day, last_day, source));
CREATE TABLE IF NOT EXISTS forward_days (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), day TEXT NOT NULL,
 warmup_start TEXT NOT NULL, data_digest TEXT NOT NULL, settings_json TEXT NOT NULL,
 admitted_at TEXT NOT NULL, PRIMARY KEY(experiment_id,day));
CREATE TABLE IF NOT EXISTS forward_exclusions (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), day TEXT NOT NULL,
 reason TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(experiment_id,day));
CREATE TABLE IF NOT EXISTS forward_attempts (
 run_id INTEGER NOT NULL REFERENCES runs(id), day TEXT NOT NULL, failed INTEGER NOT NULL,
 PRIMARY KEY(run_id,day));
CREATE TABLE IF NOT EXISTS evolution_models (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), method TEXT NOT NULL,
 source_digest TEXT NOT NULL, parameters_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(experiment_id,method));
CREATE TABLE IF NOT EXISTS move_samples (
 experiment_id INTEGER PRIMARY KEY REFERENCES experiments(id), stage TEXT NOT NULL,
 seed INTEGER NOT NULL, size INTEGER NOT NULL, reference_digest TEXT NOT NULL,
 population_digest TEXT NOT NULL, times_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS move_answers (
 experiment_id INTEGER NOT NULL REFERENCES experiments(id), t TEXT NOT NULL,
 choice TEXT NOT NULL, probs_json TEXT NOT NULL, up_count INTEGER NOT NULL,
 down_count INTEGER NOT NULL, model_reported TEXT, latency_seconds REAL NOT NULL,
 PRIMARY KEY(experiment_id,t));
'''
COLUMNS = {
    'bars': ('symbol', 'day', 'ts_raw', 'bar_end', 'open', 'high', 'low', 'close', 'volume'),
    'daily': ('symbol', 'day', 'open', 'high', 'low', 'close', 'volume'),
    'corp_events': ('symbol', 'day', 'prev_close', 'ref_price', 'source'),
}


def now_string():
    return datetime.now(TAIPEI).isoformat(timespec='seconds')


class Store:
    def __init__(self, path=DEFAULT_DB, *, readonly=False):
        self.path = Path(path) if str(path) != ':memory:' else None
        if readonly:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro',
                                      uri=True, timeout=1)
        else:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=1000')
        if not readonly:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript(SCHEMA)
            # Keep legacy DBs readable; existing reveal rows are holdout records.
            if 'segment' not in {r[1] for r in self.db.execute('PRAGMA table_info(reveals)')}:
                self.db.execute("ALTER TABLE reveals ADD COLUMN segment TEXT NOT NULL DEFAULT 'holdout'")

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _log(self, symbol, start, end, kind, status, count, final, note):
        self.db.execute('''INSERT INTO fetch_log
            (symbol,month,fetched_at,http_status,n_bars,final,note,kind,range_start,range_end)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (symbol, start[:7], now_string(), status, count, int(final), note, kind, start, end))

    def record_failure(self, symbol, start, end, kind, status=None):
        symbol_value(symbol)
        day_value(start)
        day_value(end)
        if kind not in ('bars', 'daily', 'corp'):
            raise DataError('invalid_kind')
        with self.transaction():
            self._log(symbol, start, end, kind, status, 0, False, 'fetch_failed')

    def frozen_ranges(self, symbol):
        ranges = []
        for row in self.db.execute('SELECT * FROM experiments'):
            config = json.loads(row['config_json'])
            if config.get('symbol', '2330') != symbol:
                continue
            warmup = config.get('warmup_start')
            if warmup is None:
                prior = self.db.execute('''SELECT day FROM daily WHERE symbol=? AND day<?
                    ORDER BY day DESC LIMIT 25''', (symbol, row['dev_start'])).fetchall()
                warmup = prior[-1]['day'] if prior else row['dev_start']
            day_value(warmup)
            if warmup > row['dev_start']:
                raise DataError('invalid_frozen_range')
            ranges.append((warmup, row['hold_end']))
        ranges.extend((r[0], r[1]) for r in self.db.execute('''SELECT f.warmup_start,f.day
            FROM forward_days f JOIN experiments e ON e.id=f.experiment_id
            WHERE json_extract(e.config_json,'$.symbol')=?''', (symbol,)))
        return ranges

    @staticmethod
    def _frozen(day, ranges):
        return any(start <= day <= end for start, end in ranges)

    def _replace(self, table, rows, symbol, start, end, ranges):
        columns = COLUMNS[table]
        key = 'bar_end' if table == 'bars' else 'day'
        old = {r[key]: dict(r) for r in self.db.execute(
            f'SELECT * FROM {table} WHERE symbol=? AND day BETWEEN ? AND ?', (symbol, start, end))}
        new = {r[key]: r for r in rows}
        delete, insert, differences = [], [], 0
        for identity in old.keys() | new.keys():
            before, after = old.get(identity), new.get(identity)
            day = (after or before)['day']
            if self._frozen(day, ranges):
                if before != after:
                    differences += 1
                continue
            if before is not None:
                delete.append((symbol, identity))
            if after is not None:
                insert.append(tuple(after[c] for c in columns))
        self.db.executemany(f'DELETE FROM {table} WHERE symbol=? AND {key}=?', delete)
        self.db.executemany(
            f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', insert)
        return differences, len(insert)

    def write_candles(self, batch, *, today=None):
        """One atomic month per timeframe. Empty responses never erase prior data."""
        first, last = day_value(batch.start), day_value(batch.end)
        if first > last or first.strftime('%Y-%m') != last.strftime('%Y-%m'):
            raise DataError('expected_one_month')
        if batch.status not in (200, 404) or (batch.status == 404 and batch.rows):
            raise DataError('invalid_batch_status')
        # Revalidate the entire batch even when the caller isn't an HTTP client.
        source = []
        for row in batch.rows:
            if not isinstance(row, dict) or row.get('symbol') != batch.symbol:
                raise DataError('invalid_batch_symbol')
            source.append(dict(row, date=row.get('ts_raw') if batch.timeframe == '1' else row.get('day')))
        rows = candles(source, batch.symbol, batch.start, batch.end, batch.timeframe)
        if rows != batch.rows:
            raise DataError('noncanonical_batch')
        table = 'bars' if batch.timeframe == '1' else 'daily'
        today = today or datetime.now(TAIPEI).date()
        with self.transaction():
            if not rows:
                self._log(batch.symbol, batch.start, batch.end, table, batch.status, 0, False, 'empty')
                return {'written': 0, 'frozen_differences': 0, 'final': False}
            differences, written = self._replace(table, rows, batch.symbol, batch.start, batch.end,
                                                 self.frozen_ranges(batch.symbol))
            final = table == 'bars' and self._can_finalize(batch, today)
            note = 'frozen_difference' if differences else 'written'
            self._log(batch.symbol, batch.start, batch.end, table, batch.status,
                      len(rows), final, note)
            return {'written': written, 'frozen_differences': differences, 'final': final}

    def _can_finalize(self, batch, today):
        first, last = month_bounds(batch.start[:7])
        if batch.start != first.isoformat() or batch.end != last.isoformat() or last >= today.replace(day=1):
            return False
        recent = self.db.execute('''SELECT day FROM daily WHERE symbol=? AND day<=?
            ORDER BY day DESC LIMIT 7''', (batch.symbol, today.isoformat())).fetchall()
        if len(recent) < 7 or last.isoformat() >= recent[-1]['day']:
            return False
        calendar = self.db.execute('''SELECT * FROM fetch_log WHERE symbol=? AND month=?
            AND kind='daily' ORDER BY rowid DESC LIMIT 1''', (batch.symbol, batch.start[:7])).fetchone()
        if (not calendar or calendar['note'] not in ('written', 'frozen_difference')
                or calendar['range_start'] != batch.start or calendar['range_end'] != batch.end):
            return False
        expected = {r[0] for r in self.db.execute('SELECT day FROM daily WHERE symbol=? AND day BETWEEN ? AND ?',
                                                (batch.symbol, batch.start, batch.end))}
        actual = {r[0] for r in self.db.execute('''SELECT DISTINCT day FROM bars
            WHERE symbol=? AND day BETWEEN ? AND ?''', (batch.symbol, batch.start, batch.end))}
        if not expected or actual != expected:
            return False
        if any(self.corp_state(batch.symbol, day)['state'] == 'unknown' for day in expected):
            return False
        return True

    def should_fetch(self, symbol, month, *, today=None):
        symbol_value(symbol)
        first, last = month_bounds(month)
        today = today or datetime.now(TAIPEI).date()
        if first >= today.replace(day=1):
            return True
        recent = self.db.execute('''SELECT day FROM daily WHERE symbol=? AND day<=?
            ORDER BY day DESC LIMIT 7''', (symbol, today.isoformat())).fetchall()
        if len(recent) < 7 or last.isoformat() >= recent[-1]['day']:
            return True
        row = self.db.execute('''SELECT final FROM fetch_log WHERE symbol=? AND month=? AND kind='bars'
            ORDER BY rowid DESC LIMIT 1''', (symbol, month)).fetchone()
        return not row or not row['final']

    def write_corp(self, batch):
        symbol_value(batch.symbol)
        first, last = day_value(batch.start), day_value(batch.end)
        if first > last or batch.source != SOURCE:
            raise DataError('invalid_corp_batch')
        events, seen = [], set()
        for row in batch.events:
            if (not isinstance(row, dict) or row.get('symbol') != batch.symbol
                    or row.get('source') != SOURCE):
                raise DataError('invalid_corp_event')
            day = day_value(row.get('day')).isoformat()
            if not batch.start <= day <= batch.end or day in seen:
                raise DataError('invalid_corp_event')
            seen.add(day)
            events.append(dict(symbol=batch.symbol, day=day, prev_close=decimal_value(row.get('prev_close')),
                               ref_price=decimal_value(row.get('ref_price')), source=SOURCE))
        with self.transaction():
            ranges = self.frozen_ranges(batch.symbol)
            differences, _ = self._replace('corp_events', events, batch.symbol, batch.start, batch.end, ranges)
            # Daily coverage intervals prevent a later sync extending an interval
            # across an experiment's frozen boundary or changing its fetched_at.
            coverage = []
            day = first
            while day <= last:
                key = day.isoformat()
                if not self._frozen(key, ranges):
                    coverage.append((batch.symbol, key, key, SOURCE, now_string()))
                day += timedelta(days=1)
            self.db.executemany('INSERT OR REPLACE INTO corp_coverage VALUES (?,?,?,?,?)', coverage)
            self._log(batch.symbol, batch.start, batch.end, 'corp', 200, len(events), False,
                      'frozen_difference' if differences else 'written')
            return differences

    def corp_state(self, symbol, day):
        symbol_value(symbol)
        day_value(day)
        covered = self.db.execute('''SELECT 1 FROM corp_coverage WHERE symbol=?
            AND start_day<=? AND end_day>=? LIMIT 1''', (symbol, day, day)).fetchone()
        if not covered:
            return {'state': 'unknown', 'event': None}
        event = self.db.execute('SELECT * FROM corp_events WHERE symbol=? AND day=?', (symbol, day)).fetchone()
        return {'state': 'event' if event else 'none', 'event': dict(event) if event else None}

    def available_bars(self, day, t, symbol='2330'):
        symbol_value(symbol)
        day_value(day)
        cutoff = local_time(t)
        if cutoff.date().isoformat() != day:
            raise DataError('cutoff_day_mismatch')
        return [dict(row) for row in self.db.execute('''SELECT * FROM bars
            WHERE symbol=? AND day=? AND bar_end <= ? ORDER BY bar_end''',
            (symbol, day, cutoff.isoformat(timespec='microseconds')))]
