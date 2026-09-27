# 自主進化第 1 輪：§14.2 前瞻段

已實作固定實驗 1／p1 的前瞻回放、零缺答才可揭露、累積報告與前半操作。**本輪真資料只唯讀查日期，沒有執行前瞻方法、沒有揭露，也沒有真 API 呼叫。** 等 cc 確認日期並下令後才跑。分支為 `evolve/2026-09-27`，沒有 commit／push，未修改 `news/` 或殼。

## 真資料日期清單

來源 `data/trendcast.sqlite3`，限定實驗 1 的 `hold_end` 之後，排除 `exposed_days`。只查日期、分 K 時戳與收盤到齊狀態，沒有讀取前瞻價格欄位、outcomes、預測或標籤分布。本次沒有已曝光日期需要排除。

```text
2026-09-01
2026-09-02
2026-09-03
2026-09-04
2026-09-07
2026-09-08
2026-09-09
2026-09-10
2026-09-11
2026-09-14
2026-09-15
2026-09-16
2026-09-17
2026-09-18
2026-09-21
2026-09-22
2026-09-23
2026-09-24
```

[唯讀日期輸出](verification/evolve-1-forward-dates.json) 可用以下命令重現；此命令沒有 execute 或 reveal 開關，不載入金鑰：

```sh
/usr/local/bin/python3 scripts/forward_dates.py --db data/trendcast.sqlite3
```

## 實作約定

- **日期選擇**：必須晚於 `hold_end`、未曝光且已過收盤時間；日 K 存在，並有收盤分 K，或所屬月份已定稿且有分 K。盤中缺分鐘不補值、不以完整 266 根作額外篩選，仍由 §5 逐點決定可預測／可評分。新增交易日會出現在下一次日期預覽及回放。
- **固定方法**：只接受實驗 1、p1、f1、原 model、2330、3‰ 與原映射。回放前驗證實驗原摘要，為每個前瞻日保存原設定與包含前 25 日輸入來源的摘要。新增前瞻範圍也受 Store 的凍結覆寫守衛保護。majority 只使用原開發段頻率；不吸收保留段或前瞻標籤。
- **回放**：一次工作依序跑四基準，再跑 6 並發 jev；整段持有同一個 ActivityGate，停止會取消當前工作並丟棄晚到結果。原有答案保留，重跑只補缺答；跑完不自動揭露。
- **揭露**：另一次明確確認後，在寫入交易中重新驗證來源、曝光狀態、每個有效答案、outcomes 與所有方法的完成紀錄。缺答、非法機率、資料變動、曝光變動或未完成都拒絕。每個新揭露交易日寫入一筆精確日期範圍的 `reveals`，`segment='forward'`；避免跨越排除日而一併揭露。重複揭露不重複寫入。
- **累積與新增日期**：已揭露日期留在累積前瞻報告，並永久算已曝光；新日期先保持鎖定。其他來源在回放後、揭露前使日期曝光時，拒絕揭露，下一輪回放將該日排除並留紀錄。既有累積報告不受尚未揭露的新日、run 狀態或失敗數影響。
- **出口**：未揭露的前瞻區塊只有固定鎖定訊息；`day` 不回行情。`status` 與前半即使已有部分揭露，也不顯示前瞻回放計數，避免洩漏新增日。舊 `check_data.py` 品質統計限至該實驗 `hold_end`，前瞻指標統一由專屬報告提供。
- **比較**：沿用 §7.2 的開發段基準選法、交易日配對 bootstrap 2,000 次／種子 `20260927`、四種結論用語。原開發段共同交集不足 95% 時也不下前瞻結論。保留段在畫面永久標示「保留段已使用」，只作歷史描述。
- **額度**：所有正式 JevClient 傳輸（包含既有 CLI 的真正 HTTP）在送出前，向獨立 SQLite 帳本原子取得額度；全場 3,000 次，包含重試，跨程序／重啟保留。帳本為 `data/evolve-2026-09-27-budget.sqlite3`，複製實驗 DB 不會重置。額度或帳本不可用時拒絕發請求。

## 介面與相容性

後端新增 `run_forward`、`reveal_forward`，都要求 `confirmed: true`、目前選定實驗 1；不接受 method、prompt、threshold 等覆寫欄位。殼初次載入預設顯示定案的實驗 1，原有新實驗綁定行為保留。

前半新增「前瞻段」成績區與「跑前瞻段」「看前瞻段結果」，兩者各有確認步驟。揭露後顯示已揭露範圍的累積交易日、可預測且可評分點數與完整成績。其他實驗的前瞻按鈕停用；配色與排版沿用既有主題樣式。

寫入連線會將舊 `reveals` 加上 `segment`，原紀錄預設為 `holdout`，不改動既有存取權。尚未遷移的唯讀 DB 仍可預覽日期及讀舊報告。本輪沒有對真 DB 做遷移；遷移、回放及揭露只在合成資料庫測試。

## 檔案與驗證

- 新增 `back/forward.py`、`back/forward_runner.py`、`back/evolution_budget.py`、`scripts/forward_dates.py`。
- 更新 `back/store.py`、`back/replay.py`、`back/experiment.py`、`back/jevcast.py`、`back/jobs.py`、`back/score.py`、`back/report.py`、`back/runtime.py`、`scripts/check_data.py`、`front/front.js`、`README.md`。
- 測試為 `tests/test_forward.py`、`tests/front.test.mjs`；資料表與假傳輸隔離調整在 `tests/test_store.py`、`tests/test_jev_client.py`、`tests/test_tls.py`。
- 新增 `scripts/mutation_forward.py`，並擴充 `scripts/mutation_front.py`；`scripts/mutation_runtime.py` 只調整受接線變更影響的精確變異定位。

完整輸出：[Python 231 項](verification/evolve-1-unittest.txt)、[前半 26 項](verification/evolve-1-front-tests.txt)。重點測試涵蓋日期查詢禁止讀價格欄位、已曝光／保留段排除、收盤／盤中缺漏、新日延長、固定設定與來源、所有出口隔離、累積報告不受新日污染、完成與提交守衛、取消、舊 schema、額度並行／持久化／重試，以及正式傳輸不能被原有 CLI 的 hook 繞過。

變異：[前瞻後端 25/25](verification/evolve-1-mutations.txt)、[前半 27/27（含本輪新增 6 組）](verification/evolve-1-front-mutations.txt)、[計分回歸 43/43](verification/evolve-1-score-mutations.txt)、[接線回歸 32/32](verification/evolve-1-runtime-mutations.txt)，共 **127/127** 轉紅。各輸出逐條列出拔除的守衛與轉紅測例，全部只操作暫存副本。

額度隔離檢查曾抓到既有 TLS 假傳輸測試誤記正式帳本：沒有真 HTTP，誤記的 18 次已保存在 `data/evolve-1-mocked-transport-budget.sqlite3`，測試改用明確的暫存帳本。修正後完整測試不建立或修改正式帳本，真額度尚未使用。[隔離修正紀錄](verification/evolve-1-budget-test-isolation.json)、[唯讀與零呼叫核對](verification/evolve-1-readonly-proof.json)。
