# 第 7 輪交件：jev_ind（p6）共同 576 天

實驗 4 的 576 個開發段抽樣日全部完成，三題共 1,728 筆預測；缺答 0 日／0 題。HTTP **578 次**（含 2 次補跑），本輪上限 650，帳本由 644 增為 **1,222／3,000**。兩次 HTTP 200 回應因 `choice_not_maximum` 未通過驗證，三題整批拒收後補跑成功。總耗時 124.978 秒，包含凍結 payload 的準備時間。

只跑開發段；本輪沒有對保留段、已曝光段或舊版前瞻段新增預測、標籤、成績或揭露。沒有 commit、push，也沒有修改 news 或殼。

## Prompt 修正

三個籌碼欄位 `foreign_net`、`trust_net`、`margin_chg` 均只輸出兩位小數百分比，或完整字串「本期無此資料」。分母是截至前一交易日的 20 個交易日平均成交量（股）；外資與投信原始淨額為股，融資餘額差為張，先乘 1,000 換為股。除法使用 Fraction，最後以 Decimal HALF_UP 取兩位，零不帶負號。

單位查證：[FinMind 籌碼文件（融資融券單位張）](https://finmind.github.io/tutor/TaiwanMarket/Chip/)、[TWSE 三大法人個股買賣超日報（買進／賣出股數）](https://www.twse.com.tw/rwd/zh/fund/T86?response=html&selectType=ALLBUT0999)。富果日 K 的成交量單位沿用既有資料層查證，與 §4.1 一致。

- [完整 p6 instructions／criteria](PROMPT-P6.md)，由程式直接產生。
- [實際格式的範例 HTTP body](verification/evolve-7-example-payload.json)，不帶日期、代號、絕對價格或原始籌碼數量。
- [凍結執行計畫](verification/evolve-7-run-plan.json)，固定日期清單、設定與每一日 request body hash，之後重跑必須完全相同。

抽樣仍為只看交易日清單的每 5 日一點；579 個節拍點只依日期排除最後 3 個跨開發段終點的 H14 點，三天期與兩個比較固定同一份 576 天。樣本 SHA256：`c97a1e113f8669095cd96fa04cd9c8bd86c41676ca8601897ca90f3bd52dd24f`；執行計畫 SHA256：`4c0500438f8385af5ce980367d9a2d8aa620b068dc9444ae0ecce072ff5cb53e`。

## 執行與保存

新增 `scripts/run_daily_jev.py`：只有明確 `--execute` 才執行，僅接受實驗 4、`split=dev`。正式命令如下，金鑰由 process environment 取得，不寫檔；此次沒有設定 `SSL_CERT_FILE`。

```sh
/usr/local/bin/python3 scripts/run_daily_jev.py --db data/trendcast.sqlite3 --experiment 4 --split dev --execute --max-calls 650 --report docs/verification/evolve-7-development.json
```

研究 DB 新增 `d_jev_plan` 與 `d_jev_requests`，`d_predictions` 新增 `jev_ind`。每個時點一次請求三題，全部驗證後在同一個交易中保存，commit 才算完成；保留原始已驗證回應。六並發、認證失敗永久停、429／529 退避及 TLS 驗證沿用既有協定。

額度使用原有全場帳本 `data/evolve-2026-09-27-budget.sqlite3`。每次 HTTP 前先用單一交易同時扣全場額度及記錄本輪嘗試；含重試、跨 process 重啟仍限制本輪累計最多 650 次，不重複扣款。

## 結果與獨立核對

- [完整開發段報告：各方法數字、六組差值與區間、choice 分布](verification/evolve-7-development.md)。
- [完整 JSON（含 Wilson、混淆矩陣與各類指標）](verification/evolve-7-development.json)。
- [獨立 SQL／bootstrap 核對](verification/evolve-7-independent-audit.json)：9 組方法與天期成績、6 組區間均一致；所有 576 份 payload 的籌碼均為相對百分比或指定缺值文字。
- [禁止網路的完整重跑](verification/evolve-7-resume.txt)：`ForbiddenClient.guard()` 下 576 天全部跳過、HTTP 0 次、無新增答案、帳本仍 1,222；三天期報告與第一次逐欄完全相同。

兩個比較皆採完整開發交易日序列切不重疊 20 日塊、尾塊保留，配對 bootstrap 2,000 次、seed 20260927。六組 Brier 差區間均整段大於 0，表示這份開發段抽樣上的 jev_ind Brier 比兩個參考方法差。

原有 17 個方法預測與 30 分鐘版 19 張資料表的前後摘要相同。新增結果以外的舊實驗／揭露紀錄未變；repository 交件檔沒有兩把設定金鑰。

## 測試與變異

- [全部 Python unittest 完整輸出](verification/evolve-7-python-tests.txt)：**345 項通過**，使用 `/usr/local/bin/python3`。
- [本輪 prompt／client／runner 測試](verification/evolve-7-jev-tests.txt)：19 項通過。涵蓋實際 payload 不洩漏日期／代號／絕對價格，籌碼只允許 `-?數字.兩位%` 或指定缺值文字、張轉股、分母不含當日；未來資料擾動不改 bytes；三題回應驗證、原子 commit／rollback、額度／重試、取消、dev 守衛與零 HTTP 重跑。
- [Prompt 15／15 變異轉紅](verification/evolve-7-prompt-mutations.txt)：包含拔掉融資乘 1,000、分母加入當日、缺值變零、未來資料／同日籌碼洩漏等，每項均列出抓到的測試。
- [Client／runner 13／13 變異轉紅](verification/evolve-7-jev-mutations.txt)：包含額度與重試、回應／模型驗證、重跑跳過、交易 rollback、取消及 holdout 守衛。
- [既有日線研究 44／44 變異回歸轉紅](verification/evolve-7-research-mutations.txt)。

## 程式檔案

新增：`back/daily_prompt.py`、`back/daily_jev_client.py`、`back/daily_jev.py`、`scripts/preview_daily_prompt.py`、`scripts/run_daily_jev.py`、`scripts/mutation_daily_prompt.py`、`scripts/mutation_daily_jev.py`、`tests/test_daily_prompt.py`、`tests/test_daily_jev.py`。

調整：`back/daily_run.py`、`back/daily_score.py` 的既有 17 方法查詢排除新增 `jev_ind`，讓原有報告與重跑不受新方法影響；未改原方法公式。文件與驗證輸出均在本頁及 `docs/verification/evolve-7-*`。
