# PAD Router 開發與測試

這份指南給需要在本機執行、修改或驗證 PAD Router 的維護者；使用者操作請看[使用指南](../guides/user-guide.md)，模組關係請看[架構](architecture.md)。

## 環境與原則

`pyproject.toml` 宣告 Python `>=3.10` 與固定版本 `pywebview==5.4`；本專案以 `uv` 作為唯一執行入口。`--gui` 是支援的離線 GTK webview 桌面入口，`--webview` 保留為相容別名；`pad_router_gui.py` 只保留 controller/bridge，Tk presentation 已在 web cutover 移除。

Ubuntu 的 uv project venv 預設隔離 system site-packages；要使用 GTK backend，先安裝 bindings/typelibs，再建立可讀取它們的 venv：

```bash
sudo apt-get update
sudo apt-get install --yes python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
uv venv --system-site-packages --allow-existing
uv sync
uv run python -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('WebKit2', '4.1')"
```

核心保持標準函式庫與普通 CPU 可用：辨識與規劃不依賴 ML、預訓練模型、OpenCV、雲端服務或怪物資料庫。不要把研究文件中的候選方案當成已存在的 API。

## Native launch verification

The documented native launch was attempted on the current host with `uv run python pad_router.py --gui`; it exits before creating a window because the GTK backend is unavailable and reports: `Ubuntu GTK backend is unavailable; install python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, then run uv venv --system-site-packages --allow-existing and uv sync`. This is an environment prerequisite blocker, not an application fallback; native launch is not claimed as smoke-tested here, while local-asset browser smoke and all Python safety tests remain runnable.

## 執行入口

```bash
# 支援的離線桌面 web UI（所有已交付工作流）
uv run python pad_router.py --gui

# --webview 相容別名
uv run python pad_router.py --webview

# CLI 說明
uv run python pad_router.py --help

# 自檢（不需要裝置）
uv run python pad_router.py --self-check
```

CLI 預設是 dry run，只列出偵測盤面、Combo、score、步數與路徑；只有加入 `--play` 才會呼叫 ADB 執行。`--board` 可用 30 個 1–6 整數直接提供盤面，跳過截圖辨識；實際執行仍受路徑、盤面與驗證安全條件約束。

## 測試

完整測試使用 Python 內建 `unittest`：

```bash
uv run python -m unittest
```

目前測試分工：

- `test_pad_router_planning.py`：Rule Profile JSON、Match/cascade、危害策略、手動路徑、固定 seed 搜尋、候選排序、診斷候選與執行資格，以及 search phase progress/cooperative cancellation。
- `test_pad_router_gui.py`：PNG 解析、校正、controller、人工修正、原型資料原子持久化、辨識／中心圖樣、問號重試、webview bridge 快照／非同步擷取、5×6 review DTO、rules/search invalidation、stale-generation rejection、cancelled previews、approval/learning/verification/safe-stop/conflict execution 與 web entrypoint/GTK contract。

修改核心規則、安全條件或 bridge 合約時，先跑最接近的測試類別，再跑完整命令；不要把連線裝置或使用者截圖作為測試依賴。測試應使用固定的 5×6 Board、in-memory pixels、可替換 detector/capture/device-lister/executor 與固定 seed。

## 重要資料與檔案邊界

- `pad_router.py`：核心資料模型、規則／Match／cascade、辨識、搜尋、CLI 與 ADB play。
- `pad_router_gui.py`：`BoardInspectionController`、受控 PNG snapshot、序列化 bridge、人工 correction、原型資料與執行流程；不含 presentation widgets。
- `pad_router_webview.py` 與 `webview/`：離線 pywebview shell 及本機 HTML/CSS/JavaScript 資產；前端只呼叫 bridge intent。
- `.pad-router/orb-prototypes.json`：web workspace 執行後可能產生的專案內永久學習資料；只保存小型特徵與標籤，不保存截圖。寫入先用同目錄暫存檔，再以 `Path.replace()` 原子取代正式檔。
- 使用者選擇的 Rule Profile JSON：由 web workspace 的「匯出 JSON」保存、「匯入 JSON」載入；問號重試次數不存入此 Profile。

不要在測試中引用使用者的 `/tmp` 截圖、真實 ADB 序號或任何未提交的本機學習檔。不要修改其他代理正在處理的檔案；文件變更也應保留既有研究文件與連結。

## 變更檢查清單

1. 先確認修改範圍與既有工作樹變更，不要覆原他人的內容。
2. 保持 public `RouteSearchOptions`／`RouteSearchResult` 與 controller/bridge 合約穩定，除非需求明確要求變更。
3. 對辨識或搜尋新增可觀察行為時，新增一個 deterministic、可失敗的行為測試。
4. 確認 unknown／不確定結果不會繞過路徑執行安全檢查。
5. 執行完整測試與空白檢查：

```bash
uv run python -m unittest
git diff --check
```

## 已知限制

- 盤面固定為 6×5，來源檔案支援 PNG；web UI 自動推定校正仍可能需要人工調整。
- HSV 與局部中心圖樣／邊緣描述子是校正後的 deterministic gate，不是通用視覺模型；訊號不足時會拒判為 `unknown`。
- 原型資料能改善與已標記樣本相同或高度相近的視覺輸入，不能保證任意新 skin、動畫或裝置都自動正確。
- 搜尋使用有限束寬、有限步數與固定 seed；Combo 距離勢能只是一般條件搜尋中的次級啟發式，不保證全域最佳。
- 程式不完整辨識 HP、隊伍組成、技能、地城狀態或隨機天降；外部條件必須由使用者提供或確認。
- ADB 是真實裝置操作邊界；任何失敗或驗證不確定都應保留安全放手並要求重新擷取，而不是猜測。

回到[文件入口](../README.md)或查看[目前 web UI 規格](desktop-gui-spec.md)。