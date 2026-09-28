# 第二場第 1 輪：ens_avg 與市場環境來源調查

分支 `evolve/2026-09-29`。實作依已 commit 的 `0b89af7`／SPEC §16.1，規格補記仍交由 cc。真 jev **0 次**；同一帳本已用 **1,222**，程式累計上限改為 **2,222**，本場剩 1,000。沒有 commit／push、沒有改 news 或殼、沒有安裝排程。沒有讀取保留段行情／標籤、執行保留段或揭露；真研究 DB 全程唯讀。

## A. ens_avg

每個 t 直接讀實驗 4 同一天期、同一日期的 `majority`、`vol_prior_d`、`ind_logit` 已有 walk-forward 機率，各類算術平均，平手 flat→up→down。只新增獨立研究模組 `back/daily_ensemble.py` 與 CLI；不改凍結 METHODS／PROTOCOL、既有方法、門檻、prompt、前瞻考生或畫面。

預測函式只查當下三筆 `d_predictions`，驗證三類機率、choice 與重建當時特徵的 input_hash。缺任何組件就缺答，不重配權重。平均過程不讀 outcomes／完整開發模型；計分階段另核對可評分日與既存 outcome 標籤／終點。報告附各天期 forecast_digest、缺答／覆蓋率及凍結摘要。`mode=ro` 加同一讀取交易，網路由 ForbiddenClient 封鎖；結果寫外部 JSON／Markdown，不把新方法塞入凍結的 `d_predictions` 名單。

完整開發交易日曆先切 **20 日不重疊區塊，尾塊保留**，共同日配對抽樣 2,000 次、seed=20260927。主要差值 **ens_avg − majority**；三個天期皆完整、交集覆蓋率 100%，145 個有樣本區塊。

| 天期 | 交集 n | ens_avg Brier | majority Brier | 差值 | 配對 bootstrap 95% 區間 | 入圍 |
|---|---:|---:|---:|---:|---|---|
| 3 日 | 2,891 | 0.663586999 | 0.665252405 | −0.001665407 | [−0.003325833, −0.000019820] | 是 |
| 7 日 | 2,887 | 0.627728993 | 0.631410780 | −0.003681787 | [−0.006364254, −0.001063493] | 是 |
| 14 日 | 2,880 | 0.644866963 | 0.644998923 | −0.000131961 | [−0.003446650, +0.003228150] | 否 |

3 日上界非常接近零，仍照預先定義的嚴格 `<0` 判入圍；沒有重抽種子或改區塊。3／7 日入圍只代表**值得再驗證**，不是已證明有效；14 日沒有證據顯示勝過 majority。本輪沒有改前瞻考生或啟動保留段。

[完整 JSON](verification/evolve2-1-ens-avg.json)、[Markdown](verification/evolve2-1-ens-avg.md)。另以 [獨立標準庫 audit](verification/evolve2-1-independent-audit.py) 直接查 bounded dev SQL，重算三組等權機率、Brier、20 日配對抽樣與線性分位數，完全不 import 本案實作；[三天期逐項吻合 <1e-12](verification/evolve2-1-independent-audit.txt)。

```sh
/usr/local/bin/python3 -B scripts/report_ens_avg.py \
  --db data/trendcast.sqlite3 --report data/ens-avg-development.json
```

## B. 市場環境來源

[完整調查](MARKET-SOURCES-20260929.md) 包含逐來源起點、更新時刻／未確定處、欄位單位、缺值、免費呼叫限制、授權疑慮與 t 的時間對齊；[實測 metadata](verification/evolve2-1-market-sources.json) 記固定 URL、時間、回應摘要及本機樣本位置。本輪沒有用這些資料產生任何方法成績。

- FinMind 實際起點：大盤 **1999-01-05**、TSM **1997-10-09**、SOX **1994-05-04**、USD/TWD **2006-01-02**；四者 2010 小段均免費無 token 可取。
- TWSE 大盤 1999-01 月樣本成功；Fugle 指數日 K 從 2015 起，單獨無法覆蓋 2010–2014。Yahoo 四標的亦取樣成功，但 metadata 起點與完整可得歷史不能混稱。
- FinMind 匯率早期有負值缺值碼；SOX 與 FX 的零 volume 不可當有意義成交量。Yahoo 大盤 volume 與 FinMind 不同，不能直接拼接。
- 美股只能選 **t 之前最後一個已完成美股交易日的收盤**。一般收盤在台灣夏令 04:00／冬令 05:00；不能用同日期 join 或固定減一曆日。Yahoo 日 K timestamp 是開盤／日期標記，匯率又標倫敦時區，不能視為收盤完成時間。
- 匯率取牌告即期哪一側、前一有效日或 13:30 前快照、ADR 溢價的比較時點與缺值策略，留待 cc 在第 2 輪事先定義。本輪未作選擇。

## 驗證

- [目標測試](verification/evolve2-1-targeted.txt)：**10/10**。包括 >250 個成熟標籤的真 logit 訓練，隨機擾動尚未成熟標籤／特徵，ens_avg 保持不變；SQLite authorizer 封鎖非預測表讀取，改動未來預測與 outcomes 不改當下平均；驗證天期隔離、缺組件、相同交集、strict CI 與兩個帳本入口共用 2,222 上限。
- [新增變異](verification/evolve2-1-mutations.txt)：**16/16 killed**。包括改回只用 majority、除數改 2、改平手順序、讀未來／別天期、放行保留段、訓練混入未成熟標籤、跳過 hash／choice／truth、缺答照入圍、CI 用 ≤0 或下界、改單日區塊／交集重切、恢復舊預算。
- [完整 unittest](verification/evolve2-1-unittest-local-server.txt)：**433/433 OK，173.365 秒**。
- [前半 npm test](verification/evolve2-1-front-tests.txt)：**52/52 OK**。
- [第一次沙箱內完整測試](verification/evolve2-1-unittest.txt) 因禁止 `127.0.0.1` 假服務 bind 而失敗（380 tests、1 failure／32 errors，部分 class setup 中斷）；沒有算通過。依權限流程取得沙箱外本機測試授權、移除真 API 金鑰後重跑上述 433 項，未降低檢查。

```sh
env -u FUGLE_API_KEY -u TYPESAFE_API_KEY /usr/local/bin/python3 -B -m unittest discover -s tests -v
npm test
/usr/local/bin/python3 -B -m scripts.mutation_daily_ensemble
```

本輪是唯讀 CLI 研究與來源調查，沒有前半／協定改動，未啟動真殼；未接觸 8731。預算上限是程式常數，新啟動 process 即採 2,222；已運行的舊後半仍需由擁有者重載以讀新版常數，本輪沒有替他人重啟殼。
