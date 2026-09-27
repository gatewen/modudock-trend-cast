# 第 6 輪：多日實驗 4、17 個方法的開發段評估

**三個天期皆無入圍者。** 本輪只執行開發段，使用標準庫與 `/usr/local/bin/python3`（3.12，logit 內積用 `math.sumprod`）。真實 Jev／其他 HTTP 均為 0，帳本維持 **644／3,000**。`ForbiddenClient.guard()` 同時封鎖 Jev client、HTTP transport、DNS 與 socket 連線；三個天期的 worker 各自套用守衛。不載入金鑰。

## 凍結與資料隔離

多日實驗 **4** 在 `data/trendcast.sqlite3` 的 **`d_experiments`**，與 30 分鐘版 `experiments` 分開。`d_features`、`d_predictions`、`d_outcomes`、`d_fits` 分別保存可預測點、預測、已到期標籤與每次重訓的模型。

凍結範圍為 2010-01-04～2024-07-25；暖機結束 2010-04-06，第一個可預測日 2010-04-07。開發段至 2021-12-31；保留段 2022-01-03～2024-07-25。2024-07-26 起不參與此次特徵、訓練或評分。

門檻重新依已定稿的計算規則求得並凍結：**3 日 1.0%、7 日 2.0%、14 日 2.5%**。完整指標名單、數值欄位、狀態、各參數、標準化／缺值／重訓口徑、bootstrap 設定與 FinMind 摘要均納入 config 與 digest；原始日 K、日曆、除權息事件／coverage、法人／融資、來源紀錄各自雜湊。完整原始資料摘要會讀取凍結範圍的保留段原始列以雜湊，但不計算其特徵、報酬或標籤；執行與報告的資料查詢只到開發段。

建立實驗在 SQLite `BEGIN IMMEDIATE` 交易中確認暖機、日曆／日 K 一致、日曆查詢 coverage、除權息已知，再凍結；CLI 使用與日線同步共用的檔案鎖。資料表 trigger 拒絕覆寫／刪除／插入凍結範圍；實驗 config 不可改刪。結果表 trigger 也拒絕開發段以外的寫入及跨界 outcome 終點。同步若涵蓋凍結區間會拒絕；後續追加需使用凍結範圍以後的查詢區間。

參數證據：[事先寫出的 protocol](verification/evolve-6-protocol.json)、[實驗 4 凍結紀錄與 digest](verification/evolve-6-experiment.json)。

## 看成績前固定的口徑

使用者已確認：RSI Wilder（首 14 個價差簡單平均）、KD 初值 K=D=50／平盤 RSV=50、EMA 首日值初始化、近 3 日交叉取最新、量比均量含當日、平盤獨立狀態；logit 零起始、平均交叉熵＋λ/2 權重平方、截距不罰；bootstrap 不重疊 20 日塊且保留尾塊。

調整價格採向前連乘：首日收盤指數 1；之後當日 OHLC 乘以前日調整收盤／當日參考價。新增未來除權息不回改歷史。指標值皆由截至 t 的這條序列計算。

| 指標 | logit 數值欄位／補充口徑 |
|---|---|
| ma_cross | SMA5/SMA20−1；上穿含前日等號，下穿同理；3 日含當日 |
| ma_trend | 收盤/SMA60−1；等於均線為中性 |
| rsi14 | RSI；平均漲跌皆 0 → 50，只有平均跌為 0 → 100 |
| kd | K、D；9 日最高／最低取調整高低價；低／高檔交叉用當日 K 判定 |
| macd | DIF、signal、hist 各除以當日調整收盤；hist=DIF−signal，嚴格負翻正／正翻負，零另列 |
| bollinger | (收盤−SMA20)/母體標準差；標準差 0 時數值 0 |
| bias20 | 收盤/SMA20−1；已揭曉樣本線性插值三分位，等於切點歸低組 |
| vol_price | 當日調整報酬、量／含當日 20 日均量；均量 0 為缺值 |
| foreign_net / trust_net | 前一交易日起往前 3 日合計；缺少任一必要資料為缺值 |
| margin_chg | 前一交易日餘額 − 再往前 5 個交易日餘額 |

共 15 個數值欄位、各訊號的固定 one-hot（含缺值狀態），加截距後 67 維。缺值以訓練均值補（標準化後為 0）；整欄缺值時均值 0、尺度 1。常數數值欄尺度 1。one-hot 不標準化。

每個 H 僅以可預測且可評分、起終點都在開發段、`t′+H ≤ t` 的紀錄訓練。majority 加 1 平滑；所有平手順序 flat→up→down。單一指標／vol_prior_d 組內不足 30 或當下無訊號資料時退回 majority。vol_prior_d 使用最近 **20 個日報酬**的母體標準差；分位切點只取該 H 已揭曉點，歷史樣本隨當前切點重新分組。

logit 每次由零權重做完整 300 次梯度下降，學習率 0.1、L2 λ=1，訓練不足 250 退回 majority。以第一個可預測開發交易日為重訓節拍起點，每 20 交易日檢查；均值／母體標準差與 bias20 one-hot 的分位切點只用該次已揭曉訓練集，兩次重訓間不變。模型紀錄保存 train_n、最後訓練起點／終點、fit_day、切點、標準化參數及權重。

## 完整結果

[開發段報告：三天期共 51 列成績、差值與區間、入圍名單、訊號頻率對照](verification/evolve-6-development.md)；[未四捨五入 JSON](verification/evolve-6-development.json)。

| H | 可預測點 | 評分交集 | 入圍 |
|---:|---:|---:|---|
| 3 | 2,894 | 2,891 | 無 |
| 7 | 2,894 | 2,887 | 無 |
| 14 | 2,894 | 2,880 | 無 |

每個方法每個 H 都有 2,894 筆答案，尾端不跨開發段產生標籤。全部方法覆蓋率 100%、缺答 0。實際新增 147,594 筆預測；logit 三天期分別重訓 132／132／131 次，首次達到 250 樣本且在重訓節拍上才開始。執行總耗時 **210.785 秒**。

ind_logit 的 3／7／14 日 Brier 分別為 0.661720／0.626444／0.644687；相對 majority 差值為 −0.003532／−0.004967／−0.000312，各自區間均跨 0，所以依事先規則全部未入圍。這不把準確率上升當作主要比較通過。

各天期使用所有 17 個方法共有答案的可評分交集。Brier 差＝方法 − majority。bootstrap 依完整開發交易日序列（含暖機末段但僅計有配對的點）切成不重疊 20 日塊、保留尾塊；同次抽樣對兩方法配對，按實際抽到的點數加權。2000 次，seed=20260927，線性插值 2.5%／97.5% 區間。區間上界 **嚴格 < 0** 才入圍；結果不完整不給入圍結論。

報告的「網路說法」是 SPEC 列出的待驗假說及常見方向解讀，不作有效性背書；頻率為同一交集的未平滑 up／flat／down 比例，含缺值狀態與 0 樣本狀態。單一指標實際預測仍只用當時已揭曉資料，描述性表不回饋訓練。

每個天期 11 個單一指標，名目上預期 11×2.5%=0.275 個因運氣看起來較好；三天期共 33 次為 0.825 個。含非參考基準與 logit 共 48 次比較，名目期望 1.2 個。這是未作多重比較校正的估算，入圍只代表值得再驗證。

## 驗證與操作

- **326 項通過**：[unittest 完整輸出](verification/evolve-6-python-tests.txt)；其中 [新增 28 項測試](verification/evolve-6-daily-research-tests.txt)。
- **44／44＋30／30 變異轉紅**：[本輪變異對照](verification/evolve-6-mutations.txt)、[資料層原 30 組變異重跑](verification/evolve-6-data-layer-mutations.txt)。涵蓋同日籌碼、未來價格／標籤、分位切點含未來、少一日暖機、忽略除權息參考價、量均值、Wilder／KD／EMA、30 樣本邊界、加 1 平滑、logit 訓練均值／尺度／缺值／300 次／學習率／L2／截距、20 日重訓、凍結、跨開發終點、漏 outcome、20 日 bootstrap／尾塊／切點與嚴格入圍界線。
- [真資料執行紀錄](verification/evolve-6-run.txt)、[重跑紀錄](verification/evolve-6-resume.txt)、[獨立 SQL 與 bootstrap 核對](verification/evolve-6-audit.json)。
- [原有表與額度執行前](verification/evolve-6-legacy-before.json)、[執行後](verification/evolve-6-legacy-after.json)：19 張表雜湊一致；帳本不變。獨立 SQL 核對 51 列準確率／Brier、另算 51 組區間，誤差均小於 1e-12。三天期開發段標籤與第 5 輪相同。重跑 **0 筆新增、0 次重訓、0 次網路嘗試**（10.247 秒）。

新增程式：`back/daily_config.py`、`daily_experiment.py`、`daily_indicators.py`、`daily_models.py`、`daily_run.py`、`daily_score.py`；`scripts/run_daily_dev.py`、`scripts/mutation_daily_research.py`；`tests/test_daily_research.py`。沒有改既有方法、news 或殼。

```sh
/usr/local/bin/python3 scripts/run_daily_dev.py --execute --experiment 4 --split dev --jobs 3 --report docs/verification/evolve-6-development.json
```

不帶 `--execute` 不開 DB、不發請求。只接受實驗 4／split=dev。完整重跑驗證既有 feature hash、答案格式與 outcome，跳過重訓，不覆寫已有結果；部分中斷可依原凍結參數重算並逐筆比對後續跑。沒有 commit。
