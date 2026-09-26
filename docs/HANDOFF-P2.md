# §13.2–13.3：p2 實作與實驗 2 開發段

實驗 2 開發段已完成，五方法共同交集 **2,688 點**。四個基準的答案、機率與 f1 輸入均與實驗 1 完全相同。此交件只報數字，沒有執行 §13.4 的方法定案；保留段未跑、未解鎖，沒有 commit 或修改 `news/`。

## 開發段數字

評估日期：2024-09-05～2026-01-21。準確率與 Brier 均使用相同的 2,688 點。

| 實驗 2 方法 | 正確數／樣本數 | 準確率 | 多類 Brier |
|---|---:|---:|---:|
| jev（p2） | 1,332／2,688 | 49.5536% | 0.6856722842261904 |
| always_flat | 1,361／2,688 | 50.6324% | 0.9873511904761905 |
| majority | 1,361／2,688 | 50.6324% | 0.6232414118361036 |
| momentum | 1,019／2,688 | 37.9092% | 1.2418154761904763 |
| reversal | 1,282／2,688 | 47.6935% | 1.0461309523809523 |

jev（p2）的 choice：

| choice | 次數 | 比例 |
|---|---:|---:|
| up | 2 | 0.0744% |
| flat | 2,547 | 94.7545% |
| down | 139 | 5.1711% |

p1／p2 在同一批開發段時點的並列數字：

| 問法 | 樣本數 | 準確率 | Brier |
|---|---:|---:|---:|
| p1（實驗 1） | 2,688 | 44.0104% | 0.6714065848214286 |
| p2（實驗 2） | 2,688 | 49.5536% | 0.6856722842261904 |

[完整 JSON](verification/p2-dev-results.json) 由 [獨立核對程式](verification/p2-independent-dev-audit.py) 產出。核對程式不 import 計分實作，直接以唯讀 SQL 取開發段資料，Brier 使用 Decimal 計算，不重新正規化已接受的機率、不做 bootstrap、不選定方法。Decimal 與原報告浮點運算可能在最後一位有差異。

## 啟動前檢查與執行

使用 `data/trendcast.sqlite3`，以實驗 1 原有 `dev_start`／`hold_end` 作為同一組 start／end，明確指定 `prompt_version='p2'`。

- 暖機同為 2024-08-01 起、25 個交易日，逐日清單一致。
- 開發段同為 2024-09-05～2026-01-21。
- 保留段切點同為 2026-01-22～2026-08-31；只核對設定與交易日清單，不跑或揭露。
- symbol、threshold_permille=3、f1、mapping、model 及 config_json 一致；prompt_version 是唯一設定差異，data_digest 因包含問法版本而不同。
- 四個基準先完成，各 2,688 筆與實驗 1 比對全部相同，才開始 jev 呼叫。

見 [前置核對](verification/p2-preflight.json)。`run_dev.py` 的 `--reference-experiment 1` 會再次核對切點／設定和四個基準，有差異就拒絕發 HTTP。

```sh
/usr/local/bin/python3 scripts/run_dev.py --execute \
  --db data/trendcast.sqlite3 --experiment 2 --reference-experiment 1 \
  --max-calls 3500 --report docs/verification/p2-run-1.json
```

實際由 `zsh -ic` 載入環境金鑰，並取消 `SSL_CERT_FILE`。首輪 **2,688 次 HTTP**，成功 2,686 點，2 點因 `choice_not_maximum` 拒收，沒有放寬驗證。第二輪把上限改成剩餘的 **812**，只補缺答，**2 次 HTTP 均成功**，既有 2,686 點跳過。

總計 **2,690 次 HTTP**、429／529 自動重試 **0 次**，歷史失敗 **2 次**，最終缺答 **0**。兩次 runner 合計 154.685 秒，不含前置檢查與基準預跑。見 [首輪](verification/p2-run-1.json)、[補跑](verification/p2-run-2.json) 與各自的 [首輪進度](verification/p2-run-1-progress.jsonl)、[補跑進度](verification/p2-run-2-progress.jsonl)。首輪報告是不完整中間結果，最終數字以上方獨立核對為準。

執行前後核對實驗 1 的 row 與開發段 predictions／outcomes SHA-256，均未改變；實驗 2 outcomes 與實驗 1 也完全一致。核對程式僅以活動 metadata 確認保留段沒有回放／揭露，沒有讀取保留段行情或標籤。

## 實作與測試

- `back/prompts.py`：固定 p1 原文及 p2 三段文字。p2 常數出自實驗 1 開發段 outcomes：flat 1,361、up 662、down 665，除以 2,688 後以 ROUND_HALF_UP 四捨五入為 51／25／25。來源實驗、日期範圍與門檻寫在註解；約數合計 101%，依 SPEC 保留，不當作回應機率向量。[來源核對](verification/p2-source.json)
- `back/experiment.py`、`back/runtime.py`：建立實驗可明確指定版本，未指定仍為 p1；不支援的版本在寫入前拒絕。版本保存在原 experiments 欄位並納入摘要。
- `back/jevcast.py`：從 run 綁定的實驗讀 prompt_version，逐點傳給 client。f1 state、criteria、傳輸及回應驗證規則維持不變。
- `scripts/run_dev.py`：以假伺服器整合測試覆蓋 `--experiment 2` 的 CLI 呼叫；新增 `--reference-experiment` 的 API 前檢查。CLI 仍要求 `--execute` 且只能跑 dev。
- `tests/fixtures/p1_http_payload.json`：在修改前，從 commit `4c9030f09df1f21ad09aa53ceff4668f2e5f70dd` 的 p1 程式取得合成資料 HTTP body。測試確認預設及明確 p1 的實際送出 body 都逐 byte 相同。
- `tests/test_prompts.py`、`tests/test_run_dev.py`、`tests/test_runtime.py`：涵蓋 p2 三段欄位說明／比例、實際 payload 不含代號／日期／絕對價格、state 與 criteria 相同、版本路由、摘要及切點、基準不一致時零 HTTP、重跑零 HTTP。

完整測試：[Python 209 項全過](verification/p2-unittest.txt)、[前半 22 項全過](verification/p2-front-tests.txt)。

新增 [p2 變異 11/11 轉紅](verification/p2-mutations.txt)，包括改預設 p1、拿掉欄位說明、錯誤比例、加入代號、版本未驗證／未凍結、runner 或 client 忽略 p2、CLI 忽略實驗 id，以及移除切點／基準比對守衛。變異程式為 `scripts/mutation_p2.py`。

受影響既有變異重跑：[實驗／jev 40/40](verification/p2-regression-experiment-mutations.txt)、[開發段 runner 8/8](verification/p2-regression-run-dev-mutations.txt)、[接線 32/32](verification/p2-regression-runtime-mutations.txt) 全部轉紅。本次共 91 組。所有變異只操作暫存副本。
