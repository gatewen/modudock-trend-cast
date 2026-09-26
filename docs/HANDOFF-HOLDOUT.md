# 實驗 1（p1）期末考

**結論：jev 比 majority 差。** 保留段 2026-01-22～2026-08-31，共 145 個交易日、1,160 點；五個方法皆完整作答，才提交一次完整揭露。**解鎖前已曝光 4 日重疊**，報告照實標示。實驗 2 的保留段沒有回放或揭露；兩個實驗的開發段紀錄均未改變。沒有 commit，也沒有修改 `news/`。

## 結果

候選、可預測、可評分、可預測且可評分、五方法共同交集均為 **1,160 點**。每個方法覆蓋率 **100%**、最終缺答 **0**。

| 方法 | 正確數／樣本數 | 準確率 | Wilson 95% | 多類 Brier |
|---|---:|---:|---|---:|
| jev（p1） | 577／1,160 | 49.7414% | [46.8697%, 52.6148%] | 0.6129183620689655 |
| always_flat | 783／1,160 | 67.5000% | [64.7507%, 70.1337%] | 0.6500000000000000 |
| majority | 783／1,160 | 67.5000% | [64.7507%, 70.1337%] | 0.5343331464359580 |
| momentum | 628／1,160 | 54.1379% | [51.2615%, 56.9870%] | 0.9172413793103448 |
| reversal | 655／1,160 | 56.4655% | [53.5956%, 59.2927%] | 0.8706896551724138 |

主要比較沿用開發段選定的 **majority**：jev − majority 的 Brier 差 **+0.0785852156330076**，95% 區間 **[+0.0591321157142170, +0.0966772613937811]**。使用 145 個交易日區塊的配對 bootstrap、2,000 次、固定種子 `20260927`。區間完全大於 0，照 §7.2 用語為「**jev 比 majority 差**」。

次要準確率差為 **−17.7586 百分點**，95% 區間 **[−21.4655, −14.1358] 百分點**。

| jev choice | 次數 | 比例 | 該 choice 命中率 | 同批真實比例 |
|---|---:|---:|---:|---:|
| up | 15 | 1.2931% | 6.6667% | 16.0345% |
| flat | 717 | 61.8103% | 68.6192% | 67.5000% |
| down | 428 | 36.8966% | 19.6262% | 16.4655% |

[完整報告](verification/holdout-report.md) 包含開發段與保留段的各類精確率／召回率、混淆矩陣、覆蓋率、失敗數及描述性分布；[原始 JSON](verification/holdout-run.json) 保留完整精度。

## 執行與獨立核對

在 `data/trendcast.sqlite3` 執行，事前核對實驗 1／p1、指定日期、凍結摘要、開發段 `majority` 選擇及 4 日重疊，見 [前置核對](verification/holdout-preflight.json)。

```sh
/usr/local/bin/python3 scripts/run_holdout.py --execute --experiment 1 --split holdout \
  --db data/trendcast.sqlite3 --max-calls 1500 \
  --report docs/verification/holdout-run.json \
  --markdown docs/verification/holdout-report.md
```

實際以 `zsh -ic` 載入環境金鑰，取消 `SSL_CERT_FILE`，使用系統 CA 備援並保持 TLS 驗證。**共 1,164 次 HTTP**：首輪 1,160 次，4 筆 `choice_not_maximum` 拒收；第二輪只補這 4 筆，全部成功。429／529 的 HTTP 自動重試 **0 次**。所有呼叫共用同一個 1,500 次額度，沒有放寬回應驗證。成功執行那一輪耗時 **198.711 秒**，含基準再遍歷與報告計算。

首次啟動沒有成功載入環境金鑰，在四個基準完成後、jev 建立 run 前停止；HTTP 為 0、沒有揭露。該次本地準備不包含在上述耗時，見 [啟動紀錄](verification/holdout-setup-attempt.jsonl)。正式執行見 [階段紀錄](verification/holdout-progress.jsonl)；解鎖前只記錄階段，不輸出保留段標籤或成績。

[獨立核對程式](verification/holdout-independent-audit.py) 在確認完整揭露後才讀取實驗 1 保留段，以唯讀 SQL、Decimal 重新計算 Brier，另寫交易日抽樣計算區間，不 import 計分實作。準確率、Wilson、Brier、區間與 choice 次數全部吻合。它也核對執行前後雜湊，確認實驗 2 全部既有紀錄及兩份開發段紀錄未改變，完整揭露只有 1 筆且在所有 run 完成之後。見 [獨立結果](verification/holdout-independent-results.json)。

真資料以 `--max-calls 0` 重跑，**0 次 HTTP**、不新增 run 或揭露，見 [重跑核對](verification/holdout-resume.json)。

## 檔案與守衛

- `scripts/run_holdout.py`：CLI 明確要求三個旗標；實驗 1／p1 與 1,500 次硬上限；先檢查開發段定案，再跑四基準與 6 並發 jev；額度涵蓋 HTTP 重試及缺答補跑。只寫指定實驗保留段的 predictions／outcomes。
- `back/experiment.py`、`back/jevcast.py`、`back/jobs.py`、`scripts/check_data.py`：共用硬守衛禁止其他實驗的保留段回放與揭露，殼入口同樣受限。最終完成檢查與揭露寫入共用交易；完整揭露可重複呼叫而不重複寫入。
- `back/store.py`、`back/report.py`：新增 `reveal_context` 儲存解鎖前重疊日數，避免揭露後把整段新曝光都算成原有重疊；報告列出該紀錄。既有唯讀 DB 沒有這張表時不誤報為 0。
- `back/jevcast.py`：額度用完只阻止新 HTTP，仍可遍歷已提交或不可預測的尾端時點，讓最後一筆剛好用完額度時能正確完成。
- `back/runtime.py`、`front/front.js`：實驗 2 保留段遭拒時顯示固定安全原因。
- `tests/test_run_holdout.py`、`tests/test_store.py`：新增 8 項期末考測試並更新資料表契約。
- `scripts/mutation_holdout.py`：新增 20 組變異；`README.md` 更新操作方式與定案限制。本交件文件及 `docs/verification/holdout-*` 為驗證產物，真資料仍只在忽略版控的 `data/`。

## 測試與變異

完整輸出：[Python 217 項全過](verification/holdout-unittest.txt)、[前半 22 項全過](verification/holdout-frontend.txt)。

新增測試覆蓋：缺少 `--execute` 不執行、只能指定 holdout／實驗 1、超過 1,500 拒絕、各種共用入口拒絕實驗 2、開發段選擇變更時拒絕、所有答案提交後才揭露、缺答／非法答案／缺 outcomes／outcome 不符／未遍歷完都不揭露、揭露提交失敗整筆回滾、同額度補答、最後一筆剛好用完額度、重跑零 HTTP，以及固定開發段基準與永久保存原有重疊日數。

[新增變異 20/20](verification/holdout-mutations.txt) 全轉紅；檔案逐條列出變異與轉紅測例：移除 execute／實驗／上限守衛、繞過共用回放或 labels 揭露入口、忽略開發段選擇、移除最終回呼或交易內檢查、忽略缺答／outcomes／遍歷狀態、接受局部 run 或錯誤標籤、回滾改提交、遺失重疊日數、在保留段重新選基準、恢復錯誤額度邊界，以及停止補答。

受影響回歸變異：[實驗／jev 40/40](verification/holdout-regression-experiment-mutations.txt)、[run_dev 額度與重跑 8/8](verification/holdout-regression-run-dev-mutations.txt)，合計 **68/68** 轉紅。所有變異皆在暫存副本執行。
