# 第五塊交件：殼接線、前半與第四種報告用語

> 以下保留初次交件紀錄。複審發現的 CA、同步狀態及宣告檔問題已修正；目前版本與未設定 `SSL_CERT_FILE` 的真同步證據見 [複審修正](HANDOFF-BLOCK-5-FIX.md)。

開發段報告已改為 **「jev 比 majority 差」**：共同交集 2,688 點，Brier 差（jev − majority）為 **+0.048165**，95% 日區塊配對 bootstrap 區間 **[+0.034299, +0.060991]**。數值與第四塊完全相同，只更新結論與 `score_version=s2`。見 [報告](verification/block-5-dev-report.md)、[JSON](verification/block-5-dev-report.json)、[前後核對](verification/block-5-report-comparison.json)。

本次沒有呼叫真預測 API、沒有解鎖保留段、沒有 commit，也沒有修改 `news/`。揭露與保留段回放測試全部使用合成資料庫。

## 檔案清單

| 範圍 | 檔案與變更 |
|---|---|
| 宣告與說明 | 新增 `modudock.json`、`README.md`；宣告公開範圍只含 `front/`，後半使用指定的 `/usr/local/bin/python3` |
| 後半協定 | 新增 `back/trendcast.py`、`back/protocol.py`：seq、hello/ready/up/bye/done/fail、單一 stdout writer、900 KiB 守衛、有界輸出與關閉 |
| 接線與工作 | 新增 `back/runtime.py`、`back/jobs.py`：各 op、背景讀取、同步、四個基準回放、共用 DBWriter／ActivityGate；既有 `back/db_writer.py` 延用 |
| 原子性 | 修改 `back/experiment.py`：活動查詢與可由呼叫端持有的建立保留權；從接收指令、排隊到 commit 及實驗綁定完成，都禁止插入同步／回放 |
| 持久化 | 修改 `back/replay.py`、`back/jevcast.py`：共用 outcomes 寫入，與預測／run 計數同一 transaction；進度附 experiment／method／split／generation |
| 計分 | 修改 `back/score.py`、`back/report.py`；更新 `tests/test_score.py`、`tests/test_report.py`、`scripts/mutation_score.py`；第四塊交件加上新版報告指引 |
| 前半 | 新增 `front/front.js`、`front/style.js`、`package.json`、`package-lock.json`，happy-dom 僅為 devDependency |
| 第五塊測試 | 新增 `tests/test_protocol.py`、`tests/protocol_fixture.py`、`tests/test_runtime.py`、`tests/test_jobs.py`、`tests/front.test.mjs` |
| 變異與實機 | 新增 `scripts/mutation_runtime.py`、`scripts/mutation_front.py`、`scripts/check_shell.mjs`；輸出存 `docs/verification/block-5-*` |

## 行為與介面

保留 SPEC 所列 `sync / day / run / cancel / report / reveal / new_experiment`，另支援 `status`。非同步讀取會回傳呼叫端的 `request_id`，前半丟棄舊日期、舊實驗、舊報告的回應。`run` 綁定當時的 experiment id；同時只准一個回放。`reveal` 和 `new_experiment` 均要求 `confirmed:true`，有附 `experiment_id` 時必須等於目前綁定的實驗。

寫入都由單一 `db-writer` 擁有；網路在固定 daemon workers，stdin 控制迴圈不等待它們。同步和回放各自占用 gate 與 generation；`cancel` 不影響同步。建立實驗的 gate 在排隊前取得，直到提交成功並更新目前實驗後才釋放；失敗也會釋放。預測與 outcomes 提交前再檢查取消狀態；只有提交成功才增加完成數。新建實驗的預設區間見 README，所有凍結與暖機檢查仍由既有 `create_experiment` 負責。

Outbox 每行為一個 JSON；關閉後拒絕新輸出，清除排隊中的業務訊息，`done` 為最後一行。stdout 卡住時最多等 0.8 秒便退出。狀態快照會合併到最新一筆，避免高速本地回放擠掉完成通知。過大的 `day` 以安全錯誤取代，不分段洩出內容。

未解鎖的保留段不回走勢、預測、標籤、評分，也不回回放成功／失敗／略過數；只保留活動狀態與規格要求的曝光提示。資料總範圍屬品質 metadata；已曝光重疊提示依 §7.3 顯示，兩者均不授予行情存取權。

前半具日期選擇與前後日、8 點箭頭／對錯顏色、機率與實際 30 分鐘漲跌 tooltip、五方法成績及 jev 描述性分布。日期切換先清舊圖；確認新實驗／揭露後先清舊視圖，再等提交結果。兩個確認流程都可取消，門檻只接受 1～999 的整數千分比。所有外部字串使用 `textContent`；unmount 解除回呼並移除 DOM。

## 測試與變異

後半完整輸出：[unittest](verification/block-5-unittest.txt)；前半完整輸出：[node + happy-dom](verification/block-5-front-tests.txt)。

```text
Ran 195 tests
OK

tests 21
pass 21
fail 0
```

§9.7 涵蓋 seq／hello 前靜默／fail 時機／up 門檻、外部 SQLite 鎖住仍快速 bye、網路卡住與 stdout 不讀取、done 最後一行、回放中途關閉重啟只補缺、900 KiB、同步／取消互不影響，以及實驗建立原子性。§9.8 涵蓋 mount 順序、XSS 文字化、顏色箭頭、二次確認、未解鎖資料、主題、舊回應與 unmount。

| 變異組 | 結果 | 完整「改動 → 轉紅測試」對照 |
|---|---:|---|
| 第一塊資料層回歸 | 16/16 | [輸出](verification/block-5-regression-data-mutations.txt) |
| 第二塊回放回歸 | 27/27 | [輸出](verification/block-5-regression-replay-mutations.txt) |
| 第三塊實驗／jev 回歸 | 40/40 | [輸出](verification/block-5-regression-jev-mutations.txt) |
| 開發段 runner 回歸 | 8/8 | [輸出](verification/block-5-regression-run-dev-mutations.txt) |
| 計分（原 42 + 新 1） | 43/43 | [輸出](verification/block-5-score-mutations.txt) |
| 後半接線 | 32/32 | [輸出](verification/block-5-runtime-mutations.txt) |
| 前半 | 19/19 | [輸出](verification/block-5-front-mutations.txt) |

共 185 組行為變異全部轉紅。新計分變異 `significantly_worse_hidden` 拔掉「顯著較差」分支，由四種用語的測例抓到；四種各有獨立測例，另保留碰到 0 的邊界與不完整優先測例。

第五塊變異包括拔掉 seq／up／取消 generation／晚到讀取／保留段守衛、錯誤的跨活動取消、提前釋放建立 gate、漏存 outcomes、在網路執行緒寫 SQLite、重跑既有答案、延長關閉等待、拿掉二次確認，以及改用 innerHTML、對錯顏色對調與接受舊日期回應。全部在暫存副本中操作；完整輸出列出每個被改的守衛和捕捉它的測試。

## 真資料與殼驗收

使用 `sqlite3.Connection.backup` 將 `data/block-3-real.sqlite3` 複製到後半預設的 `data/trendcast.sqlite3`。來源未變更，[backup 紀錄](verification/block-5-backup.json)；原 scratchpad 資料庫未使用也未修改。

本機從 `~/Code/modudock/shell` 執行：

```sh
env -u FUGLE_API_KEY -u TYPESAFE_API_KEY go run ./cmd/modudock \
  -addr 127.0.0.1:0 -modules /Users/gatewenlee/Code/modudock-modules
```

既有連接埠已有其他殼，因此本次使用獨立的 `http://127.0.0.1:60704`。驗收殼移除兩把金鑰，避免載入時自動同步；畫面仍從真資料副本讀取全部開發段紀錄，金鑰「未設定」是此驗收環境的真實狀態。自動同步與金鑰停用分支由 fixture 測試驗證。

內建瀏覽器工具連線不可用，改用殼工作區已有的 Playwright／Chromium。腳本核對 WebSocket catalog 含 `trend-cast`、狀態轉為 `running`、真資料 8 個點與五方法表格、tooltip、開發日期前後切換及第四種結論；只送 `status/report/day`。兩個主題都沒有水平溢出，沒有頁面錯誤；資料庫與後半程式的靜態 URL 都回 404。

- [catalog／running／瀏覽器完整證據](verification/block-5-shell-evidence.json)
- [瀏覽器測試輸出](verification/block-5-browser-tests.txt)、[殼啟動 log](verification/block-5-shell.log)
- [殼載入成功截圖](verification/block-5-shell-loaded.png)
- [淺色截圖](verification/block-5-light.png)、[深色截圖](verification/block-5-dark.png)

兩張主題截圖都已逐張檢視：走勢、8 個點、機率文字、完整成績表及保留段鎖定提示清楚可讀。真資料驗收未送 `run/sync/reveal/new_experiment`，沒有保留段行情或成績輸出。
