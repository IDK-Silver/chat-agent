# Trigger Review 系統

Post-review 架構，確保 Responder（如 Gemini Flash）遵守 system prompt 中的 trigger rules。
Brain agent 按需透過 `memory_search` tool 搜尋記憶（取代原本的 pre-fetch pass）。

## Memory Edit v2（Instruction Pipeline）

`memory_edit` 已改為 v2 嚴格契約（不做舊欄位相容）：

- Brain 輸入：`as_of`、`turn_id`、`requests[]`
- 每筆 request：`request_id`、`target_path`、`instruction`
- `memory_editor` 子代理讀取目標檔案全文後，輸出內部 operations
- `apply.py` deterministic 執行並驗證；任一 operation 失敗會回滾該 request
- request 執行模型：不同 `target_path` 可平行處理；同一檔案內 requests 仍維持序列

## 架構

```
User Message
    │
    ▼
═══ Responder ═══
Gemini Flash（帶完整工具，含 memory_search）→ 生成回應
    │  ├── memory_search(query) → sub-LLM 讀 index → 回傳路徑 + 相關性
    │  └── read_file(path) → 讀取記憶內容
    │
    ▼
═══ Post-review Pass ═══
PostReviewer ← ReviewPacket（固定證據封包）
    │       → JSON { passed, violations, required_actions, retry_instruction, label_signals }
    ▼
passed? → 輸出給用戶
not passed + required_actions? → actions 注入為 system reminders → Flash 重試（最多 N 次）
           → 程式硬驗證本輪 tool calls 是否完成 required_actions
not passed + violations only? → violations 注入為 retry prompt → Flash 重新生成回應
```

## Memory Search Tool（v0.6.0）

取代原本的 Pre-fetch Pass。Brain agent 需要記憶時自行呼叫 `memory_search`。

- **目的**：按需搜尋記憶，回傳相關檔案路徑讓 brain agent 自行 `read_file`
- **模型**：由 `config.agents.memory_searcher` 指定（如 GLM 4.7）
- **運作方式**：sub-LLM 讀取 `memory/` 下所有 `index.md` + 目錄列表，分析 query 回傳相關路徑
- **輸入**：query 字串（由 brain agent 提供）
- **輸出**：路徑 + 一句話相關性說明（如 `memory/agent/persona.md: Contains persona info`）
- **結果限制**：`memory_search` 只回傳可直接讀取的內容檔，不回傳任何 `*/index.md`
- **上限**：最多 8 個結果，context 上限 8KB

### 觸發規則

Brain agent 的 system prompt 內建觸發規則：
- 提到過去事件、回憶 → `memory_search(query="...")`
- 問到人的資訊 → `memory_search(query="...")`
- 需要背景知識 → `memory_search(query="...")`
- 簡單打招呼等不需要記憶的對話 → 不觸發

## Post-review Pass（PostReviewer）

- **目的**：審查 Responder 的回應是否遵守所有 trigger rules
- **模型**：由 `config.agents.post_reviewer` 指定（如 GLM 4.7）
- **輸入**：`ReviewPacket`（固定欄位證據封包，非全量對話）
- **輸出**：`PostReviewResult` JSON — 是否通過、違規項、`required_actions`、重試指引、`label_signals`
- **後續**：若未通過，`required_actions` 作為 system reminders 注入，Responder 重試；程式會硬驗證 action 是否完成
- **新增違規（v0.5.8）**：`near_time_context_missed`，用於「用戶要求近時事件但回答錨定在較舊記憶」
- **新增違規（v0.5.13）**：
  - `simulated_user_turn`：回應包含模擬用戶語氣的文字（hard violation）
  - `gender_confusion`：性別稱呼錯誤（hard violation）
  - 這兩種 violation 不需要 `required_actions`，只需重新生成回應

### ReviewPacket（Post-review 輸入封包）

Post-review 不再直接吃 `flatten_for_review` 全量內容，而是由程式產生固定結構：

- `latest_user_turn`
- `candidate_assistant_reply`
- `current_turn_tool_calls_summary`
- `current_turn_memory_edit_summary`（`request_id`、`target_path`、`instruction`）
- `current_turn_tool_errors`
- `recent_context_tail`
- `truncation_report`

欄位截斷策略（per-field，無全域預算）：

| 設定 | 預設值 | 作用範圍 |
|------|--------|----------|
| `history_turns` | 6 | 歷史對話輪數 |
| `history_turn_max_chars` | 1200 | 每筆歷史 turn summary + `latest_user_turn` 上限 |
| `reply_max_chars` | 3000 | `candidate_assistant_reply` 獨立上限 |
| `tool_preview_max_chars` | 180 | tool call args / error preview 上限 |

- 每個欄位獨立截斷，無全域預算（`review_max_chars` 已移除）
- `candidate_assistant_reply` 使用獨立的 `reply_max_chars`，確保長回應尾段不被截斷（reviewer 需看到完整回應以偵測 `simulated_user_turn`）
- debug 模式可見 `chars(before/after)` 與 `truncated_sections`
- 非 debug 僅在有裁剪時警告 `review_packet_truncated`

### 程式硬驗證（非 LLM）

Post-review 回傳 `required_actions` 後，App 會直接檢查本輪的 tool calls：

- 是否有呼叫指定工具（`get_current_time` / `execute_shell` / `memory_edit`）
- 檔案更新是否命中指定 `target_path` 或 `target_path_glob`
- 若 action 要求 `index_path`，是否同輪更新對應 `index.md`
- `memory_edit` 若 tool result 為 `status=failed` 或 `Error`，不算完成 action
- Post-review 重試時，送審封包會保留同一輪 user 訊息，但只包含「最新一次嘗試」的 assistant/tool trace（避免舊嘗試的違規殘留污染判定）
- 若同輪已有成功 `memory_edit` 寫入 `memory/`，重試判定會忽略 reviewer 殘留的 `turn_not_persisted`（以程式硬驗證為準）
- 若 `passed=false` 且有 `violations` 但無 `required_actions`（如 `simulated_user_turn`、`gender_confusion`），直接重試讓 Responder 重新生成回應
- 若本輪完全沒有任何 `memory/` 下的寫入（`memory_edit`），App 會自動補上 `persist_turn_memory` required action（粗網安全網）

#### Label Enforcement（v0.8.1）

**不論 `passed` 為何**，App 會檢查 `confidence >= label_confidence_threshold` 的 `label_signals`，對照本輪 `memory_edit` 是否寫入對應路徑。未寫入者自動注入 `required_action`。

互動階段（chat loop）使用白名單 label：
- `rolling_context`
- `agent_state_shift`
- `near_future_todo`
- `durable_user_fact`
- `emotional_event`
- `interest_change`
- `identity_change`

`shutdown_reviewer` 保持全量 label enforcement（包含 `correction_lesson` / `skill_change`），用於退出前補齊長期記憶。

全量 label 與路徑對應如下：

| Label | 對應路徑 prefix | Action code |
|-------|----------------|-------------|
| `rolling_context` | `memory/short-term.md` | `persist_rolling_context` |
| `agent_state_shift` | `memory/agent/inner-state.md` | `persist_agent_state_shift` |
| `near_future_todo` | `memory/agent/pending-thoughts.md` | `persist_near_future_todo` |
| `durable_user_fact` | `memory/agent/knowledge/`, `memory/people/` | `persist_durable_user_fact` |
| `emotional_event` | `memory/agent/experiences/` | `persist_emotional_event` |
| `correction_lesson` | `memory/agent/thoughts/` | `persist_correction_lesson` |
| `skill_change` | `memory/agent/skills/` | `persist_skill_change` |
| `interest_change` | `memory/agent/interests/` | `persist_interest_change` |
| `identity_change` | `memory/agent/persona.md`, `memory/agent/config.md` | `sync_identity_persona` |

與 `turn_not_persisted` 違規碼互補（兩層共存）：
- `turn_not_persisted`（粗網）：抓「完全沒 memory_edit」
- Label enforcement（細網）：抓「有寫 memory 但漏掉特定路徑」

這一層不依賴 LLM 判斷，避免 reviewer 漏判或誤判。

## Config

```yaml
warn_on_failure: true  # global switch; false disables all reviewer warnings

agents:
  memory_searcher:
    enabled: true
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    pre_parse_retries: 1
    warn_on_failure: true
  post_reviewer:
    enabled: true
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 3
    history_turns: 6
    history_turn_max_chars: 2048
    reply_max_chars: 3000
    tool_preview_max_chars: 180
    label_confidence_threshold: 0.90
  shutdown_reviewer:
    enabled: true
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 2
  memory_editor:
    enabled: true
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
```

- 每個 agent 獨立開關（`enabled: false` 或 config 中不存在即跳過）
- 所有欄位有合理預設值

### Shutdown Reviewer（選用）

- `shutdown_reviewer` 僅在 `/quit` 時執行
- 先跑 shutdown 保存，再由 reviewer 判斷本次「應更新哪些檔案」
- 用 `required_actions` 驅動補寫回合，避免把「每次都更新所有檔案」寫死

## Schema

```python
class PostReviewResult(BaseModel):
    passed: bool
    violations: list[str]
    required_actions: list[RequiredAction]
    retry_instruction: str
    label_signals: list[LabelSignal]
```

```python
class LabelSignal(BaseModel):
    label: Literal[
        "rolling_context",
        "agent_state_shift",
        "near_future_todo",
        "durable_user_fact",
        "emotional_event",
        "correction_lesson",
        "skill_change",
        "interest_change",
        "identity_change",
    ]
    confidence: float  # 0~1
    requires_persistence: bool = True
    reason: str | None = None
```

## 錯誤處理

| 情況 | 行為 |
|------|------|
| Memory search 返回 invalid JSON | 自動重試 `pre_parse_retries` 次，仍失敗回傳空結果 |
| Memory search LLM 連不上 | 捕獲異常，回傳空結果（不影響主流程） |
| Post-review 重試超限或 unresolved | fail-closed：本輪不輸出回覆，並回滾本輪 `memory_edit` 寫入 |
| Responder/Reviewer 中途例外（例如 429） | 回滾本輪已執行的 `memory_edit` 寫入，避免半成品記憶汙染 |
| Config 無 reviewer agent | reviewer = None，完全跳過 |

## Prompt 模板

| 檔案 | 位置 |
|------|------|
| `system.md`（MemorySearcher） | `kernel/agents/memory_searcher/prompts/` |
| `parse-retry.md`（MemorySearcher） | `kernel/agents/memory_searcher/prompts/` |
| `system.md`（PostReviewer） | `kernel/agents/post_reviewer/prompts/` |
| `parse-retry.md`（PostReviewer） | `kernel/agents/post_reviewer/prompts/` |
| `system.md`（Shutdown Reviewer） | `kernel/agents/shutdown_reviewer/prompts/` |
| `parse-retry.md`（Shutdown Reviewer） | `kernel/agents/shutdown_reviewer/prompts/` |

## 相關檔案

- `src/chat_agent/tools/builtin/memory_search.py` — memory_search tool 實作
- `src/chat_agent/reviewer/` — post-reviewer 模組
- `src/chat_agent/context/builder.py` — `build_with_reminders()` 注入重試提醒
- `src/chat_agent/cli/app.py` — 主迴圈整合
- `src/chat_agent/core/schema.py` — `AgentConfig`
