"""Sequential annual raw candles / corporate actions, resumable range commits."""
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from .data import DataError, day_value
from .daily_sources import FinmindClient, CALENDAR, INSTITUTIONAL, MARGIN
from .fugle import FugleClient
from .http_client import RateLimiter
from .twse import TwseClient

START = '2010-01-04'
END = '2026-09-24'
TWSE_LIMITER = RateLimiter(3.0)


@contextmanager
def no_jev():
    """Block Jev at both the prediction API and actual transport entry points."""
    from .jevcast import JevClient
    def forbidden(*args, **kwargs):
        raise DataError('jev_forbidden_in_daily_data_step')
    with patch.object(JevClient,'predict',forbidden), patch.object(JevClient,'_request',forbidden):
        yield


def years(start, end):
    first,last=day_value(start),day_value(end)
    if first>last: raise DataError('invalid_daily_range')
    return tuple((max(first,date(y,1,1)).isoformat(), min(last,date(y,12,31)).isoformat())
                 for y in range(first.year,last.year+1))


def synchronize(store, *, start=START, end=END, symbol='2330', fugle=None, twse=None,
                finmind=None, refresh=False, progress=None):
    fugle=fugle or FugleClient()
    twse=twse or TwseClient(limiter=TWSE_LIMITER)
    finmind=finmind or FinmindClient()
    result={'written':0,'skipped':0,'jev_http_calls':0}
    with no_jev():
        for dataset,kind in ((CALENDAR,'calendar'),(INSTITUTIONAL,'institutional'),(MARGIN,'margin')):
            if not refresh and store.fetched(kind,symbol,start,end):
                result['skipped']+=1; continue
            batch=finmind.fetch(dataset,start,end,symbol)
            store.write_finmind(batch); result['written']+=1
            if progress: progress({'kind':kind,'status':'written','rows':len(batch.rows)})
        for first,last in years(start,end):
            for kind in ('bars','corp'):
                if not refresh and store.fetched(kind,symbol,first,last):
                    result['skipped']+=1; continue
                if kind=='bars':
                    batch=fugle.candles(symbol,first,last,'D'); store.write_bars(batch)
                else:
                    batch=twse.events(symbol,first,last); store.write_corp(batch)
                result['written']+=1
                if progress: progress({'kind':kind,'year':first[:4],'status':'written'})
    return result
