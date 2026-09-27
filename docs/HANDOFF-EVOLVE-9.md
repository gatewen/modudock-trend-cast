# 第 9 輪：每日多日前瞻紀錄

已實作 SPEC §15.12 與 2f1dd8a 補記。本輪真 jev HTTP **0 次**，帳本 **1,222／3,000**；真 DB 前瞻預測 **0 筆**、前瞻 outcome **0 筆**。沒有 commit，沒有改 news 或殼的程式。

## 行為

- 後半啟動、每次同步結束，以及開著殼時每 15 分鐘檢查一次。日資料由富果 D（未調整）、TWT49U、FinMind 增量補齊。依資料來源的交易日曆判定，日 K 取得且已過 16:30 才產生。新增資料與抓取紀錄共用既有 DBWriter；既有已觀察值若被來源修正，拒絕悄悄覆寫。
- 只接受實驗 4 建立日之後的起點。四方法為 majority、ind_logit、vol_prior_d、jev_ind，各有 3／7／14 日預測。前三者由完整、可評分開發段凍結訓練；不把後來揭曉的答案加入訓練。首次需要前瞻預測才建立凍結模型；持久化參數、訓練摘要、程式摘要及模型 hash，後續不重新訓練。
- 實驗 digest、既有 p6 計畫的 questions／模型／籌碼正規化、PROTOCOL 都要吻合。沿用 p6 三題請求；籌碼仍用前一交易日，百分比正規化。bias20 與 vol 的切點也凍結於可評分開發資料。
- 每日先提交 input／payload，再持久化保留該日請求，才送 HTTP。回答三題全部驗證後於同一交易提交；失敗不留部分答案。`recorded_at` 在寫入交易內取得，重新判定時沿用原時間。
- 只有 429／529 可退避重試，跨重啟最多三次。每次 HTTP 前在同一個帳本交易增加全場額度與每日 attempt，沒有退還。下一交易日 09:00 整點起禁止重試。逾時、無法確認是否送出、其他失敗不自動重送。
- 未知下一交易日時，預測顯示「準時待確認」。重試只在能確定仍早於截止時進行：隔日 09:00 前可重試；過了這個保守下界就等待實際交易日曆，並不猜週末或假日。日曆補齊後依原時間判斷準時／補記，過期的重試標缺答。
- d+H 的完整日 K／除權息資料可取得後，使用原有 Fraction 調整報酬與凍結 k_H 寫 outcome。只計分前瞻表內起點。新 `daily_forward` 出口與原本四個開發段出口分開；後者仍截止 2021-12-31。
- 多日畫面最上方新增最新預測（四方法 × 三天期、答案與機率）、準時／補記／待確認成績、待到期清單與指定免責句。已到期成績直接顯示，無揭露操作。比較採 20 交易日區塊 bootstrap；不足兩個區塊標示樣本少、不下結論。

## 日期更正與 fixture

證交所公告 **2026-09-28 教師節休市**，所以完整 fixture 以 **09-29 收盤後**開始，另測 09-28 不產生預測。這是依官方日曆修正原任務與 SPEC 的日期例子，實作沒有硬編碼首個交易日。

官方來源：[證交所開休市日期表](https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=html)。FinMind 的交易日期資料是觀察到的交易日，不拿它猜未來假日。

[完整模擬紀錄](verification/evolve-9-fixture-flow.json)：合成資料、禁止 Jev transport；起點 1 日產生 12 筆預測，最初皆待確認，補齊下一交易日後全部按原始時間轉為準時；三個天期各寫一筆 outcome，待到期歸零。重複保留請求被拒絕。模擬中其餘日期與價格皆為測試 fixture，不是實際行情或正式成績。

另有服務整合測試：五種來源（calendar／法人／融資券／日 K／除權息）的假增量回補 → 自動預測；以及重啟後從已記錄 429 接續、真實 HTTP body 對假 server、09:00 退避截止、三次總上限、全場額度、提交中途失敗回滾。

## 驗收證據

Python **374 項全過**（本輪專項 20 項）；前半 **47 項全過**。後端變異 **26／26**、新前半變異 **8／8**、多日前半回歸變異 **13／13** 全部轉紅。

- [Python 完整輸出](verification/evolve-9-unittest.txt)、[本輪專項輸出](verification/evolve-9-targeted.txt)、[前半完整輸出](verification/evolve-9-front.txt)。
- [26 組後端變異逐條對照](verification/evolve-9-mutations.txt)、[8 組新前半變異](verification/evolve-9-front-mutations.txt)、[13 組既有多日前半變異](verification/evolve-9-front-regression-mutations.txt)。包括移除凍結範圍／16:30／09:00 邊界、改用當日籌碼或未來價格、取消保留請求、只存兩題、提早到期、把標籤強制盤整、取消三次上限／帳本／未知日曆守衛、跳過啟動或同步後工作、隱藏缺答或少樣本警語等。
- [真 DB 稽核](verification/evolve-9-real-audit.json)：最新日 K 2026-09-24，三天期出口皆空，沒有新增舊實驗結果。每次稽核前後對所有既有表內容做 SHA-256 比對，包含新增的空前瞻表也保持不變。
- [真殼證據](verification/evolve-9-shell-evidence.json)：127.0.0.1:62053，`SSL_CERT_FILE` 未設定；移除 API key 環境以維持本輪 0 次真 API。catalog 有 trend-cast、進入 running、三個多日天期都載入、30 分鐘畫面仍正常、0 頁面錯誤。驗收後已關殼，未建立 Playwright 暫存目錄。
- [淺色截圖](verification/evolve-9-light.png)、[深色截圖](verification/evolve-9-dark.png)。畫面已逐張檢視。

## 檔案

新增後半：`back/daily_forward.py`、`daily_forward_client.py`、`daily_forward_service.py`、`daily_forward_view.py`、`daily_incremental.py`。

修改文件：`README.md`。

修改後半：`back/runtime.py`（接啟動、同步及讀出口）；`back/daily_prompt.py`（可傳入凍結 bias 狀態，既有 p6 呼叫預設行為不變）。

新增前半：`front/prospective.js`；修改 `front/daily.js`、`front/front.js`、`front/style.js`。

新增測試：`tests/test_daily_forward.py`；修改 `tests/daily_front.test.mjs`。新增變異腳本 `scripts/mutation_daily_forward.py`、`mutation_daily_forward_front.py`；更新 `mutation_daily_front.py` 的原守衛定位。

新增驗證腳本：`scripts/check_daily_forward.py`、`check_daily_forward_fixture.py`、`check_evolve9_shell.mjs`，以及本文件與 `docs/verification/evolve-9-*`。

## 重跑

```sh
/usr/local/bin/python3 -m unittest discover -v
npm test
/usr/local/bin/python3 -m scripts.mutation_daily_forward
/usr/local/bin/python3 -m scripts.mutation_daily_forward_front
/usr/local/bin/python3 -m scripts.mutation_daily_front
/usr/local/bin/python3 -m scripts.check_daily_forward_fixture
/usr/local/bin/python3 -m scripts.check_daily_forward
```

真 DB 稽核腳本固定檢查本次 09-27 空狀態與帳本 1,222；未來開始實際前瞻紀錄後，它不再適合作為一般健康檢查。
