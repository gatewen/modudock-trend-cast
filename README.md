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
