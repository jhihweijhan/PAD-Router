# PAD Router Desktop GUI：現行規格

本文件記錄目前已實作的桌面 GUI 行為與安全邊界；操作步驟請看[使用指南](../guides/user-guide.md)，模組責任請看[架構文件](architecture.md)。研究資料集中於[研究目錄](../research/)，不代表本規格中的已實作功能。

## 範圍

目前完整桌面入口 `pad_router.py --gui` 仍使用既有 Tk 盤面流程，直到 cutover #14。Issue #9/#10 的 capture/review workspace 以明確的 `pad_router.py --webview` flag 啟動固定 GTK backend 的離線 pywebview，從相鄰的 `webview/` 載入本機 HTML、CSS 與 vanilla JavaScript；不載入遠端資產。

Issue #9/#10 的工作區垂直切片負責裝置清單、裝置選取、螢幕擷取、受控來源 PNG、persistent status/console 與 5×6 board review。`BoardInspectionBridge` 將 board cells、unknown count、selection、protected/enhanced/locked markers 序列化；修正 unknown 會前往下一格並立即更新數量。

來源截圖在既有 Tk Canvas 或 webview workspace 依可視區域顯示，辨識與校正仍使用原始 BGRA 像素、`BoardCalibration` 與 `BoardInspectionController`。Browser review 只更新受控 DTO，不修改 source image；含 unknown 時仍不可確認或執行。

## Ubuntu webview setup

Ubuntu 的 uv project venv 預設看不到 system site-packages；首次使用 `--webview` 前執行：

```bash
sudo apt-get update
sudo apt-get install --yes python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
uv venv --system-site-packages --allow-existing
uv sync
```

## 既有 Tk 完整流程（`--gui`）

1. 開啟 PNG 或選擇 ADB 裝置擷取畫面。
2. 以來源實際寬高推定盤面區域；可重新自動校正，也可提供新的校正。
3. 使用固定 HSV prototype、危害珠指標與局部中心圖樣辨識。心珠／闇珠的中心圖樣包含固定小網格的相對灰階與相鄰差分；訊號不足時回傳 `unknown`。
4. 若盤面含問號，依 `max_recognition_attempts`（1–5，預設 2）用同一份來源與校正重跑辨識，直到乾淨或達到上限。
5. `unknown` 會讓 GUI 進入 review mode；使用者可回答珠種並永久保存人工樣本。Ready 狀態另有 correction mode，可選取任何已辨識格並覆寫疑似錯誤。
6. 規則選單變更後立即建立並套用 `RuleProfile`；GUI 可載入或儲存 Profile JSON。
7. 在確認盤面上手動畫路徑或搜尋 Candidate Route，顯示 Match、cascade、既有 Combo、直接 Combo、直接最大 Combo 預估、危害與條件結果。
8. 只有可執行候選才可按「執行路徑」。按下即代表接受目前盤面，程式先寫入低權重資料，再直接呼叫 ADB；不再跳第二次確認視窗。
9. 保留手勢後盤面驗證；驗證失敗或資料寫入失敗時不得把流程當成成功。

## 操作模式

- **路徑模式（Ready）**：無問號時，Canvas 左鍵拖曳建立路徑。
- **檢視模式（Review）**：有問號時，Canvas 左鍵選取待回答格，珠種按鈕／`1`–`0` 回答；不啟動路徑拖曳。
- **修正模式（Correction）**：按「修正辨識」後，Canvas 左鍵只選取任一格；珠種按鈕／`1`–`0` 寫入人工覆寫。按「結束修正」回到路徑模式。

模式必須維持清楚分流：問號會優先進入 Review；Correction 只由使用者在 Ready 狀態主動開啟；Route drag 只在 Ready route mode 中啟動。

## 規則與搜尋

`RuleProfile` 包含條件群組、外部條件與危害策略；`RouteSearchOptions` 包含嘗試次數、seed、步數界限與 cascade。GUI 的執行步數上限預設 80：最大 Combo 的瓶頸是步數而非束寬，50 步會漏掉需要長路徑才能收尾的最後一組。Combo 下限的選項到「至少 10 Combo（5x6 上限）」為止，對應 5x6 盤面 3 顆一塊交錯排列的理論上限。搜尋保留固定 seed 可重現性，先服從條件／危害／shape 優先，再以直接 Match 數、扣除指定消法後的直接最大 Combo 預估、步數與路徑穩定性選擇結果；落珠連鎖的實際 Combo 不參與這個排序。一般條件束搜尋的同色珠距離勢能只是次級 tie-break，不取代直接最大 Combo，也不保證全域最佳。

## 安全條件

- 盤面仍有 `unknown` 時，不是可執行的 Confirmed Board。
- 執行需要符合條件且可執行的路徑、已確認盤面、有效裝置與校正。
- ADB `play` 會保留手勢按下／移動／放手與手勢後盤面驗證；異常時優先安全放手並回報失敗。
- 寫入 `.pad-router/orb-prototypes.json` 失敗時，GUI 不送出 ADB 路徑。

## 持久化

原型資料固定保存於專案內 `.pad-router/orb-prototypes.json`，不保存截圖；寫入先建立同目錄暫存檔，再用 `Path.replace()` 原子取代正式檔。舊的 10／13／18 維模型資料仍可讀取，新增資料可包含中心圖樣指紋。

## 不在範圍內

- 非 6×5 Board、非 PNG 來源、雲端服務或角色／怪物資料庫。
- 完整辨識 HP、隊伍組成、技能、地城狀態與遊戲隨機天降。
- ML、預訓練模型、OpenCV 或其他認知流程仍不在範圍內。
- pywebview 不支援的其他平台 backend、遠端資產與本地 HTTP 服務。
- 搜尋全域最佳保證或任意新 skin 的零標記辨識保證。

相關入口：[文件索引](../README.md)、[使用指南](../guides/user-guide.md)、[開發與測試](development.md)。
