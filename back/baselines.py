"""Deterministic baselines using a prepared point and time-gated observations."""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .data import DataError, TAIPEI, local_time
from .replay import LABELS, label_for_prices

METHODS = ('always_flat', 'majority', 'momentum', 'reversal')
TIE_ORDER = ('flat', 'up', 'down')


@dataclass(frozen=True)
class Prediction:
    method: str
    answer: str | None = field(repr=False)
    probabilities: dict | None = field(repr=False)
    reason: str | None = None


class Baselines:
    def __init__(self, replay, observations=()):
        self.replay = replay
        # Frozen records, not a mutable global accumulator: replay/resume order
        # cannot expose labels early or add holdout labels to the dev majority.
        self.observations = tuple(observations)

    def _counts(self, point):
        plan = self.replay.plan
        if plan.split_of(point.t.date().isoformat()) == 'dev':
            cutoff = point.t
        else:
            cutoff = datetime.fromisoformat(plan.dev_end + 'T13:30:00').replace(tzinfo=TAIPEI)
        counts = Counter()
        seen = set()
        for record in self.observations:
            t = local_time(record.t)
            if plan.split_of(t.date().isoformat()) != 'dev':
                continue
            if t + timedelta(minutes=30) > cutoff:
                continue
            if not record.predictable or not record.scorable:
                continue
            self.replay._point_time(t)
            if t in seen:
                raise DataError('duplicate_observation')
            seen.add(t)
            if (record.experiment_id != plan.experiment_id or record.symbol != plan.symbol
                    or record.threshold_permille != plan.threshold_permille):
                raise DataError('observation_plan_mismatch')
            if record.label not in LABELS:
                raise DataError('invalid_observation_label')
            counts[record.label] += 1
        return counts

    def predict(self, method, point):
        if method not in METHODS:
            raise DataError('unknown_method')
        plan = self.replay.plan
        if (point.experiment_id != plan.experiment_id or point.symbol != plan.symbol
                or point.threshold_permille != plan.threshold_permille):
            raise DataError('point_plan_mismatch')
        if not point.predictable:
            return Prediction(method, None, None, 'not_predictable')
        if method == 'always_flat':
            answer = 'flat'
        elif method == 'majority':
            counts = self._counts(point)
            answer = max(TIE_ORDER, key=lambda label: counts[label])
            denominator = sum(counts.values()) + 3
            probabilities = {label: (counts[label] + 1) / denominator for label in LABELS}
            return Prediction(method, answer, probabilities)
        else:
            if point.t.time() == time(9, 30):
                start = point.visible_bars[0]['open']
            else:
                prior = self.replay.store.available_bars(point.t.date().isoformat(),
                                                        point.t - timedelta(minutes=30), plan.symbol)
                if not prior or local_time(prior[-1]['bar_end']) < point.t - timedelta(minutes=35):
                    return Prediction(method, None, None, 'missing_momentum_start')
                start = prior[-1]['close']
            answer = label_for_prices(start, point.close_t, plan.threshold_permille)
            if method == 'reversal':
                answer = {'up': 'down', 'flat': 'flat', 'down': 'up'}[answer]
        return Prediction(method, answer, {label: int(label == answer) for label in LABELS})
