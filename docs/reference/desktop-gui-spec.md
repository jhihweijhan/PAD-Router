# PAD Router Desktop Web UI：第一視角改版 v2

本文件記錄桌面 web UI 的現行版面、行為與安全邊界；操作步驟請看[使用指南](../guides/user-guide.md)，模組責任請看[架構文件](architecture.md)。研究資料集中於[研究目錄](../research/)，不代表本規格中的已實作功能。

## 範圍

完整桌面入口 `pad_router.py --gui` 使用固定 GTK backend 的離線 pywebview，`--webview` 保留為相容別名。pywebview 從 repository 相鄰的 `webview/` 載入本機 HTML、CSS 與 vanilla JavaScript，不載入遠端資產，也不需要 HTTP server。

workspace 是「報紙漫畫格 × 掌上機」第一視角：黑底 HUD 顯示品牌、裝置、盤面切換、`截圖 ▶ 轉珠 ▶ 執行` 與校正／匯入匯出／除錯／更多工具；左欄以原始裝置 PNG 為主，右側並排目前盤面與拖曳後預覽，右欄呈現條件、設定與推理。底列是一行兩塊：寬版事件主控台與靠右的緊湊動作群，順序為「擷取畫面 → 重新計算路徑 → 執行路徑」，執行中由停止按鈕取代執行。

設定磚使用亮底 `paper`／`panel`／`panel-2`、`ink`、`red`、`sky`、`sun`、`grass` 調色、3px 黑邊與實心位移陰影；除珠子外不使用圓角。二態設定直接點擊 48px 磚切換，多值設定在磚面顯示目前值，點擊循環、Shift 點擊反向，滾輪與左右方向鍵也可調整。設定列固定八顆，不含 `<select>` 或 checkbox；步數、嘗試、seed、MOVE 間隔與外部條件均沿用既有候選值。`planningPayload()`、`searchPayload()` 與 `set_learning_enabled` 的 payload 鍵值與型別不變。

workspace 負責裝置清單、裝置選取、螢幕擷取、受控來源 PNG、盤面校正、persistent status/console、6×5／7×6 board review、規則設定、Profile JSON 匯入／匯出、背景路徑搜尋、目前候選與安全執行。設定磚 tooltip 保留「做什麼、依什麼邏輯」說明；AI 模型學習仍要等後端確認快照後才更新畫面。`BoardInspectionBridge` 以 board/rule generation 丟棄 stale candidates，並將 acceptance、gesture、verification、stop phase 及結果序列化。

來源截圖在 webview workspace 依可視區域顯示，辨識與校正仍使用 `adb screencap` 原始 RGBA_8888 像素（PNG 編解碼不換 R／B 通道）、`BoardCalibration` 與 `BoardInspectionController`。Browser UI 只更新受控 DTO；含 unknown、診斷或未核准候選時不可執行。支援的 desktop 尺寸為 1100×720、1366×768、1440×900 與 1920×1080；兩欄的設定、消法、推理、步數與動作群及縮成一條的 console 在各尺寸保持第一視角可見。

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

1. 從 HUD 更新裝置並選取序號，再由底列「擷取畫面」取得來源；device/capture/calibration 在背景執行，成功／錯誤狀態會進 status 與 structured console。
2. 左欄固定顯示受控原始截圖與路徑疊圖，截圖右側並排目前盤面與拖曳後預覽；在目前盤面修正 unknown／疑似珠子，切換強化／鎖定標記並設定／清除保護格。
3. 頂列盤面磚切換 6×5／7×6；盤面校正可從 HUD 的「校正」圖示開啟設定展開盤，輸入左上座標與格寬或按自動校正。原始來源圖不被人工修正改寫。
4. 右欄由上而下顯示條件 1、色珠／消法／Combo 選擇、八顆常用設定磚與推理；按「＋ 條件」展開條件 2／3。二態磚直接點擊，多值磚點擊循環、Shift 點擊反向，滾輪與左右方向鍵可調整。
5. 「更多」工具開啟外部條件與 seed 磚，以及危害珠顏色、校正、Profile 與除錯資訊；設定磚 tooltip 保留用途與邏輯說明，AI 模型學習要等後端確認快照後才更新畫面。
6. 底列動作群依序為擷取畫面、重新計算路徑、執行路徑；只有可執行、已確認且已核准的候選才能執行，執行中以停止按鈕取代執行按鈕，停止要求會在目前手勢安全放手後生效。


## 規則與搜尋

`RuleProfile` 包含條件群組、外部條件與危害策略；`RouteSearchOptions` 包含嘗試次數、seed、步數界限與 cascade。GUI 的執行步數上限預設 80：最大 Combo 的瓶頸是步數而非束寬，50 步會漏掉需要長路徑才能收尾的最後一組。Combo 下限的選項到「至少 14 Combo（7×6 上限）」為止；6×5 的理論上限是「至少 10 Combo（6×5 上限）」，兩者都對應 3 顆一塊交錯排列鋪滿盤面（ROWS×COLS//3）。搜尋保留固定 seed 可重現性，先服從條件／危害／shape 優先，再以直接 Match 數、扣除指定消法後的直接最大 Combo 預估、步數與路徑穩定性選擇結果；落珠連鎖的實際 Combo 不參與這個排序。一般條件束搜尋的同色珠距離勢能只是次級 tie-break，不取代直接最大 Combo，也不保證全域最佳。

## 安全條件

- 盤面仍有 `unknown` 時，不是可執行的 Confirmed Board。
- 執行需要符合條件且可執行的路徑、已確認盤面、有效裝置與校正。
- ADB `play` 會保留手勢按下／移動／放手與手勢後盤面驗證；異常時優先安全放手並回報失敗。
- 寫入 `.pad-router/orb-prototypes.json` 失敗時，GUI 不送出 ADB 路徑。

## 持久化

原型資料固定保存於專案內 `.pad-router/orb-prototypes.json`，不保存截圖；寫入先建立同目錄暫存檔，再用 `Path.replace()` 原子取代正式檔。舊的 10／13／18 維模型資料仍可讀取，新增資料可包含中心圖樣指紋。

## 不在範圍內

- 6×5 與 7×6 以外的 Board、非 PNG 來源、雲端服務或角色／怪物資料庫。
- 完整辨識 HP、隊伍組成、技能、地城狀態與遊戲隨機天降。
- ML、預訓練模型、OpenCV 或其他認知流程仍不在範圍內。
- pywebview 不支援的其他平台 backend、遠端資產與本地 HTTP 服務。
- 搜尋全域最佳保證或任意新 skin 的零標記辨識保證。

相關入口：[文件索引](../README.md)、[使用指南](../guides/user-guide.md)、[開發與測試](development.md)。
