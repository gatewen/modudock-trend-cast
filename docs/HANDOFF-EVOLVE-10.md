# 第 10 輪：獨立每日前瞻腳本

已完成。真資料於未設定 `SSL_CERT_FILE` 的環境成功同步；**新增預測 0、計分 0、jev HTTP 0、帳本 1,222／3,000**。沒有安裝／載入 launchd，沒有 commit，沒有修改 news 或殼程式。

## 執行與共用邏輯

```sh
/usr/local/bin/python3 scripts/daily_forward.py
```

預設 DB：模組下的 `data/trendcast.sqlite3`；可帶 `--db`。腳本不依賴目前工作目錄，也不需要啟動殼。它建立單次、無背景輪詢的 `DailyForwardService`，共用同一套增量同步、凍結設定檢查、預測、429／529 重試與到期計分。

`FUGLE_API_KEY`、`TYPESAFE_API_KEY` 僅讀環境；缺任一把，輸出 `missing_keys` 摘要且不開 writer／不發請求。腳本不讀 `.zshrc`、沒有金鑰 CLI 參數、摘要不包含原始例外。真驗證的外部啟動程序只把使用者既有的兩個 export 載入子程序環境，沒有把金鑰寫入檔案或印出。

摘要單位：一天四方法 × 三天期＝12 筆 `new_predictions`；一日起點三天期到期＝3 筆 `scored_outcomes`；`jev_http_calls` 包括本次重試；`ledger_used` 為共用帳本累計。只有提交成功的資料列才計數。`missing_jev_days` 提醒已有紀錄但 jev 未完成的日數。`complete`／`busy`／`missing_keys` 退出碼 0；`partial`／`error`／`no_experiment` 退出碼 1。

## 併行與持久化

- 殼與腳本共用 `back/daily_lock.py` 的 `flock(LOCK_EX | LOCK_NB)`，持有整段「同步 → 預測 → 計分」。CLI 在開 DBWriter 前取得鎖，排空並關閉 writer 後才釋放。
- 鎖檔由 DB 的解析後路徑決定，符號連結不會產生第二把鎖；權限 0600。鎖檔保持同一 inode，不刪除；程序結束或中止後由核心釋放。已有持有者時回 `busy`，不等待、不發 jev。
- 原有逐日持久化保留與共用帳本仍在。即使程序在保留後中斷，下一次也不盲目重送不確定的請求。正常完成後再執行，同一天是 0 次 HTTP。
- 這把鎖協調每日前瞻工作；舊 30 分鐘表的其他工作仍使用原有 SQLite 交易。使用者更新後須重載正在跑的 trend-cast 後半，既有舊 Python 程序不會自動換成新程式。

## launchd 範本

[範本](launchd/com.gatewen.trendcast.daily.plist.example) 已通過 `plutil -lint`，15 個 `StartCalendarInterval`：週一至週五各 16:45／17:30／20:30。不設 `RunAtLoad`，`KeepAlive=false`，不含 API key。平日休市仍可能啟動，但只有資料來源確認的新交易日起點才產生預測；已完成日不重送。

[README 的排程一節](../README.md#不開殼的每日排程) 說明絕對路徑、台北時區、將既有環境傳給使用者 launchd、登入／睡眠限制、日誌位置及使用者自行安裝／移除的命令。這些命令只是文件，**本輪未執行**。20:30 可補已公布的盤後籌碼；若資料源尚未更新，下一次再補，當日預測仍只用前一交易日籌碼。

## 真資料

[完整證據](verification/evolve-10-real.json)：單次執行 25.06 秒、退出碼 0、stderr 為空。日 K、交易日曆、TWT49U、法人、融資券五類皆 HTTP 200／written；新增 5 筆 fetch log。原開發段凍結 digest 不變。只有同步資料與 fetch log，沒有新前瞻預測或 outcome。

stdout 的一行摘要：

```json
{"status":"complete","new_predictions":0,"scored_outcomes":0,"jev_http_calls":0,"ledger_used":1222,"ledger_limit":3000,"missing_jev_days":0}
```

## 測試與變異

- Python **385 項全部通過**：[完整 unittest 輸出](verification/evolve-10-unittest.txt)。
- 本輪 **11 項**：[專項輸出](verification/evolve-10-cli-tests.txt)。跨程序測試讓「殼」保留並送出假 HTTP，在回應前啟動 CLI，確認撞鎖；放行後只提交一次 12 筆，再跑 CLI 是 0 次 HTTP。另測反向撞鎖、程序終止後釋放、符號連結、缺各把金鑰、無新交易日、摘要計數、到期計分、例外遮蔽與單次執行不開背景執行緒。
- 新變異 **14／14 轉紅**：[逐條守衛與測試對照](verification/evolve-10-mutations.txt)。涵蓋拔鎖、不同別名鎖、錯誤刪鎖、CLI 開 writer 前少鎖、殼少鎖、移除金鑰守衛、略過同步、誤報累計為新增、漏算 HTTP／計分、重送不確定保留、誤開輪詢、洩漏原始例外。
- 第 9 輪後端回歸變異 **26／26 轉紅**：[輸出](verification/evolve-10-forward-regression-mutations.txt)。前半未改。

```sh
/usr/local/bin/python3 -m unittest discover -v
/usr/local/bin/python3 -m scripts.mutation_daily_forward_cli
/usr/local/bin/python3 -m scripts.mutation_daily_forward
plutil -lint docs/launchd/com.gatewen.trendcast.daily.plist.example
```

## 檔案

新增 `scripts/daily_forward.py`、`back/daily_lock.py`、`tests/test_daily_forward_cli.py`、`scripts/mutation_daily_forward_cli.py`、launchd 範本、本說明與 `docs/verification/evolve-10-*`。

修改 `back/daily_forward_service.py`（共用檔案鎖、單次模式及提交後摘要）、`back/daily_forward_client.py`（唯讀帳本摘要）、`README.md`。
