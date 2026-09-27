"""Versioned instructions only; f1 state and direction criteria are unchanged."""
from .data import DataError

PROMPT_VERSION = 'p1'
PROMPT_VERSIONS = ('p1', 'p2', 'p3')
P1_INSTRUCTIONS = '根據 state，這檔股票從現在到 30 分鐘後，價格變化最可能落在哪一類？'

# Experiment 1, p1, 2330 development 2024-09-05..2026-01-21, k=3 permille.
# Predictable AND scorable outcomes: flat=1361, up=662, down=665, total=2688.
# Each count / 2688 * 100 rounded HALF_UP to integer percent gives 51/25/25.
# These approximate percentages sum to 101; they are explanatory text, not a
# probability vector. Frozen constants: never recompute from another experiment.
P2_BASE_PERCENT = {'flat': 51, 'up': 25, 'down': 25}
P2_INSTRUCTIONS = (
    'clock 現在時刻；minutes_to_close 距 13:30 收盤分鐘數；'
    'today 今日開／高／低／目前價相對參考價的千分比；'
    'recent 今日最近至多 60 根 1 分 K，每根 [自 09:00 起的分鐘序號, '
    '收盤相對目前價的千分比, 成交量相對過去 20 日同一分鐘平均的倍數（null＝樣本不足）]；'
    'prev_days 前 5 個交易日各 [開, 高, 低, 收 相對該日參考價的千分比, '
    '日量相對其前 20 日均量的倍數]，由遠到近。\n\n'
    f'在過去的資料中，30 分鐘後的變化約 {P2_BASE_PERCENT["flat"]}% 屬於 flat，'
    f'up 與 down 各約 {P2_BASE_PERCENT["up"]}%。\n\n'
    '請依 state 判斷，並讓三類機率反映你真正的把握程度；'
    '沒有明確訊號時，機率應接近上述基準比例。'
)

# SPEC 14.5: only p2's field glossary, deliberately no class base rates.
P3_INSTRUCTIONS = P2_INSTRUCTIONS.split('\n\n')[0] + '\n\n' + (
    '這檔股票從現在到 30 分鐘後，價格變化的幅度會落在哪一類？')


def instructions(prompt_version):
    if prompt_version not in PROMPT_VERSIONS:
        raise DataError('unsupported_prompt_version')
    return {'p1': P1_INSTRUCTIONS, 'p2': P2_INSTRUCTIONS, 'p3': P3_INSTRUCTIONS}[prompt_version]
