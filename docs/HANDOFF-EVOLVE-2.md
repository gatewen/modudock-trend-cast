# 進化第 2 輪：開發段 walk-forward 方法

依 SPEC §14.3（`10c779d`、`c36c2fd`）實作；只處理實驗 1 的開發段。不改既有方法、不呼叫 jev、不揭露任何新區段。

## 跑真資料前固定的口徑

- 學習集合：實驗 1 開發段內、可預測且可評分、`t′ + 30 分鐘 ≤ t` 的觀測。三個方法及 majority fallback 都遵守相同時間與資格限制。
- `clock_prior`：8 個時段各自累積；同時段至少 30 筆才使用組內加 1 平滑。
- `vol_prior`：最近 30 根已可見收盤形成 29 個簡單報酬，取母體標準差。每個 t 用已揭曉觀測重算線性插值 1/3、2/3 分位，也按新切點重新歸組所有已揭曉觀測。切點相等時歸較低組；某組不足 30 筆或當下不足 30 根 K 線時退回 majority。重複波動值不強迫拆組。
- `jev_calibrated`：以**當下既有** jev p1 choice 選擇校準表的一列；該列只計已揭曉點的「既有 jev choice、真實標籤」。列樣本至少 30 筆才使用加 1 平滑。缺當下 jev 答案就記缺答，不補發 HTTP、不虛構 choice。
- 機率 `(類別計數 + 1)/(組內樣本數 + 3)`；平手依 `flat → up → down`。不足樣本時直接沿用原 `Baselines.majority` 的答案與機率。
- 比較集合：三個新方法加既有五個方法皆有有效答案的**同一批**可預測、可評分開發時點。Brier 差方向為「新方法 − majority」，bootstrap 直接沿用 `back.score.paired_bootstrap`（交易日成對區塊、2000 次、種子 20260927）。
- 晉級：先篩出差值 95% 區間整段 < 0 的方法，再選其中開發段 Brier 最低者，最多一名；無符合者為「無晉級」。此解讀已由 cc 在跑之前確認。完全同分時依規格表順序。比較交集不足 95% 或回放未完成，不作結論。

## 實作與重跑

- `back/evolution.py`：純本機方法；不可對非 dev 時點預測，未成熟觀測先過濾才讀取標籤／校準 choice／波動值。
- `back/evolution_run.py`：只查詢暖機至 dev_end 的原始資料、dev 的 outcomes／既有 predictions；不呼叫會讀完整保留段的 `load_plan`／`verify_digest`。保留段端點僅使用 experiments 的設定作 ReplayPlan 的惰性邊界，不讀取該段行情或預測。報告另記本次開發資料與來源預測的 digest。
- 新方法沿用原點的 f1 `input_json/input_hash`，學習規則固定於新模組；波動計算依原始已可見 K 線，來源精度與訓練預測由報告的 `source_digest` 一併記錄。
- 三方法的 predictions／runs／run_scopes 在同一交易提交；不修改既有 outcomes 或 predictions。重跑比對已存答案及機率，不一致就拒絕覆寫。
- `ForbiddenClient.guard` 封鎖 JevClient、HTTP 傳輸入口、DNS 與 socket 連線，任何意外呼叫都中止交易。真資料執行不需要任何 API key。
- 可用 `/usr/local/bin/python3 scripts/run_evolution.py --execute --experiment 1 --split dev --output data/evolve-2-dev.json` 重跑；其他實驗或 split 會拒絕。

## 驗證

- `tests/test_evolution.py`：未來資料隨機替換、成熟邊界、8 個時段、29/30 樣本、波動定義／分位／分組、平滑與平手、校準表方向。
- `tests/test_evolution_run.py`：禁止網路、只讀寫 dev、隱藏資料亂改不影響報告、原子回滾、重跑無寫入、共同比較與晉級規則。
- `scripts/mutation_evolution.py`：每組在暫存副本拔掉或改壞一個行為，再跑指定測例；完整對照存於 `docs/verification/evolve-2-mutations.txt`。

完整 Python 回歸 **261 項通過**；兩個額外訓練後守衛斷言另跑通過；**41/41 組變異轉紅**。完整輸出：

- [unittest](verification/evolve-2-unittest.txt)
- [訓練後守衛測例](verification/evolve-2-trained-guards.txt)
- [變異名稱 → 測例對照](verification/evolve-2-mutations.txt)

## 真資料結果（只有開發段）

實驗 1，2024-09-05～2026-01-21。每個新方法新增 **2,688** 筆 predictions，缺答 0；八方法共同交集 **2,688** 點，覆蓋率 **100%**。

| 方法 | 準確率 | Brier | Brier 差（新方法 − majority） | 交易日配對 bootstrap 95% 區間 | §14.3 用語 |
|---|---:|---:|---:|---|---|
| majority（對照） | 50.6324% | 0.623241412 | — | — | — |
| clock_prior | 50.1116% | 0.617460067 | −0.005781345 | [−0.010075097, −0.001568826] | 值得前瞻驗證 |
| vol_prior | 50.5580% | 0.609984567 | −0.013256845 | [−0.020375156, −0.006377449] | 值得前瞻驗證 |
| jev_calibrated | 50.6324% | 0.621326790 | −0.001914622 | [−0.005155520, +0.001095747] | 沒有改善 |

依事先確認的規則，晉級者為 **`vol_prior`**：它在合格者中開發段 Brier 最低。本輪沒有對它執行保留段或前瞻段，沒有揭露。

HTTP **0**；執行時移除兩把 API key，`ForbiddenClient` 封鎖請求入口及連線。帳本執行前後均為 **144/3,000**。

真資料執行另加 TEMP triggers，只准新增實驗 1、三個新方法、dev 日期內的 predictions，以及對應 runs／run_scopes；authorizer 禁止所有 UPDATE／DELETE、來源資料寫入、reveals 存取及前瞻結果欄位存取。首次嚴格封鎖連 SQLite 內部外鍵 `forward_attempts.run_id` 檢查也擋住，交易整體回滾（新預測 0）；僅放行這個 ID 欄位的結構檢查後重跑成功。沒有放行前瞻的日期、失敗數、答案或成績。

- [完整開發段報告 JSON](verification/evolve-2-dev.json)（包含各方法 Wilson、混淆矩陣、各類指標及 bootstrap 設定）
- [獨立 SQL 核對](verification/evolve-2-sql-check.json)：直接由已存 predictions/outcomes 重算四方法準確率與多類 Brier，與報告一致。
- [禁止網路與寫入範圍證據](verification/evolve-2-execution-proof.json)

本輪只新增 `back/evolution.py`、`back/evolution_run.py`、`scripts/run_evolution.py`、`scripts/mutation_evolution.py`、兩個測試檔及本交件文件／驗證附件。沒有改既有方法、前半、news 或殼；沒有 commit。
