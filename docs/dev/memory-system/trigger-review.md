# Trigger Review 系統

雙 LLM 架構，確保 Responder（如 Gemini Flash）遵守 system prompt 中的 trigger rules。

## 架構

```
User Message
    │
    ▼
═══ Pre-fetch Pass ═══
PreReviewer ← 完整 context + pre-fetch prompt
    │       → JSON { triggered_rules, prefetch, reminders }
    ▼
程式執行 prefetch（grep → 自動展開讀檔）
    │
    ▼
搜索結果 + reminders 注入 context
    │
    ▼
═══ Responder ═══
Gemini Flash（帶完整工具）→ 生成回應
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

## 兩個 Pass 的職責

### Pre-fetch Pass（PreReviewer）

- **目的**：在 Responder 回答前，判斷需要搜索什麼，預先載入相關記憶
- **模型**：由 `config.agents.pre_reviewer` 指定（如 GLM 4.7）
- **輸入**：完整對話 context（跟 Responder 看到的相同）+ pre-review prompt
- **輸出**：`PreReviewResult` JSON — 觸發的規則、prefetch actions、reminders
- **後續**：程式碼執行 prefetch actions（grep → 自動展開讀檔），結果 + reminders 注入 Responder 的 system prompt
- **近時優先規則（v0.5.8）**：若偵測到「今天／剛才／到現在」語義，必須優先 prefetch `get_current_time` + `memory/short-term.md`，並提醒 responder 先用同日最近證據

### Post-review Pass（PostReviewer）

- **目的**：審查 Responder 的回應是否遵守所有 trigger rules
- **模型**：由 `config.agents.post_reviewer` 指定（如 Kimi K2.5）
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
- `current_turn_memory_edit_summary`
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

- reviewer 回 `passed=true` 時，該輪直接放行並輸出最終答案
- 是否有呼叫指定工具（`get_current_time` / `execute_shell` / `memory_edit`）
- 檔案更新是否命中指定 `target_path` 或 `target_path_glob`
- 若 action 要求 `index_path`，是否同輪更新對應 `index.md`
- 僅在 `passed=false` 時，才會檢查 `required_actions` 是否完成並進入重試流程
- 若 `passed=false` 且有 `violations` 但無 `required_actions`（如 `simulated_user_turn`、`gender_confusion`），直接重試讓 Responder 重新生成回應
- 若 `passed=false` 且本輪完全沒有任何 `memory/` 下的寫入（`memory_edit`），App 會自動補上一條 `persist_turn_memory` required action，強制至少落地一筆 rolling memory（預設 `memory/short-term.md`）
- 若 `passed=false` 且 reviewer 輸出 `label_signals` 中出現 `identity_change` 且 `confidence >= label_confidence_threshold`（預設 0.75），但本輪未同步 `memory/agent/persona.md` 或 `memory/agent/config.md`，App 會自動追加 `sync_identity_persona` action

這一層不依賴 LLM 判斷，避免 reviewer 漏判或誤判。

## 遞歸展開邏輯

```
對每個 prefetch action：
  如果是 grep / shell 命令：
    執行 → 解析輸出中的檔案路徑
    去重，取前 N 個不重複的檔案
    自動 read_file 每個檔案
  如果是 read_file / get_current_time：
    直接執行
```

## Config

```yaml
warn_on_failure: true  # global switch; false disables all reviewer warnings

agents:
  pre_reviewer:
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    pre_parse_retries: 2
    enforce_memory_path_constraints: true
    warn_on_failure: true
    max_prefetch_actions: 5
    max_files_per_grep: 3
    shell_whitelist: ["grep", "cat", "ls", "find", "wc"]
  post_reviewer:
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 5
    history_turns: 6
    history_turn_max_chars: 1200
    reply_max_chars: 3000
    tool_preview_max_chars: 180
    label_confidence_threshold: 0.75
  shutdown_reviewer:
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 2
  memory_writer:
    llm: llm/ollama/glm-4.7/no-thinking.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    writer_parse_retries: 1
    writer_max_retries: 2
```

- 每個 reviewer 獨立開關（config 中不存在即跳過）
- 安全限制在各自的 agent config 底下
- 所有欄位有合理預設值

### Shutdown Reviewer（選用）

- `shutdown_reviewer` 僅在 `/quit` 時執行
- 先跑 shutdown 保存，再由 reviewer 判斷本次「應更新哪些檔案」
- 用 `required_actions` 驅動補寫回合，避免把「每次都更新所有檔案」寫死

### AgentConfig 擴充

```python
class AgentConfig(StrictConfigModel):
    llm: LLMConfig
    llm_request_timeout: float | None = Field(default=None, gt=0)
    llm_timeout_retries: int = Field(default=1, ge=0)
    max_prefetch_actions: int = 5
    max_files_per_grep: int = 3
    max_post_retries: int = 2
    pre_parse_retries: int = Field(default=1, ge=0)
    post_parse_retries: int = Field(default=1, ge=0)
    history_turns: int = Field(default=6, ge=1)
    history_turn_max_chars: int = Field(default=1200, ge=200)
    reply_max_chars: int = Field(default=3000, ge=200)
    tool_preview_max_chars: int = Field(default=180, ge=50)
    label_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    writer_max_retries: int = Field(default=2, ge=0)
    writer_parse_retries: int = Field(default=1, ge=0)
    enforce_memory_path_constraints: bool = True
    warn_on_failure: bool = True
    shell_whitelist: list[str] = Field(
        default_factory=lambda: ["grep", "cat", "ls", "find", "wc"]
    )
```

## Schema

```python
class PrefetchAction(BaseModel):
    tool: Literal["read_file", "execute_shell", "get_current_time"]
    arguments: dict[str, str]
    reason: str

class PreReviewResult(BaseModel):
    triggered_rules: list[str]
    prefetch: list[PrefetchAction]
    reminders: list[str]

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
    reason: str | None = None
```

## 錯誤處理

| 情況 | 行為 |
|------|------|
| Pre-reviewer 返回 invalid JSON | 自動重試 `pre_parse_retries` 次，仍失敗才跳過 |
| Reviewer LLM 連不上 | 捕獲異常，返回 None |
| Prefetch action 失敗 | 單個失敗不影響其他 |
| Post-review 重試超限或 unresolved | fail-closed：本輪不輸出回覆，並回滾本輪 `memory_edit` 寫入 |
| Responder/Reviewer 中途例外（例如 429） | 回滾本輪已執行的 `memory_edit` 寫入，避免半成品記憶汙染 |
| Config 無 reviewer agent | reviewer = None，完全跳過 |

## Prompt 模板

| 檔案 | 位置 |
|------|------|
| `system.md`（PreReviewer） | `kernel/agents/pre_reviewer/prompts/` |
| `parse-retry.md`（PreReviewer） | `kernel/agents/pre_reviewer/prompts/` |
| `system.md`（PostReviewer） | `kernel/agents/post_reviewer/prompts/` |
| `parse-retry.md`（PostReviewer） | `kernel/agents/post_reviewer/prompts/` |
| `system.md`（Shutdown Reviewer） | `kernel/agents/shutdown_reviewer/prompts/` |
| `parse-retry.md`（Shutdown Reviewer） | `kernel/agents/shutdown_reviewer/prompts/` |

## 相關檔案

- `src/chat_agent/reviewer/` — 模組實作
- `src/chat_agent/context/builder.py` — `build_with_review()` 注入搜索結果
- `src/chat_agent/cli/app.py` — 主迴圈整合
- `src/chat_agent/core/schema.py` — `AgentConfig` 擴充
