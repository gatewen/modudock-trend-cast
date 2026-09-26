# 第三塊交件：實驗凍結與 jev

第三塊完成。完整 **116 項 unittest 通過**；第三塊 **40/40** 組變異轉紅，第一、二塊回歸變異分別 **16/16、27/27**。真 API 共 **6 次**（含重試的總計，實際沒有重試），6 筆皆成功 commit。沒有執行完整開發段，也沒有揭露保留段行情、標籤、預測或成績。

**檔案清單**

| 檔案 | 內容 |
|---|---|
| `back/experiment.py` | 建實驗原子檢查、資料摘要、凍結設定、跨實驗曝光、揭露與日期存取守衛 |
| `back/jevcast.py` | 固定 TypeSafe POST、逐條驗證、重試與永久停用、6 worker、提交／續跑／取消 |
| `back/db_writer.py` | 單一 daemon 擁有 SQLite 寫入連線，非阻塞 submit／close |
| `back/store.py` | 新增 `prior_exposures` 表；既有資料表與讀寫行為保留 |
| `scripts/smoke_jev.py` | 僅 6 個開發點的明示 smoke；副本與持久化 20 次硬上限 |
| `scripts/mutation_experiment.py` | 40 組守衛變異與對應測例 |
| `tests/experiment_fixture.py` | 定稿月份、暖機與切分的合成資料 |
| `tests/test_experiment.py` | 原子性、摘要、曝光與揭露持久化 |
| `tests/test_jev_client.py` | 真正 POST body、傳輸限制、逐條回應驗證 |
| `tests/test_jev_runner.py` | 6 並發、去重、重啟續跑、取消／換 generation、提交失敗與 DB 鎖 |
| `tests/test_smoke_budget.py` | 並發及重建 budget 物件都不能超過 20 次 |
| `tests/helpers.py` | 假 server 增加 POST/body 支援；既有 GET 測試照常 |
| `tests/test_store.py` | 更新資料表清單，包含 `prior_exposures` |
| `docs/verification/block-3-*` | 下列完整測試、變異、smoke 與完整性紀錄 |

`SPEC.md` 未改；沒有 commit，沒有修改 `news/`。

**實驗與曝光口徑**

`create_experiment(store, gate, start=..., end=...)` 的起訖是評估範圍；另外向前取恰好 25 個交易日暖機。前 `floor(N × 0.7)` 個評估交易日為開發段。評估起訖必須存在於交易日曆，不會偷偷縮短使用者指定的範圍。

同一個 `ActivityGate` 對建立實驗作預約，阻止同步／回放同時啟動；`BEGIN IMMEDIATE` 內檢查暖機、逐月定稿狀態、當月／最近 7 交易日、分 K 與日 K 的日期集合、除權息三態，然後計算摘要並一起提交實驗與既有曝光紀錄。失敗會 rollback，釋放預約。暖機月份也必須定稿。

`config_json` 保存 `symbol`、`warmup_start`、`mapping`。摘要按穩定順序涵蓋暖機至範圍尾端的完整 `bars`、`daily`、`corp_events`、相交 `corp_coverage`（含來源／抓取時間），以及切分、門檻、特徵版本、prompt 版本、model 與設定。回放啟動前會重算驗證；直接改掉已凍結的資料會拒跑。正常後續同步靠既有 Store 凍結守衛保護；未來範圍外資料不改變摘要。

新增 `prior_exposures` 是為了保存「有實驗之前已展示的資料」，不捏造實驗或 reveals。第一次建實驗時會原子、冪等地寫入本專案已確認的曝光：**2330，2024-07-26～2026-01-27，來源 `block-2-real-dev-audit`**。

曝光集合另外包含所有實驗的開發段及所有 reveals，依目標實驗的實際交易日去重計算重疊。換門檻、開新實驗或重啟都洗不掉。已曝光不等於自動解鎖當前實驗：`day_access()` 對保留段仍要求該實驗的完整揭露紀錄；`check_data` 原有 labels-only reveal 會計入曝光，但不自動解鎖走勢圖。

**jev 與接線介面**

請求採官方 `state/model/questions.direction` 格式，Bearer header、固定主機與 `POST /v1/systemone`；回應讀取 `answers.direction.choice/probabilities` 與頂層 `model`。[官方 API 文件](https://docs.typesafe.ai/api)（2026-09-26 查核）。依本模組規格，缺 `model` 可接受並存 null，出現而不符則拒絕。沒有擷取或保存原始錯誤 body。

每個請求只有一個 f1 時點，門檻產生三個 criteria。有限值、非 bool、鍵集合、範圍、總和的 0.01 容差、argmax、模型版本都有測例；實際假 server 收到的 POST body 也與當天全部 OHLC 比對，確認沒有絕對價格、股票代號或日期。

6 條固定 daemon worker；另有 process 共用 6 個 HTTP 槽，避免多個 client 突破上限。401／403 的停用跨 client 延續；429／529 最多重試 2 次，退避 0.5／1 秒。取消會打斷尚未開始的 HTTP 與重試等待；已在途的網路呼叫最多等待原有 15 秒逾時，回來後丟棄。

未來殼接線必須共用同一個 `DBWriter` 與 `ActivityGate`。建立實驗透過 `writer.submit(...)`；同步在派工前 `gate.claim('sync')`，整個同步完成後才 release；回放由 `JevRunner` 管理獨立的 replay generation。既有同步函式的網路與寫入拆分、線 B 與前半仍屬第五塊。

`runner.start(experiment_id, split='dev'|'holdout')` 回傳 handle；`times` 是這次 smoke 的限量介面，也會驗證所有時點都屬於指定 split。每個送出前查 DB 主鍵並排除同一輪重複時點，只補缺漏。預測與 `runs.n_ok/n_fail` 在同一 transaction；COMMIT 成功後才更新 handle、發送進度。錯誤文字不入 DB。

`cancel()` 先立即設定取消旗標，再排入 DB 取消屏障；呼叫本身不等待 DB。Future 完成代表屏障已處理：其後舊結果不能提交。已經進入提交的 transaction 排在屏障之前；在等 SQLite 寫鎖的工作取得鎖後仍會重查取消旗標。取消狀態若寫入失敗，回報安全錯誤並釋放 replay 預約，不會永久 busy。

控制迴圈不要呼叫 Future.result；進度 callback 在 DB owner 上，只應往 Outbox 排入訊息。各出口的保留段統一遮蔽與計分是第四塊，這塊提供 `day_access`、`exposed_days`、`holdout_overlap`、`reveal_holdout` 供接線。

**測試與變異證據**

執行環境：`/usr/local/bin/python3`，僅標準庫。

```text
/usr/local/bin/python3 -m unittest -v
Ran 116 tests in 21.732s
OK
```

[unittest 完整輸出](verification/block-3-unittest.txt)；[第三塊 40 組逐條變異對照](verification/block-3-mutations.txt)；[第一塊回歸 16 組](verification/block-3-regression-data-mutations.txt)；[第二塊回歸 27 組](verification/block-3-regression-replay-mutations.txt)。

代表性變異：

| 拔掉／改掉的守衛 | 轉紅的測例（完整模組路徑在對照檔） |
|---|---|
| 建實驗忽略 sync/replay 或未預約建立期間 | `test_sync_and_replay_each_refuse_creation_and_keep_separate_generations`、`test_creation_check_hash_and_insert_are_one_atomic_reservation` |
| 缺暖機、未定稿、缺分 K、corp 未知照建 | 對應 `missing_warmup...`、`unfinalized...`、`warmup_month_must_be_final` |
| 摘要各別漏 bars／daily／events／coverage／設定 | `test_digest_covers_each_dataset_settings_and_ignores_new_future_data` |
| 漏既有 audit、其他實驗 dev、已揭露範圍 | `test_prior_block2_exposure_is_seeded_once_and_survives_reopen`、`test_other_experiments_development_and_reveals_are_permanent_exposure` |
| HTTP state 加入代號／絕對價格／日期；criteria 寫死 | `test_actual_http_post_single_point_dynamic_criteria_and_private_payload` |
| 機率鍵集合／範圍／總和／argmax／model 守衛移除 | 回應驗證逐條與容差邊界測例 |
| 不永久停用、改退避、略過回應驗證、洩出原例外 | 對應 client 的 401/403、429/529、HTTP 與例外測例 |
| 1 MB 上限改為 2 MB | `test_size_cap_declared_streamed_encoding_truncation_and_json` |
| 去重關閉、worker 改 7、舊 generation 照收 | 續跑、6 並發、晚到結果與新 generation 測例 |
| SQLite 等鎖後不查 cancel；rollback 改 commit | `test_cancel_and_close_return_without_waiting_for_locked_database`、`test_insert_failure_rolls_back_prediction_count_and_can_retry` |

變異曾找到 1 MB 測例假綠：原本測試引用實作常數，而且過大內容不是有效 JSON，可能被另一個守衛擋下。已改為規格固定的 `1,048,577` bytes、有效回應 JSON 加空白，分別測宣告長度與串流內容，且斷言正確錯誤碼。現在該變異會紅。

另外有真正「到 COMMIT 才失敗」的 deferred foreign-key 測試，確認 INSERT 及計數 UPDATE 都成功仍不能提早算完成；取消時更新 run 狀態失敗也不會留下永久 busy。

**真資料 smoke（僅開發段）**

資料庫為 `data/block-3-real.sqlite3`，由第二塊的本機副本以 SQLite backup 另複製；scratchpad 原檔未操作。實驗從已定稿月份中取 25 日暖機，開發段為 **2024-09-05～2026-01-21**。開頭不完整的 2024-07 未納入正式實驗；既有低嚴重度備忘未修改。

在開發交易日等距挑 6 日，依序取 09:30、10:00、11:00、12:00、12:30、13:00。挑選不讀未來標籤或 outcomes。6 次皆 HTTP 200 且通過驗證，回報模型皆為 `jev-1.13.0`，6 筆提交成功、0 失敗、0 重試。

| 項目 | 結果 |
|---|---|
| choice 分布 | up 0、flat 3、down 3 |
| up 機率 | 最小 0.08／中位 0.165／平均 0.1617／最大 0.23 |
| flat 機率 | 最小 0.22／中位 0.425／平均 0.4200／最大 0.58 |
| down 機率 | 最小 0.19／中位 0.430／平均 0.4183／最大 0.63 |
| 每點 client 延遲 | 最小 0.267／中位 0.307／平均 0.306／最大 0.330 秒 |

這只是回應格式與延遲的 smoke，沒有做準確率或優劣結論。[原始開發段 smoke 統計](verification/block-3-real-smoke.json)。

以禁止網路的 client 再跑相同 6 點，全部由 DB 跳過、HTTP 0 次；副本資料摘要與來源一致；既有曝光已持久化；outcomes／reveals 未寫入。兩把實際 key 的記憶體內比對掃描涵蓋模組檔案與本機 DB，未找到落檔。[完整性驗證](verification/block-3-integrity.json)。

`data/block-3-api-budget.jsonl` 是含重試的持久 append-only 計數，已用 **6/20**；發 HTTP 前先 fsync，半筆損壞會拒絕繼續。CLI 必須明示 `--execute`，只會選這 6 個開發點，不會擴成正式回放。**完整開發段的正式呼叫仍待使用者確認。**
