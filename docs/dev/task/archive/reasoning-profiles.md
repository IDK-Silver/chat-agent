> **歸檔日期**：2026-02-18

# 跨 Provider Thinking Profiles 與 Ollama `/api/chat` 統一

## 目標

- 提供通用 `reasoning` 設定介面（`enabled` / `effort` / `max_tokens`）
- 支援多 provider 映射（Ollama、OpenAI、OpenRouter、Anthropic、Gemini）
- 透過 model profile YAML 切換 thinking / no-thinking
- 啟動時 fail-fast：不支援配置直接報錯，不允許 silent ignore
- Ollama 統一使用 `/api/chat`

## 主要變更

- `src/chat_agent/core/schema.py`
  - 新增 `ReasoningConfig`
  - 新增 `ReasoningCapabilities` 與 `LLMCapabilities`
  - 各 provider config 新增 `reasoning`、`capabilities`、`provider_overrides`
  - config models 改為 `extra="forbid"`
- `src/chat_agent/core/config.py`
  - 載入 LLM profile 後，執行 reasoning 正規化與驗證
- `src/chat_agent/llm/reasoning.py`
  - 通用正規化邏輯
  - capability 驗證
  - provider 映射 helper
- `src/chat_agent/llm/providers/ollama.py`
  - `chat` / `chat_with_tools` 全改走 `/api/chat`
  - 支援 `think` 映射與 `tool_calls` 解析

## Provider 映射規則

- Ollama
  - `reasoning.enabled=false -> think=false`
  - `reasoning.enabled=true -> think=true`
  - `reasoning.effort -> think=<low|medium|high>`
- OpenAI Chat Completions
  - `reasoning.effort -> reasoning_effort`
  - 本階段不支援 `reasoning.max_tokens`
- OpenRouter
  - `reasoning -> reasoning` 物件（`enabled/effort/max_tokens`）
- Anthropic
  - `reasoning.enabled=true + max_tokens -> thinking={type: enabled, budget_tokens: ...}`
  - 本階段不支援 `reasoning.effort`
- Gemini
  - `generationConfig.thinkingConfig`
  - `enabled=false -> thinkingBudget=0`
  - `effort -> thinkingLevel`（`low->LOW`, `medium/high->HIGH`）
  - `max_tokens -> thinkingBudget`

## Profile 目錄規範

- 路徑：`cfgs/llm/<provider>/<model-slug>/<profile>.yaml`
- 每個 model 至少提供：`no-thinking.yaml`
- effort-capable model 提供：`think-low.yaml`、`think-medium.yaml`、`think-high.yaml`
- 為避免 `enabled: true` 語義不明確，effort-capable 的 Ollama model 不提供通用 `thinking.yaml`

## Fail-fast 規則

- 設定了 `reasoning` 但缺少 `capabilities.reasoning`：啟動失敗
- `reasoning.effort` 不在 `supported_efforts`：啟動失敗
- `reasoning.max_tokens` 但 `supports_max_tokens=false`：啟動失敗
- provider 特有限制（例如 OpenAI `max_tokens` 不支援）：啟動失敗

## 使用方式

1. 在 `cfgs/basic.yaml` 的 agent 指定對應 profile 路徑
2. 只需切換 `llm` 檔案路徑，即可切換 thinking 策略
3. 若配置不合法，`load_config()` 會直接拋錯並包含 provider/model/path
