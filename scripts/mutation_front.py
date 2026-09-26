#!/usr/local/bin/python3
"""Run front-end behavior mutations against node/happy-dom in isolated copies."""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ('sync_all_failed_wording_hidden', [("failed: '全部失敗'", "failed: '部分失敗'")],
     'sync distinguishes total and partial failure'),
    ('sync_safe_reasons_hidden', [("syncReasons.join('、')", "''")],
     'sync distinguishes total and partial failure'),
    ('ready_before_callbacks', [('  ctx.channel.onMessage(receive);', "  ctx.report('ready');\n  ctx.channel.onMessage(receive);"),
                              ("  updateButtons(); ctx.report('ready');", '  updateButtons();')],
     'mount synchronous'),
    ('pre_up_send_guards_removed', [('if (live && !disposed && !node.disabled) action();', 'if (!disposed) action();'),
                                  ('if (!live || disposed) return;', 'if (disposed) return;')],
     'mount synchronous'),
    ('untrusted_text_becomes_html', [('node.textContent = value;', 'node.innerHTML = value;')],
     'untrusted strings'),
    ('correct_and_incorrect_swapped', [("valid && answer.correct === true ? 'correct'", "valid && answer.correct === false ? 'correct'")],
     'status initializes authorized date'),
    ('unpredictable_and_unscorable_color_guard_removed', [('p.predictable === true && p.scorable === true &&', 'true &&')],
     'unpredictable unscorable'),
    ('arrow_up_down_reversed', [("{up: '▲', flat: '▬', down: '▼'}[choice]", "{up: '▼', flat: '▬', down: '▲'}[choice]")],
     'status initializes authorized date'),
    ('plot_retained_during_date_change', [("clearDay('載入當日走勢…');", '')],
     'day navigation clears old plot'),
    ('old_report_request_guard_removed', [("if ((op === 'day' || op === 'report') && body.request_id != null && body.request_id !== requests[op]) return;", '')],
     'older report for the same experiment'),
    ('locked_day_plot_guard_removed', [("if (body.status !== 'ok') { clearDay", 'if (false) { clearDay')],
     'locked day discards'),
    ('locked_report_guard_removed', [("if (split === 'holdout' && metadata.holdout?.state !== 'revealed') continue;", '')],
     'locked metadata refuses'),
    ('reveal_confirmation_bypassed', [("() => confirm('reveal')", "() => send('reveal', {confirmed: true})")],
     'reveal requires second explicit confirmation'),
    ('new_experiment_confirmation_bypassed', [("() => confirm('new_experiment')", "() => send('new_experiment', {confirmed: true})")],
     'new experiment uses a second confirmation'),
    ('threshold_validation_removed', [('if (!Number.isInteger(value) || value < 1 || value >= 1000) return;', '')],
     'new experiment uses a second confirmation'),
    ('jev_missing_key_guard_removed', [("keyAvailable = method.value !== 'jev' || metadata.keys?.typesafe === 'available'", 'keyAvailable = true')],
     'missing key disables jev'),
    ('dev_button_runs_holdout', [("button('跑開發段', () => run('dev'))", "button('跑開發段', () => run('holdout'))")],
     'missing key disables jev'),
    ('unmount_leaves_ui', [('root.remove();', '')],
     'no experiment shows no chart'),
    ('locked_holdout_progress_counts_shown', [("(body.replay?.split === 'dev' || body.holdout?.state === 'revealed') &&", 'true &&')],
     'development progress can show counts'),
    ('experiment_change_keeps_old_confirmation', [("closeDialog(); selected = '';", "selected = '';")],
     'experiment change invalidates old report'),
    ('reveal_does_not_reload_cleared_chart', [("pendingAction = true; selected = '';", 'pendingAction = true;')],
     'successful reveal refreshes the cleared day'),
]


def main():
    killed = 0
    for name, changes, test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix='front-mutation-', dir=ROOT / 'data') as work:
            work = Path(work)
            shutil.copytree(ROOT / 'front', work / 'front')
            (work / 'tests').mkdir()
            shutil.copy2(ROOT / 'tests/front.test.mjs', work / 'tests/front.test.mjs')
            target = work / 'front/front.js'
            source = target.read_text()
            for old, new in changes:
                # textContent helper is intentionally shared by HTML and SVG;
                # its mutation changes both sites to detect unsafe rendering.
                if source.count(old) != (2 if name == 'untrusted_text_becomes_html' else 1):
                    raise RuntimeError('mutation anchor not unique: ' + name)
                source = source.replace(old, new)
            target.write_text(source)
            result = subprocess.run(['node', '--test', '--test-name-pattern=' + test, 'tests/front.test.mjs'],
                                    cwd=work, text=True, capture_output=True, timeout=15)
            output = result.stdout + result.stderr
            red = result.returncode != 0 and ('AssertionError' in output) and not any(
                error in output for error in ('SyntaxError', 'ERR_MODULE_NOT_FOUND'))
            killed += int(red)
            print(f'{name}: {"KILLED" if red else "SURVIVED/INVALID"} -> {test}', flush=True)
            if not red: print(output)
    print(f'{killed}/{len(MUTATIONS)} front behavior mutations killed')
    return 0 if killed == len(MUTATIONS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
