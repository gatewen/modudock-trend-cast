#!/usr/local/bin/python3
"""One-shot source survey only. No research DB access, predictions or scores."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, quote
from urllib.request import Request
from urllib.error import HTTPError
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from back.http_client import secure_opener, transport_error_code


def targets():
    fm = 'https://api.finmindtrade.com/api/v4/data?'
    for name, dataset, symbol in (
        ('finmind-taiex', 'TaiwanStockPrice', 'TAIEX'),
        ('finmind-tsm', 'USStockPrice', 'TSM'),
        ('finmind-sox', 'USStockPrice', '^SOX'),
        ('finmind-usdtwd', 'TaiwanExchangeRate', 'USD')):
        yield name, fm + urlencode(dict(dataset=dataset, data_id=symbol,
                       start_date='2010-01-01', end_date='2010-01-08')), {}
        yield name+'-start', fm + urlencode(dict(dataset=dataset, data_id=symbol,
                       start_date='1900-01-01', end_date='2010-01-08')), {}
        yield name+'-recent', fm + urlencode(dict(dataset=dataset, data_id=symbol,
                       start_date='2026-09-21', end_date='2026-09-25')), {}
    for name, symbol in (('taiex','^TWII'), ('tsm','TSM'), ('sox','^SOX'), ('usdtwd','TWD=X')):
        yield 'yahoo-'+name, 'https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol, safe='')+'?'+urlencode(
            dict(period1=1262304000, period2=1263168000, interval='1d', events='div,splits')), {}
    yield 'twse-taiex', 'https://www.twse.com.tw/exchangeReport/MI_5MINS_HIST?'+urlencode(
        dict(response='json', date='19990101')), {}
    yield 'twse-taiex-rwd', 'https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?'+urlencode(
        dict(response='json', date='19990101')), {}
    key = os.environ.get('FUGLE_API_KEY')
    yield 'fugle-taiex', 'https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/IX0001?'+urlencode(
        dict(timeframe='D', adjusted='false', sort='asc', **{'from':'2015-01-01','to':'2015-01-09'},
             fields='open,high,low,close,volume,turnover')), {'X-API-KEY':key} if key else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only', nargs='+')
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for name, url, headers in targets():
        if args.only and name not in args.only: continue
        result = dict(name=name, url=url, fetched_at=datetime.now(timezone.utc).isoformat(), attempts=0)
        if headers is None:
            result['error'] = 'missing_FUGLE_API_KEY'
        else:
            try:
                result['attempts'] = 1
                request = Request(url, headers={'User-Agent':'Mozilla/5.0 trend-cast source survey',
                                               'Accept':'application/json', 'Accept-Encoding':'identity', **headers})
                with secure_opener().open(request, timeout=20) as response:
                    result['http_status'] = response.status
                    raw = response.read(2*1024*1024+1)
                    if len(raw)>2*1024*1024: raise ValueError('oversize')
                    result['sha256'] = hashlib.sha256(raw).hexdigest()
                    payload = json.loads(raw)
                    # Save only public, successful market data. Never save headers or error bodies.
                    if any(value and value.encode() in raw for value in headers.values()):
                        raise ValueError('unexpected_secret_in_response')
                    result['payload'] = payload
            except HTTPError as exc:
                result['http_status'] = exc.code
                exc.close()
            except (ValueError, UnicodeError):
                result['error'] = 'invalid_or_oversize_json'
            except Exception as exc:
                result['error'] = transport_error_code(exc)
        (args.output/(name+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        summary = {k:v for k,v in result.items() if k!='payload'}
        if 'payload' in result:
            p = result['payload']
            summary['api_status'] = p.get('status', p.get('stat'))
            summary['rows'] = len(p.get('data', []))
            if 'chart' in p:
                items = p['chart'].get('result') or []
                summary['rows'] = len(items[0].get('timestamp', [])) if items else 0
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        results.append(summary)
        if name.startswith('finmind-'): time.sleep(12)
        else: time.sleep(3)
    (args.output/'summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
    return 0


if __name__=='__main__': raise SystemExit(main())
