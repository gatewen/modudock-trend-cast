# 第 5 輪：多日資料層

完成 SPEC §15.8 第 1 步，真實 jev 呼叫 **0 次**，帳本維持 **644／3,000**。所有新增資料在同一個 `data/trendcast.sqlite3` 的 `d_` 表；19 張原有資料表逐表雜湊前後相同，實驗 1–3 沒有變動。本輪沒有建立新實驗或揭露保留段，沒有 commit。

## 來源查證與選擇

採 **FinMind 免費個股查詢**，沒有使用 token、帳號或 sponsor。實測每個資料集各一個 GET，指定 `data_id=2330`、`start_date=2010-01-04`、`end_date=2026-09-24`；HTTP 200、API status 200，整段資料成功回傳。另一次查詢取得獨立交易日曆，用來檢查日 K 缺漏。

| 資料 | 實測筆數 | 實際起始日 | 最後日 |
|---|---:|---|---|
| TaiwanStockInstitutionalInvestorsBuySell | 15,593 個法人分類列／3,525 日 | 2012-05-02 | 2026-09-24 |
| TaiwanStockMarginPurchaseShortSale | 4,101 日 | 2010-01-04 | 2026-09-24 |
| TaiwanStockTradingDate | 4,101 日 | 2010-01-04 | 2026-09-24 |

FinMind 文件的資料集涵蓋年代不代表 2330 每一天都有資料。本輪以實測起始日為準，缺少法人資料的早期日子是 `null`／無資料，不補成買賣超 0。既然免費個股整段查詢可行，這輪沒有啟動 TWSE 籌碼日報逐日回補。

- [FinMind 籌碼文件](https://finmind.github.io/tutor/TaiwanMarket/Chip/)：兩個個股資料集的參數、欄位、法人分類與公布時段；全市場單日查詢是另一個付費使用方式。
- [FinMind 官方資料集權限表](https://github.com/FinMind/FinMind-MCP/blob/master/knowledge/datasets.md)：兩者列為 `Free(w/ data_id)`。
- [FinMind 免費額度](https://finmind.github.io/quickstart/)：未登入 300 次／小時，註冊並驗證信箱後 600 次／小時；新 client 的匿名預設節流為 12 秒。
- [FinMind 交易日曆](https://finmind.github.io/tutor/TaiwanMarket/Technical/#taiwanstocktradingdate)：只取交易日期，不用個股日 K 自己推定交易日。
- [富果日 K API](https://developer.fugle.tw/docs/data/http-api/historical/candles/)：`timeframe=D`、`adjusted=false`，每年獨立請求，單次小於一年。
- [TWT49U 2010 實測網址](https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=20100104&endDate=20101231&response=json)：2010 的民國年是兩位數 `99`，已擴充共用解析器，同時保留三位年份支援。日線同步使用獨立 3 秒 limiter，包含重試。

查證證據：[FinMind 實測摘要與 payload SHA256](verification/evolve-5-finmind-probe.json)。原始公開回應留在被 gitignore 的 `data/evolve-5-probe/`，沒有金鑰。

## 確認後採用的計算口徑

- 預測點維持 13:30。法人／融資資料晚於收盤公布，因此 **籌碼最多只用前一交易日**。`chips_previous_sessions` 的最後一筆固定是前一交易日；缺值保留 null。
- 跨 2017 年的外資定義採外資合計：`Foreign_Investor + Foreign_Dealer_Self`。拆分前沒有後者，拆分後若必要分類缺失，記為無資料，不默認 0。
- 暖機為前 60 個交易日（2010-01-04～2010-04-06），第一個可預測點 **2010-04-07**。日 K 特徵取含當天的最近 60 日；每列 OHLC 相對該日 `ref_i`，只輸出相對值。量比取該列之前 20 日均量，歷史不足／均量為零時為 null。
- `ref_i`：已確認無事件時用前收；有事件用公告參考價，且檢查公告前收與原始日 K 一致；未知則不可預測／不可作為報酬區間內日期。
- 報酬為 `∏(close_i / ref_i) − 1`，以 `Fraction` 連乘。H 固定為 3／7／14 個交易日。日曆與日 K 任一方缺漏、終點不足、區間有未知除權息，都不評分。可預測性不讀 t 之後資料，也不依賴當下尚未知的終點是否可評分。
- `k_H`：可預測且可評分，且起點、終點都在開發段的報酬；母體標準差的一半，以 0.5% 刻度 HALF_UP。透過整數平方根與分數平方比較完成四捨五入，不依賴浮點 sqrt；若門檻為 0 則拒絕。門檻上下界的等號分別屬 up／down。
- `development_thresholds()` 硬性拒絕晚於 2021-12-31 的擬合範圍。門檻目前由函式回傳，下一步建立多日實驗時再寫入凍結 config。

## 真資料結果

資料日期 2010-01-04～2026-09-24：**4,101 個交易日、4,101 日 K、缺日 0、日曆外日 K 0、除權息 39 次、未知除權息日 0**。

以下僅開發段，H 終點不跨入保留段：

| H | k_H | 可預測且可評分點 | up | flat | down | 最後起點 |
|---:|---:|---:|---:|---:|---:|---|
| 3 | 1.0% | 2,891 | 1,055 | 1,020 | 816 | 2021-12-27 |
| 7 | 2.0% | 2,887 | 940 | 1,378 | 569 | 2021-12-21 |
| 14 | 2.5% | 2,880 | 1,153 | 1,184 | 543 | 2021-12-10 |

完整 JSON：[資料品質清單](verification/evolve-5-inventory.json)、[開發段門檻與標籤](verification/evolve-5-development.json)。沒有計算或輸出 2022-01-03～2024-07-25 保留段的報酬／標籤統計，也沒有用已曝光段訓練。

除權息真實 fixture：2010-07-05 收 61.40；07-06 公告參考價 58.40、實際收 59.90；07-07 收 59.50、07-08 收 60.40。三交易日調整報酬檢查為 `60.40 / 58.40 − 1`，而非 `60.40 / 61.40 − 1`；fixture 同時保留 TWSE 公告與 Fugle 原始日 K。

## 檔案與驗證

新增：`back/daily_sources.py`、`back/daily_store.py`、`back/daily_sync.py`、`back/daily_replay.py`、`scripts/sync_daily.py`、`scripts/check_daily.py`、`scripts/mutation_daily.py`、`tests/daily_fixture.py`、`tests/test_daily.py`、兩份 2010 年真實 fixture。共用部分只新增 FinMind 固定 host／節流，以及 TWT49U 的兩位民國年解析。

新表：`d_calendar`、`d_bars`、`d_corp_events`、`d_corp_coverage`、`d_institutional`、`d_margin`、`d_fetch_log`。每個年／查詢區間的資料與成功紀錄一起提交；不完整回應、空日 K、失敗交易不清除舊資料。同步可續跑；完成後以禁止網路 client 重跑，37 個區間全部跳過、0 筆寫入。

- 全套 **298 項通過**，[unittest 完整輸出](verification/evolve-5-python-tests.txt)；其中日線 **17 項通過**，[日線測試完整輸出](verification/evolve-5-daily-tests.txt)。測試使用 `/usr/local/bin/python3` 與標準庫。
- [30／30 變異對照](verification/evolve-5-mutations.txt)：包括未來資料進入特徵、當日籌碼洩漏、漏日壓縮天期、錯用前收、連乘改加總、Fraction 邊界改浮點、母體改樣本標準差、跨開發段終點、交易 rollback 改 commit 等。
- [同步紀錄](verification/evolve-5-sync.txt)、[禁止網路重跑](verification/evolve-5-resume.json)。真資料同步沒有設 `SSL_CERT_FILE`，也沒有載入 TypeSafe 金鑰；`no_jev()` 同時禁止 JevClient 的 predict 與 transport。
- [原表驗證前](verification/evolve-5-legacy-before.json)、[驗證後與額度帳本](verification/evolve-5-legacy-after.json)：19 張原有資料表完全一致，jev 額度增量 0。

重跑命令：`/usr/local/bin/python3 scripts/sync_daily.py --execute`（需 Fugle 環境金鑰）；`/usr/local/bin/python3 scripts/check_daily.py`（唯讀，只報開發段）。`sync_daily.py` 未帶 `--execute` 不發請求。
