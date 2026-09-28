# 第二場第 5 輪：切換天期、缺值稽核與白話名稱

三項完成。**0 次 jev，帳本仍 1,222／2,222；未 commit／push。** 未新增研究方法、改動前瞻名單或重新產生預測。

## 快速切換天期

問題是上一個畫面的回覆雖已被忽略，讀取仍會在後端堆積。現在前端將所有唯讀操作排隊，最多 6 個已送出未回覆的讀取，為後端自身的狀態讀取保留容量；同一操作尚未送出時，只保留最新日期／範圍／刷新。

切換天期立即清掉舊畫面的未送出請求及回覆路由。已送出的讀取不強行中斷 Python 計算，等其完成後釋放名額，依 generation 只送新畫面請求。過期回覆可釋放名額，但不能更新畫面；錯誤回覆也會釋放名額，卸載後不再送出排隊請求。沒有隱藏當前畫面的錯誤，也沒有放大後端佇列。

單元測試在全部舊回覆尚未返回時連切 **3→7→14→3**，並連續送 25 次前瞻刷新通知：在途數不超過 6、未送出的舊天期讀取被丟棄、最後為 3 日且資料正確。另覆蓋同天期重新進入後的舊回覆、過期 error、卸載與 30 分鐘原有動作回覆。

## 缺值碼的稽核狀態

`MarketHistory.available()` 在報價年齡合格但最新報價無有效值時，將狀態標為 `missing_code`。匯率同時檢查 spot_buy／spot_sell；沒有紀錄仍為 missing，超過 5 日仍為 stale。**回傳的報價列、窗口、數值特徵及狀態特徵完全不變**，不往前找有效值。

實際 DB 只有一筆需要更正：`market_features` 的 **2012-01-03**，使用 **2012-01-02 USD/TWD**（年齡 1 日），由 available 改為 missing_code。保留段同類錯標 **0 筆**，已封存的 `d_hold_*` 不變。

已執行 [更正工具](../scripts/repair_market_alignment.py)，只 UPDATE `alignment_json`，交易內核對其餘欄位與研究結果，任何額外變化都回滾。工具先從來源重建每筆特徵，逐值核對 `original_hash`、`input_json`、`input_hash`，只接受 available→missing_code 這一類修正；其他日期、年齡或內容不符會拒絕。重跑為 0 筆更正。

[更正前後證據](verification/evolve2-5-alignment-repair.json) 含逐欄差異與 17 組資料指紋。2,894 筆 market_features **排除唯一允許更新的 alignment_json 後**，前後 SHA-256 都是 `169a017d04bcba36ec27716e0274b3d7da77320fc48e1e095cee17ca9f5be065`；模型、預測、outcomes、原三個 digest、保留段封存與 reveals 全部不變。整張 market_features 連同稽核欄位的舊全表摘要會因這筆授權修正而改變，不應把它誤認為特徵 hash 改變。

[成績核對](verification/evolve2-5-view-audit.json)：新增 7 方法 × 三天期的樣本、準確率、Brier、差、區間、入圍標示，及保留段三個比較／全部描述性成績，仍逐值吻合第 1–3 輪審核報告。

其他舊 DB 副本可先預覽再執行同一修正；本機正式 DB 已修好：

```sh
/usr/local/bin/python3 -B scripts/repair_market_alignment.py \
  --db data/trendcast.sqlite3 --report data/alignment-preview.json
/usr/local/bin/python3 -B scripts/repair_market_alignment.py \
  --db data/trendcast.sqlite3 --execute --report data/alignment-repair.json
```

## 白話名稱

共用 `front/method_names.js` 為全部多日方法提供白話名稱與小字代號：組合預測（ens_avg）、大環境組合模型（mkt_logit）、猜最常見答案（majority）、jev 讀指標（jev_ind）、jev 讀新聞（jev_news），以及 16 個單一指標。研究結論、保留段、開發段、前瞻表格與比較文字均使用同一套名稱；協定、資料庫與 `data-method` 仍用原代號。文字透過 DOM text node 建立，不插入 HTML。

## 驗證與截圖

- [完整 unittest](verification/evolve2-5-unittest.txt)：**468／468**，196.995 秒；[npm test](verification/evolve2-5-frontend.txt)：**59／59**。
- 變異 **74／74**：[新前半 6](verification/evolve2-5-new-front-mutations.txt)、[新稽核 5](verification/evolve2-5-new-market-mutations.txt)、[既有前半 13](verification/evolve2-5-front-mutations.txt)、[前瞻 8](verification/evolve2-5-forward-front-mutations.txt)、[市場 32](verification/evolve2-5-market-mutations.txt)、[研究卡 8](verification/evolve2-5-research-front-mutations.txt)、[已使用狀態 2](verification/evolve2-5-hold-front-mutations.txt)。排隊改變後也更新測試回覆時序，確保日期守衛的變異仍真正被抓到。
- [真殼證據](verification/evolve2-5-shell-browser.json)：不等待載入，連切 3→7→14→3。連切結束當下開發段 **0 列**、前瞻 **仍在載入**；待回覆後最後為 3 日，mkt_logit n＝2,891、Brier＝0.660868。再驗證 7／14／3 日切換、25 個開發方法與 24 個描述性方法。page error、WebSocket error（含 busy）皆 **0**。
- [收尾](verification/evolve2-5-shell-evidence.json)：使用 **127.0.0.1:59975** 的暫存殼及 DB 副本，沒有 API 金鑰或 SSL_CERT_FILE，模組網路嘗試 0。殼 exit 0、port 已關、scratch 已刪除。未使用 8731、未修改 news／殼、未安裝排程。

六張截圖已逐張檢視；開發段表格另檢查在真殼欄寬內完整顯示、不裁切：

| 畫面 | 淺色 | 深色 |
|---|---|---|
| 研究結論 | [light-summary](verification/evolve2-5-light-summary.png) | [dark-summary](verification/evolve2-5-dark-summary.png) |
| 保留段及展開表 | [light-holdout](verification/evolve2-5-light-holdout.png) | [dark-holdout](verification/evolve2-5-dark-holdout.png) |
| 開發段完整表 | [light-development](verification/evolve2-5-light-development.png) | [dark-development](verification/evolve2-5-dark-development.png) |

真殼重現：`env -u SSL_CERT_FILE /usr/local/bin/python3 -B scripts/check_daily_evolve5_shell.py`。
