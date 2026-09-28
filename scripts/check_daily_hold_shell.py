#!/usr/local/bin/python3
"""Real browser on an isolated shell and DB backup; no credentials or network."""
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
WRAPPER='''from pathlib import Path
import socket
from back.trendcast import main
def denied(*a,**kw):
    Path('network-attempt.txt').write_text('blocked')
    raise AssertionError('acceptance_network_forbidden')
socket.create_connection=socket.socket.connect=socket.socket.connect_ex=denied
raise SystemExit(main())
'''


def main():
    out=ROOT/'docs/verification';env=os.environ.copy()
    for key in ('FUGLE_API_KEY','TYPESAFE_API_KEY','FINMIND_API_KEY','SSL_CERT_FILE'):env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    shell=ROOT.parents[1]/'modudock/shell'
    env['PLAYWRIGHT_MODULE']=(shell.parent/'web/node_modules/playwright/index.mjs').as_uri()
    evidence={}
    with tempfile.TemporaryDirectory(prefix='trendcast-hold-shell-') as tmp:
        scratch=Path(tmp);receiver=scratch/'modules/trend-cast';receiver.mkdir(parents=True)
        for name in ('back','front'):shutil.copytree(ROOT/name,receiver/name,ignore=shutil.ignore_patterns('__pycache__'))
        manifest=json.loads((ROOT/'modudock.json').read_text());manifest['backend']['command']=[sys.executable,'fixture_receiver.py']
        (receiver/'modudock.json').write_text(json.dumps(manifest));(receiver/'fixture_receiver.py').write_text(WRAPPER)
        (receiver/'data').mkdir()
        with sqlite3.connect((ROOT/'data/trendcast.sqlite3').as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(receiver/'data/trendcast.sqlite3') as dst:src.backup(dst)
        binary=scratch/'modudock'
        subprocess.run(['go','build','-o',str(binary),'./cmd/modudock'],cwd=shell,env=env,check=True)
        log=out/'evolve2-3-shell.log'
        with log.open('w') as stream:
            proc=subprocess.Popen([str(binary),'-modules',str(scratch/'modules'),'-addr','127.0.0.1:0'],cwd=shell,env=env,stdout=stream,stderr=subprocess.STDOUT)
            try:
                until=time.monotonic()+30;match=None
                while time.monotonic()<until and proc.poll() is None:
                    match=re.search(r'listening on (127\.0\.0\.1:\d+)',log.read_text())
                    if match:break
                    time.sleep(.05)
                assert match;address=match[1];assert not address.endswith(':8731')
                subprocess.run(['node',str(ROOT/'scripts/check_daily_hold_shell.mjs'),'http://'+address,str(out/'evolve2-3-shell-browser.json')],cwd=ROOT,env=env,check=True,timeout=60)
                assert not (receiver/'network-attempt.txt').exists()
                evidence=dict(address=address,network_attempts=0,scratch_outside_modules=True,real_db_untouched=True)
            finally:
                proc.send_signal(signal.SIGTERM)
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:proc.kill();proc.wait();raise
                evidence['shell_exit_code']=proc.returncode
        assert proc.returncode==0
        host,port=address.split(':')
        with socket.socket() as check:assert check.connect_ex((host,int(port)))!=0
    evidence['scratch_removed']=not scratch.exists()
    (out/'evolve2-3-shell-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(evidence));return 0


if __name__=='__main__':raise SystemExit(main())
