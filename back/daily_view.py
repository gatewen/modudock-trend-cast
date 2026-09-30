"""Read-only daily UI projections; development and revealed holdout stay separate.

The shell never fits models, invokes transport, writes outcomes or reveals here.
Development outcomes must also END inside development before scoring/coloring.
"""
from collections import Counter
from datetime import date
from fractions import Fraction
import hashlib
import json
import math

from .daily_config import METHODS, INDICATORS, LABELS, CLAIMS, STATES
from .daily_ensemble import COMPONENTS, average_current, current_predictions
from .daily_hold_run import report as holdout_report
from .daily_experiment import load_experiment
from .daily_indicators import frames
from .daily_models import prediction
from .daily_prompt import bias_states, STATE_TEXT, BUCKET_TEXT, chip_percentages, sample_dates
from .daily_replay import DEV_START, DEV_END, DailyReplay, horizon
from .daily_score import paired_blocks
from .daily_reveal import holdout_status, revealed, table_exists
from .market_config import METHODS as MARKET_METHODS
from .market_run import inputs as market_inputs, saved_candidates
from .data import DataError, day_value
from .experiment import canonical
from .score import ScoredPoint, metrics

OPS = ('daily_status', 'daily_chart', 'daily_indicators', 'daily_report', 'daily_holdout')
EXTRA_METHODS = ('ens_avg', *MARKET_METHODS)
HOLD_MESSAGE = '未使用（沒有入圍者，保留給未來）'
NAMES = dict(ma_cross='5／20 日均線', ma_trend='60 日均線', rsi14='RSI', kd='KD',
    macd='MACD', bollinger='布林通道', bias20='20 日乖離', vol_price='價量',
    foreign_net='外資近 3 日', trust_net='投信近 3 日', margin_chg='融資 5 日變化')


def bounded_day(value, first=DEV_START, last=DEV_END):
    day_value(value)
    if not first <= value <= last <= DEV_END:
        raise DataError('daily_view_dev_only')
    return value


def state_text(name, state):
    if name == 'bias20': return BUCKET_TEXT.get(state, '本期無此資料')
    if name == 'kd':
        return dict(high='高檔', low='低檔', middle='中間區間', bull_cross='低檔黃金交叉',
                    bear_cross='高檔死亡交叉').get(state, '本期無此資料')
    return STATE_TEXT.get(name, {}).get(state, '本期無此資料')


class DailyViews:
    """One reader-thread cache; keys include all bounded source/result content."""
    def __init__(self):
        self.prefix_key = None
        self.history = ()
        self.prices = []
        self.report_cache = {}

    def config(self, store):
        row = load_experiment(store)
        c = row['config']
        bounded_day(c['dev_start']); bounded_day(c['dev_end'])
        return row

    def prefix(self, store, row):
        c = row['config']
        if self.prefix_key == row['dev_digest']: return
        self.history = frames(store, start=c['start'], end=c['dev_end'], symbol=c['symbol'])
        engine = DailyReplay(store, start=c['start'], symbol=c['symbol'])
        bars = {r['day']:r for r in store.db.execute('''SELECT * FROM d_bars
            WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''', (c['symbol'], c['start'], c['dev_end']))}
        days = engine.calendar(c['start'], c['dev_end'])
        prices = []; previous = None; adjusted = 100.; base = None
        for day in days:
            bar = bars.get(day)
            if bar is None: raise DataError('daily_view_incomplete')
            if previous is not None: adjusted *= float(Fraction(bar['close']) / engine.reference(day, previous))
            previous = bar
            if c['dev_start'] <= day <= c['dev_end']:
                if base is None: base = adjusted
                prices.append(dict(day=day, close=adjusted / base * 100))
        self.prices = prices
        self.prefix_key = row['dev_digest']

    def handle(self, store, body):
        op = body.get('op')
        if op not in OPS: raise DataError('unknown_operation')
        allowed = {'op', 'H', 'request_id', 'experiment_id'}
        if op == 'daily_chart': allowed.add('range')
        if op == 'daily_indicators': allowed.add('date')
        if set(body) - allowed or type(body.get('experiment_id', 4)) is not int or body.get('experiment_id', 4) != 4:
            raise DataError('daily_view_dev_only')
        H = horizon(body.get('H', 7))
        if op == 'daily_holdout':
            # Do not even read result tables until the experiment's reveal is valid.
            if not revealed(store):
                return dict(status='ok', experiment_id=4, H=H, split='holdout', state='locked')
            result = holdout_report(store)  # Rechecks the gate and sealed result digests.
            return dict(status='ok', experiment_id=4, H=H, split='holdout', state='used',
                first_day=result['first_day'], last_day=result['last_day'],
                primary_comparisons=result['primary_comparisons'],
                descriptive=[dict(method=m, **r) for m,r in result['descriptive'][str(H)].items()],
                multiplicity=result['multiplicity']['note'])
        row = self.config(store); c = row['config']
        if op == 'daily_indicators': bounded_day(body.get('date'), c['dev_start'], c['dev_end'])
        self.prefix(store, row)
        common = dict(status='ok', experiment_id=4, H=H, split='dev')
        if op == 'daily_status':
            result = dict(dev_start=c['dev_start'], dev_end=c['dev_end'],
                days=[f.day for f in self.history if f.predictable and c['dev_start'] <= f.day <= c['dev_end']],
                threshold=float(Fraction(**c['thresholds'][str(H)])), holdout=holdout_status(store))
        elif op == 'daily_indicators': result = self.indicators(store, c, H, body['date'])
        elif op == 'daily_chart': result = self.chart(store, c, H, body.get('range', '6m'))
        else: result = self.report(store, row, H)
        return dict(common, **result)

    def indicators(self, store, c, H, day):
        point = next((f for f in self.history if f.day == day and f.predictable), None)
        if point is None: raise DataError('daily_view_invalid_date')
        states = dict(point.states, bias20=bias_states(self.history, point)[H])
        chips = chip_percentages(store, c, point)
        result = []
        for name in INDICATORS:
            state = states.get(name, 'missing'); label = state_text(name, state)
            values = point.values
            if name == 'kd': detail = f'K {values["kd_k"]:.1f} · D {values["kd_d"]:.1f}'
            elif name == 'rsi14': detail = f'{values[name]:.1f}'
            elif name in ('ma_cross', 'ma_trend', 'bias20'): detail = f'{values[name]*100:+.2f}%'
            elif name == 'macd': detail = f'柱狀體 {values["macd_hist"]*100:+.2f}%'
            elif name == 'bollinger': detail = f'距均線 {values[name]:+.2f} 個標準差'
            elif name == 'vol_price': detail = f'成交量 {values["vol_price_ratio"]:.2f} 倍均量' if values['vol_price_ratio'] is not None else '本期無此資料'
            else: detail = chips[name]
            result.append(dict(name=name, title=NAMES[name], state=state, description=label, detail=detail))
        return dict(day=day, indicators=result, chip_note='籌碼只用前一交易日以前；百分比相對前 20 日均量。')

    def saved(self, store, c, H):
        first, last = c['dev_start'], c['dev_end']
        # Both the origin AND maturity date are bounded before labels are read.
        outcomes = {r['day']:dict(r) for r in store.db.execute('''SELECT * FROM d_outcomes
            WHERE experiment_id=4 AND H=? AND day BETWEEN ? AND ? AND end_day BETWEEN day AND ?''', (H, first, last, last))}
        rows = [dict(r) for r in store.db.execute('''SELECT * FROM d_predictions
            WHERE experiment_id=4 AND H=? AND day BETWEEN ? AND ? ORDER BY day,method''', (H, first, last))]
        points = {m:{} for m in (*METHODS, 'jev_ind')}; states = {}
        for r in rows:
            method, day = r['method'], r['day']
            if method not in points: raise DataError('daily_unknown_method')
            if day not in outcomes: continue
            if outcomes[day]['label'] not in LABELS: raise DataError('daily_invalid_outcome')
            probs = json.loads(r['probabilities_json'])
            if method == 'jev_ind':
                # API choice may pick any tied maximum; do not impose baseline tie order.
                if (set(probs) != set(LABELS) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in probs.values())
                    or abs(sum(probs.values())-1)>.01 or r['choice'] not in LABELS or probs[r['choice']] != max(probs.values())):
                    raise DataError('daily_invalid_choice')
            elif prediction(method, probs).answer != r['choice']: raise DataError('daily_invalid_choice')
            points[method][day] = ScoredPoint(day, outcomes[day]['label'], r['choice'], probs)
            states[day,method] = r['state']
        return outcomes, points, states, rows

    def chart(self, store, c, H, span):
        if span not in ('6m', '1y', 'all'): raise DataError('daily_view_invalid_range')
        prices = self.prices
        if prices and span != 'all':
            last = date.fromisoformat(prices[-1]['day']); months = 6 if span == '6m' else 12
            month = last.year * 12 + last.month - 1 - months
            # These ranges end at development end, never at the machine's today.
            import calendar
            year, mon = divmod(month, 12); mon += 1
            start = date(year, mon, min(last.day, calendar.monthrange(year, mon)[1])).isoformat()
            prices = [r for r in prices if r['day'] >= start]
        visible = {r['day'] for r in prices}
        _, saved, _, _ = self.saved(store, c, H)
        samples = set(sample_dates(store, c)['days'])
        markers = []
        for method in ('jev_ind', 'ind_logit'):
            for day, p in sorted(saved[method].items()):
                if day in visible and day in samples:
                    markers.append(dict(day=day, method=method, choice=p.choice, correct=bool(p.correct)))
        return dict(range=span, prices=prices, points=markers,
            note='調整收盤指數：開發段首日＝100；依參考價向前連乘。圓點 J＝jev_ind，方點 L＝ind_logit；只標共同抽樣日。')

    def extra_points(self, store, c, H, outcomes):
        """Project the registered extensions from saved development forecasts only."""
        extra = {m:{} for m in EXTRA_METHODS}
        counts = {m:0 for m in (*EXTRA_METHODS, 'majority')}
        for point in self.history:
            if not point.predictable or not c['dev_start'] <= point.day <= c['dev_end']: continue
            current = current_predictions(store, 4, H, point)
            counts['majority'] += int('majority' in current)
            if set(current) != set(COMPONENTS): continue
            counts['ens_avg'] += 1
            if point.day not in outcomes: continue
            p = average_current(current)
            extra['ens_avg'][point.day] = ScoredPoint(point.day, outcomes[point.day]['label'], p.answer, p.probabilities)
        if table_exists(store, 'market_experiments') and store.db.execute(
                'SELECT 1 FROM market_experiments WHERE experiment_id=4').fetchone():
            _, original, points, alignment = market_inputs(store)
            saved, _ = saved_candidates(store, H, points, original, alignment)
            for method, forecasts in saved.items():
                counts[method] = len(forecasts)
                extra[method] = {d:ScoredPoint(d, outcomes[d]['label'], p.answer, p.probabilities)
                    for d,p in forecasts.items() if d in outcomes}
        return extra, counts

    def report(self, store, row, H):
        c = row['config']; outcomes, points, states, rows = self.saved(store, c, H)
        extra, forecast_counts = self.extra_points(store, c, H, outcomes)
        extra_hash = hashlib.sha256(canonical([forecast_counts,{m:[(d,p.choice,p.probabilities) for d,p in sorted(ps.items())]
            for m,ps in extra.items()}]).encode()).hexdigest()
        key = (H, row['dev_digest'], hashlib.sha256(canonical([outcomes, rows]).encode()).hexdigest(), extra_hash)
        if key in self.report_cache: return dict(self.report_cache[key],holdout=holdout_status(store))
        calendar = tuple(r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day', (c['dev_start'], c['dev_end'])))
        common = set(outcomes).intersection(*(set(points[m]) for m in METHODS))
        points.update(extra)
        sample = set(sample_dates(store, c)['days'])
        methods = []; shortlist = []
        for method in (*METHODS, 'jev_ind', *EXTRA_METHODS):
            keys = (set(points[method]) & set(points['majority']) if method in EXTRA_METHODS else
                common if method != 'jev_ind' else sample & set(points[method]) & set(points['majority']))
            candidate = {d:points[method][d] for d in sorted(keys)}
            reference = {d:points['majority'][d] for d in sorted(keys)}
            comp = paired_blocks(candidate, reference, calendar)
            expected = sum(f.predictable and c['dev_start'] <= f.day <= c['dev_end'] and f.index + H < len(self.history) for f in self.history) if method != 'jev_ind' else len(sample)
            complete = bool(expected) and len(keys) == expected
            if method in EXTRA_METHODS:
                complete = complete and forecast_counts[method] == forecast_counts['majority'] == sum(
                    f.predictable and c['dev_start'] <= f.day <= c['dev_end'] for f in self.history)
            selected = bool(complete and method != 'majority' and comp['ci95'] and comp['ci95'][1] < 0)
            if selected: shortlist.append(method)
            m = metrics(candidate.values())
            methods.append(dict(method=method, n=m['n'], accuracy=m['accuracy'], brier=m['brier'],
                difference=comp['difference'], ci95=comp['ci95'], shortlisted=selected,
                verdict=('入圍（邊緣）：值得再驗證' if method == 'ens_avg' and H == 3 else '入圍：值得再驗證')
                    if selected else '未入圍' if complete else '結果不完整',
                sample='每 5 日抽樣' if method == 'jev_ind' else '完整開發段'))
        claims = []
        for name in INDICATORS:
            for state in sorted(set((*STATES[name], 'missing')) | {states.get((d,'ind_'+name), 'missing') for d in common}):
                counts = Counter(outcomes[d]['label'] for d in common if states.get((d,'ind_'+name), 'missing') == state)
                n = sum(counts.values())
                claims.append(dict(indicator=name, title=NAMES[name], state=state, description=state_text(name,state),
                    claim=CLAIMS[name].get(state, '無預設方向'), n=n,
                    frequencies={k:counts[k]/n if n else None for k in LABELS}, small_sample=n<30))
        result = dict(methods=methods, claims=claims, shortlist=shortlist,
            holdout=holdout_status(store),
            comparison_note='差＝方法 − majority；負值較好。各列使用相同日期配對，jev_ind 每 5 日抽樣，其餘用開發段可評分日。20 交易日區塊 bootstrap 2,000 次，種子 20260927。ens_avg 3 日為邊緣入圍：換種子後區間上界略跨 0。',
            multiplicity='原 11 指標每個天期預期約 0.275 個因運氣較好；ens_avg 共 3 個比較，預期約 0.075 個；大環境 6×3＝18 個比較，預期約 0.45 個。以上為名目估算、未作多重比較校正；開發段入圍只代表值得再驗證。')
        self.report_cache = {k:v for k,v in self.report_cache.items() if k[0] != H}
        self.report_cache[key] = result
        return result
