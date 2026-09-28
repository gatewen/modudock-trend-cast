# 走勢推演（trend-cast）

v0.3.0 以 **多日預測（3／7／14 個交易日）** 為主：用 2330 台積電的調整後日 K 與前一交易日以前的籌碼，比較漲／盤整／跌的機率。畫面預設 **7 日**，可切換 3 日、14 日或保留的 30 分鐘版本。各天期門檻與方法參數在實驗 4 建立時凍結。

## 結果

- 已測 **11 個技術／籌碼指標、5 個大環境指標、2 個組合模型**（ind_logit、mkt_logit）、ens_avg、jev 讀指標；另有 **jev 讀新聞（前瞻中）**。
- 開發段有 **3 組小幅入圍**：ens_avg 3 日／7 日、mkt_logit 3 日；ens_avg 3 日屬邊緣。入圍只代表值得再驗證。大環境研究共 18 個比較，預期約 0.45 個因運氣入圍。
- **保留段（2022-01～2024-07）只能用一次，已使用。三者都沒有證據比「猜最常見答案」（majority）好。** 前瞻考生名單維持現狀，不再用此段篩選方法。3 個主要比較預期約 0.075 個因運氣顯著較好，未作多重比較校正。
- **jev 讀指標在開發段顯著較差**：共同 576 個抽樣日，三個天期的 Brier 都顯著差於 majority 與 ind_logit。
- **前瞻紀錄自 2026-09-29 起累積**：準時／補記分列，待 3／7／14 個交易日期滿計分；jev 讀新聞只在有合格快照時加入。

保留段主要比較（Brier 差＝考生 − majority，負值較好；20 交易日區塊 bootstrap 95% 區間）：

| 考生 | 差 | 區間 | 結論 |
|---|---:|---|---|
| ens_avg 3 日 | −0.00008 | [−0.00252, +0.00242] | 沒有證據顯示比簡單方法好 |
| ens_avg 7 日 | −0.00405 | [−0.00877, +0.00100] | 沒有證據顯示比簡單方法好 |
| mkt_logit 3 日 | +0.00592 | [−0.00211, +0.01453] | 沒有證據顯示比簡單方法好 |

畫面最上方提供研究結論、保留段主要比較與可展開的各天期描述性成績；開發段成績包含全部 25 個方法。詳見 [SPEC §16.6](docs/SPEC.md)、[完整保留段成績](docs/HANDOFF-EVOLVE2-3.md)。**這不是投資建議。**

## 30 分鐘版本既有結果（2026-09-27）

30 分鐘版本保留歷史回放與既有研究；與多日實驗使用不同表格與切分，不再投入新的問法研究。原設定以歷史 1 分 K 預測 30 分鐘後方向，盤整門檻為 0.3%。

- **30 分鐘保留段已使用**（2026-01-22～2026-08-31，145 日、1,160 點）：jev 比 majority 差。Brier 差 +0.0786，交易日配對 bootstrap 95% 區間 [+0.0591, +0.0967]。準確率：jev 49.7%、永遠猜盤整 67.5%、反向 56.5%、順勢 54.1%。
- 開發段（2024-09-05～2026-01-21，2,688 點）：p1 jev 44.0%／Brier 0.671；p2 49.6%／0.686。依事先宣告的規則以 Brier 定案 p1。
- 結論限於本次設定：jev 的 Brier 顯著差於 majority；不推論其他股票或所有預測情境。期末考有 4 個已曝光重疊日，詳見 [完整報告](docs/verification/holdout-report.md)。

## 本機使用

後半只用 Python 標準庫，需要 Python ≥ 3.12、Expat ≥ 2.6。宣告檔使用 PATH 中的 `python3`；本機測試固定用 `/usr/local/bin/python3`。前半為原生 ES module，無框架、無建置步驟。

```sh
cd ~/Code/modudock/shell
go run ./cmd/modudock -modules <模組目錄>
```

在 catalog 載入「走勢推演」。若預設連接埠被其他殼佔用，加上 `-addr 127.0.0.1:0`，使用 log 中分配的網址。

預設資料庫是本模組的 `data/trendcast.sqlite3`。既有研究資料包含 30 分鐘實驗 1–3、多日實驗 4 與已授權的紀錄；新安裝不會附帶使用者的研究 DB，`data/` 不進版控。單獨啟動後半可指定 `--db 路徑`，但 stdin/stdout 必須使用殼的 protocol 1，並非互動式命令列。

金鑰由啟動殼的環境變數 `FUGLE_API_KEY`、`TYPESAFE_API_KEY` 提供；程式不讀 `.zshrc`，也不把金鑰存入資料庫或前半。沒有富果金鑰仍可查看既有資料；沒有 TypeSafe 金鑰仍可跑 30 分鐘四個基準，或產生多日前瞻的三個本機方法。401／403 會停用該 process 的對應功能，修正環境後需重新載入模組。

行情、除權息、籌碼與 jev 的 HTTPS client 都驗證憑證與主機名稱。若 Python 預設沒有 CA，依序嘗試 `SSL_CERT_FILE`、`/etc/ssl/cert.pem`、`/etc/ssl/certs/ca-certificates.crt`、`/etc/pki/tls/certs/ca-bundle.crt`；仍沒有 CA 就拒絕連線，絕不關閉驗證。不需要為本模組額外設定 `SSL_CERT_FILE`，但系統必須提供可信任的 CA。同步會區分完成、部分失敗、全部失敗，並顯示固定的安全原因。略過已定稿月份不當作這次同步成功。

載入後若富果金鑰存在，背景同步會自動開始。30 分鐘 jev 回放須手動確認；多日模式在實驗 4 已凍結、有新交易日及 TypeSafe 金鑰時，會自動呼叫 jev_ind（每天三題一次，含授權範圍內重試）。30 分鐘可選擇方法跑開發段或實驗 1 保留段，只補缺少的預測。同步與回放可同時進行，「停止」只取消回放，已提交的點保留。每次只有一個同步、一個回放。

以下回放、揭露與新實驗按鈕皆屬 **30 分鐘模式**；多日實驗 4 的保留段已由 §16.5 一次性 CLI 考試揭露，畫面永久標示已使用。30 分鐘保留段回放與查看結果是兩件事：「看保留段結果」需再次確認，提交揭露紀錄後才顯示走勢及成績。§13.4 已定案實驗 1（p1），共用回放與揭露入口永久拒絕其他實驗的保留段。已曝光日期跨實驗延續；「保留段已使用」是曝光提示，並不表示已授權顯示本實驗的保留段結果。開新實驗也需再次確認；同步或回放進行中會拒絕建立。介面可設定門檻，新實驗預設使用已定稿月份，保留前 25 個交易日暖機，再以交易日切 70%／30%；原實驗不變更。

## 開發與驗證

§14 的 30 分鐘前瞻段固定使用實驗 1／p1、vol_prior 與原有四基準；切到 30 分鐘模式時顯示已定案的實驗 1，初次載入整體畫面則為多日 7 日／實驗 4。成績區有「前瞻段」與「跑前瞻段」「看前瞻段結果」按鈕，兩者各需確認。回放會凍結尚未曝光的收盤日期，只補缺答；全部方法與 outcomes 驗證完整後，才接受另一次明確揭露。已揭露部分累積顯示天數、可預測且可評分點數與 §7.2 指標；新日未揭露前，不出現其數字。30 分鐘與多日保留段均已使用，各自保留獨立的揭露紀錄。

只預覽日期、不回放或揭露：

```sh
/usr/local/bin/python3 scripts/forward_dates.py --db data/trendcast.sqlite3
```

自主進化所有真正的 jev HTTP（含既有 CLI 與 429／529 重試）共用 `data/evolve-2026-09-27-budget.sqlite3`。2026-09-29 第二場把累計硬上限改為 **2,222 次**＝已用 1,222＋本場 1,000；原帳本與已用計數保留。額度帳本獨立於實驗 DB，跨程序／重啟累積，複製實驗 DB 不會重置額度；不要刪除或重設該檔。假服務測試使用各自的暫存帳本。每次真 HTTP 前原子扣額度；單純讀取摘要不會建立或重設帳本。

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

另有真正同步的驗收：在載入金鑰、取消 `SSL_CERT_FILE` 的殼上執行 `node scripts/check_sync_shell.mjs http://127.0.0.1:連接埠 --execute`，會驗證自動同步，再按一次「重新同步」。此腳本會呼叫富果／證交所並更新預設 DB；如果同時提供 TypeSafe 金鑰且有新多日交易日，同步完成後的自動前瞻也可能發出 jev_ind 請求。只驗同步時應移除 TypeSafe 金鑰；腳本本身不操作回放或揭露按鈕。修正後的實測與測試輸出見 [第五塊複審修正](docs/HANDOFF-BLOCK-5-FIX.md)。

## 實作約定與限制

stdin 控制迴圈在正常運行時不等網路或 SQLite；Outbox 單一執行緒寫 stdout，實驗資料寫入交給 `db-writer`，獨立額度帳本以短交易序列化。同步／回放各有 generation，只有提交成功才回報完成，取消後晚到結果丟棄。關閉先停止 DB 新連線與佇列工作、回滾未提交寫入，等待寫入者與唯讀 WAL 連線真正關閉後才送 `done`；連線收尾與輸出共用 0.8 秒期限。外部鎖以短等待檢查取消；若阻塞的外部程式碼或 stdout 超過期限，直接退出，不能保證收到 `done`。測試 teardown 另外等待所有背景執行緒離開，才清除暫存目錄。

股價短線接近隨機，本模組用來客觀量測 jev 的表現，不構成投資建議。即使模型看不到日期與代號，仍不能完全排除它憑記憶認出歷史行情。分 K 未還原除權息，跨日比較只使用開盤前公告的參考價。jev 預測會把相對化行情數字送至 TypeSafe AI 的雲端 API（美國託管）。

v1 不支援盤中即時預測、多檔股票、自訂股票 UI、交易下單或修正已凍結資料。期末考報告須照實保留解鎖前已曝光的 4 日重疊；實驗 2 保留段不跑、不解鎖。

實驗 1 期末考已完成；結果、完整報告、測試及變異對照見 [期末考交件](docs/HANDOFF-HOLDOUT.md)。

## 多日每日前瞻紀錄

實驗 4 已凍結時，模組啟動、同步後與每 15 分鐘自動檢查新日 K；有 `TYPESAFE_API_KEY` 才會送每日三題的 jev_ind 請求，前三個凍結方法可離線產生。只記錄實驗建立之後的交易日；準時、補記與待確認分列。每次 HTTP 都計入進化帳本，429／529 跨重啟最多共三次且須早於下一交易日 09:00，其他失敗不自動重送。

同步另補多日日 K、除權息與籌碼。可由開著的殼或下節的獨立排程自動記錄；兩者共用鎖與持久化請求保留。兩者都未執行期間的起點，之後依原始可見資料補記。自 2026-09-29 起按實際交易日資料累積。前瞻到期成績直接顯示；多日保留段已於第二場第 3 輪使用，沒有新增前瞻考生。測試與畫面見 [第 9 輪交件](docs/HANDOFF-EVOLVE-9.md)。

## 不開殼的每日排程

```sh
cd /Users/gatewenlee/Code/modudock-modules/trend-cast
/usr/local/bin/python3 scripts/daily_forward.py
```

預設使用模組內 `data/trendcast.sqlite3`，也可明確帶 `--db /絕對路徑/trendcast.sqlite3`。它與殼內後半共用 `DailyForwardService`，執行一次「增量同步日 K／除權息／籌碼 → 補缺的前瞻預測 → 到期計分」後結束。會直接呼叫所需 API；只處理實驗 4 的前瞻紀錄，不跑或揭露多日保留段。

兩個入口共用 `data/trendcast.sqlite3.daily-forward.lock`，整段流程只允許一個持有者；撞鎖回 `status=busy`、本次 0 次 jev，等下個排程。鎖路徑依實際 DB 路徑決定（符號連結會解析）；程序退出時核心自動釋放，**不要刪除鎖檔**。鎖涵蓋每日前瞻流程；其他既有 30 分鐘資料表仍由 SQLite 交易序列化。更新程式後，請先把正在運行的 trend-cast 模組卸載再載入一次，使殼內後半也使用新版鎖。

stdout 只有一行 JSON：`new_predictions` 是新提交的預測資料列數（一天四方法 × 三天期＝12 筆）；`scored_outcomes` 是新計分起點／天期數（一天三天期＝3 筆）；`jev_http_calls` 包含重試；`ledger_used` 是共用帳本的累計（目前上限 2,222）。另列 `missing_jev_days`。既有答案會跳過，完成後重跑為 0 次 jev。錯誤、缺答或未建實驗會回非零退出碼；`busy` 與 `missing_keys` 是可恢復的略過，退出碼為 0。

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


新聞廣播與 `jev_news`（p5）：預設啟用，只做多日前瞻；state 是完整 p6 加上當日 13:30 前快照的方向計數、題材與時間窗。沒有 news 模組或沒有當日合格快照時，畫面顯示「今日無新聞資料」，完全不發 p5 請求。新聞廣播狀態另顯示「未收到／最後收到時間」。題材文字中的數字在送出前遮蔽，原始快照留在本機；不送日期、代號、絕對價格或連結。獨立排程不接收殼廣播，只用殼已存的當日快照。

jev_ind 與 jev_news 各自每天一個三題請求；只允許 429／529 在下一交易日 09:00 前退避重試，各自跨重啟最多三次，所有 HTTP 共用總帳本。p5 的兩個預先指定比較是 jev_news − jev_ind 與 jev_news − majority；三天期、準時／補記分開，只比較共同到期日，未滿 60 日一律「結果不完整，不下結論」。完整文字見 [PROMPT-P5](docs/PROMPT-P5.md)，廣播契約見 [news.market_digest 提案](docs/PROPOSAL-news-market-digest.md)。news v0.10.0 的發布端已實作，但仍須另行安裝／更新 news。
