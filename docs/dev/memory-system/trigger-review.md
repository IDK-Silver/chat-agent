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
    │       → JSON { passed, violations, required_actions, retry_instruction, target_signals, anomaly_signals }
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
- **運作方式（v0.9.1）**：二段式
  1. Stage 1：sub-LLM 讀取 `memory/` 下 `index.md` + 目錄列表，先挑候選路徑
  2. Stage 2：程式讀取候選檔全文，再交給 sub-LLM 精排最終路徑
  3. Stage 2 失敗時（呼叫/解析/invalid）回退 Stage 1 結果
- **輸入**：query 字串（由 brain agent 提供）
- **輸出**：路徑 + 一句話相關性說明（如 `memory/agent/persona.md: Contains persona info`）
- **結果限制**：`memory_search` 只回傳可直接讀取的內容檔，不回傳任何 `*/index.md`
- **上限（configurable）**：
  - `context_bytes_limit`: `null`（預設）代表不做程式端 context 截斷
  - `max_results`: `null`（預設）代表不做程式端結果數量截斷
  - 註：仍受模型本身 context window 限制

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
- **輸出**：`PostReviewResult` JSON — 是否通過、違規項、`required_actions`、重試指引、`target_signals`、`anomaly_signals`
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

#### Target + Anomaly Enforcement（v0.9.0）

**不論 `passed` 為何**，App 與 Shutdown 都會對 `target_signals` 做 deterministic enforcement：

- `target_short_term` -> `memory/agent/short-term.md`
- `target_inner_state` -> `memory/agent/inner-state.md`
- `target_pending_thoughts` -> `memory/agent/pending-thoughts.md`
- `target_user_profile` -> `memory/people/user-{current_user}.md`
- `target_persona` -> `memory/agent/persona.md`

- `target_knowledge` -> `memory/agent/knowledge/*.md` + `index.md`
- `target_experiences` -> `memory/agent/experiences/*.md` + `index.md`
- `target_thoughts` -> `memory/agent/thoughts/*.md` + `index.md`
- `target_skills` -> `memory/agent/skills/*.md` + `index.md`
- `target_interests` -> `memory/agent/interests/*.md` + `index.md`

異常信號由 reviewer 與程式共同檢測並合併：
- `anomaly_missing_required_target`
- `anomaly_wrong_target_path`
- `anomaly_out_of_contract_path`
- `anomaly_missing_index_update`
- `anomaly_brain_style_meta_text`

重試策略（chat/shutdown 同步）：
- 最多重試 5 次
- 若重試簽名重複（仍 unresolved）→ 直接 fail-closed
- 不再 downgrade to warning

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
    context_bytes_limit: null
    max_results: null
    warn_on_failure: true
  post_reviewer:
    enabled: true
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 5
    history_turns: 6
    history_turn_max_chars: 2048
    reply_max_chars: 3000
    tool_preview_max_chars: 180
  shutdown_reviewer:
    enabled: true
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 5
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
    target_signals: list[TargetSignal]
    anomaly_signals: list[AnomalySignal]
```

```python
class TargetSignal(BaseModel):
    signal: Literal[
        "target_short_term",
        "target_inner_state",
        "target_pending_thoughts",
        "target_user_profile",
        "target_persona",
        "target_knowledge",
        "target_experiences",
        "target_thoughts",
        "target_skills",
        "target_interests",
    ]
    requires_persistence: bool = True
    reason: str | None = None


class AnomalySignal(BaseModel):
    signal: Literal[
        "anomaly_missing_required_target",
        "anomaly_wrong_target_path",
        "anomaly_out_of_contract_path",
        "anomaly_missing_index_update",
        "anomaly_brain_style_meta_text",
    ]
    target_signal: str | None = None
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
