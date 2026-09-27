"""Permission-first, consistent-snapshot report/day/status views for the shell.

Only explicit experiment.reveal_holdout writes a full reveal. Calling a view,
including a request for a locked day, can never unlock data or mutate the DB.
"""
from contextlib import contextmanager
import json

from .data import DataError, day_value
from .experiment import day_access, experiment_row, holdout_overlap, holdout_revealed, load_plan
from .http_client import ClientError
from .replay import Replay
from .score import (ALL_METHODS, best_development_baseline, load_split,
                    prediction_answer, split_report)


@contextmanager
def snapshot(store):
    store.db.execute('BEGIN')
    try:
        yield
        store.db.commit()
    except BaseException:
        store.db.rollback()
        raise


def current_experiment(store, experiment_id):
    if experiment_id is not None:
        return experiment_row(store, experiment_id)
    row = store.db.execute('SELECT * FROM experiments ORDER BY id DESC LIMIT 1').fetchone()
    return dict(row) if row is not None else None


def frozen_warnings(store, row, last_day):
    config = json.loads(row['config_json'])
    # A monthly fetch can straddle the split. Its log cannot locate the changed
    # day, so expose a quality warning only, never dates or difference counts.
    return [dict(r) for r in store.db.execute('''SELECT DISTINCT kind FROM fetch_log
        WHERE symbol=? AND range_start<=? AND range_end>=? AND note='frozen_difference'
        ORDER BY kind''',
        (config.get('symbol', '2330'), last_day, config.get('warmup_start', row['dev_start'])))]


def build_report(store, *, experiment_id=None, dev_only=False):
    with snapshot(store):
        row = current_experiment(store, experiment_id)
        if row is None:
            return {'status': 'experiment_required', 'message': '尚未建立實驗'}
        development = load_split(store, row, 'dev')
        baseline = best_development_baseline(development)
        result = {'status': 'ok', 'experiment_id': row['id'], 'score_version': 's2',
            'brier_definition': 'mean(sum((p_class - actual_class)^2)); unscaled multiclass',
            'dev': split_report(development, baseline),
            'frozen_warnings': frozen_warnings(store, row, row['dev_end'])}
        if not dev_only:
            if not holdout_revealed(store, row):
                # No holdout scoring/querying first and filtering later. No dates,
                # counts, runs, predictions, probabilities or exposure counts here.
                result['holdout'] = {'state': 'locked', 'message': '保留段未解鎖'}
            else:
                ready = (development.complete and len(development.eligible) > 0
                         and len(development.common) * 100 >= len(development.eligible) * 95)
                held = load_split(store, row, 'holdout')
                result['holdout'] = {'state': 'revealed', **split_report(held, baseline, development_ready=ready)}
                context = None
                if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reveal_context'").fetchone():
                    context = store.db.execute('SELECT prior_overlap_days FROM reveal_context WHERE experiment_id=?', (row['id'],)).fetchone()
                result['holdout']['prior_overlap_days'] = context[0] if context is not None else None
                result['frozen_warnings'] = frozen_warnings(store, row, row['hold_end'])
            from .forward import LOCKED, revealed_days
            forward_days = revealed_days(store, row['id'])
            result['forward'] = dict(LOCKED)
            if forward_days:
                forward = load_split(store, row, 'forward')
                result['forward'] = {'state': 'revealed', 'cumulative_days': len(forward_days),
                    **split_report(forward, baseline, development_ready=(development.complete
                        and len(development.eligible) > 0
                        and len(development.common) * 100 >= len(development.eligible) * 95))}
        return result


def status_view(store, *, experiment_id=None):
    with snapshot(store):
        row = current_experiment(store, experiment_id)
        if row is None:
            return {'status': 'experiment_required', 'message': '尚未建立實驗'}
        # Disclosure overlap is already-exposed date metadata (§7.3), not unseen
        # market/performance data. It is available in status, never locked report.
        overlap = holdout_overlap(store, row['id'])
        revealed = holdout_revealed(store, row)
        latest = {}
        for run in store.db.execute('''SELECT method,status,n_ok,n_fail FROM runs
            WHERE experiment_id=? AND split='dev' ORDER BY id''', (row['id'],)):
            if run['method'] in ALL_METHODS:
                latest[run['method']] = dict(run)
        result = {'status': 'ok', 'experiment_id': row['id'],
            'dev_start': row['dev_start'], 'dev_end': row['dev_end'], 'dev_runs': latest,
            'holdout': {'state': 'revealed' if revealed else 'locked', 'used': overlap > 0,
                        'overlap_days': overlap,
                        'message': f'保留段已使用（{overlap} 日重疊）' if overlap else '保留段尚未使用'},
            'frozen_warnings': frozen_warnings(store, row, row['dev_end'])}
        from .forward import LOCKED, revealed_days
        days = revealed_days(store, row['id'])
        result['forward'] = {'state': 'revealed', 'days': list(days), 'cumulative_days': len(days)} if days else dict(LOCKED)
        return result


def day_view(store, day, *, experiment_id=None):
    day_value(day)
    with snapshot(store):
        row = current_experiment(store, experiment_id)
        if row is None:
            return {'status': 'experiment_required', 'message': '尚未建立實驗'}
        access = day_access(store, row['id'], day)
        if access != 'allowed':
            return {'status': access, 'message': '前瞻段未揭露' if access == 'forward_locked' else '保留段未解鎖' if access == 'holdout_locked' else '日期不在實驗範圍'}
        plan = load_plan(store, row['id'])
        if day > row['hold_end']:
            from .forward import plan_for, revealed_days
            plan = plan_for(store, row['id'], days=revealed_days(store, row['id']), verify=True)
        engine = Replay(store, plan)
        stored = {}
        for prediction in store.db.execute('''SELECT * FROM predictions WHERE experiment_id=?
            AND substr(t,1,10)=? ORDER BY t,method''', (row['id'], day)):
            if prediction['method'] in ALL_METHODS:
                stored[(prediction['t'], prediction['method'])] = prediction
        points = []
        for t in engine.candidates(plan.split_of(day)):
            if t.date().isoformat() != day:
                continue
            point = engine.prepare(t)
            outcome = engine.outcome(point)
            answers = {}
            if point.predictable:
                for method in ALL_METHODS:
                    record = stored.get((t.isoformat(), method))
                    if record is None:
                        continue
                    try:
                        answer = prediction_answer(record, point, row['model'])
                    except (DataError, ClientError, ValueError, TypeError, KeyError, RecursionError):
                        continue
                    answers[method] = {'answer': answer.choice, 'probabilities': answer.probabilities,
                        'correct': answer.choice == outcome.label if outcome.scorable else None}
            points.append({'t': t.isoformat(), 'predictable': point.predictable,
                           'scorable': outcome.scorable, 'label': outcome.label,
                           'close_t': point.close_t, 'close_end': outcome.close_end,
                           'predictions': answers})
        return {'status': 'ok', 'experiment_id': row['id'], 'day': day,
                'bars': store.available_bars(day, day + 'T13:30:00+08:00', plan.symbol), 'points': points}


def markdown_report(report):
    if report.get('status') != 'ok':
        return report['message'] + '\n'
    def number(value, digits=6):
        return '—' if value is None else f'{value:.{digits}f}'
    def percent(value):
        return '—' if value is None else f'{100 * value:.4f}%'
    def percentage_points(value):
        return '—' if value is None else f'{100 * value:.4f} 百分點'
    def interval(bounds, percentages=False, points=False):
        formatter = percentage_points if points else percent if percentages else number
        return '—' if bounds is None else f'[{formatter(bounds[0])}, {formatter(bounds[1])}]'
    lines = [f'# 實驗 {report["experiment_id"]} 計分報告', '',
             'Brier＝三類機率平方誤差加總後取平均；混淆矩陣列為真實、欄為預測。', '']
    for split, title in (('dev', '開發段'), ('holdout', '保留段'), ('forward', '前瞻段')):
        if split not in report:
            continue
        data = report[split]
        if data.get('state') == 'locked':
            lines.extend(['前瞻段未揭露。' if split == 'forward' else '保留段未解鎖。', ''])
            continue
        comparison = data['comparison']
        boot = comparison['bootstrap']
        if split == 'forward':
            lines.extend([f'前瞻段累積 {data["cumulative_days"]} 個交易日、{data["predictable_and_scorable"]} 點（僅已揭露範圍）。', ''])
        if split == 'holdout':
            overlap = data.get('prior_overlap_days')
            lines.extend([f'保留段已使用（解鎖前 {overlap} 日重疊）。' if overlap else
                          '解鎖前無已曝光日期重疊。' if overlap == 0 else '解鎖前重疊日數未記錄。', ''])
        lines.extend([f'## {title}：{data["first_day"]}～{data["last_day"]}', '',
            comparison['statement'] + '。', '',
            f'候選 {data["candidates"]}；可預測 {data["predictable"]}；可評分 {data["scorable"]}；'
            f'可預測且可評分 {data["predictable_and_scorable"]}。', '',
            f'五方法共同交集 {comparison["n"]}，佔可預測且可評分點 {percent(comparison["coverage"])}。', '',
            '| 方法 | 有效樣本 | 準確率 | Wilson 95% | Brier | 覆蓋率 | 目前缺答（可評分） | 歷次失敗工作 |',
            '|---|---:|---:|---|---:|---:|---:|---:|'])
        for method, values in data['methods'].items():
            lines.append(f'| {method} | {values["n"]} | {percent(values["accuracy"])} | '
                f'{interval(values["accuracy_wilson95"], True)} | {number(values["brier"])} | '
                f'{percent(values["coverage"])} | {values["eligible_missing"]} | {values["failure_attempts"]} |')
        lines.extend(['', f'最佳基準（只由開發段共同交集 Brier 選定）：**{comparison["baseline"] or "尚無"}**。', '',
            f'主要差值（jev − 基準，Brier）：{number(comparison["brier_difference"])}；'
            f'95% 區間 {interval(boot["brier_difference_ci95"])}。', '',
            f'次要差值（jev − 基準，準確率）：{percentage_points(comparison["accuracy_difference"])}；'
            f'95% 區間 {interval(boot["accuracy_difference_ci95"], points=True)}。', '',
            f'配對 bootstrap：{boot["trading_days"]} 個交易日區塊、{boot["repetitions"]} 次、'
            f'固定種子 {boot["seed"]}；取 2.5%／97.5% 線性百分位。', ''])
        for method, values in data['methods'].items():
            lines.extend([f'### {method}：各類與混淆矩陣', '',
                '| 類別 | 真實數 | 預測數 | 精確率 | 召回率 |', '|---|---:|---:|---:|---:|'])
            for label, group in values['per_class'].items():
                lines.append(f'| {label} | {group["true_count"]} | {group["predicted_count"]} | '
                             f'{percent(group["precision"])} | {percent(group["recall"])} |')
            lines.extend(['', '| 真實／預測 | up | flat | down |', '|---|---:|---:|---:|'])
            for truth, counts in values['confusion_matrix'].items():
                lines.append(f'| {truth} | {counts["up"]} | {counts["flat"]} | {counts["down"]} |')
            lines.append('')
        lines.extend(['### jev 描述性分布（不參與結論）', '',
            f'命中率與預測／真實占比都使用同一批 jev 有效且可評分的 {data["jev_description"]["n"]} 點。', '',
            '| choice | 次數 | 命中率 | 預測占比 | 同批真實占比 | 占比差（百分點） |',
            '|---|---:|---:|---:|---:|---:|'])
        for label, group in data['jev_description']['classes'].items():
            delta = None if group['share_difference'] is None else group['share_difference'] * 100
            lines.append(f'| {label} | {group["choice_count"]} | {percent(group["hit_rate"])} | '
                f'{percent(group["choice_share"])} | {percent(group["true_share"])} | {number(delta, 4)} |')
        lines.append('')
    if report['frozen_warnings']:
        lines.extend(['凍結資料重抓時曾發現差異：只記錄警告，未覆寫。', ''])
        for warning in report['frozen_warnings']:
            lines.append(f'- {warning["kind"]}：凍結資料差異警告')
    return '\n'.join(lines).rstrip() + '\n'
