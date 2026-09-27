# 第 12 輪：關閉生命週期、0.3.0 與文字一致性

## 根因與修正

根因為 `daily-forward-worker`，不是這個測例中的 news 廣播。`_on_sync(status=complete)` 會喚醒每日前瞻；舊 `close()` 只設 cancel，`_cycle_summary()` 隨後仍會為 counts／missing 開啟唯讀 `DailyStore`。唯讀 WAL 連線也會建立／移除 WAL、SHM 附檔；原 teardown 等了 db-writer、jev、sync、view-reader，**漏等每日前瞻執行緒**，因此與 TemporaryDirectory 清除競爭。

另找到兩條相關缺口：DBWriter 舊 close 會繼續執行佇列中的工作；每日前瞻 HTTP 回來後，budget.finish 還能另開連線寫帳本。這輪一併封住。

- 新增 `DatabaseGate`：把連線建構、操作與關閉都納入生命週期；取消後拒絕新連線、新語句與 commit，中斷進行中的 SQL。連線由原擁有者執行緒 close，等待屏障確認連線已真正釋放。
- 單一語句的外部鎖等待改以 25 ms 輪詢取消，保留原本總等待上限；不重播可能部分完成的 executescript／executemany。主寫入交易已有 BEGIN IMMEDIATE 鎖定。跨執行緒 interrupt 的 API 依 [Python 官方 sqlite3 文件](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.interrupt)；另外實測單靠 interrupt 無法即時解除既有 busy wait，所以需要短等待。
- `DBWriter.close(abort=True)` 即刻停止收件、取消未開始的 Future，取消 callback 在佇列鎖之外執行，避免重入死鎖；未提交交易回滾。CLI 正常完成後仍可使用原本 graceful close。
- Runtime 關閉先取消所有入口，等 DBWriter 與唯讀連線收尾後才送 done；連線收尾＋stdout 共用 **0.8 秒**期限。任意外部阻塞超時則直接退出，不冒稱已完成 done。正常、SQLite 外部寫鎖、HTTP 尚未返回、stdout 無人讀取的 subprocess 測試均驗證 1 秒內退出。
- 每日前瞻與帳本也受停止屏障保護；已保留的 HTTP 嘗試仍計額度，關閉後晚到的 receipt 不落盤，也不因關閉而補扣／退額度。帳本連線不再只依賴 sqlite context manager 或垃圾回收釋放。
- 測試 teardown 先釋放假網路關卡，再 `wait_closed()`，明確確認包括每日前瞻在內的所有執行緒退出，之後才刪暫存目錄；沒有使用忽略 cleanup error、sleep 或重試刪除掩蓋問題。

## 重現與驗證證據

- [舊版 351520e 的隔離重現](verification/evolve-12-original-reproduction.txt)：30 次中 2 次重現相同 `OSError [Errno 66] Directory not empty`，12 次舊 teardown 返回時 daily-forward-worker 仍存活。隔離副本驗完已移除；失敗沒有算成通過。
- [修正版原測例 100 次](verification/evolve-12-target-100.txt)：100/100 OK。
- [關閉／runtime／協定目標測試](verification/evolve-12-shutdown-tests.txt)：30 項 OK。
- [每日前瞻與新增關閉測例](verification/evolve-12-targeted.txt)：26 項 OK。
- [真 subprocess 的 bye 耗時](verification/evolve-12-bye-timing.txt)：包括外部 SQLite 鎖、未返回網路與 stdout 壅塞；最慢 0.822 秒（stdout 無人讀取），外部 SQLite 鎖案例 0.046 秒。
- [前半測試](verification/evolve-12-front-tests.txt)：50 項 OK。靜態的前瞻起日說明與資料出口分別驗證，沒有移除歷史日期／跨 epoch 的資料防線。

完整 unittest 20 次由下列命令**依序**執行，每次都是全部 discovery、不跳過失敗；遇到任何失敗或程式摘要改變即停止，不會自行重試充數：

```sh
/usr/local/bin/python3 scripts/check_unittest_stability.py --runs 20 \
  --output docs/verification/evolve-12-stability
```

每次完整輸出為該目錄下的 `run-01.txt` 至 `run-20.txt`；[結果與固定程式摘要](verification/evolve-12-stability/results.json) 包含每次測試數、退出碼及耗時。重跑需指定新的輸出目錄，不會覆蓋舊證據。這個 runner 清除真 API 金鑰環境，沒有呼叫真 jev。

<!-- STABILITY_RESULTS -->
連續 **20/20 次全部通過**，每次完整 411 項，共 **8,220 項測試執行**；總耗時 3027.967 秒（約 50.5 分鐘）。期間程式摘要保持一致，沒有重試、跳過或背景執行緒例外。

| 次數 | 測試數 | 耗時（秒） | 結果 | 完整輸出 |
|---|---:|---:|---|---|
| 1 | 411 | 151.229 | OK | [log](verification/evolve-12-stability/run-01.txt) |
| 2 | 411 | 153.140 | OK | [log](verification/evolve-12-stability/run-02.txt) |
| 3 | 411 | 150.879 | OK | [log](verification/evolve-12-stability/run-03.txt) |
| 4 | 411 | 149.369 | OK | [log](verification/evolve-12-stability/run-04.txt) |
| 5 | 411 | 154.676 | OK | [log](verification/evolve-12-stability/run-05.txt) |
| 6 | 411 | 150.577 | OK | [log](verification/evolve-12-stability/run-06.txt) |
| 7 | 411 | 152.249 | OK | [log](verification/evolve-12-stability/run-07.txt) |
| 8 | 411 | 149.161 | OK | [log](verification/evolve-12-stability/run-08.txt) |
| 9 | 411 | 150.027 | OK | [log](verification/evolve-12-stability/run-09.txt) |
| 10 | 411 | 153.935 | OK | [log](verification/evolve-12-stability/run-10.txt) |
| 11 | 411 | 150.535 | OK | [log](verification/evolve-12-stability/run-11.txt) |
| 12 | 411 | 150.161 | OK | [log](verification/evolve-12-stability/run-12.txt) |
| 13 | 411 | 152.422 | OK | [log](verification/evolve-12-stability/run-13.txt) |
| 14 | 411 | 149.641 | OK | [log](verification/evolve-12-stability/run-14.txt) |
| 15 | 411 | 150.405 | OK | [log](verification/evolve-12-stability/run-15.txt) |
| 16 | 411 | 149.312 | OK | [log](verification/evolve-12-stability/run-16.txt) |
| 17 | 411 | 149.698 | OK | [log](verification/evolve-12-stability/run-17.txt) |
| 18 | 411 | 153.857 | OK | [log](verification/evolve-12-stability/run-18.txt) |
| 19 | 411 | 156.448 | OK | [log](verification/evolve-12-stability/run-19.txt) |
| 20 | 411 | 150.246 | OK | [log](verification/evolve-12-stability/run-20.txt) |
<!-- /STABILITY_RESULTS -->

## 變異

新增 [8/8 關閉變異](verification/evolve-12-shutdown-mutations.txt) 全部轉紅：改回 drain、放行 late commit、排隊 Future 不結束、取消後摘要重開 DB、恢復整秒 busy wait、漏等讀連線、讀取 gate 不停止、帳本允許晚到寫入。

受影響的既有變異也重跑：[runtime 32/32](verification/evolve-12-runtime-mutations.txt)、[每日前瞻 26/26](verification/evolve-12-daily-mutations.txt)、[排程 14/14](verification/evolve-12-cli-mutations.txt)、[新聞／鑰匙圈 27/27](verification/evolve-12-news-mutations.txt)、[多日畫面 13/13](verification/evolve-12-daily-front-mutations.txt)、[前瞻畫面 8/8](verification/evolve-12-forward-front-mutations.txt)，共 120/120 全部轉紅。原本兩支 mutation runner 的錨點隨關閉／鎖入口調整，沒有降低檢查。

## 文字一致性修正清單

| 位置 | 原不一致 | 修正 |
|---|---|---|
| manifest／package／lockfile | 0.2.0／0.2.0／0.1.0 | 三處及 lockfile 根套件統一 0.3.0；依賴版本不變 |
| README 開頭、SPEC 狀態 | 只描述 30 分鐘、狀態停在 v0.1 | 開頭改多日 3／7／14，預設 7 日；§1–§14 明列歷史規格 |
| README、開發段表格 | 缺多日目前結論 | 明列依預先規則無方法顯著勝過 majority、jev_ind 顯著較差、保留段未使用 |
| SPEC §15.12、README、最新預測卡片 | 前瞻首日寫 09-28 或未標 | 統一自 2026-09-29 起，仍依實際交易日與日 K 判定；不改候選日邏輯 |
| README 預設畫面 | 宣稱初次載入實驗 1 | 預設多日 7 日／實驗 4；30 分鐘模式才用實驗 1 |
| README 自動呼叫 | 宣稱 jev 只在按回放後執行 | 區分 30 分鐘手動回放、多日 jev_ind 自動前瞻 |
| README 無金鑰／執行入口 | 四個本機基準與必須開殼的說法混在一起 | 區分 30 分鐘四基準、多日前瞻三本機方法；殼或獨立排程可產生 |
| README 保留段／期末考 | 未清楚分 30 分鐘與多日 | 30 分鐘已使用、多日未使用；4 日重疊仍保留揭示 |
| README 前瞻考生 | 漏了 vol_prior | 30 分鐘前瞻加入既有 vol_prior；不改方法 |
| SPEC §15.2 | 把 close/ref 稱為報酬，連乘後漏寫 −1 | 明確區分報酬因子 g 與 R_H＝連乘−1；對齊原有 daily_replay.py 的 total−1，程式不變 |
| SPEC §15.8 | 舊段落可被解讀為可用當日籌碼 | 對齊 §15.9：當日（含）日 K、前一交易日（含）籌碼 |
| SPEC §15.5 | jev_news 還寫傳標題 | 改聚合計數／短題材、當日截止快照，旗標關閉、不送 API |
| SPEC §15.6–§15.8 | 約 560、計畫跑保留段／晉級規則與後續決定衝突 | 保留原計畫的歷史標記，明確指向 576 日及 §15.11–§15.12 最新決定 |
| SPEC §3、README | 環境限定與排程鑰匙圈例外、3 個 HTTPS client 的舊敘述 | 區分殼與獨立排程；涵蓋行情／除權息／籌碼／jev |
| SPEC §9.6 | 仍寫三種報告用語 | 對齊 §7.2 已有四種 |
| README 同步驗收腳本 | 宣稱不會跑預測 | 說明有 TypeSafe 金鑰＋新多日交易日時同步會觸發自動前瞻；只驗同步應移除該金鑰 |
| README、SPEC 關閉描述 | 只等 stdout，沒有 DB 收尾屏障 | 對齊本輪 abort、連線收尾、整體 0.8 秒期限；額度帳本另有短交易 |

以上只改文件與畫面文字；指標、訓練、門檻、prompt、切分、評分與凍結摘要均未修改。30 分鐘結果的結論縮限為「本次設定 Brier 顯著差於 majority」，避免把特定實驗擴張成所有情境都沒有預測力。

## 範圍

新增 `back/db_lifecycle.py`、`tests/test_shutdown.py`、`scripts/mutation_shutdown.py`、`scripts/check_unittest_stability.py`；修改 runtime、writer、protocol、每日前瞻服務／帳本的關閉生命週期、runtime teardown、相應 mutation 錨點、版本與文件／畫面文字。各項完整輸出均在 `docs/verification/evolve-12-*`。

未改 news 或殼原始碼、未 commit、未變更真實驗資料或揭露任何區段，真 jev 0 次；帳本仍 1,222／3,000。
