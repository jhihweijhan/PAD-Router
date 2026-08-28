# PAD-Router 6×5 盤面 Combo 搜尋研究

擷取與程式檢視日期：2026-08-28
範圍：小型、可解釋、單機 CPU；不訓練模型，不引入大型模型。

## 結論先行

建議先在既有雙排序束搜尋加入「同色珠距離勢能」作為**同 Combo 數時的次級排序**，而不是換掉整個搜尋器。理由是專案已在 `score`／`solve` 實作相近的同色珠距離懲罰；把它抽成私有 helper，重用於 `search_qualifying_route`，可用很小的改動為尚未形成三連珠的中間狀態提供稠密訊號。

- 【已證實：程式】目前 `search_qualifying_route` 對一般 Combo 的束排序主要看「目前若放手可得到的 `combo_count`」；除 `full_row` 形狀外，沒有衡量未完成三連珠之接近程度的通用啟發式。
- 【已證實：程式】`score` 已把 Combo 數與消除後同色珠的曼哈頓距離懲罰結合；`solve` 已用它做 Combo 導向束搜尋，但 `search_qualifying_route` 沒有重用該訊號。
- 【推論】距離勢能可降低「必要的暫時零 Combo 狀態」過早被束剪枝的機率，因此在相同束寬下有機會提高最終 Combo；這不是逐盤保證，必須用固定盤面語料比較。

## 現況摘要與精確程式符號

### 消除與 Combo 計算

- `pad_router.py:_find_resolved_matches`（約 503–536）：在 5×6 盤面標出橫向或縱向連續至少 3 顆同 key 的珠，再把正交相連、同 key、且已標記的格子合成一個 `ResolvedMatch`。因此交叉或延長線仍算一個連通 match。
- `pad_router.py:_resolve_rounds`（約 539–556）：每輪找 match、清除、令現存珠向下掉；`cascade=True` 時重複直到無 match，`False` 時只算第一輪。空格補成 `0`，不生成新的隨機天降珠。
- `pad_router.py:evaluate_manual_route`（約 679–696）：驗證盤面、路徑非空、格子在 5×6 內、相鄰步驟曼哈頓距離為 1；以 `expected_board_after_path` 套用交換，再交給 `_evaluate_expected_route`。
- `pad_router.py:_evaluate_expected_route`（約 699–759）：用 `_resolve_rounds` 算各輪 match，`combo_count` 是所有輪次 `ResolvedMatch` 數量總和；再評估 `RuleProfile` 條件、危險珠政策、確認與可執行狀態。

【已證實：外部來源】GungHo 的官方 App Store 列表說明同色珠橫向或縱向 3 顆以上會消除，移珠可串起多個 Combo；官方《Puzzle & Dragons GOLD》頁也說同色珠 3 顆以上成列。這支持本專案「橫／縱三連」的核心規則。
【推論】官方摘要沒有完整定義本專案的連通元件合併、危險珠、特殊形狀與無新珠 cascade 細節；這些仍以本專案程式與測試為準，不能由外部摘要反推。

### 搜尋介面與流程

#### `RouteSearchOptions`

目前欄位：

- `attempts=100`：隨機路徑嘗試數；同時被用來推導束寬。
- `seed=0`：控制隨機起點、步數與鄰居，確保可重現。
- `min_steps=1`、`max_steps=50`：候選路徑步數界限。
- `cascade=True`：候選評估是否計入 cascade。

限制：沒有獨立的 `beam_width`、最大狀態評估數、CPU／時間預算、局部改善預算或診斷統計。`attempts` 同時代表隨機樣本數與 `max(30, attempts * 24)` 束寬，兩種成本無法分別調整。

#### `RouteSearchResult`

目前欄位：

- `qualifying_candidate`：最佳合格候選。
- `diagnostic_candidate`：沒有合格結果時的最佳診斷候選。
- `attempts`、`seed`：回報隨機搜尋參數。
- `candidate`、`route_evaluation`、`qualifying`、`diagnostic`：便利 property。

限制：沒有 `expanded_states`、`unique_states`、`evaluated_states`、束寬、停止原因、各階段最佳 Combo 或最優性缺口。`attempts` 也沒有反映束搜尋實際評估的大量子節點，使用者無法由結果判斷 CPU 工作量與搜尋收斂程度。

#### `search_qualifying_route`

流程：

1. 以 `random.Random(seed)` 產生 `attempts` 條隨機路徑；禁止立即走回前一格。
2. 若 `profile.condition_groups` 非空，再從 30 個起點執行逐深度束搜尋。
3. 每個子節點先建立 `next_board`，立即完整呼叫 `_evaluate_expected_route`。
4. 以 `(next_board, next_cursor, previous_cursor)` 去重；保留條件排序較佳者。
5. 束的一半按條件優先，另一半按 Combo 優先，合併至 `beam_width=max(30, attempts*24)`。
6. 最終合格候選按「Combo 多、步數少、路徑字典序小」選出。

現況優點：固定 seed 可重現；條件優先與 Combo 優先並存；狀態 key 保留「禁止立即回頭」所需的前一游標；已有完整合格條件／危險珠評估。

主要限制：

- 一般 Combo 的中間獎勵稀疏。尚未成三連珠時，多個狀態常同為 0 Combo；只有 `full_row` 有 `_shape_search_progress` 與 `_full_row_target_distance` 特化訊號。
- `score`／`solve` 的通用同色珠距離啟發式沒有進入合格路徑搜尋。
- 同一層可能由多個父節點產生相同 state key，但程式在 dict 去重**之前**就做完整 cascade 與條件評估，浪費 CPU。
- 固定束寬會剪掉其餘狀態；束搜尋本質上不完備，當前 heuristic 也沒有可採納下界，故沒有最佳 Combo 保證。
- `profile.condition_groups` 為空時不跑束搜尋，只保留隨機嘗試；此時「純求高 Combo」與 `solve` 是兩條不同路徑。
- 每步都建立完整 immutable board 與延長完整 `path` tuple；盤面僅 30 格，尚可接受，但深度 50–100、束寬數千時配置與比較成本會累積。

### 現有測試保障

`test_pad_router_planning.py` 已覆蓋：

- 固定 seed 的可重現搜尋與合格候選。
- 合格候選以 Combo、步數、路徑字典序排序。
- `full_row` 條件優先、散落暗珠收集、成列後仍保留高 Combo 候選。
- cascade 是否納入條件。
- 危險珠預設排除、明確需求例外、最佳非合格診斷。

缺口：沒有固定盤面語料比較「相同 CPU／狀態評估預算下的實際 Combo 分布」，也沒有驗證去重前後的評估次數。

## 候選改善排序

排序原則依序為：預期 Combo 收益、CPU 成本、實作風險。收益均為待量測推論，不是保證。

| 排名 | 候選 | 預期收益 | CPU 成本 | 實作風險 | 判斷 |
|---:|---|---|---|---|---|
| 1 | 束排序加入同色珠距離勢能 | 高 | 低 | 低至中 | 最小且直接改善稀疏獎勵 |
| 2 | 先去重再評估 state | 中（間接） | 降低 | 低 | 省下成本後可增加束寬／深度 |
| 3 | 束內多樣性配額／分桶 | 中 | 近似不變 | 中 | 防止整束集中在相似局部區域 |
| 4 | 對前 K 個合格路徑做局部改善 | 中 | 中、可固定預算 | 中 | 直接爬升完成解，容易解釋 |
| 5 | 單人 MCTS／偏置 rollout | 不確定 | 高 | 高 | 文獻支持單人 puzzle，但轉移到 PAD 未證實 |

### 1. 同色珠距離勢能作為 tie-break

做法：把 `score` 中的同色珠曼哈頓距離懲罰抽成私有 helper；對未消除的 `next_board` 計算勢能。在 `combo_rank` 中置於 `-result.combo_count` 之後，在 `condition_rank` 中置於既有條件、形狀與 Combo 欄位之後。這樣不改變「合格條件第一」與「已實現 Combo 第一」的主要語意，只在目前資訊相同時偏好同色珠較接近的節點。

- 【已證實：程式】`score` 已有相近計算，`solve` 已用它排序；不需新依賴或模型。
- 【推論】此訊號對 30 格盤面為小型可解釋運算，可讓 0 Combo 的中間狀態有區別。
- 風險：最近鄰距離不是可採納下界；某些高 Combo 解需要先把珠拉遠，勢能可能誤導。故只能當次級排序，不應取代條件與實際 Combo。

### 2. 先去重，再呼叫 `_evaluate_expected_route`

做法：每層先生成 `(next_board, next_cursor, previous_cursor) -> 最小字典序 next_node`，再逐一評估 unique state。對相同 board／cursor／previous，條件與 Combo 結果相同；路徑只影響最後 tie-break，因此可先保留較小路徑。

- 【已證實：程式】現行流程在 `candidates.get(key)` 前已完成 `_evaluate_expected_route`。
- 【推論】減少重複 cascade 評估可把相同 CPU 預算投入更大束寬或更深搜尋，間接提高找到高 Combo 的機率。
- 風險：必須保留 `previous_cursor`，否則「禁止立即回頭」的可走邊集合不同，錯誤合併會改變搜尋圖。

### 3. 束內多樣性配額

做法：在現有 condition-first／combo-first 各半之外，再限制同一 `cursor`、起點或粗略盤面特徵占用的最大比例；剩餘名額按原排序補滿。不要新增隨機性。

- 【已證實：論文摘要】束搜尋逐層只保留固定數量最有希望的節點，屬不完備 heuristic search；排序函式品質直接影響被保留的節點。參見 Bounded-Suboptimal Beam Search 與一般組合最佳化束搜尋來源。
- 【推論】多樣性配額可降低早期 heuristic 誤判造成的整束塌縮，但配額選錯也會排擠真正優秀的狀態。

### 4. 完成路徑的局部改善

做法：對前 K 個合格候選採固定、可重現的鄰域，例如替換長度 2–4 的連續移動片段、刪除回到同一游標的閉合片段，或在尾端追加不立即回頭的 1–3 步；只接受仍合格且排名更佳的結果，評估次數設硬上限。

- 【已證實：來源】Firecrawl 找到 Springer 的 *Local search in combinatorial optimization*；局部搜尋以候選解的鄰域反覆改善，是組合最佳化的既有方法。
- 【推論】PAD 路徑具有短、離散、可精確重評的特性，適合小鄰域；但「哪個路徑編輯鄰域最有效」沒有 PAD 專屬證據。

### 5. 單人 MCTS／偏置 rollout

做法：把游標＋盤面視為 deterministic state；選擇階段用 UCB，rollout 用「不立即回頭＋距離勢能」偏置，terminal reward 採「先合格、再 Combo、再短路徑」。設定固定模擬次數與 seed。

- 【已證實：論文】Schadd 等人的 *Single-Player Monte-Carlo Tree Search* 把 MCTS 改為單人 puzzle，並在 SameGame 研究其可行性；論文明確針對缺少準確 admissible heuristic 的單人遊戲。
- 【推論】這只證明方法類型可用於另一種單人 puzzle，不能證明 PAD 6×5 會優於目前束搜尋。MCTS 評估次數高、參數更多、解釋成本較大，因此不應作第一步。

## 建議的最小第一步

### 程式修改範圍

只改 `pad_router.py` 與 `test_pad_router_planning.py`；不改 GUI，不加依賴。

1. `pad_router.py`
   - 從 `score` 抽出私有 `_combo_distance_penalty(board) -> int`，內容沿用目前按 orb key 分組與最近同色珠曼哈頓距離總和。
   - `score` 改呼叫 `_combo_distance_penalty(remaining)`，保留既有輸出語意。
   - `search_qualifying_route` 在建立 `next_board` 後計算 `combo_distance = _combo_distance_penalty(next_board)`。
   - `combo_rank`：保留 `-result.combo_count` 第一；緊接加入 `combo_distance`，再沿用合格／形狀／條件／路徑欄位。
   - `condition_rank`：保留既有條件與形狀欄位；在 `-result.combo_count` 後、`next_node.path` 前加入 `combo_distance`。
   - 第一個變更**不新增** `RouteSearchOptions` 或 `RouteSearchResult` 欄位，維持外部 interface 與固定 seed 結果型態。

2. `test_pad_router_planning.py`
   - 新增 `test_search_combo_potential_improves_fixed_sparse_reward_board`：使用 literal 5×6 盤面、固定 `RuleProfile`、固定 `RouteSearchOptions`；fixture 必須先由 before／after 小盤面語料找出「舊排序與新排序確實不同」的案例，再凍結盤面。斷言：候選合格、新版 `combo_count` 高於凍結的舊版基準、步數不超過 `max_steps`。
   - 同一測試重跑兩次並斷言結果相等，守住 seed 可重現契約。
   - 保留並執行既有 `test_search_ranks_qualifying_candidates_by_combos_steps_then_route_order`，確認 tie-break 新欄位沒有改變「實際 Combo、步數、路徑」的最終結果排序。
   - 不直接測 `_combo_distance_penalty`；測試應跨 `search_qualifying_route` 的既有 interface 驗證可觀察結果。

### 驗證門檻

在實作前建立一個小而固定的盤面語料（建議至少涵蓋：純 Combo、`full_row`＋Combo、hazard avoid、cascade on/off），以相同 seed、步數與 state 評估預算比較：

- 合格率不得下降。
- 每盤最佳 Combo 取中位數與最差差值；至少存在固定回歸盤面有可重現提升，且既有盤面不得出現未解釋退步。
- CPU 應量測完整 `search_qualifying_route`；不要只量 helper。

【推論】若沒有固定盤面能顯示提升，應刪除該 heuristic，而不是繼續加權參數。下一步才考慮「先去重再評估」。

## 明確不可保證事項

1. 無法保證找到全域最大 Combo：隨機搜尋與固定寬束搜尋都會剪枝，沒有 admissible bound 或完整列舉。
2. 無法保證距離勢能逐盤改善；它可能偏好短期聚集而錯過需要繞路的高 Combo 解。
3. 程式的 `cascade=True` 只做既有珠下落，清除後補 `0`，沒有模擬遊戲隨機天降新珠；因此 `combo_count` 不是實機所有可能天降 Combo 的期望值。
4. 搜尋只最佳化偵測到的盤面與本專案規則。辨識錯誤、觸控偏移、遊戲版本、技能、盤面鎖定或特殊珠規則都可能令實機結果不同。
5. 外部束搜尋、局部搜尋、SP-MCTS 文獻是一般方法或其他 puzzle 的證據；套用到 PAD 6×5 的收益皆屬待驗證推論。
6. 官方摘要只支持同色三連與連鎖 Combo 的核心概念；本專案的 connected-component 合併、hazard policy、特殊形狀與 cascade 細節由本地程式定義。
7. 沒有大型訓練模型不等於零調參；束寬、局部鄰域與 rollout 次數仍需固定語料與 CPU 預算驗證。

## Firecrawl 查詢紀錄與來源

所有查詢使用 OMP Firecrawl `firecrawl_search`；擷取日期均為 2026-08-28。Firecrawl 僅用於取得摘要、來源頁與論文 metadata；未擷取或重製受版權保護攻略全文。

### A. PAD 消除／Combo 規則

查詢字詞：

- `Puzzle & Dragons official how to play match 3 or more orbs combo GungHo`（成功）
- `Puzzle & Dragons App Store official match 3 or more orbs same color GungHo`（成功）
- `site:puzzleanddragons.us "3 or more Orbs" combo`（明示空結果；後改用一般 query）

來源：

- GungHo 官方 App Store 列表，*PUZZLE & DRAGONS STORY*：https://apps.apple.com/us/app/puzzle-dragons-story/id1636844853
- GungHo 官方 App Store 列表，*Puzzle & Dragons (English)*：https://apps.apple.com/us/app/puzzle-dragons-english/id563474464
- GungHo 官方 *Puzzle & Dragons GOLD* 頁：https://pad-g.gungho.jp/en/
- 北美官方 Tutorial overview：https://www.puzzleanddragons.us/single-post/tutorial-overview

可支持的結論：同色珠橫／縱 3 顆以上消除；移珠可串出多個 Combo。不能由這些摘要推導所有特殊規則。

### B. 束搜尋與組合最佳化

查詢字詞：

- `beam search implementation bounded width priority queue combinatorial`（成功）
- `beam search combinatorial optimization heuristic bounded width primary paper PDF`（一次成功回傳論文結果；後續同字詞重試明示空結果）
- `site:arxiv.org beam search combinatorial optimization`（明示空結果）

來源：

- David W. Thomas、Jordan Wissow，*Bounded-Suboptimal Beam Search*：https://dwthomas.github.io/publication/Thomas-Wissow-Bounded-beam.pdf
- *Parallel Beam Search for Combinatorial Optimization*（ACM）：https://dl.acm.org/doi/fullHtml/10.1145/3547276.3548633
- *A Policy-Based Learning Beam Search for Combinatorial Optimization* 摘要頁：https://link.springer.com/chapter/10.1007/978-3-031-30035-6_9

可支持的結論：束搜尋逐層保留有限數量節點，以有限時間／記憶體換取近似解；標準束搜尋不保證最優，排序 heuristic 是關鍵。本研究不採用來源中的機器學習部分。

### C. 6×5 小盤面的模擬／局部改善

查詢字詞：

- `Single-Player Monte-Carlo Tree Search SameGame Schadd Winands van den Herik paper`（成功）
- `Monte Carlo tree search single player puzzle combinatorial optimization primary paper`（成功）
- `iterated local search combinatorial optimization primary source PDF`（成功，回傳 Springer local-search 來源）
- `Iterated Local Search Lourenco Martin Stutzle combinatorial optimization PDF`（明示空結果；未據此建立結論）

來源：

- M. P. D. Schadd 等，*Single-Player Monte-Carlo Tree Search*（Maastricht University PDF）：https://dke.maastrichtuniversity.nl/m.winands/documents/CGSameGame.pdf
- 同論文 Springer 摘要頁：https://link.springer.com/chapter/10.1007/978-3-540-87608-3_1
- Y. Crama、A. W. J. Kolen、E. J. Pesch，*Local search in combinatorial optimization*，DOI 10.1007/BFb0027029：https://link.springer.com/content/pdf/10.1007/BFb0027029.pdf

可支持的結論：局部搜尋以鄰域改善組合解；SP-MCTS 是單人 deterministic puzzle 的既有研究方向。把兩者移植到 PAD、以及預期收益高低，均屬本研究推論。

### Firecrawl 擷取限制

- 官方頁搜尋摘要可直接驗證核心三連規則；Apple 頁的結構化 JSON 擷取為 `null`，但回傳 HTTP metadata 與官方開發者身分。
- 兩份 PDF 以 `maxAge: 0` 擷取時回傳 HTTP 200 與頁數 metadata，但結構化 JSON extraction 失敗；本報告只採 Firecrawl 搜尋結果中的摘要級結論與論文摘要頁，不宣稱讀取了未回傳的全文內容。
- 空結果已如實保留，沒有用空結果支撐結論。
