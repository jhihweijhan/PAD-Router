# 第一視角改版 v2：報紙漫畫格 × 掌上機

第一視角的資訊架構（v1）已經實作完成。這一版處理兩件事：**修掉「太擠」的三個成因**，以及**把深色儀表風格換成亮底的 8-bit ＋ Peanuts**。只動 `webview/index.html`、`webview/style.css`、`webview/app.js`，以及被這三個檔綁住的測試與規格文字。

## A. 先修「太擠」

三個獨立成因，量測自目前實作：

1. **重疊。** `.setting-orb > small` 是 `position: absolute; top: calc(100% + 2px)`，不佔高度，所以下面的 `<select>` 只能用 `margin-top: 12px` 手動讓位。40px 圓 + 2px + 11px 說明 = 53px，而選單從 52px 開始——**差 1px 就疊在一起**。修法不是調參數：**說明文字要回到文件流**，用 grid 的 `gap` 排，不要絕對定位。
2. **過密。** `.settings-orb-row` 是 `repeat(9, minmax(0,1fr))` 加 4px 間距，每格只有約 44px。所有標籤都掛 `overflow:hidden; text-overflow:ellipsis`，這就是被截斷的原因。
3. **太小。** 說明 9px、下拉 10px，低於可讀下限。這是過密的症狀。

修正值：**說明離圖示 8px（不是 2px）、說明字級 12px（不是 9px）、圖示 48px（不是 40px）、圖示間距 14px。**

## B. 互動：toggle 就直接點圖示

**二態的設定不要下拉選單，點圖示本身就切換。**

| 設定 | 兩個狀態 | 做法 |
| --- | --- | --- |
| 危害珠策略 | 避開 / 允許 | 點磚切換，`aria-pressed` 表狀態 |
| 落珠連鎖 | 計入 / 只計直接 | 同上 |
| 條件關係 | 全部符合 / 任一符合 | 同上 |
| 轉珠後確認 | 停手確認 / 直接放手 | 同上，移除原本的 checkbox |
| AI 模型學習 | 開 / 關 | 同上，移除原本的 checkbox；維持既有「等後端確認快照才更新」的邏輯 |
| 盤面 | 6×5 / 7×6 | 頂列工具區，點擊切換 |

**多值的設定（步數、嘗試、種子、MOVE 間隔、外部條件）也不用下拉，改成把值印在磚面上、點一下換下一個值。**

- 點擊 → 下一個值；`Shift` + 點擊 → 上一個值；滾輪與 `←` `→` 也要能調。
- 目前值直接顯示在 48px 磚面中央（`80`、`30`、`.04`），磚底下是 2 字說明。
- `aria-label` 要播報目前值（例如「步數 80，共 4 段」），值變動時更新。
- 候選值沿用現有清單：步數 `30/50/80/100`、嘗試 `5/10/25/30/50`、種子 `0/1/42/2026`、MOVE 間隔沿用 `min=0 step=0.01` 但以 `0.02/0.04/0.06/0.10` 四段循環，預設仍是 `0.04`。

**設定列不得再出現任何 `<select>` 或 `<input type="checkbox">`。**送給後端的 `planningPayload()`、`searchPayload()`、`set_learning_enabled` 鍵值與型別完全不變——UI 換了，payload 沒換。

## C. 視覺系統

亮底。整體從深色儀表改成「報紙漫畫格 × 掌上機」。

**顏色**（Peanuts 調色 + 遊戲珠色）

```
--paper   #FBF7EE   紙（底）
--panel   #FFFFFF   面板
--panel-2 #F1ECE0   次級面板
--ink     #0B0A0A   墨（所有邊框與文字）
--ink-2   #5A554C   次級文字
--red     #F60808   執行／主要動作
--sky     #71C7FB   已選狀態
--sun     #FDF203   注意／數值標記
--grass   #3FA34D   通過
```

珠色不動：`.board-cell[data-color=n]` 現有的六個 hex 與危害珠色照舊。珠色是遊戲的既定資料，改了就對不上截圖。

**幾何（取自 8-bit）**

- 8px 網格：所有邊距、間距、尺寸都是 8 的倍數（14px 的圖示間距是唯一例外，為了讓一列八顆放得下）。
- **零圓角**，除了珠子。
- 邊框一律 `3px solid var(--ink)`。
- 陰影一律實心位移，不要模糊：面板 `4px 4px 0 var(--ink)`，小元件 `3px 3px 0`。按下時改成 `1px 1px 0` 並 `translate: 2px 2px`。
- 不要漸層。進度條用 `repeating-linear-gradient` 做成方塊格，不要平滑填色。
- 轉場用 `steps()`，或者乾脆不要轉場。

**形狀語彙：圓＝盤面真實資料，方＝工具**

目前所有東西都是圓的，設定和珠子長一樣，眼睛分不出資料和控制。切一刀：

- **圓**：色珠選擇、盤面格、條件裡的色珠。用遊戲珠色 + 3px 黑邊。
- **方**：設定磚、消法磚、動作按鈕、面板。白底黑邊。

**字型（離線限制）**

本機 355 個字族裡沒有任何點陣字或漫畫字，能顯示繁中的只有 Noto CJK。所以風格不靠字型：中文 `"Noto Sans TC", "Noto Sans CJK TC", system-ui, sans-serif`（900／700／500），數字與代號 `"IBM Plex Mono", "Noto Sans Mono CJK TC", ui-monospace, monospace`。**不得引入任何字型檔或遠端字型。**

## D. 版面重排

留白放大之後右欄在 900px 高度塞不下（設定架 354px + 推理 308px + 面板頭 35px + 間距 > 可用的 733px）。解法不是把間距調回去：

- **主欄改 `minmax(0, 1fr) 600px`。**
- **兩個小盤面（現在／拖曳後預覽）從右欄搬到左欄**，貼在原始截圖右側——左欄本來就有約 430px 的空白寬度沒用到。左欄＝「盤面長什麼樣」（真實截圖 + 已辨識 + 預測），右欄＝「我要什麼、算出什麼」。
- 右欄由上到下：條件列 / 色珠列 / 消法列 / 設定列，然後推理面板。
- 頂列改成黑底 HUD：品牌、裝置、盤面、`截圖 ▶ 轉珠 ▶ 執行`、以及右側工具鈕（校正 ◎、匯入匯出 ⇅、除錯 #、其餘設定 ⋯）。「其餘 ⋯」從設定列移到這裡，設定列因此是八顆。
- 底列一行兩塊：主控台（寬）＋ **動作群（緊湊一組、靠右、不滿版）**，順序為 擷取畫面 → 重新計算路徑 → 執行路徑，`#execute-route` 為紅色主要動作，執行中由 `#stop-execution` 取代。

## E. 沿用 v1、不要改壞的部分

- 形狀消法的七個點陣遮罩照舊，且必須繼續對著 `pad_router.py:612` 的 `_shape_matches()`：`4:1111`、`5:11111`、`6:111111`（跟著盤面切換成 7）、`3:111111111`、`3:010111010`、`3:100100111`、`3:111010010`。
- 每顆設定磚的 tooltip 照舊，寫「做什麼、依什麼邏輯」。tooltip 在亮底上要用白底 3px 黑邊 + 實心陰影。
- 條件 1 常駐、條件 2／3 用 `el.hidden` 展開。
- 推理區常駐：階段 + 方塊進度條 + 逐條件核對 + 路徑步數與起點。

## F. 不可破壞的約束

- **離線。** 不得引入任何遠端資產，`assertNotIn("://", html + css + js)` 必須繼續通過。
- **不動 Python。** `pad_router.py`、`pad_router_gui.py`、`pad_router_webview.py` 不改；bridge payload 形狀不變。
- **所有 `command()` 名稱與呼叫點保留**：`select_cell`、`correct_cell`、`set_protected_cell`、`set_rule_profile`、`search_route`、`cancel_search`、`execute_route`、`stop_execution`、`capture_screen`、`calibrate`、`auto_calibrate`、`import_rule_profile`、`export_rule_profile`、`set_learning_enabled`。`app.js` 內不得出現 `adb` 或 `solver` 字樣。
- **安全閘門原樣。** unknown 珠不可執行、未核准／診斷候選不可執行、`#execution-gate`／`#execution-status`／手勢後驗證邏輯不變。
- **無障礙。** 每顆磚都要 `aria-label`（多值的要播報目前值）與可見 focus；`:focus-visible` 用 `3px solid var(--red)`。盤面方向鍵操作保留。尊重 `prefers-reduced-motion`。
- **對比。** 亮底會讓光珠 `#b9a939` 與心珠 `#c46d98` 更難分，所以每顆珠都要 3px 黑描邊——這是功能不是裝飾。
- **四個支援尺寸**（1100×720、1366×768、1440×900、1920×1080）：設定列、消法列、推理、步數與三顆動作都不得落在折線之下；body 不得橫向捲動；設定列不得換行；1100×720 下 topbar 不得換行。注意實心陰影每個面板右下多吃 4px，排版要算進去。

## G. 要一起更新的檔案

- `test_pad_router_gui.py` 的 `WebviewAssetTests`：現有斷言綁著舊的深色樣式與 `<select>`／checkbox 設定。改成對應新結構，**但保留所有安全性斷言**（離線、command 名稱、無 adb／solver、id 存在性）。新增斷言：設定列不含 `<select>` 與 `type="checkbox"`、`--paper`／`--ink` token 存在。
- `docs/reference/desktop-gui-spec.md`：更新版面與視覺段落。

## H. 驗收

```bash
uv run python -m unittest
uv run python pad_router.py --self-check
```

兩個都要綠。另外用 `google-chrome --headless` 量四個尺寸，確認設定列、消法列、推理與三顆動作按鈕的 `getBoundingClientRect().bottom` 都小於 `innerHeight`、`scrollWidth === clientWidth`、且設定列的磚沒有換行（子元素 `offsetTop` 全部相同），把數字寫進完成回報。

---

## A-2. v2 實作後的三個回歸（必修）

### 1. 說明文字仍然被塞在磚裡面

A 段的原始訴求沒修好。目前 `.setting-orb` 與 `.condition-orb` 都是：

```css
width: 48px; height: 48px;
grid-template-rows: 1fr auto; gap: 8px;
```

`<small>` 是它的**子元素**，所以圖示和說明擠在同一個 48px 方塊內，說明壓在磚的下邊框上。正確結構是說明在磚的**外面下方**：

```
button.setting-orb        無邊框、無固定高度、display:grid、gap:8px、justify-items:center
  span.setting-face       48×48、3px solid var(--ink)、3px 3px 0 實心陰影、放圖示或數值
  small                   12px、正常文件流、在磚下方 8px
```

`.condition-orb` 同樣處理。兩者的 `<small>` 字級從 11px 改成 12px。

**磚的背景色（含 `aria-pressed` 時的 `--sky`）只套在 `.setting-face` 上，不要套在整顆 `button` 上**，否則說明文字會坐在色塊裡。

### 2. 頂列狀態區太擠

「尚未載入來源／閒置」「尚未更新裝置。」「目前 尚未選取」三行 11px 文字塞在很窄的欄位，黑底上的黃字在這個字級難讀。收成一行，或給它足夠寬度；字級不低於 12px。

### 3. 條件關係磚的說明沒反映狀態

那顆磚的說明寫死「條件」，應該跟著狀態顯示「全部」或「任一」，和其他多值磚一樣把目前值反映在說明上。

### 驗收（追加）

headless 四個尺寸下，除了原有的檢查，還要確認**說明文字的 `getBoundingClientRect().top` 大於等於磚面的 `bottom`**——也就是說明真的在磚的外面下方，不是疊在上面。

---

## A-3. 實機回報的兩個遮擋（必修）

兩個都用 headless 量到，成因不同。

### 1. 條件選項面板被 `.planning-panel` 的 `overflow: hidden` 切掉

`.condition-menu` 是 `position: absolute; left: 50%; transform: translateX(-50%)`，最寬 440px。它的祖先 `section.panel.planning-panel` 是 `overflow: hidden`。1440×987 下實測：門檻那顆的選單**右邊被切掉 121px**（所以最後一個選項看不到）；視窗較矮時下緣也會被切。

修法（不要直接拿掉 `.planning-panel` 的 `overflow: hidden`，它擋的是別的東西）：

- **水平方向夾住**：開啟時計算位置，讓選單完全落在 `.planning-panel` 的 padding box 內；不要無條件 `left: 50%`。貼右邊的觸發鈕要往左推，貼左邊的往右推。
- **垂直方向翻面**：若 `top: calc(100% + 14px)` 會讓選單底部超出面板，就改開在觸發鈕上方。
- **不要橫向捲動**：把 `overflow-x: auto` 換成 `flex-wrap: wrap`，選項多就往下長，不要藏在看不見的捲動裡。

### 2. 珠子調色盤最左邊那顆的 focus 外框被邊框蓋住

`.orb-palette` 是 `repeat(6, minmax(0, 1fr))`，但按鈕是固定 48px。視窗變窄時欄位比按鈕還窄，按鈕就溢出欄位；置中之後最左邊那顆會頂到 fieldset 的內距邊界。實測各寬度下最左邊按鈕距 fieldset padding box 的餘裕：

| 視窗寬 | 欄寬 | 按鈕寬 | 餘裕 |
| --- | --- | --- | --- |
| 960 | 42px | 48px | **0px** |
| 1000 | 49px | 48px | **0px** |
| 1040 | 55px | 48px | 4px |
| 1100 | 65px | 48px | 9px |
| 1440 | 122px | 48px | 37px |

`:focus-visible` 是 `outline: 3px solid var(--red); outline-offset: 2px`，需要 5px 餘裕。**所以 1040px 以下一定會被切**。

修法：

- **固定尺寸的控制項不要放進會收縮的等寬格線**。`.orb-palette` 與 `.hazard-orb-group` 改成 `display: flex; flex-wrap: wrap; gap: 8px;` ——按鈕永不收縮，空間不夠就換行，不會溢出。
- `.correction-controls fieldset` 的 `padding` 從 8px 加到 12px，讓 5px 的 focus 外框永遠有空間。
- 順手掃一遍：**任何固定尺寸元件放在 `minmax(0, 1fr)` 軌道裡都有同樣的病**，找到就一起改。

### 驗收（追加）

- 960／1000／1040／1100／1440 五個寬度下，`.orb-palette` 與 `.hazard-orb-group` 的每顆按鈕，其 `getBoundingClientRect()` 加上 5px 外框餘裕後都要落在 fieldset 的 padding box 內。
- 三種條件選單（色珠／消法／門檻）在條件 1／2／3 全部展開時，`getBoundingClientRect()` 都要完全落在 `.planning-panel` 的 padding box 內。

---

## A-4. 條件模型的兩個修正

### 1. Combo 門檻是獨立項目，不屬於任何一條條件

目前每一條條件都有自己的「門檻」磚，`planningPayload()` 會為每一列各吐一個 combo 條目：

```js
conditions: Array.from(conditionRows).flatMap((row) => {
  const color = row.dataset.colorValue || "不指定";
  return [row.dataset.shapeValue, row.dataset.comboValue]
    .filter(...).map((label) => ({ label, color }));
}),
```

這是錯的模型。「至少 N Combo」講的是整個盤面的結果，不是某一條排列條件的屬性——三條條件各設一個門檻沒有意義。

改成：

- **條件列只有兩顆磚：色珠 + 消法。**把門檻磚從條件列拿掉。
- **Combo 門檻變成一個獨立的全域控制項**，放在條件列外面（建議緊接在條件區塊下方，或與「條件關係」同一排），只有一個。
- `planningPayload()` 的 `conditions` 依然是同一個扁平陣列、同樣的 `{label, color}` 形狀：先放各列的 `shapeValue` 條目，**最後再附加一個全域的 combo 條目**（`color` 用 `"不指定"`），未設定時不附加。**payload 的鍵值與型別不變**，只是條目怎麼組出來變了。
- 選項沿用現有清單（不限／至少 3／5／7／8／9／10（6×5 上限）／11／12／13／14（7×6 上限））。

### 2. 條件可以新增，但不能移除

現在只有「＋ 條件」，加了就拿不掉。每一條條件要能移除：

- 條件 2、3 各自帶一個移除控制（沿用同一套視覺：方磚或小按鈕，3px 黑邊，`aria-label` 寫「移除條件 2」）。
- 移除時把該列 `hidden`、清掉它的 `data-color-value` 與 `data-shape-value`，並**重新編號**剩下的條件，讓編號永遠是連續的 1、2、3。
- 條件 1 不可移除（至少要有一條），但可以清成「不指定」。
- 全部條件都移除／未指定時，`conditions` 陣列就只剩全域 combo 條目（或空陣列），搜尋仍要能執行。
- 「＋ 條件」在已經有 3 條時要 `disabled`，移除之後恢復。

### 驗收（追加）

- 條件列不再有 `data-condition-trigger="combo"`；全域門檻控制項只有一個。
- 設好門檻並設定條件 1／2 之後，`planningPayload().conditions` 裡的 combo 條目**恰好一個**且在陣列最後。
- 新增到 3 條、逐一移除後，剩餘條件的編號是連續的，且「＋ 條件」的 `disabled` 狀態正確。

---

## A-5. 自動儲存設定（新功能）

每次關掉再打開，工作區要回到上一次的設定。

### 存什麼：直接存 UI 已經在送的 payload，不要另外發明 schema

檔案 `.pad-router/workspace-settings.json`：

```json
{
  "version": 1,
  "rule_profile": { "conditions": [...], "operator": "...", "hazard_policy": "...", "external": "..." },
  "search": { "attempts": 30, "max_steps": 80, "seed": 0, "cascade": true },
  "board_size": "6x5",
  "move_delay": 0.04,
  "learning_enabled": false,
  "verify_after_gesture": true
}
```

`rule_profile` 與 `search` **就是 `planningPayload()` 和 `searchPayload()` 現在送出去的那兩個 dict，原封不動存下來**。不要另外定義設定模型——存線上格式，回存時就不可能跟 UI 漂移。

### 怎麼存：沿用既有的原子寫入

`OrbPrototypeModel._save()`（`pad_router_gui.py`）已經有正確的寫法：`tempfile.NamedTemporaryFile` 開在同目錄 → `flush()` → `Path.replace()`。照抄同一個模式，不要用別的寫法，也不要引入新套件。

### Bridge 命令

新增兩個 action：

- `load_settings` → 回傳存下來的 dict；檔案不存在、無法解析、或 `version` 不認得時回 `{}`。
- `save_settings` → 把 `payload["settings"]` 寫進檔案。

兩個都要加進 `command()` 開頭那個「執行中仍允許」的 action 集合——設定的讀寫不該被手勢執行擋住，也不會影響手勢。

### 失敗政策（和原型資料不同，不要抄錯）

原型資料寫入失敗會擋住 ADB 送路徑。**設定不是安全關鍵，寫入失敗不得擋住任何操作**：

- 寫入失敗 → 在事件主控台記一則 `warning`，其餘照常運作。
- 讀取失敗、檔案損毀、欄位缺漏 → 靜靜退回預設值，記一則 `info`，**絕不能讓 `--gui` 啟動失敗**。

### 前端行為

- 任何設定變動就存，在 `app.js` 用單一 `setTimeout` 做約 300ms 的 debounce，避免滾輪調步數時狂寫檔。
- 啟動時 `load_settings`，套用順序：**board_size → rule_profile → search → move_delay / learning_enabled / verify_after_gesture**。盤面大小要先套，因為它會改變「一橫列」的判定與其他與盤面相關的顯示。
- **回存一律走既有的 `command()` 呼叫**（`set_board_size`、`set_rule_profile`、`set_learning_enabled`、`set_verify_after_gesture` 等），不要直接寫進 controller 狀態。手改過或過期的設定檔因此不可能把 app 推進非法狀態。
- 沒有設定檔時就是現在的預設值，不要跳任何提示。

### 不在這次範圍

盤面校正不存。它綁裝置與解析度，生命週期和這些設定不同；要存是另一個題目。

### 驗收

- 改幾個設定 → 關掉 → 重開，全部設定與關掉前一致（含 6×5／7×6、條件、全域門檻、步數、嘗試、種子、連鎖、危害、外部條件、MOVE 間隔、AI 學習、轉珠後確認）。
- 把 `.pad-router/workspace-settings.json` 改成不合法的 JSON，`--gui` 仍要正常啟動並用預設值。
- 把 `.pad-router/` 設成唯讀，改設定不得跳錯或擋住擷取／搜尋／執行，只在主控台留一則 warning。
- 新增單元測試涵蓋：round-trip、損毀檔退回預設、寫入失敗不擋執行。
