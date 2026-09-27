# 第 8 輪：多日畫面（0 次 jev）

已完成預設 7 日的天期切換、多日開發段走勢、指標卡片、18 個方法的成績表、網路說法與實際頻率，以及永久唯讀的多日保留段卡片。本輪沒有呼叫 jev；帳本維持 1,222／3,000。沒有 commit、push，也沒有修改 news 或殼。

## 畫面

- 頂部可切換 30 分鐘／3 日／7 日／14 日。30 分鐘元件原樣移到 `front/intraday.js`，內容與本輪開始前的 `front/front.js` 完全相同。
- 日線圖可選近 6 個月、1 年或全部開發段，範圍以最後一個開發交易日為終點。圖畫「調整收盤指數，開發段首日＝100」，除權息依已凍結參考價向前連乘；不採會被未來事件重寫的 adjusted=true。
- 共同抽樣日上顯示 jev_ind 圓點、ind_logit 方點；綠／紅為正確／錯誤。滑過或鍵盤選取顯示日期、方向與對錯，點選後切換當日指標。
- 指標卡片有日期選擇、前後交易日導航與 11 個白話狀態。bias20 狀態依所選 H 的當時已到期樣本；籌碼仍只用前一交易日以前，量值以百分比呈現。
- 每個 H 列出 18 個方法的 n、準確率、Brier、方法 − majority 的差與 95% 區間、入圍與否。既有 17 方法沿用完整開發段交集；jev_ind 沿用固定 576 天，與 majority 同樣本比較。畫面明列樣本數及此差異。
- 「網路說法 vs 實際」預設看 KD，可切換任一指標或全部；顯示原本未平滑的開發段頻率，n < 30 標記「樣本太少」，另列多重比較提醒。
- 多日保留段卡片固定顯示「未使用（沒有入圍者，保留給未來）」，沒有執行或揭露按鈕。

## 出口與界線

新增四個只讀 op：`daily_status`、`daily_chart`、`daily_indicators`、`daily_report`，共同參數 `H=3/7/14`、`experiment_id=4`。`daily_chart` 的 `range` 為 `6m/1y/all`；`daily_indicators` 用 `date` 選日期。沿用 reader worker，使用 SQLite `mode=ro`，不進 db-writer 或 predictor。

查詢與輸出均限制在開發段，最晚 2021-12-31。計分／預測點另外要求 outcome 到期日也在開發段內，年底跨界的答案不列入。實驗 config、保留段切點與原始未授權資料不回傳。前半另外檢查日期上限、H、實驗、split 與 request id；切換天期時銷毀前一個元件，舊回覆不會滲入新畫面。

四個出口全部通過實際 Outbox 編碼測試；最大約 242 KB，低於 900 KB。讀取快取包含來源摘要與開發段結果內容；變更已存預測會重算，來源摘要不符會拒絕呈現。

## 真資料核對與真殼

[唯讀核對結果](verification/evolve-8-data-audit.json)：禁止網路 client 下呼叫數 0；54 列成績、54 組區間、153 列頻率均與第 6／7 輪已審過報告一致。查核前後資料庫 33 張表的內容摘要完全相同，帳本仍是 1,222。沒有對保留段或已曝光段新增標籤、預測、成績或揭露。

[真殼證據](verification/evolve-8-shell-evidence.json)：獨立 `127.0.0.1:51321`，未設定 `SSL_CERT_FILE`，這次唯讀驗收也移除兩把 API key。殼 catalog 有 trend-cast，狀態進入 running，頁面錯誤 0。驗過三天期、三種圖範圍、圖點選日期、全部指標頻率與切回 30 分鐘。所有送出的指令皆為查詢，所有多日回覆均不含開發段以後的日期；深淺主題無橫向溢出。

- [淺色完整截圖](verification/evolve-8-light.png)
- [深色完整截圖](verification/evolve-8-dark.png)

驗收殼完成後已關閉；沒有留下本輪背景瀏覽器或測試殼，沒有建立 `.playwright-mcp` 暫存目錄。上層既有 13:36 的 console 日誌未動；[清理紀錄](verification/evolve-8-cleanup.json)。

## 測試與變異

- [Python 完整輸出](verification/evolve-8-python-tests.txt)：`/usr/local/bin/python3 -m unittest -v`，**354 項通過**。
- [前半完整輸出](verification/evolve-8-front-tests.txt)：`npm test`，**43 項通過**（34 項原有 30 分鐘測試＋9 項多日與切換整合測試）。
- [多日後端 13／13 變異轉紅](verification/evolve-8-view-mutations.txt)：日期／到期日／prediction 查詢界線、config 洩漏、除權息參考價、圖範圍、兩個考生、對錯顏色、差值方向、樣本警示、指標漏項、快取失效與保留段文字。
- [多日前半 13／13 變異轉紅](verification/evolve-8-front-mutations.txt)：預設天期、跨元件舊回覆、未授權日期與文字、日期選擇、兩種標記與顏色、n=29 警示、數值欄位、HTML 注入、保留段文字、切換清除，以及舊版確認操作的 status 回覆。
- [原 30 分鐘前半 40／40 變異回歸轉紅](verification/evolve-8-intraday-mutations.txt)。

後端測試會故意在 fixture 插入保留段／已曝光段資料，以及開發段起點但到期日跨界的 outcome，驗證所有多日出口皆不包含這些內容。實際 reader worker 也在禁止網路 client 下測過四個 op，沒有寫入或 HTTP。

## 檔案與重跑

新增主要程式：`back/daily_view.py`、`front/daily.js`、`front/intraday.js`；接線調整 `back/runtime.py`、`front/front.js`、`front/style.js`。測試設定更新 `package.json`，原有前半測試與變異改指向原樣保留的 intraday 元件。

新增測試／驗證：`tests/test_daily_view.py`、`tests/daily_front.test.mjs`、`scripts/mutation_daily_view.py`、`scripts/mutation_daily_front.py`、`scripts/check_daily_view.py`、`scripts/check_evolve8_shell.mjs`。本頁與所有證據在 `docs/verification/evolve-8-*`。

```sh
/usr/local/bin/python3 -m unittest -v
npm test
/usr/local/bin/python3 scripts/mutation_daily_view.py
/usr/local/bin/python3 scripts/mutation_daily_front.py
/usr/local/bin/python3 scripts/mutation_front.py
/usr/local/bin/python3 scripts/check_daily_view.py
```

真殼需自行在獨立 port 起好，再執行 `node scripts/check_evolve8_shell.mjs http://127.0.0.1:PORT`；腳本只發查詢，會保存深淺截圖及證據。
