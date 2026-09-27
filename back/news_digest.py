"""Untrusted broadcast -> bounded latest value and causal daily news snapshots.

No network client lives here. p5 is a disabled input preview, not a prediction.
Unknown-calendar candidates are promoted only after the trading calendar confirms
the date. This permits capture before that day's daily sources become available.
"""
from datetime import datetime, time
import json
import re
import unicodedata

from .data import TAIPEI, day_value

TOPIC = 'news.market_digest'
MAX_BYTES = 8 * 1024
JEV_NEWS_ENABLED = False
DIRECTIONS = {'bullish', 'mixed', 'unrelated', 'bearish'}
FIELDS = {'schema', 'at', 'window_hours', 'signal_counts', 'top_themes', 'source_count'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS news_digests(
 id INTEGER PRIMARY KEY CHECK(id=1),at TEXT NOT NULL,received_at TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_snapshots(
 day TEXT PRIMARY KEY,at TEXT NOT NULL,received_at TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_pending_snapshots(
 day TEXT PRIMARY KEY,at TEXT NOT NULL,received_at TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_forward_inputs(
 day TEXT PRIMARY KEY,method TEXT NOT NULL CHECK(method='jev_news'),prompt_version TEXT NOT NULL CHECK(prompt_version='p5'),
 status TEXT NOT NULL CHECK(status IN ('no_news','disabled')),message TEXT NOT NULL,state_json TEXT,recorded_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS news_input_immutable BEFORE UPDATE ON news_forward_inputs
BEGIN SELECT RAISE(ABORT,'news_input_immutable'); END;
'''


def now():return datetime.now(TAIPEI)
def stamp(value):return value.astimezone(TAIPEI).isoformat(timespec='microseconds')
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})',value):raise ValueError('timestamp')
    return datetime.fromisoformat(value).astimezone(TAIPEI)


def integer(value, low=0, high=1000000):return type(value) is int and low<=value<=high


def validate(body, received_at, *, wire_size=None):
    """Return an owned canonical value; invalid data silently disappears."""
    try:
        encoded=canonical(body)
        if len(encoded.encode('utf-8'))>MAX_BYTES or (wire_size is not None and wire_size>MAX_BYTES):return None
        if type(body) is not dict or set(body)!=FIELDS or type(body['schema']) is not int or body['schema']!=1:return None
        at=timestamp(body['at'])
        if received_at.tzinfo is None or at>received_at:return None
        if not integer(body['window_hours'],1,168) or not integer(body['source_count']):return None
        counts=body['signal_counts'];themes=body['top_themes']
        if type(counts) is not dict or set(counts)!=DIRECTIONS or not all(integer(v) for v in counts.values()):return None
        if type(themes) is not list or len(themes)>10:return None
        names=set()
        for theme in themes:
            if type(theme) is not dict or set(theme)!={'name','events','direction'}:return None
            name=theme['name']
            if not isinstance(name,str) or not 1<=len(name)<=80 or name.strip()!=name or name in names:return None
            if any(unicodedata.category(c).startswith('C') for c in name) or re.search(r'://|www\.|\]\(|[\w-]+\.[a-zA-Z]{2,}(?:\b|/)',name):return None
            if not integer(theme['events'],1) or theme['direction'] not in DIRECTIONS:return None
            names.add(name)
        return dict(at=stamp(at),received_at=stamp(received_at),payload_json=encoded)
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):return None


def exists(store,table):return store.db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone() is not None
def ensure_schema(store):store.db.executescript(SCHEMA)


def _promote(store):
    if not exists(store,'d_calendar'):return
    store.db.execute('''INSERT OR IGNORE INTO news_snapshots SELECT p.* FROM news_pending_snapshots p
        JOIN d_calendar c ON p.day=c.day''')
    # Calendar-unconfirmed dates never become usable simply because they are weekdays.
    store.db.execute('DELETE FROM news_pending_snapshots WHERE day IN (SELECT day FROM news_snapshots)')


def receive(store, value):
    ensure_schema(store)
    with store.transaction():
        old=store.db.execute('SELECT at FROM news_digests WHERE id=1').fetchone()
        if old and old['at']>=value['at']:return False
        args=tuple(value[k] for k in ('at','received_at','payload_json'))
        store.db.execute('INSERT OR REPLACE INTO news_digests VALUES (1,?,?,?)',args)
        at,received=timestamp(value['at']),timestamp(value['received_at'])
        day=received.date().isoformat();cutoff=datetime.combine(received.date(),time(13,30),TAIPEI)
        if at.date()==received.date() and at<=cutoff and received<=cutoff:
            known=exists(store,'d_calendar') and store.db.execute('SELECT 1 FROM d_calendar WHERE day=?',(day,)).fetchone()
            table='news_snapshots' if known else 'news_pending_snapshots'
            # Only a same-day pre-cutoff receipt can replace the still-open snapshot.
            store.db.execute(f'''INSERT INTO {table} VALUES (?,?,?,?) ON CONFLICT(day) DO UPDATE SET
                at=excluded.at,received_at=excluded.received_at,payload_json=excluded.payload_json
                WHERE excluded.at>{table}.at''',(day,*args))
        _promote(store)
    return True


def snapshot(store,day):
    day_value(day)
    if not exists(store,'news_snapshots') or not exists(store,'d_calendar'):return None
    if not store.db.execute('SELECT 1 FROM d_calendar WHERE day=?',(day,)).fetchone():return None
    row=store.db.execute('SELECT * FROM news_snapshots WHERE day=?',(day,)).fetchone()
    if row is None:return None
    cutoff=datetime.combine(day_value(day),time(13,30),TAIPEI)
    at,received=timestamp(row['at']),timestamp(row['received_at'])
    if at.date().isoformat()!=day or received.date().isoformat()!=day or at>cutoff or received>cutoff:return None
    return json.loads(row['payload_json'])


def record_input(store,day,recorded_at):
    """Separate immutable p5 preview. Never alter frozen p6 state or call jev."""
    ensure_schema(store)
    with store.transaction():
        _promote(store)
        if store.db.execute('SELECT 1 FROM news_forward_inputs WHERE day=?',(day,)).fetchone():return
        # Existing daily-forward admission has already checked freeze, calendar and data.
        row=store.db.execute('SELECT body_json FROM d_forward_days WHERE day=?',(day,)).fetchone()
        if row is None:return
        digest=snapshot(store,day)
        state=None
        if digest is not None:
            # Timing metadata stays local. p4 daily relative values + aggregate news only.
            news={k:v for k,v in digest.items() if k!='at'}
            state=canonical(dict(daily=json.loads(row['body_json'])['state']['daily'],news=news))
        status='no_news' if state is None else 'disabled'
        store.db.execute('INSERT INTO news_forward_inputs VALUES (?,\'jev_news\',\'p5\',?,?,?,?)',
                         (day,status,'無新聞資料' if state is None else '未啟用',state,stamp(recorded_at)))


def status_view(store):
    row=store.db.execute('SELECT received_at FROM news_digests WHERE id=1').fetchone() if exists(store,'news_digests') else None
    return dict(status='ok',received_at=row['received_at'] if row else None,jev_news_enabled=JEV_NEWS_ENABLED)


def decode_packet(line):
    """Reject duplicate JSON keys/nonfinite numbers; measure original body bytes.

    Whitespace and escaped characters count toward the 8 KiB wire-body limit.
    """
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('duplicate key')
            result[k]=v
        return result
    def reject(_):raise ValueError('nonfinite')
    source=line.decode('utf-8') if isinstance(line,bytes) else line
    packet=json.loads(source,object_pairs_hook=unique,parse_constant=reject)
    size=None
    if isinstance(packet,dict) and packet.get('t')=='event' and 'body' in packet:
        decoder=json.JSONDecoder();pos=source.index('{')+1
        while source[pos:].lstrip() and source[pos:].lstrip()[0]!='}':
            pos+=len(source[pos:])-len(source[pos:].lstrip())
            key,end=decoder.raw_decode(source,pos);pos=source.index(':',end)+1;start=pos
            pos+=len(source[pos:])-len(source[pos:].lstrip());_,end=decoder.raw_decode(source,pos)
            if key=='body':size=len(source[start:end].encode('utf-8'));break
            pos=end+len(source[end:])-len(source[end:].lstrip())
            if source[pos]==',':pos+=1
    return packet,size
