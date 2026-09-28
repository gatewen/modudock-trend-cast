#!/usr/local/bin/python3
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
MUTATIONS=[
 ('legacy_status_ack_dropped','front.js',[(" && !(!isDaily && body.op === 'status')", "")],'legacy action status acknowledgement'),
 ('default_horizon_wrong','front.js',[("select.value = '7';", "select.value = '3';")],'daily default seven'),
 ('late_epoch_replies_allowed','front.js',[("if (!match || (body.op !== 'error' && body.op !== match.op && !(!isDaily && body.op === 'status'))) { pumpReads(); return; }", "if (!match) { receive?.({...body, request_id: 3}); pumpReads(); return; }" )],'daily horizon and range switching'),
 ('future_text_guard_removed','daily.js',[("    if(forbiddenDate(body))return;", "")],'daily rejects future'),
 ('date_controls_guard_removed','daily.js',[("if (!allowedDay(value) || !days.includes(value))", "if (false)")],'daily bounded date'),
 ('correct_colors_swapped','daily.js',[("p.correct===true?'correct'", "p.correct===false?'correct'")],'daily chart shape'),
 ('logit_points_hidden','daily.js',[("!['jev_ind','ind_logit'].includes(p.method)", "!['jev_ind'].includes(p.method)")],'daily chart shape'),
 ('n29_warning_lost','daily.js',[("r.n<30?'樣本太少'", "r.n<29?'樣本太少'")],'daily all methods'),
 ('brier_shown_as_difference','daily.js',[("number(r.difference,6)", "number(r.brier,6)")],'daily all methods'),
 ('jev_row_dropped','daily.js',[("body.methods.filter(r=>METHODS.includes(r.method))", "body.methods.filter(r=>METHODS.includes(r.method)&&r.method!=='jev_ind')")],'daily all methods'),
 ('markup_injected','method_names.js',[("else node.append(doc.createTextNode(token));", "else node.insertAdjacentHTML('beforeend',token);")],'daily strings stay text'),
 ('holdout_status_lost','daily.js',[("正在讀取保留段狀態…", "已使用")],'daily default seven'),
 ('daily_legacy_not_cleared','front.js',[("child?.unmount(); epoch++;", "epoch++;")],'thirty minute mode keeps'),
]
def main():
    killed=0
    for name,file,changes,test in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix='daily-front-mutation-',dir=ROOT/'data') as tmp:
            work=Path(tmp);shutil.copytree(ROOT/'front',work/'front');(work/'tests').mkdir()
            shutil.copy2(ROOT/'tests/daily_front.test.mjs',work/'tests/daily_front.test.mjs')
            target=work/'front'/file;source=target.read_text()
            for old,new in changes:
                if source.count(old)!=1:raise RuntimeError('nonunique mutation '+name)
                source=source.replace(old,new)
            target.write_text(source)
            r=subprocess.run(['node','--test','--test-name-pattern='+test,'tests/daily_front.test.mjs'],cwd=work,text=True,capture_output=True,timeout=15)
            output=r.stdout+r.stderr
            red=r.returncode!=0 and 'AssertionError' in output and not any(s in output for s in ('SyntaxError','ERR_MODULE_NOT_FOUND'))
            killed+=int(red);print(f'{name}: {"KILLED" if red else "SURVIVED/INVALID"} -> {test}',flush=True)
            if not red:print(output)
    print(f'{killed}/{len(MUTATIONS)} front behavior mutations killed')
    return int(killed!=len(MUTATIONS))
if __name__=='__main__':raise SystemExit(main())
