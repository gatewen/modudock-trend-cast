from datetime import date

from back.data import month_bounds
from back.experiment import ActivityGate, create_experiment
from tests.replay_fixture import seed_replay

TODAY = date(2024, 8, 1)


def seed_experiment(store):
    days = seed_replay(store, count=85)
    with store.transaction():
        for month in sorted({day[:7] for day in days}):
            first, last = month_bounds(month)
            store._log('2330', first.isoformat(), last.isoformat(), 'bars', 200, 1000, True, 'written')
    return days


def frozen_experiment(store, days, gate=None, **kwargs):
    return create_experiment(store, gate or ActivityGate(),
        start=kwargs.pop('start', days[25]), end=kwargs.pop('end', days[55]), today=TODAY, **kwargs)
