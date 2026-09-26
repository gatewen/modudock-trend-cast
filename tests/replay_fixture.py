"""Synthetic exchange calendar and hand-computable prices, independent of APIs."""
from datetime import date, datetime, timedelta

from back.data import TAIPEI
from back.replay import ReplayPlan
from back.twse import CorpBatch


def seed_replay(store, count=40):
    days = []
    day = date(2024, 1, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars, daily = [], []
    for i, day in enumerate(days):
        daily.append(('2330', day, '1000', '1001', '999', '1000', str((i + 1) * 1000)))
        opening = datetime.fromisoformat(day + 'T09:00:00').replace(tzinfo=TAIPEI)
        for minute in (*range(265), 270):
            stamp = opening + timedelta(minutes=minute)
            end = stamp if minute == 270 else stamp + timedelta(minutes=1)
            bars.append(('2330', day, stamp.isoformat(), end.isoformat(),
                         '1000', '1001', '999', '1000', str(i + 1)))
    with store.transaction():
        store.db.executemany('INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?)', bars)
        store.db.executemany('INSERT INTO daily VALUES (?,?,?,?,?,?,?)', daily)
    store.write_corp(CorpBatch('2330', days[0], days[-1], []))
    return tuple(days)


def plan_for(days):
    return ReplayPlan(days, days[25], days[29], days[30], days[-1], warmup_start=days[0])


def at(day, clock='09:30'):
    return datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=TAIPEI)
