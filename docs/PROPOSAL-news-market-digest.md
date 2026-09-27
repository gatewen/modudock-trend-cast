# 提案：news.market_digest v1

本文件供 news 維護者評估，尚未替 news 實作或啟用發布。trend-cast 已宣告訂閱；news 未安裝或未運行時，既有行情、四個多日前瞻考生與成績均正常。

## 發布方式

建議 news 的 manifest 新增 `provides: ["news.market_digest"]`，每輪分析完成後發一則後半協定訊息：

```json
{"t":"publish","seq":42,"topic":"news.market_digest","body":{"schema":1,"at":"2026-09-28T13:20:00+08:00","window_hours":24,"signal_counts":{"bullish":0,"mixed":0,"unrelated":0,"bearish":0},"top_themes":[],"source_count":0}}
```

上例 seq=42 是示意，實際使用當前 epoch；body 改用本輪摘要。殼轉送為 `t=event`、同一個 `topic`／`body`；接收端的 `seq` 是自己的 epoch，並沒有可驗證的發送者欄位。不新增 requires、不要求 news 一定存在。殼只投遞給正在運行的訂閱者，沒有持久化或補發機制；未運行期間的新聞不可事後當成已收到。

## body 格式與限制

```json
{
  "schema": 1,
  "at": "2026-09-28T13:20:00+08:00",
  "window_hours": 24,
  "signal_counts": {"bullish": 3, "mixed": 2, "unrelated": 1, "bearish": 2},
  "top_themes": [
    {"name": "半導體需求", "events": 3, "direction": "bullish"},
    {"name": "匯率波動", "events": 2, "direction": "mixed"}
  ],
  "source_count": 5
}
```

- 所有列出的欄位必填，不接受額外欄位（巢狀物件亦同）、重複 JSON key、NaN／Infinity。`schema` 必須是整數 1，布林值不算整數。
- `at` 為本輪完成／發布時間：含秒、明確時區的 ISO 8601，最多 6 位小數；不可晚於接收端本機時間。請同步系統時鐘，不可用新聞發生時間假裝發布時間。
- `window_hours`：整數 1–168，統計窗口長度。
- `signal_counts`：窗口內分析事件按四類分類的數量，四個鍵全有；值為 0–1,000,000 的整數。不是機率，不要求與來源數相等。
- `top_themes`：至多 10 筆，可空。`name` 為 1–80 個字元的短主題名，無首尾空白、控制字元、URL 或連結；同一摘要不可重複名稱。`events` 為該主題事件數（整數 1–1,000,000）；`direction` 僅 `bullish / mixed / unrelated / bearish`。主題可重疊，不強制合計等於 `signal_counts`。
- `source_count`：本輪去重來源數，整數 0–1,000,000。
- **上限 8 KiB（8192 bytes）**：JSON body 的 UTF-8 傳輸大小（含跳脫字元與 body 內空白），接收端另檢查標準化後大小。建議用緊湊 JSON；不含殼 envelope。
- 不傳原始文章、摘要全文、標題清單或連結，只傳計數與短主題名。不把個股代號、價格或歷史日期寫入主題名。接收端僅能驗證結構、長度及明顯連結，無法單靠結構驗證證明自由文字沒有可識別線索。

## trend-cast 接收與時間口徑

1. 嚴格驗證失敗即丟棄，不記錄原文或錯誤例外。排隊前由本機蓋 `received_at`，不能由 payload 指定；DBWriter 排隊最多 64 筆，滿時丟棄，不阻塞殼的控制訊息。
2. `news_digests` 永遠只保存一筆最新值與本機接收時間。重複或較舊的 `at` 忽略，不能用重播刷新接收時間。
3. `news_snapshots` 每個已確認交易日最多一筆：取 **發布與接收同屬當日，且兩者都 ≤ 台北 13:30** 的最後一筆。13:30:00 恰好可用，之後收到的內容只能更新最新值，不能覆蓋當日快照；不拿昨天的值替代。
4. 當天交易日曆尚未更新時，先保存 `news_pending_snapshots` 候選（同樣每日期最多一筆、同樣截止規則）；日曆確認交易日後才移入正式快照。不能用星期幾猜開市，候選未確認前不能供預測使用。
5. 多日前瞻流程另外記 `news_forward_inputs`：有快照則準備 **p5 的日 K 相對值＋新聞** state（不送 `at` 或 `received_at`）；沒有快照則記「無新聞資料」。這份紀錄不可改寫，不改既有 p6 body、實驗 4 凍結設定或四個方法。
6. **`JEV_NEWS_ENABLED = False`，本輪沒有 p5 transport，也沒有新增 jev 呼叫。** 有快照狀態為「未啟用」，無快照也不發請求。未來正式啟用須另外固定 p5 instructions、新聞視為不可信資料的處理方式、研究設定與額度，再審核；此 state 是輸入預備紀錄，不是已完成預測。
7. 畫面只顯示「新聞廣播：未收到」或「最後收到時間（台北）」及未啟用狀態，不顯示新聞原文，也不混入歷史開發段走勢／成績。

若只有獨立 daily_forward 排程在跑、殼與 news 沒開著，排程不會收到殼廣播；它只讀先前已存的當日快照。沒有就如實記缺新聞，不從網路補抓或推造歷史快照。


## §15.13 啟用補記

前述旗標關閉是第 11 輪的歷史狀態；本分支已預設啟用正式 p5，改用完整 p6＋三個指定新聞欄位，僅當日合格快照才發請求。完整 prompt、每日獨立重試與比較規則見 [PROMPT-P5.md](PROMPT-P5.md)。news 發布端已在 ec90f84／v0.10.0 實作，尚待該模組出貨；本次未修改 news。
