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
- 僅允許 read-only 工具白名單（`memory_search`, `read_file`, `get_channel_history`, `schedule_action(list)`）
- 禁止 `send_message` / `memory_edit` / 任何寫入或對外行動工具
- 進入 Stage 1 前，runtime 會先清掉 user message 中偏行動導向的 reminders（如 `send_message` 頻道提醒、`Decision Reminder`、`memory_edit` 導向片段），避免 gather 階段被 action prompt 汙染
- **Runtime Gate**：若 `memory_search` 可用且對話中無先前 Stage 1 Findings，第一個工具呼叫必須是 `memory_search`，且 query 不可為空
- 若對話中已有先前 findings（`_stage1_gather` tool result），gate 跳過，LLM 可判斷是否需要重新搜尋
- 最大迭代數由 `gather_max_iterations` 控制

此階段結果：
- **寫入 `Conversation`**（synthetic `_stage1_gather` tool pair），供後續 turn 複用
- 以 overlay 注入 Stage 3（因當前 turn 的 messages snapshot 早於 conversation.add）

### Stage 2: plan（規劃）

- 使用 `chat(...)`（不帶 tools）
- 可使用 `reasoning_effort`
- 進入 Stage 2 前，runtime 會額外注入完整 `long-term.md` 作為規劃錨點（system message）
- 若 `long-term.md` 讀取失敗：顯示 warning，並以 fail-open 繼續 Stage 2
- 讀取 Stage 1 收集結果，輸出純文字規劃（不做 schema 驗證）
- 規劃內容要求包含：`CURRENT_STATE`、`DECISION`、`ACTION_PLAN`、`FILE_UPDATE_PLAN`、`SCHEDULE_PLAN`、`EXECUTION_RULES`

此階段計畫：
- 會顯示在 TUI（供觀察與除錯）
- **不寫入 `Conversation`**
- **不寫入 session `messages.jsonl`**

### Stage 3: execute（執行）

- 使用既有 brain responder tool loop（`chat_with_tools(...)`）
- 以 overlay 注入 Stage 1 findings + Stage 2 plan

## 上下文邊界

### 會進主對話 history

- 使用者輸入
- Stage 1 findings（synthetic `_stage1_gather` tool pair）
- responder loop 的 assistant/tool 訊息（Stage 3）
- 最終 assistant 文字（若有）

### 不會進主對話 history

- Stage 2 規劃內容（plain-text plan）
- TUI 顯示用的 stage 記錄

## 失敗策略

任一階段失敗（特別是 Stage 2 回空內容）：
- 顯示 warning
- 退回舊的單段 brain responder tool loop（fail-open）
