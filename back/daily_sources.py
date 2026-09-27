"""Free, symbol-specific FinMind data plus the existing raw Fugle/TWSE clients.

No token/account is required for these three datasets. Never request an entire
market's chip data: that is a different, paid API use case.
"""
from dataclasses import dataclass
from decimal import Decimal

from .data import DataError, day_value, symbol_value
from .http_client import ClientError, JsonClient

INSTITUTIONAL = 'TaiwanStockInstitutionalInvestorsBuySell'
MARGIN = 'TaiwanStockMarginPurchaseShortSale'
CALENDAR = 'TaiwanStockTradingDate'
NAMES = frozenset(('Foreign_Investor', 'Foreign_Dealer_Self', 'Investment_Trust',
                   'Dealer', 'Dealer_self', 'Dealer_Hedging'))


@dataclass(frozen=True)
class FinmindBatch:
    dataset: str
    symbol: str
    start: str
    end: str
    rows: tuple


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise DataError('invalid_chip_integer')
    try:
        n = Decimal(str(value))
        if not n.is_finite() or not 0 <= n <= 10**16 or n != n.to_integral_value():
            raise ValueError
        return int(n)
    except (ValueError, ArithmeticError):
        raise DataError('invalid_chip_integer') from None


def parse_finmind(payload, dataset, symbol, start, end):
    symbol_value(symbol); day_value(start); day_value(end)
    if dataset not in (INSTITUTIONAL, MARGIN, CALENDAR) or start > end:
        raise DataError('invalid_finmind_query')
    if not isinstance(payload, dict) or payload.get('status') != 200 or payload.get('msg') != 'success':
        raise DataError('unconfirmed_finmind_response')
    if not isinstance(payload.get('data'), list):
        raise DataError('invalid_finmind_schema')
    rows, seen = [], set()
    for raw in payload['data']:
        if not isinstance(raw, dict):
            raise DataError('invalid_finmind_row')
        day = raw.get('date'); day_value(day)
        if not start <= day <= end:
            raise DataError('finmind_outside_range')
        row = {'day': day}
        if dataset != CALENDAR:
            if raw.get('stock_id') != symbol:
                raise DataError('finmind_wrong_symbol')
            row['symbol'] = symbol
        identity = day
        if dataset == INSTITUTIONAL:
            name = raw.get('name')
            if name not in NAMES:
                raise DataError('unknown_institution_category')
            row.update(name=name, buy=integer(raw.get('buy')), sell=integer(raw.get('sell')))
            identity = (day, name)
        elif dataset == MARGIN:
            row.update(margin_balance=integer(raw.get('MarginPurchaseTodayBalance')),
                       short_balance=integer(raw.get('ShortSaleTodayBalance')))
        if identity in seen:
            raise DataError('duplicate_finmind_row')
        seen.add(identity); rows.append(row)
    return FinmindBatch(dataset, symbol, start, end,
                        tuple(sorted(rows, key=lambda r:(r['day'],r.get('name','')))))


class FinmindClient:
    def __init__(self, **transport):
        self.http = JsonClient('api.finmindtrade.com', **transport)

    def fetch(self, dataset, start, end, symbol='2330'):
        symbol_value(symbol); day_value(start); day_value(end)
        if dataset not in (INSTITUTIONAL, MARGIN, CALENDAR) or start > end:
            raise DataError('invalid_finmind_query')
        params = dict(dataset=dataset, start_date=start, end_date=end)
        if dataset != CALENDAR:
            params['data_id'] = symbol
        response = self.http.get('/api/v4/data', params)
        # A HTTP 200 with status 402 is quota exhaustion, never empty coverage.
        if isinstance(response.payload, dict) and response.payload.get('status') == 402:
            raise ClientError('finmind_quota_exhausted', 402)
        return parse_finmind(response.payload, dataset, symbol, start, end)
