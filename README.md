# 走勢推演（trend-cast）

用 2330 歷史 1 分 K 回放，預測 30 分鐘後的漲／盤整／跌，將 jev 與四個簡單方法放在相同時點比較。預設門檻為 0.3%，實驗設定、資料摘要及開發／保留切分在建立時凍結。

## 結果（2026-09-27）

- **期末考（保留段 2026-01-22～2026-08-31，145 日、1,160 點，只解鎖一次）：jev 比 majority 差。** Brier 差（jev − majority）+0.0786，交易日配對 bootstrap 95% 區間 [+0.0591, +0.0967]。準確率：jev 49.7%、永遠猜盤整 67.5%、反向 56.5%、順勢 54.1%。
- 開發段（2024-09-05～2026-01-21，2,688 點）：p1 jev 44.0%／Brier 0.671；p2（補欄位說明與基準比例）49.6%／0.686，幾乎只猜盤整；依事先宣告的規則以 Brier 定案 p1。
- 讀法：在這個設定下，jev 對台積電 30 分鐘方向**沒有**預測力，且偏猜跌。這不是投資建議；詳見 `docs/SPEC.md` §12–§13 與 `docs/verification/holdout-report.md`。

## 本機使用

後半只用 Python 標準庫，需要 Python ≥ 3.12、Expat ≥ 2.6。宣告檔使用 PATH 中的 `python3`；本機測試固定用 `/usr/local/bin/python3`。前半為原生 ES module，無框架、無建置步驟。

```sh
cd ~/Code/modudock/shell
go run ./cmd/modudock -modules <模組目錄>
```

在 catalog 載入「走勢推演」。若預設連接埠被其他殼佔用，加上 `-addr 127.0.0.1:0`，使用 log 中分配的網址。

預設資料庫是本模組的 `data/trendcast.sqlite3`。本次交件已用 SQLite backup 從 `data/block-3-real.sqlite3` 複製現有實驗 1 與其開發段紀錄；`data/` 不進版控。單獨啟動後半可指定 `--db 路徑`，但 stdin/stdout 必須使用殼的 protocol 1，並非互動式命令列。

金鑰由啟動殼的環境變數 `FUGLE_API_KEY`、`TYPESAFE_API_KEY` 提供；程式不讀 `.zshrc`，也不把金鑰存入資料庫或前半。沒有富果金鑰仍可查看既有資料；沒有 TypeSafe 金鑰仍可跑四個基準方法。401／403 會停用該 process 的對應功能，修正環境後需重新載入模組。

三個 HTTPS client 都驗證憑證與主機名稱。若 Python 預設沒有 CA，依序嘗試 `SSL_CERT_FILE`、`/etc/ssl/cert.pem`、`/etc/ssl/certs/ca-certificates.crt`、`/etc/pki/tls/certs/ca-bundle.crt`；仍沒有 CA 就拒絕連線，絕不關閉驗證。不需要為本模組額外設定 `SSL_CERT_FILE`，但系統必須提供可信任的 CA。同步會區分完成、部分失敗、全部失敗，並顯示固定的安全原因。略過已定稿月份不當作這次同步成功。

載入後若富果金鑰存在，背景同步會自動開始；jev 只在按下回放按鈕後執行。選擇方法後可跑開發段或保留段，只補缺少的預測。同步與回放可同時進行，「停止」只取消回放，已提交的點保留。每次只有一個同步、一個回放。

保留段回放與查看結果是兩件事：「看保留段結果」需再次確認，提交揭露紀錄後才顯示走勢及成績。§13.4 已定案實驗 1（p1），共用回放與揭露入口永久拒絕其他實驗的保留段。已曝光日期跨實驗延續；「保留段已使用」是曝光提示，並不表示已授權顯示本實驗的保留段結果。開新實驗也需再次確認；同步或回放進行中會拒絕建立。介面可設定門檻，新實驗預設使用已定稿月份，保留前 25 個交易日暖機，再以交易日切 70%／30%；原實驗不變更。

## 開發與驗證

§14 前瞻段固定使用實驗 1／p1 與原有四基準；殼初次載入預設顯示已定案的實驗 1。成績區有「前瞻段」與「跑前瞻段」「看前瞻段結果」按鈕，兩者各需確認。回放會凍結尚未曝光的收盤日期，只補缺答；全部方法與 outcomes 驗證完整後，才接受另一次明確揭露。已揭露部分累積顯示天數、可預測且可評分點數與 §7.2 指標；新日未揭露前，不出現其數字。保留段永久標示已使用。

只預覽日期、不回放或揭露：

```sh
/usr/local/bin/python3 scripts/forward_dates.py --db data/trendcast.sqlite3
```

自主進化所有真正的 jev HTTP（含既有 CLI 與 429／529 重試）共用 `data/evolve-2026-09-27-budget.sqlite3` 的 **3,000 次**硬上限。額度帳本獨立於實驗 DB，跨程序／重啟累積，複製實驗 DB 不會重置額度；不要刪除或重設該檔。假服務測試使用各自的暫存帳本。未發出真 HTTP 前不建立正式帳本。

第一輪實作、日期清單及驗證輸出見 [前瞻段交件](docs/HANDOFF-EVOLVE-1.md)。

§13 的 p2 只更換問法，f1 state 與 criteria 不變。`create_experiment(..., prompt_version='p2')` 可明確建立 p2 實驗；未指定仍為 p1。p2 的 51%／25%／25% 來自實驗 1 開發段 outcomes，已固定在 `back/prompts.py`，不從新實驗或保留段重新估計。

對指定實驗跑開發段可使用下列命令。`--reference-experiment` 會先核對暖機／切點與其他設定，再核對四個基準的全部答案、機率與 f1 輸入；不同即停止，不發 API 請求。

```sh
/usr/local/bin/python3 scripts/run_dev.py --execute \
  --db data/trendcast.sqlite3 --experiment 2 --reference-experiment 1 \
  --max-calls 3500 --report data/p2-development.json
```

`--max-calls` 計入 HTTP 重試；重跑只補缺答。如須分次補跑，後續上限應使用總額度扣除已用次數。此命令只處理開發段，不跑或揭露保留段。

使用者授權的期末考使用以下命令，三個旗標缺一不可。只允許實驗 1／p1；先驗證凍結摘要及開發段選定的 `majority` 基準，再跑五個方法。HTTP 重試與缺答補跑共用最多 1500 次的額度，只有所有可預測點的五種答案、可評分 outcomes 與完整遍歷紀錄都驗證通過，才在同一交易內記錄解鎖前重疊日數並揭露。缺答或額度耗盡時保持鎖定；已完成後重跑不發 HTTP、不重複揭露。

```sh
/usr/local/bin/python3 scripts/run_holdout.py --execute --experiment 1 --split holdout \
  --db data/trendcast.sqlite3 --max-calls 1500 \
  --report data/holdout-run.json --markdown data/holdout-report.md
```

happy-dom 只作為測試用 devDependency，執行前半不需要 node_modules。測試環境使用 Node 23.10.0。

```sh
cd <模組目錄>/trend-cast
/usr/local/bin/python3 -m unittest discover -v
npm ci --ignore-scripts
npm test
/usr/local/bin/python3 scripts/mutation_runtime.py
/usr/local/bin/python3 scripts/mutation_score.py
/usr/local/bin/python3 scripts/mutation_front.py
/usr/local/bin/python3 scripts/mutation_tls_sync.py
/usr/local/bin/python3 scripts/mutation_p2.py
/usr/local/bin/python3 scripts/mutation_holdout.py
/usr/local/bin/python3 scripts/mutation_forward.py
```

變異測試在 `data/` 暫存副本中移除守衛，不修改工作來源或真資料。後半整合測試用假服務、可控制的執行緒關卡與合成資料庫，涵蓋外部 SQLite 鎖、網路未返回、stdout 不讀取時的退出，以及重啟後只補未提交的點。

唯讀產出開發段報告，不會呼叫 API 或揭露保留段：

```sh
/usr/local/bin/python3 scripts/report.py \
  --db data/trendcast.sqlite3 --experiment 1 --dev-only \
  --format markdown --output data/dev-report.md
```

本機殼驗收腳本：`node scripts/check_shell.mjs http://127.0.0.1:連接埠`。腳本使用相鄰 `modudock/web` 已安裝的 Playwright 與 Chromium（可用 `PLAYWRIGHT_MODULE` 指定模組路徑），只載入 trend-cast、讀開發段、切換主題並截圖。為避免載入時的自動同步，驗收殼以移除兩把金鑰的環境啟動；這不會更動原本的金鑰設定。產物見 [第五塊交件](docs/HANDOFF-BLOCK-5.md)。

另有真正同步的驗收：在載入金鑰、取消 `SSL_CERT_FILE` 的殼上執行 `node scripts/check_sync_shell.mjs http://127.0.0.1:連接埠 --execute`，會驗證自動同步，再按一次「重新同步」。此腳本會呼叫富果／證交所、更新預設 DB，不跑預測、不解鎖。修正後的實測與測試輸出見 [第五塊複審修正](docs/HANDOFF-BLOCK-5-FIX.md)。

## 實作約定與限制

stdin 控制迴圈不等網路或 SQLite；Outbox 單一執行緒寫 stdout，所有資料庫寫入交給 `db-writer`。同步／回放各有 generation，只有提交成功才回報完成，取消後晚到結果丟棄。關閉最多等輸出 0.8 秒，之後退出；若接收端完全不讀 stdout，不能保證它收到 `done`，但仍不讓關閉卡住。

股價短線接近隨機，本模組用來客觀量測 jev 的表現，不構成投資建議。即使模型看不到日期與代號，仍不能完全排除它憑記憶認出歷史行情。分 K 未還原除權息，跨日比較只使用開盤前公告的參考價。jev 預測會把相對化行情數字送至 TypeSafe AI 的雲端 API（美國託管）。

v1 不支援盤中即時預測、多檔股票、自訂股票 UI、交易下單或修正已凍結資料。期末考報告須照實保留解鎖前已曝光的 4 日重疊；實驗 2 保留段不跑、不解鎖。

實驗 1 期末考已完成；結果、完整報告、測試及變異對照見 [期末考交件](docs/HANDOFF-HOLDOUT.md)。

## 多日每日前瞻紀錄

實驗 4 已凍結時，模組啟動、同步後與每 15 分鐘自動檢查新日 K；有 `TYPESAFE_API_KEY` 才會送每日三題的 jev_ind 請求，前三個凍結方法可離線產生。只記錄實驗建立之後的交易日；準時、補記與待確認分列。每次 HTTP 都計入進化帳本，429／529 跨重啟最多共三次且須早於下一交易日 09:00，其他失敗不自動重送。

同步另補多日日 K、除權息與籌碼。模組與殼須保持執行才能自動記錄；關閉期間的起點，下次開啟後依原始可見資料補記。前瞻到期成績直接顯示；既有多日保留段仍未使用。測試與畫面見 [第 9 輪交件](docs/HANDOFF-EVOLVE-9.md)。

## 不開殼的每日排程

```sh
cd /Users/gatewenlee/Code/modudock-modules/trend-cast
/usr/local/bin/python3 scripts/daily_forward.py
```

預設使用模組內 `data/trendcast.sqlite3`，也可明確帶 `--db /絕對路徑/trendcast.sqlite3`。它與殼內後半共用 `DailyForwardService`，執行一次「增量同步日 K／除權息／籌碼 → 補缺的前瞻預測 → 到期計分」後結束。會直接呼叫所需 API；只處理實驗 4 的前瞻紀錄，不跑或揭露多日保留段。

兩個入口共用 `data/trendcast.sqlite3.daily-forward.lock`，整段流程只允許一個持有者；撞鎖回 `status=busy`、本次 0 次 jev，等下個排程。鎖路徑依實際 DB 路徑決定（符號連結會解析）；程序退出時核心自動釋放，**不要刪除鎖檔**。鎖涵蓋每日前瞻流程；其他既有 30 分鐘資料表仍由 SQLite 交易序列化。更新程式後，請先把正在運行的 trend-cast 模組卸載再載入一次，使殼內後半也使用新版鎖。

stdout 只有一行 JSON：`new_predictions` 是新提交的預測資料列數（一天四方法 × 三天期＝12 筆）；`scored_outcomes` 是新計分起點／天期數（一天三天期＝3 筆）；`jev_http_calls` 包含重試；`ledger_used` 是共用 3,000 次帳本的累計。另列 `missing_jev_days`。既有答案會跳過，完成後重跑為 0 次 jev。錯誤、缺答或未建實驗會回非零退出碼；`busy` 與 `missing_keys` 是可恢復的略過，退出碼為 0。

兩把金鑰優先從 `FUGLE_API_KEY`、`TYPESAFE_API_KEY` 環境變數讀；缺少時，macOS 以 `/usr/bin/security` 讀登入使用者（`$USER`）的 `trendcast-fugle`／`trendcast-typesafe` 鑰匙圈項目。每筆最多等 5 秒；非 macOS、鑰匙圈鎖定或讀取失敗就略過。備援金鑰只在本次程序記憶體中使用，結束即移除。腳本不讀 `.zshrc`、不接收金鑰命令列參數；缺任一把時列出缺少的**變數名稱**後結束，不開 writer、不同步、不發 jev。摘要不包含金鑰或原始例外。

排程範本：[com.gatewen.trendcast.daily.plist.example](docs/launchd/com.gatewen.trendcast.daily.plist.example)。以下是使用者決定安裝時才要做的步驟，本輪沒有實際安裝或載入：

1. 確認 Mac 系統時區為台北，並修改範本中的 Python（須 ≥ 3.12）、模組、DB、工作目錄及日誌**絕對路徑**；plist 不會展開 `~` 或 shell 變數。確認 `data/` 存在且已有實驗 4。
2. launchd 不讀互動式 shell 的 `.zshrc`。先在登入使用者的終端機建立兩筆鑰匙圈項目（可重開機保留）：

   ```sh
   /usr/bin/security add-generic-password -U -s trendcast-fugle -a "$USER" -w
   /usr/bin/security add-generic-password -U -s trendcast-typesafe -a "$USER" -w
   ```

   `-w` 放在最後且不帶值，由 `security` 互動詢問密碼（Apple 隨系統附的 `man security` 所建議用法），輸入各 API 金鑰。不要把金鑰接在命令後、放進 plist 或版控。若系統詢問鑰匙圈存取權，由使用者確認；先在終端機執行一次腳本確認讀取成功。登入鑰匙圈未解鎖或權限不足時，排程仍會安全略過，請查看日誌中的 `missing_keys`；不要以顯示密碼的命令測試。

3. 自行決定安裝後，再複製、驗證並載入：

   ```sh
   mkdir -p "$HOME/Library/LaunchAgents"
   cp docs/launchd/com.gatewen.trendcast.daily.plist.example \
     "$HOME/Library/LaunchAgents/com.gatewen.trendcast.daily.plist"
   plutil -lint "$HOME/Library/LaunchAgents/com.gatewen.trendcast.daily.plist"
   launchctl bootstrap "gui/$(id -u)" \
     "$HOME/Library/LaunchAgents/com.gatewen.trendcast.daily.plist"
   ```

範本的 `StartCalendarInterval` 列出週一至週五 **16:45、17:30、20:30**，不設 `RunAtLoad` 或持續重啟。launchd 不認識證交所休市日，因此仍可能在平日假日啟動；是否有新起點由同步後的交易資料判定。20:30 主要補較晚發布的籌碼；若來源尚未更新，下一次再補。預測仍只使用前一交易日以前的籌碼，已記錄的輸入不會改寫。

日誌在 `data/daily-forward.log` 與 `data/daily-forward-error.log`。如果 Mac 睡眠，`StartCalendarInterval` 會在喚醒時合併補一次；關機時不執行，所以仍可能被標為補記。此為使用者 LaunchAgent，須處於登入狀態。[Apple 排程說明](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html)、[Apple LaunchAgent 說明](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)。

需要停止排程時由使用者執行：

```sh
launchctl bootout "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/com.gatewen.trendcast.daily.plist"
```

第 10 輪真資料驗證、測試與變異對照見 [交件說明](docs/HANDOFF-EVOLVE-10.md)。


新聞廣播接收（尚未啟用 jev_news）：多日畫面顯示「未收到／最後收到時間」。只訂閱 `news.market_digest`，不要求安裝 news；摘要嚴格限制 8 KiB，另存每日 13:30 前快照，前瞻只讀當日快照，缺少就記「無新聞資料」。`jev_news` 的 p5 輸入預備紀錄與既有 p6 分開，旗標關閉、沒有新增 API 呼叫。欄位、時間規則與尚待 news 實作的發布方式見 [news.market_digest 提案](docs/PROPOSAL-news-market-digest.md)。獨立排程不會接收殼廣播，只能用殼先前已存的快照。
