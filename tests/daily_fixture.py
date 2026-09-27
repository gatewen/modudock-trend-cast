from datetime import date,timedelta
from decimal import Decimal

from back.daily_sources import parse_finmind,CALENDAR,INSTITUTIONAL,MARGIN
from back.fugle import CandleBatch
from back.data import candles
from back.twse import CorpBatch


def seed_daily(store,count=130,start=date(2010,1,4)):
    days=[];d=start
    while len(days)<count:
        if d.weekday()<5: days.append(d.isoformat())
        d+=timedelta(days=1)
    calendar=parse_finmind({'status':200,'msg':'success','data':[{'date':d} for d in days]},CALENDAR,'2330',days[0],days[-1])
    store.write_finmind(calendar)
    rows=[]
    for i,day in enumerate(days):
        close=Decimal('100')+Decimal((i*7)%19-9)
        rows.append(dict(date=day,open=str(close),high=str(close+1),low=str(close-1),close=str(close),volume=str(1000+i)))
    store.write_bars(CandleBatch('2330',days[0],days[-1],'D',200,candles(rows,'2330',days[0],days[-1],'D')))
    store.write_corp(CorpBatch('2330',days[0],days[-1],[]))
    raw=[dict(date=d,stock_id='2330',name=name,buy=100+i,sell=i) for i,d in enumerate(days)
         for name in ('Foreign_Investor','Investment_Trust')]
    store.write_finmind(parse_finmind({'status':200,'msg':'success','data':raw},INSTITUTIONAL,'2330',days[0],days[-1]))
    raw=[dict(date=d,stock_id='2330',MarginPurchaseTodayBalance=10000+i,ShortSaleTodayBalance=0) for i,d in enumerate(days)]
    store.write_finmind(parse_finmind({'status':200,'msg':'success','data':raw},MARGIN,'2330',days[0],days[-1]))
    return days
