# jev_news（p5）交件

分支 `feat/jev-news`，依 SPEC §15.13。未 commit；未改 news／殼；真 jev 0 次，總帳本仍 1,222／3,000。真資料庫沒有新增預測、快照或揭露；殼驗收使用 scratch 中的獨立副本。

## 行為與凍結邊界

- `JEV_NEWS_ENABLED=True`。只在實驗 4 凍結後、日 K 已可取得的前瞻日，且有「發布與本機接收同屬當日、兩者都 ≤ 13:30」快照時送 p5。沒有 news 模組或快照時存 `no_news`，0 次 p5 請求；不拿最新值或昨日快照替代。
- p5 state 保留完整 p6 `daily`、11 項 `indicators`，只新增 `news.signal_counts`／`top_themes`／`window_hours`。criteria、模型與三天期沿用 p6，instructions 加 §15.13 新聞說明。完整文字見 [PROMPT-P5.md](PROMPT-P5.md)。
- 題材名稱是自由文字，可能夾帶代號、日期或價格：送出投影遮蔽其中數字序列，計數與事件數照規格保留；原始快照不改。沒有把 `at`、接收時間、source_count、schema 或本機研究 metadata 傳出。新聞內容明定是資料、不是指令。
- `news_forward_requests` 保存獨立且不可覆寫的完整 p5 body、body_hash、原始快照與政策摘要；`news_forward_policy` 固定 p5 instructions／questions／投影規則。既有四考生的 model、p6 body、實驗 digest 不變；舊 `news_forward_inputs` 預備紀錄保留為歷史。
- p5 預測使用共用 `d_forward_predictions`，市場特徵 input_hash 沿用 p6；額外的新聞及指令由同日 p5 request 的 body_hash 綁定。三題全部驗證後同一交易寫入；只有 commit 成功才算完成，沿用既有 recorded_at／準時待確認／補記／到期計分。
- jev_ind 與 jev_news 各自每天一個邏輯請求、各自最多三次 HTTP 嘗試；先持久化保留，再發送。p5 的 `daily_forward_news_http` 與既有 p6 嘗試分開，共用總預算交易，所有重試扣總帳本；僅 429／529 在下一交易日 09:00 前重試，不盲目重送中斷或不確定的請求。沿用既有 TLS、驗證、授權停用及取消機制。
- 殼與 daily_forward.py 共用服務，鎖／DBWriter 不變；關閉時兩個 budget 都停止，runtime 同時等待兩者連線收尾。

## 畫面與比較

最新預測卡片在 3／7／14 日各新增 jev_news 一列，顯示答案、三類機率與準時狀態；`no_news` 顯示「今日無新聞資料／未發請求」。新聞接收狀態可區分預設啟用與旗標關閉。

成績沿用準時／補記／待確認分組，增加兩個預先指定比較：jev_news − jev_ind、jev_news − majority。每個天期各自取雙方都有答案且到期的交集；沿用完整前瞻日曆切不重疊 20 日區塊、2000 次、seed=20260927。每個比較未滿 60 個共同到期日、或屬待確認組，都顯示「結果不完整，不下結論」。本輪沒有產生真實 p5 成績。

## 驗證

<!-- TEST_RESULTS -->
[完整 unittest](verification/jev-news-unittest.txt)：**423 項全過**（154.121 秒）。[前半](verification/jev-news-front-tests.txt)：**52 項全過**。[目標測試](verification/jev-news-targeted.txt)：62 項全過。以下 **96/96 組變異全部轉紅**（新增 22、既有 74）。
<!-- /TEST_RESULTS -->

- [新增後半變異](verification/jev-news-mutations.txt)：17/17。拔掉 p6 指標、混入 metadata、拿掉新聞說明／數字遮蔽、以盤後最新值取代快照、漏檢快照來源／政策／body、跳過保留、只存兩題、允許重複 claim、不排程 p5、共享 p6 每日扣額度、漏扣帳本、允許 09:00 重試、59 日就下結論、只報一個比較，均轉紅。
- [新增前半變異](verification/jev-news-front-mutations.txt)：5/5。漏 p5 列、無快照誤顯示缺答、忽略啟用狀態、漏比較、漏 60 日提示，均轉紅。
- 受影響既有變異：[新聞／鑰匙圈 27/27](verification/jev-news-keychain-mutations.txt)、[每日前瞻 26/26](verification/jev-news-daily-mutations.txt)、[關閉生命週期 8/8](verification/jev-news-shutdown-mutations.txt)、[新聞前半 5/5](verification/jev-news-old-front-mutations.txt)、[前瞻前半 8/8](verification/jev-news-forward-front-mutations.txt)。p5 預設旗標及方法列索引／變異錨點已隨新規格調整，沒有移除 p6 檢查。

重跑命令（Python `/usr/local/bin/python3`）：

```sh
/usr/local/bin/python3 -B -m unittest discover -s tests -v
npm test
/usr/local/bin/python3 -B -m scripts.mutation_news_forward
/usr/local/bin/python3 -B -m scripts.mutation_news_p5_front
/usr/local/bin/python3 -B -m scripts.mutation_news_keychain
/usr/local/bin/python3 -B -m scripts.mutation_daily_forward
/usr/local/bin/python3 -B -m scripts.mutation_shutdown
/usr/local/bin/python3 -B scripts/check_news_shell.py
```

## 真殼廣播驗收

[shell evidence](verification/jev-news-shell-evidence.json)、[browser evidence](verification/jev-news-shell-browser.json)、[殼 log](verification/jev-news-shell.log)。

- 在系統 scratch 目錄建立 `news-digest-fixture`（僅後半，provides `news.market_digest`）及 trend-cast 副本；不放進 modudock-modules，不載入也不修改 news。
- 以本機 Go 原始碼編譯臨時殼，另開 `127.0.0.1:62295`；不設 SSL_CERT_FILE、移除所有 API 金鑰。真實瀏覽器載入兩個模組，catalog／running 均核對成功。
- 為在盤後可重現截止前收件，**只在測試副本**固定本機收件時鐘為 2026-09-29 13:29，並在合成日曆建立該交易日。發布器在 `up` 後 publish 一次，digest.at 為同日 13:20。
- 證據包含實際由殼扇出的 event、後半提交後的 news_changed、前半最後收到時間；SQLite `news_snapshots` 恰一筆，內容與發布器完全相同，發布／收到時間分別為 13:20／13:29。不是直接呼叫 receive 假裝整合成功。
- 測試後半封鎖所有 socket 連線，網路嘗試 0；真帳本前後均 1,222。瀏覽器頁面錯誤 0。殼正常退出 0、port 已關、scratch 已清除。

## 檔案

新增 `back/news_forward.py`、`tests/test_news_forward.py`、兩支 p5 mutation runner、Python／Node 殼整合驗收腳本、PROMPT-P5 與本交件／驗證輸出。修改共用每日服務、額度／關閉生命週期、前瞻 view、前半狀態與卡片、README／提案補記及相應測試。沒有改凍結的 daily_config、daily_models、daily_indicators、daily_replay、daily_prompt，沒有改方法、門檻或開發／保留切分。
