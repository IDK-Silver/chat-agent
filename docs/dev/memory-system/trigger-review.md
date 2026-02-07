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
PostReviewer ← 完整 context + Flash 的回應 + tool call 記錄
    │       → JSON { passed, violations, required_actions, retry_instruction }
    ▼
passed? → 輸出給用戶
not passed? → required_actions 注入為 system reminders → Flash 重試（最多 N 次）
           → 程式硬驗證本輪 tool calls 是否完成 required_actions
```

## 兩個 Pass 的職責

### Pre-fetch Pass（PreReviewer）

- **目的**：在 Responder 回答前，判斷需要搜索什麼，預先載入相關記憶
- **模型**：由 `config.agents.pre_reviewer` 指定（如 GLM 4.7）
- **輸入**：完整對話 context（跟 Responder 看到的相同）+ pre-review prompt
- **輸出**：`PreReviewResult` JSON — 觸發的規則、prefetch actions、reminders
- **後續**：程式碼執行 prefetch actions（grep → 自動展開讀檔），結果 + reminders 注入 Responder 的 system prompt

### Post-review Pass（PostReviewer）

- **目的**：審查 Responder 的回應是否遵守所有 trigger rules
- **模型**：由 `config.agents.post_reviewer` 指定（如 Kimi K2.5）
- **輸入**：完整對話 context + Responder 的回應 + 所有 tool call 記錄
- **輸出**：`PostReviewResult` JSON — 是否通過、違規項、`required_actions`、重試指引
- **後續**：若未通過，`required_actions` 作為 system reminders 注入，Responder 重試；程式會硬驗證 action 是否完成

### 程式硬驗證（非 LLM）

Post-review 回傳 `required_actions` 後，App 會直接檢查本輪的 tool calls：

- 是否有呼叫指定工具（`get_current_time` / `execute_shell` / `write_file` / `edit_file`）
- 檔案更新是否命中指定 `target_path` 或 `target_path_glob`
- 若 action 要求 `index_path`，是否同輪更新對應 `index.md`

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
    llm: llm/ollama/glm-4.7.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    pre_parse_retries: 2
    enforce_memory_path_constraints: true
    warn_on_failure: true
    max_prefetch_actions: 5
    max_files_per_grep: 3
    shell_whitelist: ["grep", "cat", "ls", "find", "wc"]
  post_reviewer:
    llm: llm/ollama/kimi-k2.5.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 2
  shutdown_reviewer:
    llm: llm/ollama/glm-4.7.yaml
    llm_request_timeout: 120
    llm_timeout_retries: 1
    post_parse_retries: 2
    warn_on_failure: true
    max_post_retries: 2
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
class AgentConfig(BaseModel):
    llm: LLMConfig
    llm_request_timeout: float | None = Field(default=None, gt=0)
    llm_timeout_retries: int = Field(default=1, ge=0)
    max_prefetch_actions: int = 5
    max_files_per_grep: int = 3
    max_post_retries: int = 2
    pre_parse_retries: int = Field(default=1, ge=0)
    post_parse_retries: int = Field(default=1, ge=0)
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
```

## 錯誤處理

| 情況 | 行為 |
|------|------|
| Pre-reviewer 返回 invalid JSON | 自動重試 `pre_parse_retries` 次，仍失敗才跳過 |
| Reviewer LLM 連不上 | 捕獲異常，返回 None |
| Prefetch action 失敗 | 單個失敗不影響其他 |
| Post-review 重試超限 | 停止重試，輸出最後版本 |
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
