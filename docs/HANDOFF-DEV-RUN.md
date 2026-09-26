# 開發段正式回放

範圍固定為 `data/block-3-real.sqlite3` 現有實驗 1 的開發段：**2024-09-05～2026-01-21**。這次授權是完整開發段的 TypeSafe 呼叫；不沿用 smoke 的 20 次限制。

**交付檔案與行為**

| 檔案 | 變更 |
|---|---|
| `scripts/run_dev.py` | 明示執行、dev-only 本機方法／outcomes、6 並發 jev、呼叫保險、續跑與原始統計 |
| `back/jevcast.py` | 新增可選 `stop_dispatch`：停止派新點後讓在途工作完成提交；原本 cancel 與 generation 規則保留 |
| `tests/test_run_dev.py` | 12 項測試，含 CLI 授權、範圍、並發額度、重試、續跑、majority 時序、交集與原子提交 |
| `scripts/mutation_run_dev.py` | 8 組刻意拔除守衛的變異 |
| `docs/verification/run-dev-*` | 完整測試、變異、正式執行與續跑證據 |

標準執行方式：

```sh
/usr/local/bin/python3 scripts/run_dev.py --execute \
  --db data/block-3-real.sqlite3 --experiment 1 --max-calls 3500 \
  --report docs/verification/run-dev-real.json
```

金鑰只讀環境變數。這次從使用者指定的 `~/.zshrc` export 行，在 Python 記憶體內用 `shlex` 解析，沒有輸出或寫入金鑰；使用 `/etc/ssl/cert.pem` 驗 TLS。

沒有 `--execute` 就拒絕執行，連資料庫 writer 都不建立。沒有 `--split` 選項；所有候選點、outcomes、方法預測與報告都固定 dev。原有的完整凍結摘要驗證仍由實驗／JevRunner 執行；不計算或輸出任何保留段數值，也不寫 reveals。

`--max-calls` 預設 **3500**，是**每次腳本執行的 HTTP 嘗試總數，含 429／529 重試**。6 條 worker 在同一把鎖下領額度；用完後停止派新點，已在途的回應仍可提交，未取得額度的請求不發送。`--max-calls 0` 可只完成本機工作、不打 HTTP。達到上限或仍有缺漏會以 exit 1 回報未完整，保留已提交資料供下次續跑。

另用檔案鎖排除兩支腳本同時跑同一 DB。predictions 依既有主鍵續跑；基準方法既有紀錄會比對輸入與答案，一致就保留，不會覆寫。outcomes 和四方法新增 predictions／runs 在同一 transaction 提交，任何寫入失敗都 rollback。

majority 的累積集合限定「**可預測且可評分**」的開發點；按時間處理，且仍經 Baselines 的 `t′ + 30 ≤ t` 守衛。不可預測但可評分的點可以有 outcome，不能進 majority 頻率或方法比較的樣本集合。

報告先取「可預測且可評分」，再與 **jev、always_flat、majority、momentum、reversal** 五個方法的答案時點取交集。五列使用完全相同的樣本數。只輸出原始正確數與準確率；不做 bootstrap 或任何優劣結論。jev choice 分布統計開發段所有已提交的 jev 答案。

`committed_this_run` 只計本次成功 commit；`existing_skipped` 是此前已提交而跳過的點；`successful_total` 為兩者在開發段的合計。`failed_this_run` 是 runner 未取得答案的工作數；若因額度不足未發出首次 HTTP，另列 `quota_blocked_before_first_http`，避免混同實際 API 錯誤。`retries` 只計本次同一點首次嘗試之後實際發出的 HTTP；失敗後另一次腳本續跑算新的首次嘗試。

**測試**

```text
/usr/local/bin/python3 -m unittest -v
Ran 128 tests in 24.143s
OK
```

[完整 unittest 輸出](verification/run-dev-unittest.txt)；[本次 8/8 變異對照](verification/run-dev-mutations.txt)；[第三塊 40/40 回歸變異](verification/run-dev-jev-regression-mutations.txt)。

變異包括：拔掉 `--execute`、本機回放／jev 改跑 holdout、拿掉並發額度守衛、重試不扣額度、把樣本數改成各方法自己的樣本數、取消去重、把 rollback 改成 commit。全部由離線 fixture 測試抓到。

**真實執行結果（僅開發段）**

候選點、可預測點、可評分點，以及「可預測且可評分」的集合，這次均為 **2,688 點**。四個基準方法各新增 2,688 筆預測，outcomes 也寫入 2,688 個開發點。

| 執行 | 新增成功提交 | 該輪失敗 | 已有紀錄跳過 | HTTP 次數 | HTTP 退避重試 | 耗時 |
|---|---:|---:|---:|---:|---:|---:|
| 首輪，max-calls=3500 | 2,681 | 1 | 6 | 2,682 | 0 | 135.728 秒 |
| 僅補缺漏，max-calls=3 | 1 | 0 | 2,687 | 1 | 0 | 17.898 秒 |
| 合計 | 2,682 | 1 次失敗，已補成功 | — | **2,683** | **0** | **153.626 秒** |

首輪唯一錯誤是 `choice_not_maximum`：API 回應的 choice 不是三類機率最大者，依規格拒收。補跑僅重問這個缺漏點，沒有根據標籤或猜對與否挑答案。首輪報告與失敗紀錄保留；補跑用 1 次 HTTP 成功，沒有放寬任何回應驗證。

最終 jev 共 **2,688 筆成功**（本次新增 2,682，加上既有 smoke 6 筆），**未完成／最終失敗 0 點**。自動 HTTP 重試 0 次；另有跨執行補跑 1 點／1 次 HTTP。這兩輪累計仍在最初 3,500 次保險內，沒有用到 smoke 計數檔。

五方法的共同交集為 **2,688 點**，原始準確率如下：

| 方法 | 正確／共同樣本 | 原始準確率 |
|---|---:|---:|
| jev | 1,183 / 2,688 | 44.0104% |
| always_flat | 1,361 / 2,688 | 50.6324% |
| majority | 1,361 / 2,688 | 50.6324% |
| momentum | 1,019 / 2,688 | 37.9092% |
| reversal | 1,282 / 2,688 | 47.6935% |

jev choice 分布：**up 45、flat 1,682、down 961**。以上只呈現原始統計，沒有 bootstrap 或優劣結論。

[首輪 JSON 報告](verification/run-dev-real.json)／[首輪進度](verification/run-dev-real-progress.txt)；[補跑 JSON 報告](verification/run-dev-retry.json)／[補跑進度](verification/run-dev-retry-progress.txt)。

完成後移除環境中的 API key，並將 HTTP transport 替換為一呼叫就報錯，再跑同一支 CLI：**0 次 HTTP、2,688 筆全部跳過、基準新增 0 筆、所有準確率與 choice 分布不變**。這次驗證另耗時 17.700 秒，不包含在上表正式執行與補跑的 153.626 秒內。[0 HTTP 重跑紀錄](verification/run-dev-zero-http.json)。

開發段 DB 核對：outcomes 2,688 筆；五個方法各 2,688 筆；同一時點的五方法 input_hash 一致。金鑰僅在記憶體內與檔案／DB 比對，未發現兩把金鑰落檔。[合併統計與完整性驗證](verification/run-dev-summary.json)。沒有 commit、沒有修改 `news/`；沒有執行保留段預測或計分。
