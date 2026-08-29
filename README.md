# PAD Router

以 Python 標準函式庫把《龍族拼圖》6×5 盤面從截圖整理成可檢查、可規劃的轉珠路徑。

<p align="center">
  <img src="docs/assets/pad-router-overview.svg" alt="PAD Router 工作流程：PNG 或 ADB 截圖輸入，經過 6×5 盤面辨識與人工修正，再進行路徑規劃。" width="960">
</p>

## 三個核心能力

- **看懂來源**：從 PNG 或 Android ADB 截圖建立固定 6×5 盤面，並保留可檢查的辨識結果。
- **修正資料**：問號或疑似誤判時可人工修正；相近的後續截圖可使用專案內永久學習資料。
- **規劃並驗證**：依規則評估手動路徑或搜尋 heuristic Candidate Route，通過安全條件後執行並驗證手勢。

## Quick start

需求：Python 3.10 以上、[`uv`](https://docs.astral.sh/uv/)、`pywebview==5.4`，以及 Linux GTK/WebKit 系統套件；ADB 擷取或執行手勢才需要 `adb` 與已授權的 Android 裝置。

```bash
# 離線 pywebview 工作區
uv run python pad_router.py --gui

# 舊版 Tk 完整盤面流程（後續切片移除）
uv run python pad_router.py --tk-gui

# CLI dry run（不送出 ADB）
uv run python pad_router.py --board 111222345634456563123451234563

# 自檢與完整測試
uv run python pad_router.py --self-check
uv run python -m unittest
```

## 文件

[開啟文件入口](docs/README.md)

## 限制

- 盤面固定為 6×5，辨識只在校正後來源上工作；訊號不足時會保留 `unknown`，仍需人工修正。
- 搜尋是有限寬度、有限步數的 heuristic，不保證全域最佳；距離勢能只作次級排序。
- 實際手勢需要正確校正、已授權 ADB 裝置與手勢後驗證；按下執行前仍會阻擋不可執行或未確認的路徑。
