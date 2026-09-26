# 第五塊複審修正

三個 client 已共用 CA 備援；同步會明確區分完成、部分失敗、全部失敗並顯示安全原因；manifest 改回 `python3`。**未設定 `SSL_CERT_FILE` 的真實殼同步已成功**。

前次真資料驗證帶有 CA 環境設定，不能證明一般殼啟動能同步，確實漏了這個環境條件。這次用 `zsh -ic` 載入兩把金鑰後明確 `unset SSL_CERT_FILE`，從真實殼觸發同步並核對 SQLite。

## 修正內容

1. `back/http_client.py` 新增共用 `secure_context()`：預設 CA 空時依序嘗試 `SSL_CERT_FILE`、`/etc/ssl/cert.pem`、`/etc/ssl/certs/ca-certificates.crt`、`/etc/pki/tls/certs/ca-bundle.crt`。仍無 CA 就在建立 opener 前拒絕連線；維持 `CERT_REQUIRED` 與 hostname 驗證。富果、證交所及 `back/jevcast.py` 全部使用同一個 opener。
2. 直接或包在 `URLError` 裡的憑證錯誤保留安全代碼 `tls_certificate_error`，其他 TLS／網路錯誤分別為 `tls_error`／`network_error`。`SSLCertVerificationError` 同時繼承 `ValueError`，因此必須先分類 SSL 錯誤，避免誤報 JSON 格式問題。對外不保留原始例外鏈或文字。
3. `back/jobs.py` 依本次實際提交成功／失敗的「月 × 資料種類」分類同步結果。略過已定稿月份不計成功；無需重抓且沒有失敗則完成。成功數在 commit 之後增加；有失敗且沒有成功為 `failed`，有成功及失敗為 `partial`，沒有失敗為 `complete`。理由只送固定代碼。
4. `front/front.js` 顯示「全部失敗／部分失敗／完成」及固定中文原因，例如「無法驗證 TLS 憑證」、「金鑰無效」、「連線失敗」。拒絕不認識的理由，不反射上游文字；下一次成功不殘留失敗原因。
5. `modudock.json` 改為 `["python3", "back/trendcast.py"]`，README 同步修正；測試仍用 `/usr/local/bin/python3`。前半沒有讀取 `dev_runs`，依複審指示不改其語意。

新增 `tests/test_tls.py`、`scripts/mutation_tls_sync.py`、`scripts/check_sync_shell.mjs`；擴充 `tests/test_jobs.py`、`tests/front.test.mjs`。`tests/test_clients.py` 更新 TLS 錯誤預期；兩支既有秘密清洗變異更新字串定位，原測試目的不變。新增前半變異位於 `scripts/mutation_front.py`。

## 測試

完整 [Python 輸出](verification/block-5-fix-unittest.txt)、[前半輸出](verification/block-5-fix-front-tests.txt)：

```text
Ran 201 tests in 48.463s
OK

tests 22
pass 22
fail 0
```

新增測例把環境清空、預設 CA 設為空，經三個 client 的正式 opener 路徑確認載入系統 CA；另外驗證備援順序、有預設 CA 不另載入、全部找不到 CA 不建立連線、TLS 與網路錯誤分類及無例外文字洩漏。同步測例涵蓋全失敗、部分失敗、全成功、全部略過、略過月份不能掩蓋其他月份全部失敗，以及未知錯誤代碼清洗。

變異全部重跑：**193/193 轉紅**。

| 組別 | 結果與完整對照 |
|---|---|
| 資料層 | [16/16](verification/block-5-fix-data-mutations.txt) |
| 回放 | [27/27](verification/block-5-fix-replay-mutations.txt) |
| 實驗／jev | [40/40](verification/block-5-fix-jev-mutations.txt) |
| 開發段 runner | [8/8](verification/block-5-fix-run-dev-mutations.txt) |
| 計分 | [43/43](verification/block-5-fix-score-mutations.txt) |
| 接線 | [32/32](verification/block-5-fix-runtime-mutations.txt) |
| 前半 | [21/21](verification/block-5-fix-front-mutations.txt) |
| 新增 TLS／同步 | [6/6](verification/block-5-fix-tls-sync-mutations.txt) |

新 6 組分別移除 CA 備援、移除零 CA 拒絕連線守衛、關閉憑證與 hostname 驗證、丟失憑證錯誤分類、把全部失敗改回部分失敗，以及回傳任意例外代碼；各自的行為測試全部抓到。前半新增 2 組是把全部失敗改成部分失敗、隱藏安全原因。

## 真實殼驗證

在 `~/Code/modudock/shell` 執行：

```sh
zsh -ic 'unset SSL_CERT_FILE; go run ./cmd/modudock -addr 127.0.0.1:0 -modules /Users/gatewenlee/Code/modudock-modules'
```

這次分配到 `127.0.0.1:55542`，驗證完已關閉此驗收殼，不影響審核者使用的 8741。PATH 的 `python3` 實際為 `/usr/local/bin/python3`；`SSL_CERT_FILE` 不存在、預設 CA 為 **0**，備援後 CA 為 **128**，憑證及 hostname 驗證均開啟。[環境證據](verification/block-5-fix-real-environment.json) 只記錄金鑰有無，不記錄內容。

瀏覽器從 catalog 載入 trend-cast，先等自動同步完成，再按「重新同步」。兩次均為 **complete、6 成功、0 失敗**；成功指已提交的月／資料種類批次。讀取 `data/trendcast.sqlite3` 新增的 fetch_log 核對：2026-09、2024-07 的 daily／corp／bars，兩次共 12 筆，全部 **HTTP 200、written**。這是殼實際執行的同步，不是單獨 client 測試。

- [殼事件／自動與手動同步證據](verification/block-5-fix-sync-shell.json)
- [SQLite 核對結果](verification/block-5-fix-sync-database.json)
- [瀏覽器輸出](verification/block-5-fix-sync-browser.txt)、[殼 log](verification/block-5-fix-shell.log)
- [同步完成狀態截圖](verification/block-5-fix-sync.png)

真實操作只發 `status/report/day/sync`，未呼叫 jev、未解鎖保留段。沒有 commit，沒有修改 `news/`，原 `block-3-real.sqlite3` 與 scratchpad DB 均未修改。
