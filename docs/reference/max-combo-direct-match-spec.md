# 最大 Combo 直接消除計算規格

## 目的

搜尋「不限（以最大 Combo 為主）」時，候選盤面必須先保留使用者指定的消珠條件，再以未被該些消除占用的珠子估算可直接成立的最大 Combo。估算不可使用消除後的落珠或天降連消；候選也必須在完成盤面當下直接滿足指定消法，不能只靠 cascade 才滿足。

## 定義

- **直接消除**：完成拖珠後、尚未移除任何珠子的盤面上，已形成的橫向或縱向三連（及其同色連通區）。
- **特定消珠**：為滿足已選 `LeaderCondition` 而保留的直接消除，例如指定色的十字、直排、橫排、全列、3×3、L、T 或「4 顆消除」。
- **可分配珠**：未屬於特定消珠、且現有危害策略允許消除的珠子。
- **直接 Combo 數**（`direct_combo_count`）：完成盤面首輪 `MatchRound` 的 Match 數。
- **直接最大 Combo 預估**（`direct_combo_estimate`）：本規格定義的直接消除目標；若指定消法未在首輪成立，值為 `None`。

## 計算規則

1. 只以完成盤面的首輪 `MatchRound` 計算 `direct_combo_count` 與 `direct_combo_estimate`；不使用落珠／cascade 結果。既有 `RouteEvaluation.combo_count`、`rounds`、手動評估與一般 `RuleProfile` 判定維持原本的 cascade 語意。
2. 對每個啟用的條件群組，以首輪 Match 評估其條件。`all` 必須全部直接成立；`any` 至少一個直接成立。外部條件與危害策略不保留格子，仍沿用既有可執行資格檢查。只有在**不存在**可讓每個 `all`／`any` 群組直接成立的首輪證據集合時，`direct_combo_estimate` 才是 `None`；`any` 群組中未被選擇、只會 cascade 成立的分支不會使值變成 `None`。
3. 由首輪 Match 選出能使全部直接條件成立的保留集合。`combo_minimum`、`attribute`、`simultaneous_attributes`、`match_count`、`connected_orb_count`、`enhanced_orb`、`shape` 與 `required_orbs` 都以其達標所需的首輪 Match 作為證據；`forbidden_orbs` 只驗證首輪不存在被禁止珠，不保留格子。`all` 群組合併證據，`any` 群組選一條成立分支；同一 Match 可同時證明多個正向條件。
4. 枚舉有限的首輪 Match 子集，選取使「保留 Match 數 + 餘珠 `// 3` 總和」最大的集合；同分時按保留 Match 的 `cells` 字典序選擇。保留一個 Match 必須扣除其完整 `cells`，包括連到指定形狀的額外同色格；同一格不會重複扣除。
5. 以 Match key 分組所有可分配珠。六種普通色（火、水、木、光、暗、心）一律計入；危害珠在 `hazard_policy == "allow"` 時一律計入，在「避免危害珠」時只有被既有規則認定為 required 的危害珠計入。`enhanced`／`locked` 不改變珠種；`unknown` 與空格／`0` 排除。每種的額外 Combo 為 `count // 3`。
6. `direct_combo_estimate` = 保留集合的 Match 數 + 所有珠種的額外 Combo 數。它是最大可排盤上限；`direct_combo_count` 是候選已實現的直接 Combo。

## 搜尋與顯示契約

- `RouteEvaluation` 新增 `direct_combo_count: int` 與 `direct_combo_estimate: int | None`，但不改寫既有 `combo_count` 的含意。
- 束搜尋必須保留 `direct_combo_estimate is None` 的中間節點，並繼續以既有條件／shape-progress 啟發式排序，讓多步路徑有機會形成直接指定消法；沒有直接解時也保留既有 deterministic 診斷候選。只有完成候選的最大 Combo 結果選擇才排除 `None`。
- 有數值的完成候選，依 `direct_combo_count` 降冪、`direct_combo_estimate` 降冪、步數升冪、路徑字典序升冪排序。`combo_count` 不得參與任何最大 Combo 排名或 tie-break，只保留既有一般評估與顯示用途；因此開關 cascade 不會改變最大 Combo 排名。
- 沒有特定消法時，保留集合為空，`direct_combo_estimate` 是固定盤面庫存上限；此時必須以 `direct_combo_count` 分辨排盤。無條件（沒有啟用條件群組）的 Profile 走專用的最大 Combo 束搜尋：節點只計算首輪 Match 數與「剩餘珠三連距離」啟發值，因此束寬可放大到 `max(30, attempts * 6)`，避免排盤結束後仍留下湊得出三連的同色珠；不能只靠隨機路徑嘗試。剩餘珠啟發值以蛇行順序把每種珠切成整組三顆並計算跨度，不足三顆的餘數不計分，避免搬動永遠湊不成 Combo 的零星珠子被誤判為進展。避免危害珠時，會直接排除首輪形成危害珠 Match 的候選。
- 有啟用條件群組時分兩段搜尋：前 `CONDITION_SEARCH_STEPS`（40）步用既有的完整節點評估把指定消法排出來，接著以同一條路徑接續跑最大 Combo 束搜尋，並把已成立的首輪 Match 格子與其 Match key 當成必須保留的條件，只有仍保留全部這些格子的候選才會被記錄。因此選了特定消法也會繼續累積 Combo，而不是排完形狀就停。
- `max_combo_layout` 列舉 5x6 用直／橫 3 格方塊完整鋪滿的全部 22 種版型，對每種版型指派珠種（相鄰方塊不得同色，每種最多 `count // 3` 塊），保留最好的幾個計畫；同分時取與當下盤面吻合格數最多者，讓顯示的目標版型看起來像從這個盤面排出來的。接著**必須把沒有被方塊認領的餘珠也填進盤面**（填法避開同色鄰居），再用 `_find_resolved_matches` 實際數該盤面的 Match 數。因此顯示的數字是「這個具體排法真的成立幾組」，不是庫存上限：偏色盤面（例如木 25／暗 5）的餘珠會把同色方塊接成一塊，數字會遠低於 `direct_combo_estimate`。它是可達下界，不保證是全域最佳排法。
- GUI 不新增控制項；候選結果文字同時顯示既有 Combo、`direct_combo_count` 與 `direct_combo_estimate`，使使用者可分辨真實 cascade 結果和最大 Combo 直接目標。

## 例子

盤面已保留一組火十字（5 顆，算 1 Combo），其餘可分配珠為火 4、木 8、水 2。

- 扣除火十字後：火 `4 // 3 = 1`、木 `8 // 3 = 2`、水 `2 // 3 = 0`。
- 最大 Combo 預估為特定消珠 `1` + 額外 `3` = **4**。

即使消除火十字後落下的珠子可以再湊成三連，也不得增加此數字。

## 驗收條件

1. 對相同的合格候選，開關 cascade 不改變 `direct_combo_count`、`direct_combo_estimate` 或最大 Combo 排名；兩個直接指標相同但 `combo_count` 不同的候選仍依步數與路徑決定順序。
2. 含指定形狀的盤面會先扣除該 Match 的全部格子，再對每個可分配珠種套用 `// 3`；形狀的額外連通格也必須扣除。
3. 同一直接 Match 同時符合多個正向條件時，只扣一次，並只貢獻一個保留 Combo；`all`／`any`、多個 Match、`match_count`、屬性組合、強化珠與禁止珠皆有 deterministic 測試。
4. 已有可在落珠後形成的連消、但每一條 `all`／`any` 證據指派都無法在直接盤面成立時，`direct_combo_estimate` 必為 `None`，且該完成候選不得被選為最大 Combo 結果；它仍可保留在束搜尋與診斷結果中。
5. 不含特定消珠條件時，預估是所有普通珠依珠種 `// 3` 的總和，但無條件搜尋仍依 `direct_combo_count` 選能立即消除最多組的排盤。
6. 危害珠在策略允許時依危害種類納入餘珠；「避免危害珠」會排除未被條件要求的危害珠。心珠、強化珠與鎖定珠依其 Match key 納入；`unknown` 與空格排除。
7. 多步 shape 搜尋可從 `None` 的中間盤面走到有值的直接解；沒有直接解時仍回傳既有最佳診斷候選。RouteEvaluation 的既有 Combo／cascade、一般手動評估、危害處理、JSON Profile 相容性與路徑執行安全條件維持不變；GUI 結果與規則／架構／使用指南文件同步使用「直接最大 Combo 預估」一詞。

## 實作邊界

- 修改集中於 `pad_router.py` 的候選排名／最大 Combo 估算、`pad_router_gui.py` 的結果文字，以及對應 planning／GUI／使用者文件測試。
- 不新增套件、不改 Profile JSON schema、不新增 GUI 控制項；GUI 仍透過既有 `RuleProfile` 呼叫核心。
