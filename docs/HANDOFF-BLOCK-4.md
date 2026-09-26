# 第四塊交件：計分與報告

> §7.2 已補上第四種用語。最新開發段結論為「**jev 比 majority 差**」，數值不變，見[第五塊重產報告](verification/block-5-dev-report.md)。以下保留第四塊審核當時的交件紀錄。

**開發段結論：沒有證據顯示 jev 比簡單方法好。** 最佳基準為 `majority`；交集 2,688 點，Brier 差（jev − majority）為 **+0.048165173**，交易日配對 bootstrap 95% 區間 **[+0.034299223, +0.060991019]**。

本次只讀取 `data/block-3-real.sqlite3` 的既有實驗產生開發段報告，沒有 API 呼叫，沒有解鎖保留段，沒有 commit，也沒有修改 `news/`。

## 檔案

| 檔案 | 內容 |
|---|---|
| `back/score.py`（新增） | Wilson、各類精確率／召回率、混淆矩陣、多類 Brier、有效交集、覆蓋率／失敗數、完成判斷、交易日配對 bootstrap、三種結論、jev 描述性分布 |
| `back/report.py`（新增） | 同一讀取快照內產生 report／day／status；先檢查揭露權限；Markdown 格式化；凍結差異警告 |
| `scripts/report.py`（新增） | 唯讀 CLI；JSON／Markdown；`--dev-only`；沒有解鎖選項；拒絕把資料庫本身當作輸出檔 |
| `back/experiment.py` | 新增共用 `holdout_revealed()`，沿用既有 `reveal_holdout()` 寫入揭露紀錄 |
| `back/store.py` | 新增 `run_scopes(run_id, full_split)`，區分完整範圍與指定少數時點的回放 |
| `back/jevcast.py` | 和 run 同一交易寫入 scope；`times=None` 才記為完整範圍 |
| `scripts/run_dev.py` | 基準方法整段回放同一交易寫入 scope |
| `tests/test_score.py`、`tests/test_report.py`、`tests/score_fixture.py`（新增） | 統計手算、抽樣單位、完成／交集邊界、所有出口控制；保留段案例只用人工 fixture |
| `tests/test_jev_runner.py`、`tests/test_run_dev.py` | 補實際 runner 寫入 scope 的斷言 |
| `scripts/mutation_score.py`（新增） | 42 組離線行為變異，逐條列出改動與應轉紅的測試 |
| `docs/verification/block-4-*` | 完整測試輸出、變異對照、真報告及獨立核對程式／結果 |

`report/day/status` 是供第五塊協定接線使用的資料出口函式；本塊尚未新增前端或 stdin 協定。

## 明確口徑

- 每個方法的描述性指標只用自身有有效答案、且「可預測並可評分」的點。覆蓋率分母是全部可預測且可評分點；缺答／無效答案不混入命中率分母。
- 主要／次要比較一律用**五個方法共同有效的交集**。最佳基準也只由這批開發段交集的 Brier 選定；同分依 `always_flat → majority → momentum → reversal`。同份報告的保留段與所有 bootstrap 重抽都沿用該選擇，不在保留段或重抽樣本中重選。
- `majority` 沿用已確認口徑：只累積可預測且可評分、`t′+30 ≤ t` 的開發標籤。本塊不重寫任何既有答案。
- 多類 Brier 是每點三類平方誤差之和再平均，不除以類別數或 2；已驗證接受的機率直接計分，不重新正規化。公式參考 [scikit-learn 官方 Brier 文件](https://scikit-learn.org/dev/modules/generated/sklearn.metrics.brier_score_loss.html)，實作只用標準庫。
- Wilson 使用 `z=1.959963984540054`；空樣本及零分母的精確率／召回率回 `null`（Markdown 顯示「—」）。公式參考 [NIST Wilson interval](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)。Wilson 是按規格列出的描述性區間；方法差值另用交易日區塊處理同日相依性。
- bootstrap 固定 **2,000 次、seed=20260927**；N 是共同交集有觀測的交易日數。每次有放回抽 N 日，抽到一天就納入當日全部交集時點，兩方法及兩項差值使用同一抽樣。每天點數不同時按**時點**加權，不平均各日均值。區間取 2.5%／97.5% 線性插值百分位。
- 完整性優先：交集覆蓋率 `<95%` 或回放未完成，就只用「結果不完整，不下結論」。恰好 95% 不被擋；區間碰到 0 不宣稱較好；正向 Brier 差也不能宣稱較好，因此歸入規格的「沒有證據顯示 jev 比簡單方法好」。次要準確率差不改寫主要結論。
- `eligible_missing` 是目前可評分點的缺答數；`predictable_missing` 另含不可評分點；`failure_attempts` 是該方法／該 split 歷次 `runs.n_fail` 加總，補跑成功不抹除歷史失敗，也不把它誤當現在仍缺答。
- 完成狀態要求最新 run 為 `complete`、沒有損壞的預測，且有完整範圍完成紀錄或所有預期答案齊全。指定少數點的 smoke 完成不足以代表整段完成。舊 DB 沒有 `run_scopes` 仍可唯讀計分：全部答案齊全才作為完成證據；不為產報告遷移真 DB。

## 出口與資料保護

- `build_report()` 使用一致的 SQLite 讀取快照。未解鎖的保留段只回文字狀態，不計算其指標，也不回日期、樣本數、預測、標籤、機率或曝光日數；`load_split()` 本身也有權限守衛。
- `day_view()` 在載入行情與預測前拒絕未解鎖日期；無實驗時 report／day／status 都拒絕行情。`reveal_holdout()` 仍是完整解鎖途徑；其他實驗的揭露、`check_data.py` 的 labels-only 揭露都不會默默解鎖目前實驗。
- `status_view()` 依 §7.3 保留「已曝光日期重疊」的使用狀態，這是揭露紀錄的 metadata，不含未揭露的行情或成績；鎖定的 report 不列此數字。`check_data.py` 沿用已核准的 §4.5 資料品質 1–6 項及開發標籤規則。
- 凍結資料差異只顯示品質警告，不覆寫資料。月同步範圍可能跨切點，因此報告只列資料種類，不列差異次數或日期；完全落在未揭露範圍的 fetch 紀錄不進開發報告。
- 真資料報告使用 `dev_only=True`，在 `Replay.prepare/outcome` 加上僅能走 dev 的斷言，並禁止建立網路連線。資料庫以 `mode=ro` 開啟，前後檔案摘要相同；證據見 [唯讀驗證](verification/block-4-real-integrity.json)。

## 測試與變異

執行環境：`/usr/local/bin/python3`，只用標準庫。

```sh
/usr/local/bin/python3 -m unittest -v
/usr/local/bin/python3 scripts/mutation_score.py
/usr/local/bin/python3 scripts/mutation_check.py
/usr/local/bin/python3 scripts/mutation_replay.py
/usr/local/bin/python3 scripts/mutation_experiment.py
/usr/local/bin/python3 scripts/mutation_run_dev.py
```

- **160 項 unittest 全過**：前 128 項加本塊 32 項；[完整逐項輸出](verification/block-4-unittest.txt)。包含 §9.6 的手算、固定種子、交錯失敗時的交集、不可預測／不可評分排除、三種結論、持久揭露／跨實驗曝光、凍結警告，以及既有的建立實驗原子檢查與模型版本拒收。
- **本塊 42/42 變異轉紅**：[每條變異 → 測試對照](verification/block-4-mutations.txt)。包含 Wilson／Brier 公式改錯、混淆矩陣轉置、精確率／召回率分母互換、逐點重抽、平均每日均值、取消配對、差值反向、修改種子／次數、95% 邊界、主指標改成準確率、交集改聯集、把不可評分答案放進覆蓋率、略過 input/model/outcome 驗證、忽略完成狀態、重選保留段基準、拆掉 report／day／status 權限守衛，以及 scope 與輸出檔保護。
- 回歸變異：[資料層 16/16](verification/block-4-regression-data-mutations.txt)、[回放 27/27](verification/block-4-regression-replay-mutations.txt)、[實驗／jev 40/40](verification/block-4-regression-jev-mutations.txt)、[run_dev 8/8](verification/block-4-regression-run-dev-mutations.txt) 全轉紅。
- 變異在臨時複本上跑，只有人工 fixture，沒有使用真資料或真金鑰。

## 真資料開發段報告

既有實驗 1；開發段 **2024-09-05～2026-01-21**。候選、可預測、可評分、可預測且可評分及五方法共同交集，均為 **2,688 點**；共同交集涵蓋 **336 個交易日**。各方法覆蓋率均 **100%**，目前缺答均 **0**。jev 留存歷次失敗 **1 次**，其他方法為 **0**。

| 方法 | 準確率 | Wilson 95% | Brier |
|---|---:|---|---:|
| jev | 44.0104% | [42.1437%, 45.8942%] | 0.671407 |
| always_flat | 50.6324% | [48.7429%, 52.5202%] | 0.987351 |
| majority | 50.6324% | [48.7429%, 52.5202%] | 0.623241 |
| momentum | 37.9092% | [36.0936%, 39.7593%] | 1.241815 |
| reversal | 47.6935% | [45.8099%, 49.5836%] | 1.046131 |

主要差值與區間如開頭。次要準確率差（jev − majority）為 **−6.6220 百分點**，同次配對 bootstrap 95% 區間 **[−8.8542, −4.3155] 百分點**。上述句子只針對這份開發段報告。

jev 的描述性分布使用其同一批 2,688 點，不參與選基準或結論：

| choice | 次數 | 該 choice 命中率 | 預測占比 | 真實占比 | 占比差（百分點） |
|---|---:|---:|---:|---:|---:|
| up | 45 | 15.5556% | 1.6741% | 24.6280% | −22.9539 |
| flat | 1,682 | 52.7348% | 62.5744% | 50.6324% | +11.9420 |
| down | 961 | 30.0728% | 35.7515% | 24.7396% | +11.0119 |

完整各類精確率／召回率、混淆矩陣與描述性表格見 [Markdown 報告](verification/block-4-real-report.md)；未四捨五入的值見 [JSON](verification/block-4-real-report.json)。

另外用獨立 SQL＋標準庫程式驗算，**不 import 計分實作**：從已寫入的開發段 outcomes／predictions 核對命中數、混淆矩陣、精確率／召回率；Brier 用 Decimal；Wilson 用反解二次不等式；bootstrap 每次實際展開抽中的整日配對時點，核對區間。全部一致，見 [核對程式](verification/block-4-independent-check.py) 與 [結果](verification/block-4-independent-check.json)。

重現開發段報告：

```sh
/usr/local/bin/python3 scripts/report.py \
  --db data/block-3-real.sqlite3 --experiment 1 --dev-only \
  --format markdown --output data/dev-report.md
/usr/local/bin/python3 docs/verification/block-4-independent-check.py
```
