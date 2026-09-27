# 第 11 輪交件：鑰匙圈備援與新聞廣播接收

本輪真 jev **0 次**；只讀確認帳本仍為 **1,222 / 3,000**。未安裝／載入 launchd，未讀寫真實鑰匙圈項目，未變更真資料庫、news 或殼原始碼，未 commit。

## A：排程金鑰

`scripts/daily_forward.py` 沿用共用前瞻服務與檔案鎖，只在開始流程前增加 `back/keychain.py`：

- 既有環境變數優先；缺少才在 macOS 呼叫固定路徑 `/usr/bin/security find-generic-password -s <service> -a "$USER" -w`。兩個 service 為 `trendcast-fugle`／`trendcast-typesafe`。
- 參數陣列、不經 shell；stdin／stderr 都導向 DEVNULL，stdout 只在記憶體接收。每筆逾時 5 秒；非 macOS、沒有 USER、工具不存在、非零退出、密碼格式不合法、逾時都安全略過。
- 備援金鑰只在本次服務生命週期內放入程序環境，正常／異常結束都還原；缺任一把仍只印 `missing_keys` 與變數名稱，0 次 HTTP、不開 DBWriter。
- README 已移除 `launchctl setenv` 步驟，改為使用者以 `security add-generic-password ... -w` 互動輸入後再載入 plist。`-w` 最後且不帶值的用法核對了 macOS 隨附 `/usr/share/man/man1/security.1`。

測試用臨時 fake security 可執行檔，沒有讀取使用者鑰匙圈。包含實際 5 秒逾時、子程序同時輸出假金鑰至 stdout／stderr、服務丟出含假金鑰的例外；正常程式的摘要與 OS 層 stderr 都沒有金鑰。

## B：接收、快照、畫面

發布提案見 [PROPOSAL-news-market-digest.md](PROPOSAL-news-market-digest.md)，核對本機 `modudock/docs/RUNTIME-PROTOCOL.md` §9 與 `MANIFEST.md` §3.9：發送為 `publish`、接收為 `event`；沒有發送者識別欄位，也沒有廣播補發。

- Manifest 只加 `subscribes`，不增加 news 依賴。後半在正確 epoch、已 up 時接受指定 topic；驗證後才排入 DBWriter。佇列最多 64 筆，滿時丟棄，不阻塞控制訊息。
- Schema 1 完整欄位及型別檢查、四種方向、最多 10 主題、名稱長度／連結／控制字元檢查、整數不接受 bool、拒絕額外欄位／重複 JSON keys／非有限數。8 KiB 同時檢查原始 UTF-8 body（含跳脫與空白）與標準化內容。
- `news_digests` 只留最新一筆及本機接收時間；較舊或重複發布時間不刷新接收時間。
- `news_snapshots` 依補記保存「發布與接收同屬當日、兩者皆 ≤ 台北 13:30」的最後一筆，盤後不能覆蓋，13:30:00 恰好可用、後一微秒不可。日期以台北時區判斷。
- 尚未出現在交易日曆的日期先存入 `news_pending_snapshots`，日曆確認交易日後才移入正式快照；不能用平日推定開市。候選值不能直接供前瞻使用。
- `news_forward_inputs` 與既有 `d_forward_*` 分開：只用當日正式快照預備 p5 state（日 K 相對值＋新聞、移除發布時間）；沒有則記「無新聞資料」。輸入紀錄不可更新。
- **`JEV_NEWS_ENABLED=False`，沒有 p5 transport**；有新聞仍只記「未啟用」。既有 p6 body、四考生、實驗 4 digest 與成績計算不變。將來啟用 p5 需另案固定 prompt／研究規則，不是改旗標就會自動扣額度。
- 多日畫面新增「新聞廣播：未收到／最後收到時間（台北）」；廣播存妥後推通知，再讀 timestamp。只以 textContent 顯示，拒絕舊 request／epoch，歷史開發段的日期防線維持原狀。

獨立排程不接收殼廣播，只使用殼先前存下的當日快照；殼／news 沒運行而沒收到，就如實缺新聞。前瞻四個既有方法照常。

## 驗證

Python **405 項**、前半 **49 項**全過；新增變異 **32/32**、受影響的既有變異 **22/22** 全部轉紅。完整輸出：

- [Python unittest](verification/evolve-11-unittest.txt)
- [本輪 20 項目標測試](verification/evolve-11-targeted.txt)
- [前半測試](verification/evolve-11-front-tests.txt)
- [新增後半 27/27 變異](verification/evolve-11-mutations.txt)
- [新增前半 5/5 變異](verification/evolve-11-front-mutations.txt)
- [排程既有 14/14 變異回歸](verification/evolve-11-cli-regression-mutations.txt)
- [前瞻畫面既有 8/8 變異回歸](verification/evolve-11-forward-front-regression-mutations.txt)

變異輸出逐條列出移除的守衛與轉紅測例，包括環境優先、平台、timeout、stderr、失敗退出、金鑰清理／意外 print、8 KiB 原始大小、schema／額外欄位／bool／主題上限、重複 keys、未來發布時間、重播、接收截止、同日、盤後覆蓋、交易日曆確認、誤用最新值、啟用旗標、送出日期、錯誤 topic、協定路由與服務未記錄，以及前半請求／通知／時間文字／驗證／epoch。

另以真 subprocess 的 NDJSON 入口驗證：news 不存在仍可讀狀態；錯誤 topic／epoch、壞 schema、含大量空白的超大摘要不存；合法 event 存妥後收到 news_changed，查到本機時間，正常 bye。這是協定實測，不是殼 UI 截圖；本輪沒有啟動新殼。

重跑：

```sh
/usr/local/bin/python3 -m unittest discover -s tests -v
npm test
/usr/local/bin/python3 -m scripts.mutation_news_keychain
/usr/local/bin/python3 -m scripts.mutation_news_front
/usr/local/bin/python3 -m scripts.mutation_daily_forward_cli
/usr/local/bin/python3 -m scripts.mutation_daily_forward_front
```

## 檔案

新增 `back/keychain.py`、`back/news_digest.py`；修改 `scripts/daily_forward.py`、`back/daily_forward_service.py`、`back/runtime.py`、`back/trendcast.py`、`modudock.json`、`front/daily.js`、`front/front.js`、README。

新增 `tests/test_keychain.py`、`tests/test_news_digest.py`、`scripts/mutation_news_keychain.py`、`scripts/mutation_news_front.py`；修改 `tests/daily_front.test.mjs`，並更新既有 `scripts/mutation_daily_forward_front.py` 的一個錨點以適應新增新聞狀態請求。

文件為本交件、發布提案與上述七份驗證輸出。SPEC 僅讀取使用者已提交的補記。
