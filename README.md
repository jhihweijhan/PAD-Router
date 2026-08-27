# PAD Router

PAD Router 是用於檢視《龍族拼圖》6×5 珠盤的桌面工具。它可檢查可設定的隊長技發動條件、搜尋或評估轉珠路徑，並在使用者確認後，透過 ADB 安全地執行路徑。

## 需求

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/)
- 桌面介面需要 Python 的 Tk 支援
- 只有擷取或執行路徑時，才需要 `adb` 與已連線的 Android 裝置

## 使用 uv 啟動

```bash
uv run python pad_router.py --gui
```

介面可開啟 PNG 截圖，或擷取指定 Android 裝置的畫面。它會顯示原始圖片、辨識出的盤面與覆蓋標示；你可校正盤面、修正珠子、建立及儲存規則設定、手動畫出轉珠路徑，或搜尋符合條件的路徑。

只有在盤面已確認、隊伍條件通過，且使用者確認最終路徑後，程式才會執行路徑。執行後會比對預期盤面與手勢後的實際盤面。

## 缺少 tkinter 時

若執行時出現「開啟桌面介面需要 Python 的 tkinter 模組」，請安裝與 `uv run python --version` 相同版本的 Tk 系統套件。以目前的 Ubuntu 26.04／Python 3.14 為例：

```bash
sudo apt update
sudo apt install -y python3.14-tk
uv run python pad_router.py --gui
```

可用下列指令確認安裝成功：

```bash
uv run python -c "import tkinter; print(tkinter.TkVersion)"
```

## 測試

```bash
uv run python -m unittest -v
uv run python pad_router.py --self-check
```

## 注意事項

- 初版介面支援 PNG 輸入與標準 6×5 盤面。
- 規則設定的 JSON 欄位和值（例如 `combo_minimum`）維持英文，確保檔案可攜與相容；介面會以繁體中文說明其用途。
- 使用即時擷取或執行功能前，請先執行 `adb devices` 確認裝置已連線。
