# 第一塊交件：資料層

日期：2026-09-26。依 SPEC v0.1 定稿實作；未 commit，未改 news/。

## 檔案清單

- `back/__init__.py`
- `back/data.py`：日期／時區、原始十進位值、OHLC、重複與區間驗證、保守時間映射。
- `back/http_client.py`：固定 HTTPS 主機、停用 proxy／redirect、驗 TLS、16 MiB、節流與重試、安全例外。
- `back/fugle.py`：只從環境讀 key，分 K／未還原日 K client。
- `back/twse.py`：TWT49U client、民國日期／欄位解析、範圍回應核對。
- `back/store.py`：全部 10 張資料表、月寫入、凍結保護、三態、`available_bars()`。
- `back/sync.py`：26 個月日期規劃、由新到舊同步；空月份不截斷更早資料。
- `scripts/__init__.py`
- `scripts/check_data.py`：資料品質 1–6；有實驗才讀開發段 outcomes；明確揭露先 commit 紀錄再輸出。
- `scripts/mutation_check.py`：在 `data/` 下隔離副本執行 16 組守衛變異，完成後清除副本。
- `tests/__init__.py`、`tests/helpers.py`
- `tests/test_clients.py`、`tests/test_store.py`、`tests/test_sync.py`、`tests/test_check_data.py`
- `tests/fixtures/twt49u.json`：縮減欄位 fixture；2330 列來自本次觀察，0050 列為合成的排除／千分位測例，並非完整官方下載。
- `docs/verification/block-1-unittest.txt`：完整 unittest 輸出。
- `docs/verification/block-1-mutations.txt`：每個變異及轉紅測試名稱。
- `docs/verification/block-1-fugle-live.json`：取得金鑰後的真實分 K／日 K、成交量與 404 觀察。
- `docs/verification/block-1-live-quality.json`：上述真實資料的 `check_data` 1–6 項輸出。
- 本交件說明 `docs/HANDOFF-BLOCK-1.md`。

原有 `.gitignore`、`docs/SPEC.md`、`docs/IDEA.md` 未修改。

## 驗證

在 `trend-cast/` 執行，Python 為 `/usr/local/bin/python3`（3.12.8）：

```sh
/usr/local/bin/python3 -m unittest -v
/usr/local/bin/python3 scripts/mutation_check.py
```

42 項 unittest 全過；16/16 變異轉紅。完整逐行輸出附於上述兩個文字檔，非摘要。

變異包括：移除節流、移除 429 重試、移除 401/403 process 停用、允許 redirect、把 16 MiB 改為 32 MiB、洩漏底層例外、rollback 改 commit、取消分 K／日 K／除權息凍結、可見性改用 ts_raw、截止時間加 60 秒、取消收盤競價例外、未知除權息當無事件、取消 TWT49U 範圍核對、不重抓當月、讓開發段統計吃到保留段。各自對應的失敗測試見 mutations 輸出。

變異確實找到測試盲點：回應大小測試最初引用實作的 MAX_BYTES，調大實作上限時測試資料跟著變大而仍通過。現改為獨立的規格常數，含有效但超大的 JSON，32 MiB 變異已被抓到。

月原子性除了壞資料預驗證，也以 SQLite trigger 在刪除／插入途中故意失敗，驗證原有資料及 fetch_log 一起回滾。事件與 coverage 亦有提交途中失敗測例。

## TWT49U 真實觀察

官方說明頁：<https://www.twse.com.tw/zh/announcement/ex-right/twt49u.html>

實際請求（無 key、無註冊、未使用 proxy、未關 TLS 驗證）：

<https://www.twse.com.tw/rwd/zh/exRight/TWT49U?startDate=20240901&endDate=20240930&response=json>

本次回應 `stat="OK"`，共有 78 列；`strDate="20240901"`、`endDate="20240930"`。注意回應欄位叫 **strDate**，不是請求參數 **startDate**。`fields` 是中文欄名陣列，`data` 是依該順序排列的陣列。

2330 列觀察：

| 欄位 | 原始值 |
|---|---|
| 資料日期 | `113年09月12日` |
| 股票代號 | `2330` |
| 除權息前收盤價 | `901.00` |
| 除權息參考價 | `896.99` |
| 開盤競價基準 | `897.00` |

client 按欄名取「除權息參考價」，不誤用「開盤競價基準」。只持久化日期、代號、前收、參考價及固定來源；忽略報表的財報欄位。本次亦以實作的 `TwseClient.events()` 真打，成功得到 `2024-09-12 / 901 / 896.99`。

環境觀察：這台 Python 預設 CA 鏈驗證失敗；以系統 CA 指定 `SSL_CERT_FILE=/etc/ssl/cert.pem` 後成功。程式維持 `ssl.create_default_context()`；沒有自動降級、沒有 `verify=False`，未配置有效 CA 時拒絕連線。

## 儲存與後續接線約定

- `Store` 寫入連線保持 sqlite 預設同執行緒限制，WAL、busy timeout 1 秒。後续接線應由 db-writer 建立／持有；控制迴圈不可直接呼叫同步網路或等待 Store。
- `write_candles(CandleBatch)` 每個月份／timeframe 為一筆交易；日 K、分 K 分開交易。`write_corp(CorpBatch)` 的事件和 coverage 同筆交易。網路與完整資料驗證都在寫入前完成；某來源失敗不撤銷其他已成功來源，因此不完整月份不定稿。
- `fetch_log` 比 SPEC 示意多 `kind`、`range_start`、`range_end`，分辨日 K／分 K／corp，以及完整月與起訖部分月。
- 定稿要求完整月請求、不是當月／最近 7 個交易日所在月、日 K 月查詢成功、分 K 日期與日 K 日期一致、除權息日期均已知。分鐘缺口另列品質報告，不補分鐘、不猜缺失資料。
- `corp_coverage` 以一天一個閉區間存放，避免重抓跨越凍結邊界時改掉舊實驗 coverage／fetched_at。只有 `stat=OK`、範圍吻合、欄位和列都驗過才建立 coverage；未知狀態、404、壞格式不當成無事件。
- 實驗建立屬第三塊；目前 Store 會讀既有 experiments 保護資料。後續 `config_json` 請存 `symbol` 與 `warmup_start`。若未提供 warmup_start，保守取 dev_start 之前最多 25 個既有交易日；新程式應明確存起點。
- 同步中凍結範圍的新增／變更／刪除都擋住，寫 `frozen_difference`；報告只顯示日期範圍／來源警告，不輸出修正前後價格。
- `check_data.py` 預設只讀資料庫，不會因路徑錯誤建立空 DB。無實驗只輸出 1–6 項；有實驗讀已存 outcomes，不在資料層重做回放或標籤。`--include-holdout` 必須已有實驗，且揭露紀錄 commit 完才輸出。
- HTTP 的 20 秒包含 socket timeout 及讀 body 的 elapsed 檢查；標準庫阻塞 DNS／單次 socket read 不能承諾硬性 wall-clock 中斷。模組的 1 秒退出仍須由後續協定層 daemon worker／Outbox 處理。

有資料後檢查：

```sh
/usr/local/bin/python3 scripts/check_data.py
```

資料同步函式的本機呼叫方式（有金鑰／有效 CA 後，背景工作或 CLI 使用）：

```python
from back.fugle import FugleClient
from back.store import Store
from back.sync import sync_history
from back.twse import TwseClient

with Store() as store:
    summary = sync_history(store, FugleClient(), TwseClient())
```

## 補驗：富果真實資料（取得金鑰後）

2026-09-26 22:43（Asia/Taipei），使用本次實作的 FugleClient、Store 與 check_data，查 2330 的 2023-05-23～24。金鑰僅載入環境變數，沒有寫入程式、fixture、交件檔或 SQLite。

- 分 K、未還原日 K 都回 HTTP 200：532 根分 K、2 根日 K。
- 每天 266 根，原始時間完整符合 09:00～13:24 每分鐘一根，再加 13:30；兩天首根開盤均等於對應日 K 開盤（報告不輸出價格）。
- 查文件起點前一天 2023-05-22 的分 K：HTTP 404、0 根；沒有據此截斷其他月份。
- 寫入隔離的 `data/live-smoke-20230523-24.sqlite3`（gitignore），未混入滾動實驗資料庫。讀回 532 根；無實驗，check_data 僅輸出資料品質 1–6，沒有標籤／行情。

成交量實測：

| 日期 | 分 K volume 加總（張） | 日 K volume（股） | 原始加總 ÷ 日量 | 換成股後 ÷ 日量 |
|---|---:|---:|---:|---:|
| 2023-05-23 | 19,555 | 20,126,817 | 0.0009715893 | 97.15893% |
| 2023-05-24 | 19,034 | 22,621,221 | 0.0008414223 | 84.14223% |

單位採富果官方明訂的「整股分 K＝張、日 K＝股」，樣本的量級也符合；但乘 1,000 後並非精確相等，**不能用加總必須相等當成資料完整性守衛**。本次尚未把差額按交易種類逐項核帳，因此不把差額成因寫成已證實。原始精度數值見 `block-1-fugle-live.json`。

官方單位／起點／404 依據：<https://developer.fugle.tw/docs/data/http-api/historical/candles/>。

另查到官方遷移指南明示 v1.0 Candles 將 09:00:00～09:00:59 的成交記在 09:00，支持起點標記的解讀：<https://developer.fugle.tw/docs/data/migration-guide/>。沿用 SPEC 的 `bar_end = ts_raw + 1 分鐘` 與 13:30 例外，未改映射。

TWT49U 窄區間的額外觀察：查 2023-05-23～24 回應只有 `stat="很抱歉，沒有符合條件的資料!"`，沒有範圍／欄位；client 拒絕將其視為已確認 coverage。改查整個 2023-05-01～31，範圍與欄位驗證通過，這兩天均為「已確認無事件」。此行為符合三態的保守原則。

本輪補验沒有修改 runtime 或測試程式；前述 42 項單元測試、16 組變異結果不變。本次為兩個交易日的真實資料驗收，尚非整個 26 月資料集的完整同步驗收。
