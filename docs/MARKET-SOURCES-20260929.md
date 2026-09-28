# §16.1B 市場環境來源調查（2026-09-29）

只調查來源；沒有產生新指標、ADR 溢價序列、預測或成績，也沒有寫入研究 DB。規格仍由 cc 決定與補記。本報告不替第 2 輪選定特徵或匯率口徑。

## 實測與可回溯起點

本機實測於台北 00:54–01:07。普通小段為 2010-01-01～01-08（Yahoo 的 exclusive period2 為 01-11 00:00 UTC）；另查 FinMind 1900-01-01～2010-01-08，以確認回傳起點。富果取 2015-01-01～01-09，TWSE 取 1999-01。全部避開 2022-01-03～2024-07-25。FinMind 未帶 token；富果使用既有免費會員 key；沒有設定 SSL_CERT_FILE，TLS 使用程式既有 CA 備援，沒有跟隨 redirect。

共 19 次真來源 HTTP：18 次 200、TWSE 舊路徑 1 次 307；改用現行固定路徑後取得 200。前一次沙箱網路失敗不算成功取樣。[可審核 metadata、URL、取得時間、回應 SHA256](verification/evolve2-1-market-sources.json)；原始小段與起點探查資料只留在 gitignored `data/market-source-survey*-20260929/`，不將供應商原始資料收入版控。

| 標的／來源 | 可回溯起點與證據 | 取樣結果 | 欄位／單位 |
|---|---|---|---|
| 大盤：TWSE `MI_5MINS_HIST` | **1999-01-05**，官網明列，1999-01 實測一致 | 現行 `/rwd/zh/TAIEX/MI_5MINS_HIST` 200／21 列；舊 `/exchangeReport/…` 307 未跟隨 | 日期為民國年字串，OHLC 指數點；逗號千分位；無量 |
| 大盤：FinMind `TaiwanStockPrice/TAIEX` | **1999-01-05**，查 1900 起回傳首日；不能拿全資料集「1994-10-01」宣稱這個代碼的起點 | 小段 5 列；起點探查 2,775 列 | `date,stock_id,open,max,min,close,spread`（指數點），`Trading_Volume`（股）、`Trading_money`（TWD 元）、`Trading_turnover`（筆） |
| 大盤：Fugle `IX0001`, D, adjusted=false | 官方指數日 K 自 **2015**；小段首日 **2015-01-05** | 5 列 200；無 null | `date,open,high,low,close`（點），`volume`（股）、`turnover`（TWD 元） |
| 大盤：Yahoo `^TWII` | metadata `firstTradeDate`＝**1997-07-02**；本輪未逐日證實這個 metadata 起點的完整性 | 小段 5 列；OHLC/volume 無 null | `timestamp` Unix 秒，`quote` OHLC（點）；volume 的量級與 FinMind 不同，**不視為股數使用** |
| TSM：FinMind `USStockPrice/TSM` | **1997-10-09**，查 1900 起回傳首日 | 小段 5 列；起點探查 3,082 列 | `date,stock_id,Open,High,Low,Close,Adj_Close`（USD／ADS），`Volume`（ADS 數） |
| TSM：Yahoo `TSM` | metadata＝**1997-10-09**；TSMC 官網上市日期為 **1997-10-08**，兩者不能混稱 | 小段 5 列；OHLC/volume 無 null，成交量與 FinMind 相同 | `timestamp,quote.open/high/low/close/volume,adjclose`；USD／ADS、ADS 數 |
| SOX：FinMind `USStockPrice/^SOX` | **1994-05-04**，查 1900 起回傳首日 | 小段 5 列；起點探查 3,951 列 | 與美股 schema 同；OHLC 為**指數點**，不是 USD 股價；Volume 不能當有意義的成交量 |
| SOX：Yahoo `^SOX` | metadata＝**1994-05-04**；Nasdaq 指數推出日 **1993-12-01** 更早，不代表本來源涵蓋那段 | 小段 5 列；OHLC 無 null，5 列 volume 全 0 | `quote` OHLC 為指數點；metadata currency=USD 並不把指數點變成美元價格 |
| USD/TWD：FinMind `TaiwanExchangeRate/USD` | **2006-01-02**，查 1900 起回傳首日 | 小段 5 列；起點探查 1,010 列 | `date,currency,cash_buy,cash_sell,spot_buy,spot_sell`；每 1 USD 的 TWD 數，臺銀牌告買入／賣出（銀行角度） |
| USD/TWD：Yahoo `TWD=X` | metadata＝**2004-03-24** | 小段 6 列；OHLC 無 null，volume 全 0 | OHLC＝TWD／USD，`exchangeTimezoneName=Europe/London`；**日線 timestamp 不是收盤完成時間** |

TWSE [起點說明](https://www.twse.com.tw/zh/indices/taiex/mi-5min-hist.html?myear=)、[開放資料集](https://data.gov.tw/dataset/11755)；FinMind [台股 schema](https://finmind.github.io/tutor/TaiwanMarket/Technical/)、[美股 schema](https://finmind.github.io/tutor/UnitedStatesMarket/Technical/)、[匯率 schema](https://finmind.github.io/tutor/ExchangeRate/)、[舊版匯率文件的臺銀來源](https://finmind.github.io/v3/tutor/ExchangeRate/)；Fugle [歷史日 K 起點及單位](https://developer.fugle.tw/docs/data/http-api/historical/candles/)；TSMC [上市日期](https://investor.tsmc.com/english/faq)；Nasdaq [SOX factsheet](https://indexes.nasdaq.com/docs/FS_SOX.pdf)。

另以 2026-09-21～09-25 作近期可得性核對（不計分）：FinMind TAIEX 與 USD 各 4 列、最新 09-24；TSM 與 SOX 各 5 列、最新 09-25，皆 HTTP/API 200、無 null。這證明本輪可取到近期資料，仍不是每日更新準時性的 SLA。

這些是來源可得起點或 metadata，**不是全期資料完整性證明**。FinMind 起點探查範圍沒有重複日期或 null；不等於沒有整日缺漏，尚未將全歷史逐日對美／台市場日曆核對。

## 更新時間與 t＝13:30 的資訊邊界

| 來源 | 文件更新時間／實測限制 | 對預測時間的意義 |
|---|---|---|
| TWSE 日 OHLC | 開放資料標示每日更新，沒有精確發布分鐘承諾；歷史月表無 received_at | 指數收盤值在市場收盤後確定，但歷史端點何時可讀要另記錄；不能說端點 13:30 即完成更新 |
| Fugle 歷史日 K | 官方稱每交易日 **16:30 前**更新 | 依既有 §15.12 可盤後記錄 t 的資訊；若要求 13:30 實時取得，需另處理盤中來源，不是歷史端點 |
| FinMind 台股日 K | 官方 **週一至五 17:30**，以實際 API 為準 | 同上，保留事件所屬日與實際取得時間 |
| FinMind USStockPrice | 官方 **每天 08:00**、以 API 為準；頁面未明列時區，也無各筆首次發布時間 | 本輪只證明可抓歷史；不能由一次抓取證明歷史每一天都準時。正式前瞻需確認更新時区／落盤時間 |
| FinMind 匯率 | 現行欄位文件**未查到更新時刻或日內哪個報價的承諾** | 不可把 t 同日牌告日值假定已知；使用前一有效報價日或截止前快照，須由 cc 先定口徑 |
| Yahoo chart | 本輪四標的皆 200；未找到 chart v8 對外的固定更新 SLA／完整日線完成時間契約 | `meta.regularMarketTime/currentTradingPeriod` 是查詢時狀態，不能拿來替歷史每天蓋可用時刻；不可用日 K 開盤 timestamp 當收盤可用時間 |

時間依據：Fugle [candles](https://developer.fugle.tw/docs/data/http-api/historical/candles/)、FinMind [台股](https://finmind.github.io/tutor/TaiwanMarket/Technical/)／[美股](https://finmind.github.io/tutor/UnitedStatesMarket/Technical/)。

TSM 和 SOX 必須按 **America/New_York 的交易日與實際收盤時間**，選 `close_at < t` 的最後一個已完成美股交易日，再轉 Asia/Taipei；不可用「美國日期與台灣日期相同」join，也不可固定減一曆日。NYSE 正常核心時段 09:30–16:00 ET；換算一般為台灣翌日夏令 **04:00**、冬令 **05:00**，另須處理休市與提早收盤。[NYSE 時段](https://www.nyse.com/trade/trading-information)

例：台灣 2010-01-05 13:30 可用紐約 2010-01-04 收盤（台灣 01-05 05:00），不能用紐約 01-05 收盤；週一或美股假日需退到真正最近的已完成 session。本輪 Yahoo TSM/SOX 小段的 timestamp 為 14:30 UTC（09:30 EST），本身顯然是開盤標記而非收盤標記。

ADR 換算涉及 **1 ADS＝5 普通股**（[TSMC 公告](https://pr.tsmc.com/english/news/1427)）。概念上每股台幣等值是 `已完成美股 session 的 TSM USD 價 × 已知 USD/TWD ÷ 5`；要再除以哪個時點的 2330 價格、匯率選即期買／賣／中價、允許報價落後多久，留待第 2 輪預先定義。本輪沒有選口徑或計算任何溢價數字。

匯率另有官方替代：CBC 提供 [1993 年起日資料檔](https://www.cbc.gov.tw/tw/np-520-1.html)，並稱每工作日 **16:00–17:00** 公布當日銀行間收盤匯率（[官方說明及公開樣本](https://www.cbc.gov.tw/tw/lp-645-1-2-40.html)）。它晚於 t；同日收盤不可用。此處只核對官網，沒有下載完整 XLS 或實作 adapter，不能把 CBC 銀行間匯率與臺銀牌告即期混為同一序列。

## 缺值、調整價格與來源差異

- FinMind USD 起點 **2006-01-02** 的 `spot_buy/spot_sell=-1`；官方示例同日顯示 **-99**。兩者都屬不可用報價，不能只識別某一個 magic number：價應有限且 >0。起點探查 1,010 列中，spot buy/sell 各 1 個非正值，cash buy/sell 各 13 個非正值。2010 小段五列全有效。缺值不改拿 cash 代替 spot，也不自動補值。
- SOX 的 FinMind/Yahoo 小段 volume 五列全 0；FinMind 起點探查 3,951 列中 3,792 列為 0。這不是「沒有指數」；OHLC 存在。不要用 SOX volume 當成交量特徵。
- Yahoo USD/TWD 六列 volume 全 0，還有不同於股市的日期列；匯率需獨立日曆，不能用 2330 交易日數衡量是否缺資料。
- Yahoo 大盤 volume 與 FinMind 量級、數值均不一致；本輪未查到穩定統一的量綱保證，故 OHLC 可以交叉核對，volume 不能直接拼接。Fugle 另明示量／值不含零股及鉅額（[來源規範](https://developer.fugle.tw/docs/data/intro/)）。
- TSM 要分 `Close` 和 `Adj_Close`；後者會因股息／分割回改歷史，不能用來算歷史 ADR 溢價。[Yahoo 調整收盤說明](https://ca.help.yahoo.com/kb/SLN28256.html)。即使名為 Close，正式納入前仍須核對拆分與 ADS 比率的歷史口徑；不能只因欄位沒寫 adjusted 就宣稱是當時原始價格。本輪 FinMind 的 TSM 取兩位小數，Yahoo 有浮點尾差。
- 本輪 FinMind SOX 的 `Adj_Close` 與 `Close` 也有差異；僅調查紀錄，沒有把兩者任選其一產生報酬。任何需要除權息或歷史修訂的特徵，仍須遵守 §3 的當時可知資訊限制。

## 免費、呼叫限制與條款疑慮

| 來源 | 免費與限制 | 條款／營運限制 |
|---|---|---|
| FinMind | 四資料集均無 token 實測成功；官方 **300 次／小時（無 token）、600（有 token）**。本次相鄰請求間隔至少 12 秒。未做壓測 | 服務供教育參考，資料可能修訂；API 訂閱不等於原始資料再散布授權，不得轉售／建立鏡像；美股上游的個別授權鏈需另確認。原始樣本留本機 |
| Fugle | 免費會員可取歷史；**60 次／分鐘**、每次區間 **<1 年**。本次只 1 次 | 僅台灣證券／指數來源；沒有找到 TSM 美股、SOX 或 USD/TWD 對應端點。行情轉接、再授權、傳第三方受來源規範限制；免費不是可自由散布 |
| TWSE | 月份歷史與最新月開放資料免費；本輪未查到歷史 rwd 明確呼叫上限。建議工程自限 ≥3 秒，但這不是官方保證 | 政府資料集 11755 是最新月份、政府資料開放授權條款第 1 版；官網一般條款限制未經同意的自動下載。不能直接把「最新月開放資料授權」擴大成所有 rwd 歷史抓取授權；正式長期回補需確認 |
| Yahoo chart v8 | 四標的無憑證皆 200；未找到官方承諾的免費 chart API 額度，無可依賴的固定 rate limit／SLA | Yahoo 一般條款對未經許可的自動蒐集、商用／再散布有約束；API 通則不能被解讀為匿名 chart 端點的專門授權。本輪證明可讀，沒有證明適合長期產品化 |
| Nasdaq SOX 官方 | 官方可查 factsheet、指數 history 畫面；本輪未取得可確證免費的大量歷史 API 權利 | GIW 有登入、資料方案及 entitlement；指數推出日 1993-12-01 不代表免費下載最早日。作指數定義與上市來源核對，不冒稱已接通完整免費日線 |

依據：[FinMind 額度](https://finmind.github.io/en/quickstart/)、[使用條款](https://finmind.github.io/PrivacyPolicy/)／[授權說明](https://finmind.github.io/Disclaimer/)；[Fugle 價格](https://developer.fugle.tw/docs/pricing/)／[使用規範](https://developer.fugle.tw/docs/data/intro/)；[TWSE 條款](https://www.twse.com.tw/zh/terms/use.html)／[政府資料集](https://data.gov.tw/dataset/11755)；[Yahoo 一般條款（本輪成功讀取的加拿大英文版）](https://legal.yahoo.com/ca/en/yahoo/terms/otos/)／[API 通則](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apitnc/index.html)；[Nasdaq SOX history 與資料服務入口](https://indexes.nasdaq.com/Index/History/SOX)。Yahoo 台灣條款本輪 web 工具取得失敗，區域適用文字仍待核實，未把加拿大版當成台灣用戶的確定法律結論。

技術可行性：FinMind 四組都覆蓋 2010 起；TWSE 大盤可作官方核對，Fugle 缺 2010–2014；Yahoo 可作第二來源核對，但匯率日線截止語意、資料授權、日後修訂等尚待決定。**本輪沒有依來源或樣本結果挑選任何新指標，也沒有碰保留段。**
