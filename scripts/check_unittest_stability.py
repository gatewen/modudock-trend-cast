#!/usr/local/bin/python3
"""Sequential full-suite evidence. Stop on any failure or source change."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def source_digest():
    digest=hashlib.sha256()
    for directory in ('back','front','scripts','tests'):
        for path in sorted((ROOT/directory).rglob('*')):
            if path.suffix not in ('.py','.js','.mjs'):continue
            digest.update(str(path.relative_to(ROOT)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runs',type=int,default=20)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    if args.runs<1:parser.error('--runs must be positive')
    args.output.mkdir(parents=True,exist_ok=True)
    manifest=args.output/'results.json'
    if manifest.exists():parser.error('output already has results; use a fresh evidence directory')
    code=source_digest();env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    for key in ('FUGLE_API_KEY','TYPESAFE_API_KEY','FINMIND_API_KEY','SSL_CERT_FILE'):env.pop(key,None)
    summary=dict(command=[sys.executable,'-B','-m','unittest','discover','-s','tests','-v'],source_digest=code,
                 requested_runs=args.runs,started_at=datetime.now(timezone.utc).isoformat(),runs=[])
    for index in range(1,args.runs+1):
        if source_digest()!=code:raise SystemExit('Source changed: stability sequence invalid')
        path=args.output/f'run-{index:02}.txt';start=time.monotonic()
        print(f'Start {index}/{args.runs}',flush=True)
        with path.open('w') as stream:
            result=subprocess.run(summary['command'],cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
        text=path.read_text();match=re.search(r'Ran (\d+) tests in ([\d.]+)s',text)
        clean=result.returncode==0 and text.rstrip().endswith('OK') and not any(s in text for s in ('Exception in thread','Exception ignored in'))
        clean=clean and source_digest()==code
        row=dict(run=index,ok=clean,exit_code=result.returncode,tests=int(match[1]) if match else None,
                 elapsed_seconds=round(time.monotonic()-start,3),log=path.name)
        summary['runs'].append(row);manifest.write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(row),flush=True)
        if not clean:return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
