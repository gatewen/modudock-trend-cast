# 第二塊交件：回放、f1 與基準

依 SPEC §5、§6.2 實作；只使用 Python 標準庫，執行器 `/usr/local/bin/python3`。未 commit，未修改 news/，第一塊 runtime 與兩項低嚴重度備忘均未改動。

## 本輪確認的口徑

1. **majority 只累積可預測且可評分的開發点標籤，且 `t′ + 30 分鐘 ≤ t`。** 真資料標籤分布也使用相同的可預測且可評分集合。使用者已確認，cc 預計第四塊回寫 SPEC §6.2。
2. f1 序列化在本塊完整驗證；**實際送出的 HTTP payload 驗證於第三塊 jev client 加入**。本塊沒有假稱已驗過尚未實作的 HTTP 路徑。
3. 真資料本輪只使用暫時切分，不建立正式 experiments。報告與所有點數均限開發段，不遍歷保留段預測點，也不輸出保留段的日期、數量、標籤或成績。

## 檔案

新增：

- `back/replay.py`：ReplayPlan、f1、可預測性、事後可評分性、精確標籤與 LabelObservation。
- `back/baselines.py`：always_flat、majority、momentum、reversal。
- `scripts/check_replay.py`：唯讀、暫時切分、僅開發段統計。
- `scripts/mutation_replay.py`：重用第一塊的隔離 runner，執行第二塊 27 組變異。
- `tests/replay_fixture.py`：可手算、與 API 無關的合成交易日／行情。
- `tests/test_replay.py`、`tests/test_baselines.py`、`tests/test_future_isolation.py`、`tests/test_replay_audit.py`。
- `docs/verification/block-2-unittest.txt`：本輪全套 unittest 完整輸出。
- `docs/verification/block-2-mutations.txt`：每個變異及轉紅測試的完整對照。
- `docs/verification/block-2-real-dev.json`：真資料開發段統計。
- 本交件說明。

## 介面與第三塊接線

```python
from back.replay import Replay, ReplayPlan, observation
from back.baselines import Baselines

# 第三塊提供已凍結的 experiments row 與交易日曆（包含暖機）。
plan = ReplayPlan.from_experiment(experiment_row, trading_days)
replay = Replay(store, plan)
point = replay.prepare(t)       # 只讀當時已知資料
result = replay.outcome(point)  # 明確獨立的事後讀取
record = observation(point, result)

methods = Baselines(replay, observations)
prediction = methods.predict('majority', point)
```

- `PreparedPoint` 帶 experiment_id、symbol、門檻、t、predictable／reason、input_json／input_hash；不可預測時兩個 input 欄位均為 None。raw close 和 visible bars 只供本機，沒有序列化進 f1，也從 repr 隱藏。
- `point.state` 是從 canonical JSON 取出的新副本；下游修改副本不會更改原始 input_json 或 hash。
- `ReplayPlan.from_experiment()` 對應現有 experiments 欄位，從 config_json 讀 `symbol`、`warmup_start` 與可選 `mapping`；f1 及時間映射版本不符會拒絕。
- 回放應在穩定的資料快照中執行。歷史日快取最多 64 日，只快取嚴格早於當日的資料；同連線寫入或其他連線提交會使快取失效。當日可見棒每次都經既有 `Store.available_bars()`。
- observations 為不可變的本機標籤紀錄序列。開發段 majority 每次按揭曉時間篩選，因此亂序／續跑不靠可污染的全域累積器；保留段只取固定的開發段紀錄。可用的重複點、不同實驗／股票／門檻紀錄會拒絕。
- 暫時統計工具遇到已有 experiments 的資料庫會拒絕，避免重新切分繞過正式保留段規則。第三塊的正式報告須用 experiments 的切分。
- **曝光延續提醒：本輪已展示開發段 2024-07-26～2026-01-27 的標籤統計。第三塊建立正式實驗時，必須把此區間視為已曝光，不能因暫時切分尚未入 experiments 而重新宣稱未使用。** 本輪報告保留了明確的 dev_start／dev_end 供接線延續。

## f1 與計算細節

- state 只有 `clock`、`minutes_to_close`、`today`、`recent`、`prev_days`；today 的鍵為 open/high/low/close。沒有代號、日期、參考價或絕對價格。
- 價格相對值以精確 Fraction 做 ROUND_HALF_EVEN，輸出整數 ‰；量比亦先精確取到兩位小數。canonical JSON 使用 sort_keys、緊密分隔符、禁止 NaN；hash 為其 UTF-8 bytes 的 SHA-256。
- 前 25 個完整交易日的實作定義：既有交易日曆中的前 25 日，每日有日 K 與至少一根當日已結束分 K。**不要求每分鐘都有棒**，否則會與規格的缺分鐘量比規則衝突。前 5 日各自的參考價亦須已知，否則沒有合法 f1，點標不可預測。
- 每分鐘均量只用前 20 日中有該分鐘的日子；不足 10 日或均量為 0 則 null。volume=0 是存在的樣本，不等於缺資料。不跨日補 recent，不補缺分鐘。
- 前 5 日日量比各取該日之前 20 日，不含自身；均量為 0 時記 null，避免除以 0。
- 標籤用原始十進位值轉精確有理數，以整數交叉比較處理 ±k‰ 邊界，不受浮點與 Decimal context 精度影響。
- majority 採加 1 平滑；沒有標籤時 flat、三類各 1/3；票數同分的固定順序為 flat、up、down。
- momentum 的 09:30 使用當日首根 open；其他點只取同日 `[t−35,t−30]` 中最新的已結束棒，沒有就失敗。其 label 計算共用精確門檻；reversal 交換 up/down。
- 「可評分」是獨立的事後資料條件，因此有些暖機不足的點仍有真實端點；**標籤分布與 majority 只取可預測且可評分的交集**。

## 測試與變異

```sh
/usr/local/bin/python3 -m unittest -v
/usr/local/bin/python3 scripts/mutation_replay.py
```

全套 78 項 unittest 通過（包含第一塊），第二塊 27/27 變異轉紅。詳見兩份完整輸出。

關鍵驗證：

- 對同一日全部 8 個時點、兩組固定亂數種子，改動所有未來分 K、當日日 K、後續交易日資料／除權息及未揭曉標籤／可用旗標，input_json、hash、可預測性及四個基準的答案／機率完全相同。
- 刪除未來棒與當日日 K，仍不改輸入與基準；只改變事後可評分性。
- raw timestamp 在 t、實際 bar_end 大於 t 的棒被改動時輸入不變；bar_end 等於 t 被實質改動時，輸入和 hash 都必須改變，避免空測試假綠。
- 25 日暖機、同分鐘最低样本、零量／缺資料差別、首根開盤、同日 lookback、新鮮度邊界、13:30 競價、t+30 端點、門檻與原始小數精度皆有測例。
- 同一批價格整體乘 7，f1 完全相同，配合精確欄位白名單與序列化值檢查，驗證沒有絕對價格／識別欄位。
- `< t+60s` 在整分鐘 t 與整分鐘 bar_end 的集合上可能與 `≤t` 等價，故以共用 available_bars 的非整分鐘 cutoff 明確區分並抓掉該變異；沒有把等價條件假報為已抓到。

變異對照含：改用 raw timestamp、放寬／收緊 cutoff、偷用當日日 K 或完整當日行情、均量補零／丟零／包含自身、忽略除權息三態、門檻改開區間／浮點計算、偷看第 31 分鐘、取消收盤競價／價格新鮮度、majority 的成熟度／交集／開發段限制、momentum／reversal，以及統計納入錯誤集合／遍歷保留段。

## 真資料：只列開發段

使用 cc 提供的資料庫以唯讀連線做 SQLite backup，隔離副本在 `data/block-2-real.sqlite3`，不修改來源檔。未重抓 API、未使用金鑰。

```sh
/usr/local/bin/python3 scripts/check_replay.py --db data/block-2-real.sqlite3
```

暫時切分為交易日排序的前 floor(70%)；暖機日保留在候選集合，未拿掉後再重切。開發段為 **2024-07-26～2026-01-27，369 個交易日**。

| 開發段項目 | 點數 |
|---|---:|
| 候選點 | 2,952 |
| 可預測 | 2,752 |
| 可評分（事後端點存在） | 2,952 |
| 可預測且可評分／標籤分布分母 | 2,752 |
| 不可預測：前 25 日暖機不足 | 200 |

| 開發段標籤 | 點數 | 比例 |
|---|---:|---:|
| 漲 | 670 | 24.3459% |
| 盤整 | 1,409 | 51.1991% |
| 跌 | 673 | 24.4549% |

保留段未遍歷、未列統計。本輪是資料回放驗收，不是 jev 預測能力或交易績效評估。
