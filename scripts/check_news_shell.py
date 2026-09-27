#!/usr/local/bin/python3
"""Real shell/broker + fixture publisher, isolated outside module catalog.

The receive clock is fixed to a known fixture session before 13:30. No live
market data, news installation or model credentials are used.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from back.daily_store import DailyStore
from back.daily_forward_client import ledger_usage

PUBLISHER='''import json,sys
body=json.load(open('digest.json'))
for line in sys.stdin:
    p=json.loads(line);kind=p.get('t');seq=p.get('seq')
    if kind=='hello':print(json.dumps(dict(t='ready',seq=seq)),flush=True)
    elif kind=='up':print(json.dumps(dict(t='publish',seq=seq,topic='news.market_digest',body=body)),flush=True)
    elif kind=='bye':
        print(json.dumps(dict(t='done',seq=seq)),flush=True);break
'''
WRAPPER='''from datetime import datetime
from pathlib import Path
import socket
from back import news_digest
from back.data import TAIPEI
from back.trendcast import main
news_digest.now=lambda:datetime(2026,9,29,13,29,tzinfo=TAIPEI)
def denied(*a,**kw):
    Path('network-attempt.txt').write_text('blocked')
    raise AssertionError('acceptance_network_forbidden')
socket.create_connection=socket.socket.connect=socket.socket.connect_ex=denied
raise SystemExit(main())
'''


def main():
    out=ROOT/'docs/verification';out.mkdir(exist_ok=True)
    before=ledger_usage();env=os.environ.copy()
    for k in ('FUGLE_API_KEY','TYPESAFE_API_KEY','FINMIND_API_KEY','SSL_CERT_FILE'):env.pop(k,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    shell=ROOT.parents[1]/'modudock/shell'
    env['PLAYWRIGHT_MODULE']=(shell.parent/'web/node_modules/playwright/index.mjs').as_uri()
    evidence={}
    with tempfile.TemporaryDirectory(prefix='trendcast-news-shell-') as tmp:
        scratch=Path(tmp);modules=scratch/'modules';modules.mkdir()
        assert not scratch.is_relative_to(ROOT.parent)
        receiver=modules/'trend-cast';receiver.mkdir()
        for name in ('back','front'):shutil.copytree(ROOT/name,receiver/name,ignore=shutil.ignore_patterns('__pycache__'))
        manifest=json.loads((ROOT/'modudock.json').read_text());manifest['backend']['command']=[sys.executable,'fixture_receiver.py']
        (receiver/'modudock.json').write_text(json.dumps(manifest));(receiver/'fixture_receiver.py').write_text(WRAPPER)
        db=receiver/'data/trendcast.sqlite3'
        with DailyStore(db) as store:
            store.db.execute("INSERT INTO d_calendar VALUES ('2026-09-29','fixture')");store.db.commit()
        sender=modules/'news-digest-fixture';sender.mkdir()
        (sender/'modudock.json').write_text(json.dumps(dict(protocol=1,id='news-digest-fixture',name='新聞廣播測試',version='0.0.1',backend=dict(command=[sys.executable,'publish.py']),provides=['news.market_digest'])))
        digest=dict(schema=1,at='2026-09-29T13:20:00+08:00',window_hours=24,signal_counts=dict(bullish=2,mixed=1,unrelated=0,bearish=1),top_themes=[dict(name='半導體需求',events=2,direction='bullish')],source_count=4)
        (sender/'digest.json').write_text(json.dumps(digest,ensure_ascii=False));(sender/'publish.py').write_text(PUBLISHER)
        binary=scratch/'modudock';subprocess.run(['go','build','-o',str(binary),'./cmd/modudock'],cwd=shell,env=env,check=True)
        log=out/'jev-news-shell.log'
        with log.open('w') as stream:
            proc=subprocess.Popen([str(binary),'-modules',str(modules),'-addr','127.0.0.1:0'],cwd=shell,env=env,stdout=stream,stderr=subprocess.STDOUT)
            try:
                until=time.monotonic()+30;match=None
                while time.monotonic()<until and proc.poll() is None:
                    match=re.search(r'listening on (127\.0\.0\.1:\d+)',log.read_text())
                    if match:break
                    time.sleep(.05)
                assert match,log.read_text()
                address=match[1]
                subprocess.run(['node',str(ROOT/'scripts/check_news_shell.mjs'),'http://'+address,str(out/'jev-news-shell-browser.json')],cwd=ROOT,env=env,check=True,timeout=45)
                with DailyStore(db,readonly=True) as store:
                    latest=dict(store.db.execute('SELECT * FROM news_digests').fetchone())
                    snapshot=dict(store.db.execute('SELECT * FROM news_snapshots').fetchone())
                    assert snapshot['day']=='2026-09-29'
                    assert json.loads(snapshot['payload_json'])==digest
                    assert snapshot['received_at']=='2026-09-29T13:29:00.000000+08:00'
                    assert latest['received_at']==snapshot['received_at']
                    assert store.db.execute('SELECT count(*) FROM news_snapshots').fetchone()[0]==1
                assert not (receiver/'network-attempt.txt').exists()
                evidence=dict(address=address,publisher='test-only backend, publish once after up',
                    scratch_outside_modules=True,fixture_clock='2026-09-29T13:29:00+08:00',
                    snapshot=snapshot,network_attempts=0,ssl_cert_file_unset=True,real_db_untouched=True)
            finally:
                proc.send_signal(signal.SIGTERM)
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:proc.kill();proc.wait();raise
                evidence['shell_exit_code']=proc.returncode
        assert proc.returncode==0
        host,port=address.split(':')
        with socket.socket() as check:assert check.connect_ex((host,int(port)))!=0
    assert not scratch.exists();evidence['scratch_removed']=True
    evidence.update(ledger_before=before,ledger_after=ledger_usage())
    assert evidence['ledger_after']==before
    (out/'jev-news-shell-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(evidence,ensure_ascii=False))

if __name__=='__main__':main()
