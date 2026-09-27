"""SPEC 14.3: local, development-only methods with time-gated learning.

This module never requests a model prediction. Existing baselines are unchanged.
Thirty visible closes yield 29 simple returns; terciles are recomputed from
matured eligible development observations at each point, including regrouping.
"""
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
import math
from statistics import pstdev

from .baselines import Baselines, Prediction, TIE_ORDER
from .data import DataError, local_time
from .replay import LABELS, LabelObservation, exact
from .score import percentile

METHODS = ('clock_prior', 'vol_prior', 'jev_calibrated')
MIN_SAMPLES = 30


@dataclass(frozen=True)
class LearningPoint:
    observation: LabelObservation = field(repr=False)
    volatility: float | None = field(default=None, repr=False)
    jev_choice: str | None = field(default=None, repr=False)


def volatility(point):
    bars = [bar for bar in point.visible_bars if local_time(bar['bar_end']) <= point.t]
    if len(bars) < 30:
        return None
    closes = [exact(bar['close']) for bar in bars[-30:]]
    returns = [float(right / left - 1) for left, right in zip(closes, closes[1:])]
    return pstdev(returns)


def smoothed(method, counts):
    denominator = sum(counts.values()) + 3
    probabilities = {label: (counts[label] + 1) / denominator for label in LABELS}
    return Prediction(method, max(TIE_ORDER, key=lambda label: probabilities[label]), probabilities)


class WalkForward:
    def __init__(self, replay, records=()):
        self.replay = replay
        self.records = tuple(records)

    def matured(self, point):
        plan = self.replay.plan
        seen, ready = set(), []
        for record in self.records:
            obs = record.observation
            t = local_time(obs.t)
            if plan.split_of(t.date().isoformat()) != 'dev':
                continue
            if t + timedelta(minutes=30) > point.t:
                continue
            if not obs.predictable or not obs.scorable:
                continue
            self.replay._point_time(t)
            if t in seen:
                raise DataError('duplicate_observation')
            seen.add(t)
            if (obs.experiment_id != plan.experiment_id or obs.symbol != plan.symbol
                    or obs.threshold_permille != plan.threshold_permille):
                raise DataError('observation_plan_mismatch')
            if obs.label not in LABELS:
                raise DataError('invalid_observation_label')
            if record.jev_choice is not None and record.jev_choice not in LABELS:
                raise DataError('invalid_training_choice')
            if record.volatility is not None and (not math.isfinite(record.volatility) or record.volatility < 0):
                raise DataError('invalid_training_volatility')
            ready.append(record)
        return tuple(ready)

    def predict(self, method, point, *, jev_choice=None):
        if method not in METHODS:
            raise DataError('unknown_method')
        plan = self.replay.plan
        if (point.experiment_id != plan.experiment_id or point.symbol != plan.symbol
                or point.threshold_permille != plan.threshold_permille):
            raise DataError('point_plan_mismatch')
        self.replay._point_time(point.t)
        if plan.split_of(point.t.date().isoformat()) != 'dev':
            raise DataError('evolution_dev_only')
        if not point.predictable:
            return Prediction(method, None, None, 'not_predictable')
        ready = self.matured(point)

        def fallback():
            base = Baselines(self.replay, (r.observation for r in ready)).predict('majority', point)
            return Prediction(method, base.answer, base.probabilities, 'majority_fallback')

        if method == 'clock_prior':
            selected = [r for r in ready if r.observation.t.time() == point.t.time()]
        elif method == 'vol_prior':
            current = volatility(point)
            samples = [r for r in ready if r.volatility is not None]
            if current is None or not samples:
                return fallback()
            values = [r.volatility for r in samples]
            cuts = (percentile(values, 1 / 3), percentile(values, 2 / 3))
            group = bisect_left(cuts, current)
            selected = [r for r in samples if bisect_left(cuts, r.volatility) == group]
        else:
            if jev_choice not in LABELS:
                return Prediction(method, None, None, 'missing_current_jev')
            selected = [r for r in ready if r.jev_choice == jev_choice]
        if len(selected) < MIN_SAMPLES:
            return fallback()
        return smoothed(method, Counter(r.observation.label for r in selected))
