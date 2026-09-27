"""p6 draft: date-only sampling and identity-free, three-question request bytes.

This module has no transport or persistence. It never reads outcomes, predictions
or label frequencies; bias20 cuts use only feature dates whose H has elapsed.
"""
from decimal import Decimal, localcontext, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import json
import math

from .daily_config import INDICATORS, STATES
from .daily_indicators import frames
from .daily_models import cuts, bucket
from .daily_replay import DailyReplay, HORIZONS, DEV_START, DEV_END
from .data import DataError, day_value
from .experiment import MODEL, canonical

PROMPT_VERSION='p6'
SAMPLE_STEP=5
ROUND_CALL_LIMIT=650
COMMON_INSTRUCTIONS=(
    'state 是單一股票在同一個收盤時點已知的資料；三題均以這個收盤為起點，'
    '分別預測未來 3、7、14 個交易日的調整報酬。只使用 state，不推測股票身分或歷史日期。\n\n'
    'daily：最近 60 個已完成交易日，由遠到近，最後一列是當日。每列依序為 '
    '[開盤、高點、低點、收盤相對該日參考價的千分比，成交量相對該日之前 20 個交易日均量的倍數]。'
    '前四欄的公式為 (價格/參考價−1)×1000；參考價在除權息日採公告參考價，其他日採前收。'
    '最後一欄的均量不含該列當日；null 表示歷史不足或均量為零，不是 0。\n\n'
    'indicators：當日指標的文字描述，價格指標使用截至當日、依參考價向前連乘的調整價格。'
    'ma_cross 是 SMA5/SMA20−1 的百分比，近 3 日交叉取最新一次；'
    'ma_trend 是收盤/SMA60−1 的百分比；'
    'rsi14 用 Wilder 平滑，首 14 日簡單平均，<30 超賣、>70 超買；'
    'kd 是 KD(9,3,3)，K、D 初值 50，最高等於最低時 RSV=50，低檔上穿與高檔下穿依當日 K<20／K>80 判定；'
    'macd 是 EMA(12,26,9)，EMA 以首值初始化，DIF、signal、hist=DIF−signal 均以當日調整收盤的百分比表示；'
    'bollinger 是 20 日、2 倍母體標準差，z=(收盤−均線)/標準差；'
    'bias20 是收盤/SMA20−1 的百分比，另外列出 3／7／14 日各自的低／中／高三分位狀態：'
    '切點只用該天期已到期的開發樣本（歷史起點+該天期≤當下），線性插值，等於切點歸較低組，無樣本時標無資料；'
    'vol_price 是當日調整報酬與當日量/含當日 20 日均量，倍數≥1 為量增，平盤獨立標示。\n\n'
    'foreign_net、trust_net 是截至前一交易日的最近 3 日淨買賣合計；'
    'margin_chg 是前一交易日融資餘額與再往前 5 個交易日餘額之差。'
    '三個籌碼欄位均先換成股，再除以截至前一交易日的20日平均成交量（股），'
    '以百分比呈現到小數2位；正負號表示淨方向。'
    '當日盤後才發布的籌碼不在 state 中；欄位也可能寫「本期無此資料」，不視為0或持平。\n\n'
    '目標 R_H 為從當日收盤到第 H 個交易日收盤的調整報酬：'
    '連乘未來各日的 收盤/該日參考價，再減 1。up、flat、down 的界線以該題 criteria 為準。'
    '指標的多空、超買超賣是訊號描述，不是已知的未來答案。'
    '請給三類機率，總和為 1，choice 選機率最高者；訊號不足時保留不確定性。'
)
STATE_TEXT={
    'ma_cross': {'bull':'近3日最新為黃金交叉','bear':'近3日最新為死亡交叉','neutral':'近3日無交叉'},
    'ma_trend': {'bull':'收盤在60日均線上','bear':'收盤在60日均線下','neutral':'收盤等於60日均線'},
    'rsi14': {'oversold':'超賣','overbought':'超買','middle':'中間區間'},
    'kd': {'bull_cross':'低檔黃金交叉（當日K<20且上穿D）','bear_cross':'高檔死亡交叉（當日K>80且下穿D）',
           'low':'K低於20','middle':'K介於20與80（含邊界）','high':'K高於80'},
    'macd': {'bull_flip':'柱狀體當日由負翻正','bear_flip':'柱狀體當日由正翻負','positive':'柱狀體為正','negative':'柱狀體為負','zero':'柱狀體為零'},
    'bollinger': {'above':'收盤在上軌外','below':'收盤在下軌外','inside':'收盤在兩軌間（含邊界）'},
    'vol_price': {'up_high':'價漲量增','up_low':'價漲量縮','down_high':'價跌量增','down_low':'價跌量縮','flat_high':'平盤量增','flat_low':'平盤量縮'},
    'foreign_net': {'positive':'買超','negative':'賣超','zero':'持平'},
    'trust_net': {'positive':'買超','negative':'賣超','zero':'持平'},
    'margin_chg': {'positive':'融資餘額增加','negative':'融資餘額減少','zero':'融資餘額不變'},
}
BUCKET_TEXT={'low':'低三分位','middle':'中三分位','high':'高三分位','missing':'無已揭曉樣本／無資料'}


def sample_dates(store, config):
    """Query dates only; no eligibility, labels or prices influence the grid."""
    if not DEV_START<=config['dev_start']<=config['dev_end']<=DEV_END:
        raise DataError('p6_dev_only')
    days=tuple(r[0] for r in store.db.execute('''SELECT day FROM d_calendar
        WHERE day BETWEEN ? AND ? ORDER BY day''',(config['start'],config['dev_end'])))
    origin=next((i for i,d in enumerate(days) if d>=config['dev_start'] and i>=60),None)
    if origin is None: raise DataError('p6_missing_warmup')
    if days[origin]!=config['first_prediction']: raise DataError('p6_sample_origin_mismatch')
    grid=tuple(range(origin,len(days),SAMPLE_STEP))
    selected=tuple(i for i in grid if i+max(HORIZONS)<len(days))
    return dict(days=tuple(days[i] for i in selected),grid_count=len(grid),
                tail_excluded=len(grid)-len(selected),step=SAMPLE_STEP,
                rule='common_H14_endpoint_in_dev')


def threshold_text(threshold):
    if not isinstance(threshold,Fraction) or not 0<threshold<1: raise DataError('p6_invalid_threshold')
    denominator=(threshold*100).denominator
    for prime in (2,5):
        while denominator%prime==0: denominator//=prime
    if denominator!=1: raise DataError('p6_nonterminating_threshold')
    with localcontext() as context:
        context.prec=50
        value=Decimal(threshold.numerator)*100/Decimal(threshold.denominator)
    return format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else format(value,'f')


def questions(config):
    result={}
    for H in HORIZONS:
        k=threshold_text(Fraction(**config['thresholds'][str(H)]))
        result[f'direction_{H}d']=dict(type='choice',
            instructions=COMMON_INSTRUCTIONS+f'\n\n本題 H={H}：從當日收盤到第 {H} 個交易日收盤，R_H 最可能落在哪一類？',
            criteria=dict(up=f'R_H ≥ +{k}%（上漲 {k}% 或更多）',
                          flat=f'−{k}% < R_H < +{k}%（漲跌幅皆未達 {k}%）',
                          down=f'R_H ≤ −{k}%（下跌 {k}% 或更多）'))
    return result


def number(value, *, percent=False):
    if value is None: return '無資料'
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise DataError('p6_invalid_indicator_value')
    value=value*100 if percent else value
    text=f'{value:.6f}'.rstrip('0').rstrip('.')
    if text=='-0':text='0'
    return text+('%' if percent else '')


def bias_states(history, point):
    result={}
    for H in HORIZONS:
        visible=[r.values.get('bias20') for r in history
            if r.predictable and DEV_START<=r.day<=point.day and r.index+H<=point.index]
        result[H]=bucket(point.values.get('bias20'),cuts(visible))
    return result


def chip_percentages(store, config, point):
    """Fugle D volume and FinMind institutions: shares; FinMind margin: lots.

    FinMind Chip docs: margin balances are lots; one lot = 1000 shares.
    https://finmind.github.io/tutor/TaiwanMarket/Chip/
    """
    prior=tuple(r[0] for r in store.db.execute('''SELECT day FROM d_calendar
        WHERE day>=? AND day<? ORDER BY day DESC LIMIT 20''',(config['start'],point.day)))
    volumes=[store.db.execute('SELECT volume FROM d_bars WHERE symbol=? AND day=?',
                             (config['symbol'],day)).fetchone() for day in prior]
    average=(sum((Fraction(r[0]) for r in volumes),Fraction())/20
             if len(volumes)==20 and all(r is not None for r in volumes) else None)
    result={}
    for name,multiplier in (('foreign_net',1),('trust_net',1),('margin_chg',1000)):
        value=point.values.get(name)
        if value is None or average is None or average<=0:
            result[name]='本期無此資料';continue
        fraction=Fraction(value)*multiplier/average*100
        with localcontext() as context:
            context.prec=50
            rounded=(Decimal(fraction.numerator)/Decimal(fraction.denominator)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        if rounded==0:rounded=abs(rounded)
        result[name]=format(rounded,'.2f')+'%'
    return result


def describe_indicators(history, point, chip_text):
    v=point.values
    def state(name):
        value=point.states.get(name,'missing')
        if value=='missing':return '無資料'
        if value not in STATES[name]: raise DataError('p6_invalid_indicator_state')
        return STATE_TEXT[name][value]
    groups=bias_states(history,point)
    result={
        'ma_cross':f'5/20均線差={number(v.get("ma_cross"),percent=True)}；{state("ma_cross")}',
        'ma_trend':f'收盤相對60日均線={number(v.get("ma_trend"),percent=True)}；{state("ma_trend")}',
        'rsi14':f'RSI(14)={number(v.get("rsi14"))}；{state("rsi14")}',
        'kd':f'KD(9,3,3)：K={number(v.get("kd_k"))}，D={number(v.get("kd_d"))}；{state("kd")}',
        'macd':f'MACD(12,26,9)：DIF={number(v.get("macd_dif"),percent=True)}，signal={number(v.get("macd_signal"),percent=True)}，hist={number(v.get("macd_hist"),percent=True)}；{state("macd")}',
        'bollinger':f'布林20日/2倍母體標準差：z={number(v.get("bollinger"))}；{state("bollinger")}',
        'bias20':f'20日乖離率={number(v.get("bias20"),percent=True)}；'+ '；'.join(f'{H}日狀態={BUCKET_TEXT[groups[H]]}' for H in HORIZONS),
        'vol_price':f'當日調整報酬={number(v.get("vol_price_return"),percent=True)}；量/含當日20日均量={number(v.get("vol_price_ratio"))}倍；{state("vol_price")}',
        **chip_text,
    }
    assert tuple(result)==INDICATORS
    return result


def request_payload(store, config, day):
    day_value(day)
    if not DEV_START<=config['dev_start']<=day<=config['dev_end']<=DEV_END:
        raise DataError('p6_dev_only')
    history=frames(store,start=config['start'],end=day,symbol=config['symbol'])
    if not history or history[-1].day!=day or not history[-1].predictable:
        raise DataError('p6_not_predictable')
    prepared=DailyReplay(store,start=config['start'],symbol=config['symbol']).prepare(day)
    if not prepared.predictable: raise DataError('p6_not_predictable')
    if hashlib.sha256(prepared.input_json.encode()).hexdigest()!=prepared.input_hash:
        raise DataError('p6_feature_hash_mismatch')
    daily=json.loads(prepared.input_json)['daily']
    if len(daily)!=60 or any(len(row)!=5 for row in daily): raise DataError('p6_invalid_daily_shape')
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x)
           for row in daily for x in row[:4]): raise DataError('p6_invalid_daily_value')
    if any(row[4] is not None and (isinstance(row[4],bool) or not isinstance(row[4],(int,float))
           or not math.isfinite(row[4]) or row[4]<0) for row in daily): raise DataError('p6_invalid_daily_value')
    # Only allowlisted state fields cross the eventual HTTP boundary. Dates,
    # experiment metadata, absolute OHLC and feature hashes remain local.
    return dict(state=dict(daily=daily,indicators=describe_indicators(history,history[-1],chip_percentages(store,config,history[-1]))),
                model=MODEL,questions=questions(config))


def payload_bytes(store, config, day):
    return canonical(request_payload(store,config,day)).encode('utf-8')
