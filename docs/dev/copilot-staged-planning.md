# Brain 三階段（gather / plan / execute）流程

本文件說明 brain agent 的三階段流程與上下文邊界。

相關文件：
- `docs/dev/provider-api-spec.md`（provider adapter 規則與 reasoning/tool 相容性）
- `docs/dev/provider-architecture.md`（provider vs orchestration 邊界）

## 設定

使用 `agent.yaml`：

```yaml
agents:
  brain:
    staged_planning:
      enabled: true
      gather_max_iterations: 4   # Stage 1 最大迭代數
      plan_context_files:        # 注入 Stage 2 + Stage 3 的參考檔案
        - "memory/agent/long-term.md"
```

`plan_context_files` 中的檔案會以 system message 注入 Stage 2（規劃）和 Stage 3（執行）overlay，確保 plan 和 execution 都能直接參考這些規則。建議將重要的持續性規則（如 `long-term.md`）放在這裡而非 `boot_files`，以獲得更高的注意力權重。

規則：
- **僅 brain agent 生效**
- 任何 provider 均可使用
- `enabled: false` 時退回單段 responder loop

## 三階段流程

### Stage 1: gather（資訊收集）

- 使用 `chat_with_tools(...)`
- 為維持 prompt cache parity，模型會看到 full tool schema
- 僅允許 read-only 工具白名單（`memory_search`, `web_search`, `web_fetch`, `read_file`, `get_channel_history`, `read_image`, `read_image_by_subagent`, `schedule_action(list)`）
- 禁止 `send_message` / `memory_edit` / 任何寫入或對外行動工具
- Stage 1 prompt 會明確告知自己只是三階段中的 gather 階段：只蒐證，不起草對外訊息；若已知道後續應做什麼，應把該意圖寫成 findings 交給 Stage 2/3，而不是直接呼叫執行工具
- 若回覆依賴可驗證的外部事實（例如菜單品項、營業時間、價格、天氣、時刻表），Stage 1 應優先蒐證（通常用 `web_search`；已知 URL 時可直接 `web_fetch`），不可把未驗證的舊印象直接寫進 findings
- 若當輪最新用戶訊息是在糾正先前的具體主張，Stage 1 應把它視為 red flag：要嘛查證，要嘛放棄舊主張；不可把已被當輪質疑的事實原樣帶進後續規劃
- 本地圖片分析視為 read-only gathering，可在 Stage 1 先讀附件或快取圖，再決定後續回覆
- 進入 Stage 1 前，runtime 會先清掉 user message 中偏行動導向的 reminders（如 `send_message` 頻道提醒、`Decision Reminder`、`memory_edit` 導向片段），避免 gather 階段被 action prompt 汙染
- **Runtime Gate**：若 `memory_search` 可用且對話中無先前 Stage 1 Findings，第一個工具呼叫必須是 `memory_search`，且 query 不可為空
- 若對話中已有先前 findings（`_stage1_gather` tool result），gate 跳過，LLM 可判斷是否需要重新搜尋
- 同一次 Stage 1 gather 內，若第二次 `memory_search` 回傳與先前完全相同的結果，runtime 會直接回錯，提示 refine query 或停止搜尋，避免重複消耗
- 若 Stage 1 誤呼叫 forbidden action（如 `send_message`、`memory_edit`、`schedule_action(add/remove)`），runtime 會回傳明確錯誤，要求把該動作轉寫成 findings；並在 transcript 記一條 `stage1-intent` 後直接結束 gather，避免浪費後續迭代
- 最大迭代數由 `gather_max_iterations` 控制

此階段結果：
- **寫入 `Conversation`**（synthetic `_stage1_gather` tool pair），供後續 turn 複用
- 以 overlay 注入 Stage 3（因當前 turn 的 messages snapshot 早於 conversation.add）

### Stage 2: plan（規劃）

- 使用 `chat_with_tools(...)`
- 為維持 prompt cache parity，模型會看到 full tool schema
- 僅允許 `read_file` / `web_search` / `web_fetch` 做少量補查；其餘工具一律 runtime 拒絕
- 可使用 `reasoning_effort`
- 進入 Stage 2 前，runtime 會額外注入完整 `long-term.md` 作為規劃錨點（system message）
- 若 `long-term.md` 讀取失敗：顯示 warning，並以 fail-open 繼續 Stage 2
- 讀取 Stage 1 收集結果，必要時用 read-only 工具補洞，最後輸出純文字規劃（不做 schema 驗證）
- Stage 2 prompt 會重申 memory routing guardrails：`memory/archive/` 不可作為 live write target；持續生效的禁令/約定/規則寫入 `long-term.md`；僅當前脈絡寫入 `temp-memory.md`；可重用方法寫入 `skills/`；身份邊界改動寫入 `persona.md`
- Stage 2 prompt 也會要求先做 timeline normalization：若當輪對話、較早摘要、與舊記憶之間出現日期/星期/時間矛盾，先整理成單一時間線；當輪最新明確更正優先於較早說法與舊記憶；被更正推翻的事實不可再帶入 plan
- Stage 2 prompt 也會要求檢查近期對話的邏輯關係：哪些提醒/建議/事實剛說過、哪些已被更正、哪些已失效；不可把同一個提醒或主張換句話在同一輪或短時間內重複送出
- Stage 2 prompt 也會要求分開「已知事實」與「推論」：不可把不同時間點的人事物硬接成同一事件。例：只知道某人晚點會來接，不代表現在可以說成要和那個人一起吃飯
- Stage 2 prompt 也會要求檢查訊息切分：若多句其實都在服務同一個即時 ask / reminder / action，應合併成同一則 `send_message`；只有重點真的不同時才拆多則
- 上述「鼓勵同一輪先規劃好多個 `send_message`」提示受 `features.send_message_batch_guidance.enabled` 控制；同一個 flag 也同步影響 brain system prompt、tool description 與 per-turn reminder，不做 inbound 來源分流；預設為 `false`
- brain system prompt 的這類可選文字不再硬編碼在 Python，而是由 kernel fragment 檔 + resolver 載入，方便 migration、diff 與日後擴充更多可選區塊
- Stage 2 prompt 也會要求重算時間語意：以最新 user timestamp 當作現在；若同時提到「再過 X 分鐘」與某個時鐘時間，兩者必須一致。閒聊中不可把內部精確時間計算直接端給使用者，除非對方明確要求或必須釐清衝突
- 若某個回覆依賴外部現實事實，Stage 2 必須要求「已有 Stage 1 證據」或在 `ACTION_PLAN` 中明列先驗證（例如 `web_search` / `web_fetch`）後再決定，不可直接把未驗證內容當既定事實
- 規劃內容要求包含：`CURRENT_STATE`、`DECISION`、`ACTION_PLAN`、`FILE_UPDATE_PLAN`、`SCHEDULE_PLAN`、`EXECUTION_RULES`

此階段計畫：
- 會顯示在 TUI（供觀察與除錯）
- **不寫入 `Conversation`**
- **不寫入 session `messages.jsonl`**

### Stage 3: execute（執行）

- 使用既有 brain responder tool loop（`chat_with_tools(...)`）
- 以 overlay 注入 Stage 1 findings + Stage 2 plan
- Stage 3 應沿用 Stage 2 已整理好的時間線，不得在執行時把已被較新更正推翻的日期/星期/行程事實撿回來
- Stage 3 也要遵守 plan 中的邏輯去重與查證要求：沒有新理由時不重複同一提醒/主張；外部事實未驗證時先查或明確保留不確定性
- Stage 3 不可把內部時間算式外洩成對外話術；像「再過五分鐘（20:50）」這種 relative/absolute 不一致的內容，應在執行前被視為 plan 違規並改寫
- Stage 3 不自己處理 skill prerequisite；真正的 prerequisite enforcement 在共用 responder loop
- 因此即使 `staged_planning=false`，受 `meta.yaml` 治理的工具仍會在執行前先載入對應 guide
- 若 Stage 3 首次請求受管工具但本輪尚未載入 guide，runtime 會先注入 synthetic skill guide，再讓 responder loop 自然重跑一次

## 上下文邊界

### 會進主對話 history

- 使用者輸入
- Stage 1 findings（synthetic `_stage1_gather` tool pair）
- responder loop 的 assistant/tool 訊息（Stage 3）
- 最終 assistant 文字（若有）

### 不會進主對話 history

- Stage 2 規劃內容（plain-text plan）
- TUI 顯示用的 stage 記錄

### 會以 synthetic tool pair 進入主對話 history

- Stage 1 findings（`_stage1_gather`）
- Skill prerequisite guide 載入（例如 Discord `send_message` 前補入 `guide.md`）

### 會以 latest-turn note 注入 prompt，但不寫回主對話 history

- Message-time common ground

### Prompt Cache 規則

- Stage 1 / Stage 2 / Stage 3 都必須從同一份 latest-turn cache breakpoint 組裝 prompt
- 不允許只有 Stage 3 tool loop 才 advance BP3，讓 Stage 1 / Stage 2 落回 raw builder breakpoint

## 失敗策略

任一階段失敗（特別是 Stage 2 回空內容）：
- 顯示 warning
- 退回舊的單段 brain responder tool loop（fail-open）
