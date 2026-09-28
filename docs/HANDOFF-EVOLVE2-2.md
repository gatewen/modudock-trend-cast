# 第二場第 2 輪：市場環境指標（SPEC §16.3）

分支 `evolve/2026-09-29`。只做實驗 4 開發段，原模型、原資料摘要與既有結果不改；市場資料與研究結果另存新表。本輪真 jev 0 次；未 commit、push，未修改 news／殼、未安裝排程。

## 成績

**本輪入圍：`mkt_logit`／3 日，只有這一組。** 五個單一指標的所有天期，以及 mkt_logit 的 7／14 日均未入圍。ADR 溢價的 14 日區間整段大於 0，表示本次開發段 Brier 較 majority 差。

下表為「考生 − majority」Brier 差與 95% 區間；五個單一指標的完整方法名有 `ind_` 前綴。三個天期可評分 n 分別為 2,891／2,887／2,880，每個考生均完整覆蓋；H 尚未在開發段結束前到期的最後 3／7／14 日不評分。

| 考生 | 3 日 | 7 日 | 14 日 |
|---|---|---|---|
| mkt_trend | −0.002433 [−0.005759, +0.000830] | −0.003595 [−0.008892, +0.001429] | +0.004214 [−0.003588, +0.012889] |
| mkt_ret5 | −0.000414 [−0.003313, +0.002460] | −0.000282 [−0.005578, +0.004455] | +0.003442 [−0.001063, +0.007886] |
| adr_premium | +0.000685 [−0.001807, +0.003244] | +0.002153 [−0.000746, +0.005176] | +0.005018 [+0.001041, +0.009240] |
| sox_ret1 | −0.000581 [−0.002267, +0.001200] | +0.000436 [−0.001641, +0.002489] | +0.001173 [−0.000626, +0.003015] |
| sox_trend | −0.000418 [−0.002311, +0.001569] | −0.000454 [−0.005284, +0.004257] | +0.001299 [−0.005833, +0.008622] |
| mkt_logit | **−0.004385 [−0.008505, −0.000291]** | −0.005798 [−0.011898, +0.000589] | +0.000446 [−0.007306, +0.008908] |

完整開發交易日曆切不重疊 20 日區塊、保留尾塊，2000 次、seed=20260927；區間上界嚴格 < 0 才入圍。**本輪 18 個比較，預期約 0.45 個因運氣入圍**（名目估算，未作多重比較校正）。入圍只代表值得再驗證；保留段仍未使用。依 §16.2，累計待一次性考試者為 ens_avg 3 日（邊緣）／7 日，以及本輪 mkt_logit 3 日；本輪沒有重考第一輪或增加其他比較。

[完整結果 JSON](verification/evolve2-2-development.json)、[逐組結果 Markdown](verification/evolve2-2-development.md)、[執行紀錄](verification/evolve2-2-development-run.txt)。新增 52,092 筆預測、2,894 筆輸入、395 筆擬合（132／132／131）；訓練與評分網路嘗試 0。

[獨立驗算](verification/evolve2-2-independent-audit.json) 直接讀 DB、獨立實作 Brier 與 bootstrap，18／18 點估計及區間與報告一致（容差 1e−14）。原兩個 digest 及開發段 d_features／d_predictions／d_outcomes／d_fits 的筆數和 SHA-256 全部不變，包含原 149,322 筆預測；未讀保留段來做核對。

[重跑紀錄](verification/evolve2-2-resume.txt) 與 [最終核對](verification/evolve2-2-final-check.json)：三天期重跑均新增 0 筆、重訓 0 次、網路嘗試 0；除執行次數紀錄外，完整報告逐值相同。凍結後重新同步也直接略過，網路嘗試 0。真 jev 帳本仍 **1,222／2,222**。

## 資料與凍結

FinMind 公開 API，四次完整區間請求 `2009-01-01..2021-12-31`，不帶金鑰。第一次 sandbox DNS 不通，重新以允許網路的相同命令取得資料；失敗與成功日誌均保留。沒有取保留段資料。

| 來源 | 資料集／代號 | 筆數 | 實際首日 | 末日 | 使用欄位 |
|---|---|---:|---|---|---|
| 大盤 | TaiwanStockPrice／TAIEX | 3,205 | 2009-01-05 | 2021-12-30 | close，指數點 |
| ADR | USStockPrice／TSM | 3,273 | 2009-01-02 | 2021-12-31 | Close，USD／ADR |
| 半導體指數 | USStockPrice／^SOX | 3,273 | 2009-01-02 | 2021-12-31 | Close，指數點 |
| 匯率 | TaiwanExchangeRate／USD | 3,244 | 2009-01-05 | 2021-12-30 | spot_buy、spot_sell，TWD／USD |

合計 12,995 筆。USStockPrice 使用未調整的 `Close`，不使用 `Adj_Close`。匯率使用即期中價 `(spot_buy+spot_sell)/2`；非正、非有限數值及無法解析值都保存為 NULL，不補更早有效值。原始回應另記 SHA-256，正規化資料另記逐來源摘要。

`market_rows` 保存數值；`market_fetches` 保存請求涵蓋範圍、時間、筆數及回應摘要；`market_experiments` 保存獨立 `market_digest`、規格及原 `data_digest`／`dev_digest`。摘要綁定全部四來源、2009 暖機資料與本輪政策。來源凍結守衛包含原實驗到 2024-07-25 的保護範圍；本輪 schema／CLI 另外禁止保存或取用 2021-12-31 之後資料。後續一次性保留段須由另案明確擴充入口，本輪不提供解鎖功能。

凍結時間 `2026-09-29T01:40:31+08:00`，`market_digest=643dc062e57e2c901d83541f82aa2ecee632adb9431625d3ac89967d98fc70c8`。原 `data_digest=ed155f4a27d2e526a90353e74b0b2b6af035a5cd229fb9d5445fc6486fab660f`、`dev_digest=dc2d71d42d9890c7677aeca6a1bc0a7345da0969b9bf0a2dab555509d01b44c1`。

## 特徵與訓練

- 預測日 d：大盤最後一筆 `day<=d`；TSM、SOX、匯率最後一筆 `day<d`。報價年齡一律 `d−day`，5 日可用、6 日 missing。取最後紀錄後才驗缺值，不往前搜尋有效報價。
- 大盤 60 日、SOX 20 日均線包含最後可用收盤；數值 `close/MA−1`，相等 neutral。報酬以各來源交易紀錄計數，大盤 5 日需要 6 筆、SOX 1 日需要 2 筆；窗口中有缺值即 missing。
- ADR 溢價 `(TSM Close × FXmid ÷ 5) / 2330 d 日原始收盤 − 1`，以 Fraction 算到最後轉浮點。
- 2,894 個可預測日中，ADR 溢價缺 13 日：12 日匯率年齡超過 5 日、1 日使用到 2012-01-02 匯率缺值。其餘四個新增特徵無缺值。缺值不刪樣本；單一指標退回 majority，組合模型以訓練均值填補並有 missing one-hot。
- 五個單一指標只用 `t′+H<=t` 的開發段標籤，Laplace +1、同狀態至少 30 筆；三分位切點也只由已揭曉樣本計算，相等歸較低組。
- `mkt_logit` 合併原 11 個＋新 5 個指標（20 個數值欄位及 16 組狀態 one-hot）。原流程不變：至少 250 筆、每 20 台股交易日重訓、從零起步、L2=1、300 步、學習率 0.1；均值、母體標準差、三分位及填補都只取當次已揭曉訓練集。
- `market_features` 保存原特徵 hash、新輸入 hash／JSON、來源報價日及年齡；`market_predictions`／`market_fits` 保存各 H 的結果／擬合。每個新預測時點會重算 majority，與原表同日同 H 的機率及答案逐值核對。

## 驗證與重跑

[資料抓取](verification/evolve2-2-market-sync-network.txt)、[跑前快照](verification/evolve2-2-preflight.json)、[目標測試](verification/evolve2-2-targeted.txt)、[變異](verification/evolve2-2-mutations.txt)、[完整 unittest](verification/evolve2-2-unittest.txt)、[前半測試](verification/evolve2-2-front-tests.txt)。

目標測試 **18／18**；完整 unittest **451／451**（175.975 秒）；前半 **52／52**；新增行為變異 **32／32** 被抓到。全部在真實成績執行前通過。

新增測試與變異涵蓋：當日／未來 US 及當日匯率擾動不變、可用過去資料擾動有變、5／6 日邊界、缺值不補、ADR 1:5、中價、均線與報酬窗口、成熟標籤、30／250 樣本門檻、重訓週期、均值填補與 one-hot、原 optimizer 等價、資料凍結、完整性及嚴格入圍判定。變異只在暫存副本進行。

```sh
env -u FUGLE_API_KEY -u TYPESAFE_API_KEY -u SSL_CERT_FILE \
  /usr/local/bin/python3 -B scripts/sync_market.py --execute
env -u FUGLE_API_KEY -u TYPESAFE_API_KEY \
  /usr/local/bin/python3 -B scripts/run_market_dev.py --execute --jobs 3 \
  --report docs/verification/evolve2-2-development.json
/usr/local/bin/python3 -B -m unittest tests.test_market -v
/usr/local/bin/python3 -B -m scripts.mutation_market
/usr/local/bin/python3 -B scripts/audit_market_dev.py \
  --db data/trendcast.sqlite3 \
  --report docs/verification/evolve2-2-development.json \
  --before docs/verification/evolve2-2-preflight.json \
  --output data/market-independent-audit.json
```

凍結後同步直接略過、不再發請求；完整重跑驗證後略過擬合。訓練與報告由 `ForbiddenClient` 封鎖 HTTP／DNS／socket connect。完整測試使用假服務，不帶真 API 金鑰。本輪沒有前半或殼行為變更，因此不啟動真殼。
