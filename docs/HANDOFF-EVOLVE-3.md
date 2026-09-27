# 進化第 3 輪：固定 vol_prior 前瞻推論與前半

實驗 1 的 vol_prior 前瞻推論已完成：**144 點，缺答 0**。沒有揭露，沒有產生或查看真前瞻段成績、標籤或 choice 分布。禁止網路 client 驗證 HTTP 為 0；執行與殼驗收後 reveals 及 TypeSafe 帳本皆未增加。未 commit，未改 news 或殼的檔案。

## 固定模型與揭露守衛

- 新增 `back/evolution_forward.py`。使用實驗 1 開發段的可預測且可評分觀測，成熟截止為開發段最後一天 13:30，包含最後 13:00 點的已揭曉標籤。切點、三組計數、majority fallback 計數及算法設定存在 `evolution_models`；同時保存開發來源 digest。
- 三分位、母體標準差、29 個簡單報酬、切點相等歸低組、30 筆門檻、加 1 平滑、平手順序，完全沿用 §14.3。後續新增前瞻日仍核對同一份凍結模型，來源或參數不符即拒絕，不能覆寫。
- `scripts/run_forward_vol.py --execute --experiment 1` 只跑已納入的前瞻日期，不呼叫 jev、不讀前瞻 outcomes、不新增 outcomes、不揭露。實際執行另移除兩把 API key，封鎖網路及非授權資料寫入，並把 `Replay.outcome` 限制為 dev 呼叫；輸出僅執行計數。取消會回滾模型／預測寫入；重跑核對既有答案，不重複寫入。
- 殼的既有「跑前瞻段」工作已納入 vol_prior，原五方法行為不變。揭露必須六方法皆完成且零缺答；vol_prior 的答案與機率還要符合凍結模型。
- 揭露後固定報告 **jev p1 − majority**、**vol_prior − majority**，同一個六方法交集，兩組 Brier 差與交易日配對 bootstrap 都輸出，無論結果有利或不利。各自沿用 §7.2 用語。新比較只在合成資料揭露測試中驗證，真資料沒有進入此計分路徑。

## 前半與隱私出口

- 成績區新增「進化新方法（開發段）」：三個新方法的準確率、Brier、對 majority 差值及區間、§14.3 判讀；固定顯示「開發段勝出＝值得前瞻驗證，不是證明有效」。資料來自已存 dev 預測，檢視不回放、不寫入。
- 前瞻區顯示六位考生、已跑點數與未揭露狀態。`status.forward.run_points` 只數六方法皆有紀錄的時點主鍵，不讀答案、機率、outcomes 或價格；這是本輪明確授權顯示的執行資訊。
- `report.forward` 在未揭露時仍只有固定 `state/message`，不帶成績或分布；前半也要求 report 與 status 都已揭露才渲染雙比較。`day` 仍受既有揭露守衛保護。
- 保留段成績與操作區固定標示「保留段已使用」。截圖中的保留段是原已揭露的舊結果，本輪沒有新增揭露。

## 驗證

- [Python 完整輸出：271 項通過](verification/evolve-3-unittest.txt)
- [前半完整輸出：31 項通過](verification/evolve-3-front-tests.txt)
- [新前瞻變異：22/22](verification/evolve-3-mutations.txt)
- [前半變異：34/34](verification/evolve-3-front-mutations.txt)
- [前瞻回歸變異：25/25](verification/evolve-3-forward-regression-mutations.txt)
- [計分回歸變異：43/43](verification/evolve-3-score-regression-mutations.txt)

合計 **124/124** 組變異轉紅。涵蓋訓練混入未來、漏掉最後開發點、錯誤分位與同值分組、樣本門檻、凍結來源／參數守衛、前瞻標籤讀取、自動揭露、取消、缺少／錯誤 vol 答案、未揭露計分、比較方向與 bootstrap、只報有利結果、前半用語與跨實驗隔離。

[實際離線執行及驗收後不變量](verification/evolve-3-execution.json) 記錄禁止網路、前瞻 outcome 呼叫為 0、reveals 不變、帳本不變與寫入範圍。

## 真殼

在 `~/Code/modudock/shell` 另開 `127.0.0.1:55354`，以 `zsh -ic` 載入環境後執行 `unset SSL_CERT_FILE`，並檢查該變數未設定，再執行：

```sh
go run ./cmd/modudock -addr 127.0.0.1:55354 \
  -modules /Users/gatewenlee/Code/modudock-modules \
  -settings /Users/gatewenlee/Code/modudock-modules/trend-cast/data/evolve-3-shell-settings.toml
```

使用模組 data/ 下獨立設定檔，沒有改使用者的殼設定。Playwright 只載入 trend-cast、只送 status/report/開發段 day；送出其他模組操作會被驗收腳本拒絕。自動同步顯示完成，catalog 與 running 狀態正確，開發新方法三列、前瞻未揭露／已跑點數、保留段已使用都通過斷言；兩種主題無水平溢出、無頁面錯誤。截圖已逐張檢視，驗收殼已關閉。

- [殼驗收證據](verification/evolve-3-shell-evidence.json)
- [淺色截圖](verification/evolve-3-light.png)
- [深色截圖](verification/evolve-3-dark.png)

重現工具：`node scripts/check_evolution_shell.mjs http://127.0.0.1:<另一個 port>`。初始尚未綁定實驗的舊 report 可能屬於實驗 2；驗收與前半一致，忽略非目前實驗的回應。

## 檔案

新增：`back/evolution_forward.py`、`scripts/run_forward_vol.py`、`scripts/check_evolution_shell.mjs`、`scripts/mutation_evolution_forward.py`、`tests/test_evolution_forward.py`、本文件與驗證附件。

更新：`back/evolution_run.py`、`back/forward.py`、`back/forward_runner.py`、`back/report.py`、`back/runtime.py`、`back/score.py`、`back/store.py`、`front/front.js`、`front/style.js`、`scripts/mutation_front.py`、`tests/front.test.mjs`、`tests/test_forward.py`、`tests/test_store.py`。
