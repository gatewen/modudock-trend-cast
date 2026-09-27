#!/usr/local/bin/python3
"""Read-only p6 review artifacts. No execute option, API client or DB writer."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_experiment import load_experiment
from back.daily_prompt import (PROMPT_VERSION, ROUND_CALL_LIMIT, sample_dates, payload_bytes, questions)
from back.daily_store import DailyStore
from back.evolution_run import ForbiddenClient
from back.experiment import canonical, MODEL
from back.store import DEFAULT_DB

ROOT=Path(__file__).resolve().parents[1]


def render_prompt(config, sample):
    lines=['# jev_ind / p6 完整 prompt（依審核修正，已授權執行）','',
        f'模型 `{MODEL}`。一次 request 共用同一份 state，questions 有 direction_3d、direction_7d、direction_14d 三題。',
        '以下 instructions／criteria 直接由 request builder 產生；不另加隱藏說明、基準分布或歷史成績。','',
        'state 固定只有 daily（60×5 相對值陣列）與 indicators（11 個指標的文字）。'
        '籌碼欄位只含相對20日均量的百分比（2位小數）或「本期無此資料」；其他文字數值最多小數6位。'
        '狀態由未四捨五入值判定，不輸出代號、日期、絕對價格、絕對數量或實驗 metadata。','',
        '## 抽樣與比較口徑','',
        f'已確認：暖機後第一個開發交易日起每 5 個交易日取一天，只讀日曆。{sample["grid_count"]} 個節拍點中，'
        f'依日期排除 14 日終點跨出開發段的最後 {sample["tail_excluded"]} 點，固定共同 {len(sample["days"])} 天；不依標籤或指標狀態增刪。'
        'bias20 的 3／7／14 日狀態分別用各 H 已到期樣本計算。','',
        '使用者已授權修正後執行；本輪上限 650 次 HTTP（含重試），全場帳本沿用 3,000 次上限。'
        '同一天的三題應全部驗證、原子保存；比較使用同一批有效回答日的 jev_ind − majority、jev_ind − ind_logit，'
        '每個 H 分開，完整開發交易日序列切不重疊 20 日塊、保留尾塊、配對 bootstrap 2000 次、seed=20260927。','',
        '以下為 API questions 中的完整文字。']
    for name,question in questions(config).items():
        lines.extend(['',f'## {name}','',f'`type`: `{question["type"]}`','', '### instructions','',question['instructions'],'','### criteria','',
                      '```json',json.dumps(question['criteria'],ensure_ascii=False,indent=2),'```'])
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--out',type=Path,default=ROOT/'docs'/'verification')
    parser.add_argument('--prompt-doc',type=Path,default=ROOT/'docs'/'PROMPT-P6.md')
    args=parser.parse_args(argv)
    guard=ForbiddenClient()
    with guard.guard(),DailyStore(args.db,readonly=True) as store:
        store.db.execute('BEGIN')
        row=load_experiment(store,4)
        sample=sample_dates(store,row['config'])
        if not sample['days']: raise ValueError('empty_sample')
        # First grid point, selected without labels, signals or performance.
        body=payload_bytes(store,row['config'],sample['days'][0])
        prompt=render_prompt(row['config'],sample)
    sample_hash=hashlib.sha256(canonical(sample['days']).encode()).hexdigest()
    review=dict(experiment=4,method='jev_ind',prompt_version=PROMPT_VERSION,model=MODEL,
        approved_to_run=True,selection=sample['rule'],step=sample['step'],
        date_grid_count=sample['grid_count'],sample_count=len(sample['days']),tail_excluded=sample['tail_excluded'],
        sample_sha256=sample_hash,experiment_data_digest=row['data_digest'],
        questions_sha256=hashlib.sha256(canonical(questions(row['config'])).encode()).hexdigest(),
        example_body_sha256=hashlib.sha256(body).hexdigest(),example_body_bytes=len(body),
        example_selection='first_sample_day_only',round_max_calls=ROUND_CALL_LIMIT,
        http_calls=0,network_attempts=guard.calls)
    args.out.mkdir(parents=True,exist_ok=True);args.prompt_doc.parent.mkdir(parents=True,exist_ok=True)
    # This file is exactly the UTF-8 JSON HTTP body, not a wrapper with dates.
    (args.out/'evolve-7-example-payload.json').write_bytes(body)
    (args.out/'evolve-7-preflight.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
    args.prompt_doc.write_text(prompt)
    print(json.dumps(review,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
