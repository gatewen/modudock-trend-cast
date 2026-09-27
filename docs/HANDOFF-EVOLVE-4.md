# 第 4 輪交件：A 暫停，B 完成

使用者重新確認預測時間尺度，依暫停指令停止 A。指令抵達前，實驗 3（p3）已建立，階段 A 已提交 500 點、缺答 0；HTTP 500 次、重試 0，帳本累計 644／3,000。指令後沒有新增真實 jev 請求，也沒有補跑或進入階段 B。A 的程式、固定抽樣清單與已完成結果留在工作目錄，待 cc 決定。

- [暫停狀態](verification/evolve-4-pause.json)
- [實驗範圍一致性檢查](verification/evolve-4-preflight.json)：暖機 2024-08-01、開發段 2024-09-05～2026-01-21、保留段 2026-01-22～2026-08-31，均與實驗 1 相同。
- [先保存的 500 點清單](verification/evolve-4-stage-a-times.json)：seed=20260927；抽樣只讀時間戳，不讀標籤或機率。
- 暫停前已產生的 A 執行紀錄與結果：`verification/evolve-4-stage-a-run.txt`、`verification/evolve-4-stage-a-result.json`。沒有重新選樣或挑成功子集合。

## B 的畫面修正

1. 原始「開發段成績」表格先顯示，接著是獨立卡片「進化新方法（開發段）」。
2. 「保留段已使用」與「前瞻段」各只有一張卡片；對應的執行／看結果按鈕放在該卡內。保留段表格使用完整寬度，按鈕放於表格下方。
3. 狀態列在資料範圍旁顯示「另有前瞻段 18 日未揭露」。後端只計算已納入前瞻段、尚未揭露的日期集合，不讀標籤或成績。切換實驗、無未揭露日期時清除舊註記。
4. 切換實驗時清除所有分開的成績容器，避免留下上一個實驗的資料。

主要檔案：`front/front.js`、`front/style.js`、`back/evolution_forward.py`、`tests/front.test.mjs`、`tests/test_evolution_forward.py`、`scripts/mutation_front.py`、`scripts/check_evolve4_shell.mjs`。

## 驗證

- Python：281 項通過，完整輸出 [evolve-4-python-tests.txt](verification/evolve-4-python-tests.txt)。此輪全套測試在暫停前啟動，測試 jev 使用假 server，未消耗真實額度。
- 前端：34 項通過，完整輸出 [evolve-4-front-tests.txt](verification/evolve-4-front-tests.txt)。
- 前端變異：40／40 轉紅，含新增的卡片巢狀、重複標題、按鈕位置、未揭露日數、跨實驗註記及舊成績殘留守衛，[逐組對照](verification/evolve-4-front-mutations.txt)。
- Python 新增變異：22／22 轉紅；於暫停前完成，包含日數不得計入已揭露日及 A 的抽樣／換算守衛，[逐組對照](verification/evolve-4-mutations.txt)。
- 真殼使用獨立 `127.0.0.1:60622`，`SSL_CERT_FILE` 未設定，另取消載入 TypeSafe 金鑰。瀏覽器只准 `status/report/day`，不送 run/reveal。catalog 包含 trend-cast、前半載入成功、同步完成、無頁面錯誤。
- [真殼證據](verification/evolve-4-shell-evidence.json)、[驗收輸出](verification/evolve-4-shell-test.txt)、[淺色截圖](verification/evolve-4-light.png)、[深色截圖](verification/evolve-4-dark.png)。驗收後關閉殼與瀏覽器。

沒有新增揭露、沒有 commit／push，沒有修改 news 或殼的檔案。
