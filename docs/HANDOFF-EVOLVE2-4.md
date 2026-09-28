# 第二場第 4 輪：把研究結論做進產品

多日畫面與 README 已同步 SPEC §16.6。研究結論卡置於最上方，接著是已使用的保留段卡片。**本輪 0 次 jev，帳本仍 1,222／2,222；沒有新增研究方法或改動前瞻名單。未 commit／push。**

## 畫面與資料出口

- 研究結論短文列出 11 個技術／籌碼指標、5 個大環境指標、2 個組合模型、ens_avg、jev 讀指標、jev 讀新聞（前瞻中）。交代開發段 3 組小幅入圍、ens_avg 3 日邊緣、一次性保留段三者皆無證據較好、jev 讀指標在開發段顯著較差、2026-09-29 起累積前瞻，以及「這不是投資建議」。
- 保留段同時列出 3 個主要比較，不隨天期選單省略。可展開所選 3／7／14 日的 **24 個已封存方法**描述性成績（樣本、準確率、Brier、覆蓋率、缺答），不新增優劣比較或入圍標示。
- 開發段成績增加 ens_avg、5 個大環境單一指標與 mkt_logit，共 **25 列**。入圍仍要求資料完整且差值區間整段小於 0；ens_avg 3 日標「邊緣」。補上 18 個比較、預期約 0.45 個運氣入圍的說明。
- 新增唯讀 `daily_holdout`，有效的實驗 4 揭露紀錄存在後，才呼叫原封存報告並重新核對模型／預測／outcomes 摘要。未揭露僅回傳 locked metadata，不讀取保留段成績。前半另驗證 request、天期、實驗、split、state 與固定範圍；開發段走勢／指標／成績日期守衛維持原界線。
- 新方法使用既有已保存的開發段機率；ens_avg 重建當下三個機率的算術平均。市場輸入與結果沿用原摘要驗證，快取含新增預測內容。沒有重訓、抓資料、寫 outcomes 或再次揭露。

## 顯示數字

| 主要比較（考生 − majority） | 差 | 95% 區間 | 用語 |
|---|---:|---|---|
| ens_avg 3 日 | −0.00008 | [−0.00252, +0.00242] | 沒有證據顯示 ens_avg 比簡單方法好 |
| ens_avg 7 日 | −0.00405 | [−0.00877, +0.00100] | 沒有證據顯示 ens_avg 比簡單方法好 |
| mkt_logit 3 日 | +0.00592 | [−0.00211, +0.01453] | 沒有證據顯示 mkt_logit 比簡單方法好 |

負值較好，20 交易日區塊 bootstrap 2,000 次、固定種子。3 個事先指定比較，預期約 0.075 個因運氣顯著較好；名目估算、未作多重比較校正。

[畫面投影核對](verification/evolve2-4-view-audit.json)：新增 7 方法 × 三天期的樣本、準確率、Brier、差、區間與入圍標示，逐值吻合第 1／2 輪審核報告；保留段 3 個比較與 24 方法 × 三天期描述性指標，逐值吻合第 3 輪封存報告。使用真 DB 唯讀連線，total_changes＝0。

[獨立 SQL／算術驗算](verification/evolve2-4-independent-audit.json)：3／3 主要比較一致；原三個 digest、七張開發段結果表、最後擬合參數不變，foreign_key_check 無錯。

## 驗證

- [完整 unittest](verification/evolve2-4-unittest.txt)：**466／466**，200.154 秒。
- [npm test](verification/evolve2-4-frontend.txt)：**56／56**。
- 變異共 **41／41** 被抓到：[新前半 8](verification/evolve2-4-research-front-mutations.txt)、[新出口 5](verification/evolve2-4-research-view-mutations.txt)、[既有前半 13](verification/evolve2-4-front-mutations.txt)、[既有出口 13](verification/evolve2-4-view-mutations.txt)、[已使用狀態 2](verification/evolve2-4-hold-front-mutations.txt)。包括未揭露數字洩漏、錯誤 split／天期／已曝光終點、差值與區間符號錯誤、描述性考生漏列、均值改成 majority、快取忽略市場預測等。
- [真殼瀏覽器](verification/evolve2-4-shell-browser.json)：3／7／14 日均 25 列開發成績、24 列描述性成績；入圍名單依序為 ens_avg＋mkt_logit、ens_avg、無。主要比較始終 3 列，日期控制仍不超過 2021-12-31；頁面錯誤與 WebSocket error 回覆均 0，私有路徑 404，只送讀取操作。驗收等整頁（含前瞻區）載入後再切換；連續操作超過原有讀取佇列容量時仍保留 busy 提示。
- [真殼收尾](verification/evolve2-4-shell-evidence.json)：另開 **127.0.0.1:54765**，scratch 模組＋SQLite backup；移除 API 金鑰及 SSL_CERT_FILE，模組網路嘗試 0。驗完 exit 0、port 關閉、scratch 刪除；未使用 8731，未修改殼或 news，未安裝排程。完整測試的本機假 HTTP 服務在允許 loopback 的執行環境驗證。

## 截圖

所有截圖均來自真殼並逐張檢視；展開表格用較高的瀏覽器視窗完整截取，沒有改動殼樣式。

| 畫面 | 淺色 | 深色 |
|---|---|---|
| 研究結論與主要比較 | [light-summary](verification/evolve2-4-light-summary.png) | [dark-summary](verification/evolve2-4-dark-summary.png) |
| 保留段與展開描述表 | [light-holdout](verification/evolve2-4-light-holdout.png) | [dark-holdout](verification/evolve2-4-dark-holdout.png) |
| 開發段完整 25 列 | [light-development](verification/evolve2-4-light-development.png) | [dark-development](verification/evolve2-4-dark-development.png) |

重現：`/usr/local/bin/python3 -B scripts/audit_daily_research_view.py`；真殼 `env -u SSL_CERT_FILE /usr/local/bin/python3 -B scripts/check_daily_research_shell.py`（腳本內移除金鑰、隔離副本並收尾）。
