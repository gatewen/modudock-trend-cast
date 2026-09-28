# 第二場第 3 輪：多日保留段一次性考試

依 SPEC §16.5（37755a0）完成。**三個主要比較都沒有證據顯示考生比簡單方法好，沒有通過者；前瞻名單維持現狀。** 本場不再新增研究方法。本輪真 jev 0 次，帳本仍 **1,222／2,222**。未 commit、push；未修改 news／殼原始碼，未安裝排程。

## 結果

| 考生／天期 | n | Brier 差（考生 − majority） | 95% 區間 | §7.2 結論 |
|---|---:|---:|---|---|
| ens_avg／3 日 | 616 | −0.000077137 | [−0.002523971, +0.002420794] | 沒有證據顯示 ens_avg 比簡單方法好 |
| ens_avg／7 日 | 612 | −0.004053970 | [−0.008773728, +0.001002477] | 沒有證據顯示 ens_avg 比簡單方法好 |
| mkt_logit／3 日 | 616 | +0.005917197 | [−0.002109221, +0.014534748] | 沒有證據顯示 mkt_logit 比簡單方法好 |

使用完整保留段交易日曆切不重疊 20 日區塊，保留尾塊，共 31 塊；2000 次、seed=20260927。**3 個事先指定比較，預期約 0.075 個因運氣顯著較好**（名目估算，未作多重比較校正）。不增列其他優劣比較，也不換種子或門檻重考。

保留段 2022-01-03～2024-07-25，共 619 個交易日；實際最後交易日 2024-07-23。24 個離線方法 × 三天期均零缺答，共 **44,568 筆預測**、619 筆輸入。可評分 n＝616／612／605，共 **1,833 筆 outcomes**；最後 H 日終點超出保留段，僅保存預測、不取 2024-07-26 起已曝光資料計分。

[全部 24 方法 × 三天期描述性成績](verification/evolve2-3-holdout.md) 包含五基準、11＋5 單一指標、兩個 logit 及 ens_avg；只列描述，不另作結論。[完整 JSON](verification/evolve2-3-holdout.json) 另含 Wilson 區間、各類 precision／recall、混淆矩陣、覆蓋率、缺答、政策及封存摘要。

## 跑前查核與一次性揭露

[跑前查核](verification/evolve2-3-preflight.json)：現有 DB 的 d_features／d_fits／d_predictions／d_outcomes／d_jev_requests、market_* 結果、日線前瞻紀錄，以及實驗 4 的共用 predictions／outcomes／move_answers，在此保留段均 **0 筆**。沒有實驗 4 的保留段 runs、reveals，沒有重疊 prior_exposures，也沒有既存保留段／成績表。此為現存資料與紀錄的查核；既有原始行情早已隨實驗 4 凍結，不屬預測或標籤。

實際執行順序：

1. 寫入 `d_hold_models`：只取開發段，凍結政策、參數及跑前查核。
2. 取得 FinMind 保留段原始市場資料，另存 `d_hold_market_rows`／`d_hold_market_snapshot`。大盤 619 筆、TSM 643、SOX 643、匯率 627；第二輪 market_rows 與 market_digest 不改。
3. 產生全部保留段 features／predictions。此階段不呼叫任何保留段 outcome 計算；進度只輸出階段名稱，不輸出保留段數字。
4. 重算並逐值驗證所有 `(day,H,method)` 的固定模型答案、機率與輸入；三天期全齊後寫入 `d_hold_completion`，封存完整預測及輸入摘要。
5. 才計算保留段 outcomes；再次驗證預測，在同一交易寫 outcomes、`d_hold_seal` 與 reveals。失敗全部回滾。

已在 **2026-09-29T02:22:02+08:00** 寫入一筆 `reveals`：experiment_id=4、namespace=daily、segment=holdout、what=all、範圍 2022-01-03～2024-07-25。紀錄不可修改或刪除；跨實驗曝光檢查也納入此範圍。保留段永久標示「已使用」。

既有 reveals 的外鍵只指向 30 分鐘 experiments，因此新增 `reveal_experiments(namespace,id)` 登記表，以複合外鍵區分 intraday／daily。舊揭露紀錄全部保留，原欄位內容不改，外鍵驗證維持啟用；沒有假造一筆 30 分鐘實驗 4，也沒有關閉外鍵來塞入紀錄。新增／移除原實驗時由 trigger 維護登記，原 d_experiments 仍不可改寫。

## 凍結口徑

- majority：完整可評分開發段的標籤頻率＋Laplace 1，三天期樣本 2,891／2,887／2,880。
- vol_prior_d 與 16 個 ind_*：完整可評分開發段的切點與各狀態條件頻率；至少 30 筆，缺值或不足則退回已凍結 majority。
- ind_logit／mkt_logit：直接使用各自最後一筆持久化擬合，**均為 2021-12-13**，各 H 訓練 n＝2,878／2,874／2,867。權重、均值、母體標準差與分類切點全部逐值保留，沒有用完整開發段再重訓一次。
- ens_avg：每個時點平均上述固定 majority、vol_prior_d、ind_logit 的三類機率，平手 flat→up→down。
- always_flat／momentum_H／reversal_H 沿用原規則與固定 k_H。特徵仍依該預測日可用行情計算；市場對齊、中價、5 日年齡、1 ADR＝5 股等完全沿用 §16.3。
- `FrozenDaily` 只接收參數及當下 Frame，沒有讀 DB、讀 outcomes、訓練或更新介面。

## 驗證

- [完整 unittest](verification/evolve2-3-unittest.txt)：**463／463**，197.142 秒；[新增保留段測試](verification/evolve2-3-targeted.txt)：12／12；[前半](verification/evolve2-3-front-tests.txt)：**53／53**。皆在真考試前通過。
- [保留段變異](verification/evolve2-3-mutations.txt)：18／18；[新增前半變異](verification/evolve2-3-new-front-mutations.txt)：2／2；[既有前半](verification/evolve2-3-front-mutations.txt)：13／13；[市場回歸](verification/evolve2-3-market-regression-mutations.txt)：32／32；[出口變異錨點更新](verification/evolve2-3-view-mutation.txt)：1／1；合計 **66／66** 被抓到。
- 重點覆蓋：擾動保留段標籤後所有固定預測不變；以 SQLite authorizer 禁止預測階段讀 holdout outcomes；重訓／取錯 fit／只用部分開發段頻率會轉紅；任何 H／方法缺答或缺完成封存時，第一個 outcome 計算前就拒絕；揭露交易失敗不留 outcomes；CLI／UI 出口與進度均不能提前輸出數字。
- [真 DB 揭露前出口查核](verification/evolve2-3-locked-exits.json)：正式報告與獨立稽核 CLI 均拒絕，未建立任何報告檔。
- [獨立 SQL／算術驗算](verification/evolve2-3-independent-audit.json)：3／3 差值與 bootstrap 區間一致；六組 logit 參數與最後 fit 逐值相同，majority 與 DB 全開發段頻率一致。原 data_digest／dev_digest／market_digest 及七張開發結果表的筆數與 SHA-256 全部不變，foreign_key_check 為空。
- [重跑](verification/evolve2-3-resume.txt) 與 [最終核對](verification/evolve2-3-final-check.json)：已揭露後完全略過執行，報告除 execution 欄外逐值一致；沒有新預測、重訓、重抓市場資料或第二筆 reveals，帳本仍 1,222。
- [真殼瀏覽器](verification/evolve2-3-shell-browser.json)、[收尾證據](verification/evolve2-3-shell-evidence.json)、[畫面](verification/evolve2-3-shell-browser.png)：scratch 中的模組與 DB 副本，另開 **127.0.0.1:58983**，3／7／14 日均顯示已使用；只有讀取操作，頁面錯誤 0、資料／後半私有路徑 404。沒有金鑰，模組網路嘗試 0。殼 exit 0、port 已關、scratch 已刪除；未動 8731。

## 重現

已揭露結果只需唯讀輸出：

```sh
env -u FUGLE_API_KEY -u TYPESAFE_API_KEY \
  /usr/local/bin/python3 -B scripts/run_daily_holdout.py --report-only \
  --db data/trendcast.sqlite3 --report data/daily-holdout-report.json
/usr/local/bin/python3 -B scripts/audit_daily_holdout.py \
  --db data/trendcast.sqlite3 --report docs/verification/evolve2-3-holdout.json \
  --before docs/verification/evolve2-3-development-before.json \
  --output data/daily-holdout-audit.json
```

`--execute` 為本次已授權的一次性入口；已揭露時只讀既有結果。預設沒有執行旗標時只預覽；`--report-only` 在未揭露時拒絕。原 d_*／market_* 開發段寫入守衛維持不變，保留段獨立於 `d_hold_*`，沒有擴張既有開發段出口。
