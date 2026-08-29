# PAD Router Desktop Web UI：現行規格

本文件記錄目前已實作的桌面 web UI 行為與安全邊界；操作步驟請看[使用指南](../guides/user-guide.md)，模組責任請看[架構文件](architecture.md)。研究資料集中於[研究目錄](../research/)，不代表本規格中的已實作功能。

## 範圍

完整桌面入口 `pad_router.py --gui` 使用固定 GTK backend 的離線 pywebview，`--webview` 保留為相容別名。pywebview 從 repository 相鄰的 `webview/` 載入本機 HTML、CSS 與 vanilla JavaScript，不載入遠端資產，也不需要 HTTP server。

workspace 負責裝置清單、裝置選取、螢幕擷取、受控來源 PNG、盤面校正、persistent status/console、5×6 board review、規則設定、Profile JSON 匯入／匯出、背景路徑搜尋、目前候選核准與安全執行。`BoardInspectionBridge` 以 board/rule generation 丟棄 stale candidates，並將 acceptance、gesture、verification、stop phase 及結果序列化。

來源截圖在 webview workspace 依可視區域顯示，辨識與校正仍使用原始 BGRA 像素、`BoardCalibration` 與 `BoardInspectionController`。Browser UI 只更新受控 DTO；含 unknown、診斷或未核准候選時不可執行。支援的 desktop 尺寸為 1100×720、1366×768、1440×900 與 1920×1080，status 與 console 在各尺寸保持可見。

## Ubuntu webview setup

Ubuntu 的 uv project venv 預設看不到 system site-packages；首次使用 `--gui` 前執行：

```bash
sudo apt-get update
sudo apt-get install --yes python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
uv venv --system-site-packages --allow-existing
uv sync
```

啟動時 `pad_router_webview.py` 要求 `gi.require_version("Gtk", "3.0")` 與 `gi.require_version("WebKit2", "4.1")`，再呼叫 `webview.start(..., gui="gtk")`；缺少套件時會回報精確安裝指令，不會改用未驗證的 backend。

## Workspace workflows

1. 更新裝置、選取序號並擷取畫面；device/capture/calibration 在背景執行，成功／錯誤狀態會進 status 與 structured console。
2. 盤面校正區可輸入左上座標與格寬，或按自動校正；原始來源圖不被人工修正改寫。
3. 檢查 5×6 盤面，修正 unknown／疑似珠子，切換強化／鎖定標記並設定／清除保護格。
4. 在規則與搜尋區設定條件、危害策略、外部條件、搜尋界限與 seed；搜尋結果會區分可執行候選與診斷預覽，且可取消。
5. 在相容性區匯入／匯出既有 `RuleProfile` JSON；在除錯區查看來源、generation、待處理工作與 execution phase，不取代 board/rule/search/execution。
6. 只有可執行、已確認且已核准的候選才能執行；執行前完成接受／學習，執行中拒絕衝突命令，停止要求會在目前手勢安全放手後生效。


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
