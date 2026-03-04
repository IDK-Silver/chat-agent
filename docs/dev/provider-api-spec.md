# LLM Provider API 規格盤點

本文件記錄各 LLM provider 的 API 事實、本專案 adapter 規則、實測/逆向資訊。
作為 LLM config/client 設計的依據。

架構邊界與設計準則見 `docs/dev/provider-architecture.md`。

每個 provider 分三段：
1. **官方 API 事實**（有來源連結、可信度標註）
2. **本專案 adapter 規則**（非 API 事實，是本專案的映射/驗證邏輯）
3. **實測/逆向資訊**（無官方保證的內容）

---

## Copilot (GitHub Copilot API)

### 1. 歷史官方 + 逆向/實測資訊

> **重要標示**：原始官方文件頁面（[Use Copilot's LLM](https://docs.github.com/copilot/how-tos/use-copilot-extensions/build-a-copilot-agent/use-copilots-llm)）已被 MCP 文件取代，目前無法從現行官方頁面直接確認以下內容。以下所有項目均標示為歷史/實測/逆向依據。

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| Endpoint | `POST {endpoint}/chat/completions`（OpenAI Chat Completions 相容） | 歷史官方文件 + 實測 | 中 | 原始 Copilot Extensions LLM 頁面已不可用 |
| Request 格式 | OpenAI Chat Completions 相容（messages, model, stream, tools） | 歷史官方文件 + 實測 | 中 | 同上 |
| Auth（Extensions） | Copilot Extensions 透過 `X-GitHub-Token` header 交換 | 歷史官方文件 | 中 | 頁面內容已不可用 |
| Token 交換 | `GET https://api.github.com/copilot_internal/v2/token` with `Authorization: token {github_token}` | 逆向（copilot-api 專案） | 中 | 非穩定契約 |
| Device Flow | client_id `Iv1.b507a08c87ecfe98`, scope `read:user` | 逆向 | 中 | 隨時可能變更 |
| IDE Headers | `copilot-integration-id: vscode-chat`, `editor-version: vscode/1.109.5`, `editor-plugin-version: copilot-chat/0.26.7`, `x-vscode-user-agent-library-version: electron-fetch` | 逆向 | 低 | 偽裝 VS Code，版本號會過期 |
| X-Initiator | `"user"` 消耗 premium request，`"agent"` 不消耗 | 逆向 | 中 | 計費機制非官方文件 |
| `reasoning_effort` | `/chat/completions` 使用頂層 `reasoning_effort`（依 copilot-api 相容行為） | 逆向 + 實測 | 中 | GitHub 未文件化此欄位；非現行官方可驗證契約 |
| Vision header | 需 `copilot-vision-request: true` | 逆向（copilot-api） | 中 | — |
| 非串流 max output | 16K tokens | 實測 | 中 | 可能隨 API 更新變動 |
| 串流 max output | 64K tokens | 實測 | 中 | 同上 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 |
|------|------|-----------|
| `reasoning` payload | 送頂層 `reasoning_effort` string（非 `reasoning` object） | `src/chat_agent/llm/providers/copilot.py` |
| Vision header 自動偵測 | request 含 `image_url` 時由 forked `copilot-api` 加 `copilot-vision-request: true` | `../copilot-api`（外部 repo） |
| `force_agent` | 注入空 assistant message 讓 X-Initiator 判定為 agent | `src/chat_agent/llm/providers/copilot.py` |
| tools + reasoning 表現差異（觀測） | Copilot gateway 上 `reasoning_effort + tools` 可能有模型別差異（例如部分 GPT-5 family 可能影響 tool calling）；**本專案目前不做 adapter 自動特判，交由使用者選模型/配置** | `src/chat_agent/llm/providers/copilot.py`（無特判） |
| Proxy 不注入 reasoning | proxy 純 passthrough，不 setdefault `reasoning_effort` | `../copilot-api`（外部 repo，行為契約） |

---

## OpenAI

### 1. 官方 API 事實

| 項目 | 事實 | 來源類型 | 來源連結 | 可信度 | 模型/版本相關 |
|------|------|---------|---------|--------|-------------|
| Endpoint | `POST /v1/chat/completions` | 官方文件 | [OpenAI Chat API Reference](https://platform.openai.com/docs/api-reference/chat) | 高 | 否 |
| Auth | `Authorization: Bearer {api_key}` | 官方文件 | 同上 | 高 | 否 |
| **Chat Completions reasoning** | `reasoning_effort: "low"\|"medium"\|"high"` — **頂層 string 欄位** | 官方文件 | [GPT-5.2 Guide](https://developers.openai.com/api/docs/guides/latest-model/) 原文："Chat Completions API uses: `reasoning_effort: 'none'`" | 高 | 是（reasoning models） |
| **Responses API reasoning（對照）** | `reasoning: {"effort": "..."}` — nested object。**與 Chat Completions 格式不同** | 官方文件 | [OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning/) + [GPT-5.2 Guide](https://developers.openai.com/api/docs/guides/latest-model/) | 高 | — |
| Effort 值（GPT-5.2+） | `"none"`, `"low"`, `"medium"`, `"high"`, `"xhigh"` | 官方文件 | [GPT-5.2 Guide](https://developers.openai.com/api/docs/guides/latest-model/) | 高 | 是（xhigh + none 僅 GPT-5.2+） |
| Reasoning summary | `reasoning: {"summary": "auto"\|"detailed"}`（Responses API） | 官方文件 | [OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning/) | 高 | 是 |
| Vision | `image_url` content parts | 官方文件 | [OpenAI Chat API Reference](https://platform.openai.com/docs/api-reference/chat) | 高 | 是（vision models） |
| Tools | OpenAI function calling format（`type: "function"`, `function: {name, description, parameters}`） | 官方文件 | 同上 | 高 | 否 |
| max_tokens | 可選 | 官方文件 | 同上 | 高 | 否 |
| temperature | 可選 | 官方文件 | 同上 | 高 | 否 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| 送 `reasoning_effort` 頂層欄位 | `OpenAICompatibleClient` 送 `reasoning_effort` | `src/chat_agent/llm/providers/openai_compat.py` | 符合 Chat Completions API 官方格式 |
| `enabled=false` 需要 override | 驗證要求有 `provider_overrides.openai_reasoning_effort` | `src/chat_agent/core/schema.py`（`OpenAIConfig.validate_reasoning()`） | 本專案規則，非 API 限制 |
| `max_tokens` 在 reasoning 裡擋掉 | OpenAI provider schema 不提供 reasoning.max_tokens 欄位 | `src/chat_agent/core/schema.py`（`OpenAIReasoningConfig`） | 本專案規則 |

### 3. 逆向/實測資訊

無。

---

## Anthropic

### 1. 官方 API 事實

| 項目 | 事實 | 來源類型 | 來源連結 | 可信度 | 模型/版本相關 |
|------|------|---------|---------|--------|-------------|
| Endpoint | `POST /v1/messages` | 官方文件 | [Messages API](https://platform.claude.com/docs/en/api/messages) | 高 | 否 |
| Auth | `Authorization: Bearer {api_key}` 或 `x-api-key: {api_key}`，加 `anthropic-version: 2023-06-01` | 官方文件 | 同上 | 高 | 否 |
| max_tokens | **必填** | 官方文件 | 同上 | 高 | 否 |
| temperature | 可選，default 1.0 | 官方文件 | 同上 | 高 | 否 |
| Tools | `{name, description, input_schema: {type, properties, required}}`，**非** OpenAI function calling | 官方文件 | 同上 | 高 | 否 |
| Vision image source | `base64` 和 `url` 兩種 source type | 官方範例 | [Messages Examples](https://platform.claude.com/docs/en/api/messages-examples)，Vision 段落 Option 1 (base64) + Option 2 (url) | 高 | 否 |
| Vision media types | `image/jpeg`, `image/png`, `image/gif`, `image/webp` | 官方文件 | [Messages API](https://platform.claude.com/docs/en/api/messages) | 高 | 否 |
| **Extended thinking（手動）** | `thinking: {"type": "enabled", "budget_tokens": N}`，budget_tokens >= 1024 | 官方文件 | [Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking) | 高 | 是（見下方） |
| **Adaptive thinking** | `thinking: {"type": "adaptive"}` | 官方文件 | [Adaptive Thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking) | 高 | 是（僅 Opus 4.6, Sonnet 4.6） |
| **Effort 參數** | `output_config: {"effort": "low"\|"medium"\|"high"\|"max"}`，**獨立於 thinking**，影響所有 token | 官方文件 | [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) 段落 "The effort parameter is supported by Claude Opus 4.6, Claude Sonnet 4.6, and Claude Opus 4.5"，代碼範例 `output_config={"effort": "medium"}` | 高 | 是（僅 Opus 4.6, Sonnet 4.6, Opus 4.5） |
| Effort `max` | 僅 Opus 4.6，其他模型報錯 | 官方文件 | [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) | 高 | 是 |
| **Opus 4.6 deprecation** | `thinking: {"type": "enabled", "budget_tokens": N}` 在 Opus 4.6 和 Sonnet 4.6 上 deprecated | 官方文件 | [Adaptive Thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking) Warning box 原文："`thinking.type: 'enabled'` and `budget_tokens` are **deprecated** on Opus 4.6 and Sonnet 4.6" | 高 | 是 |
| 舊模型 | Sonnet 4.5, Opus 4.5, Sonnet 4, Haiku 4.5 等僅支援 `thinking: {"type": "enabled", "budget_tokens": N}` | 官方文件 | [Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking) | 高 | 是 |
| Thinking disabled | 省略 `thinking` 參數 或 `thinking: {"type": "disabled"}` | 官方文件 | [Messages API](https://platform.claude.com/docs/en/api/messages) | 高 | 否 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| Thinking payload | `_map_thinking()` 組裝 `{"type": "enabled", "budget_tokens": N}` | `src/chat_agent/llm/providers/anthropic.py` | 不支援 adaptive thinking 和 output_config.effort |
| `effort` 驗證擋掉 | Anthropic schema 不提供 `reasoning.effort` 欄位（改用 `AnthropicThinkingConfig`） | `src/chat_agent/core/schema.py` | 本專案 adapter 尚未支援 `output_config.effort` |
| `budget_tokens` 必填 | `enabled=true` 時必須有 max_tokens 或 override | `src/chat_agent/core/schema.py`（`AnthropicConfig.validate_reasoning()`） | 本專案規則 |
| `provider_overrides` | `anthropic_thinking` / `anthropic_thinking_budget_tokens` | `src/chat_agent/llm/providers/anthropic.py` + `src/chat_agent/core/schema.py` | 本專案 escape hatch |

### 3. 逆向/實測資訊

無。

---

## Gemini

### 1. 官方 API 事實

| 項目 | 事實 | 來源類型 | 來源連結 | 可信度 | 模型/版本相關 |
|------|------|---------|---------|--------|-------------|
| Endpoint | `generateContent`（REST） | 官方文件 | [Gemini Thinking](https://ai.google.dev/gemini-api/docs/thinking) | 高 | 否 |
| Auth | `x-goog-api-key` header 或 `key=` query parameter | 官方文件 | 同上（範例中兩種都有） | 高 | 否 |
| Tools | `functionDeclarations` format：`{name, description, parameters: {type, properties, required}}`。Response function call 用 `args`（非 OpenAI 的 `arguments` JSON string）。Result 用 `functionResponse: {name, response: {...}}`（非 OpenAI 的 `role: "tool"`） | 官方文件 | [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)，declaration 結構 + response `args` 欄位 + result `functionResponse` 欄位 | 高 | 否 |
| Vision | `inlineData` parts（base64） | 官方文件 | 同上 | 高 | 否 |
| max_tokens | `generationConfig.maxOutputTokens` | 官方文件 | 同上 | 高 | 否 |
| **thinkingLevel（Gemini 3）** | `thinkingConfig: {"thinkingLevel": "minimal"\|"low"\|"medium"\|"high"}` | 官方文件 | [Gemini Thinking](https://ai.google.dev/gemini-api/docs/thinking) | 高 | 是 |
| thinkingLevel 支援矩陣 | 3.1 Pro: low/medium/high；3 Pro: low/high（無 medium/minimal）；3 Flash: minimal/low/medium/high | 官方文件 | 同上，ThinkingLevel 表格 | 高 | 是 |
| Gemini 3 Pro 不能關 thinking | 原文："You cannot disable thinking for Gemini 3 Pro." | 官方文件 | 同上 | 高 | 是 |
| Default thinkingLevel | `high`（所有 Gemini 3） | 官方文件 | 同上 | 高 | 是 |
| **thinkingBudget（Gemini 2.5）** | `thinkingConfig: {"thinkingBudget": N}`，0=關閉，-1=動態 | 官方文件 | 同上 | 高 | 是 |
| thinkingBudget 範圍 | 2.5 Pro: 128-32768；2.5 Flash: 0-24576；2.5 Flash Lite: 512-24576 | 官方文件 | 同上 | 高 | 是 |
| thinkingBudget 在 Gemini 3 | 向後相容接受，官方警告 "may result in unexpected performance" | 官方文件 | 同上 | 高 | 是 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| `_EFFORT_TO_LEVEL` | `low->LOW`, `medium->MEDIUM`, `high->HIGH` | `src/chat_agent/llm/providers/gemini.py` | 官方 API 值是小寫，SDK 用大寫 |
| `thinkingBudget` 設定 | `reasoning.max_tokens` -> `thinkingBudget` | `src/chat_agent/llm/providers/gemini.py` | 直接映射 |
| `enabled=True` 無 budget | 預設 `thinkingBudget: 1024` | `src/chat_agent/llm/providers/gemini.py` | 本專案 fallback |
| `enabled=False` | 設 `thinkingBudget: 0` | `src/chat_agent/llm/providers/gemini.py` | 對 Gemini 3 Pro 有問題（不能關閉） |
| 不支援 `minimal` | mapping 只有 low/medium/high | `src/chat_agent/llm/providers/gemini.py` | 遺漏 Gemini 3 Flash 的 minimal |
| `provider_overrides` | `gemini_thinking_config` 整體覆蓋 | `src/chat_agent/llm/providers/gemini.py` | 本專案 escape hatch |

### 3. 逆向/實測資訊

無。

---

## OpenRouter

### 1. 官方 API 事實

| 項目 | 事實 | 來源類型 | 來源連結 | 可信度 | 模型/版本相關 |
|------|------|---------|---------|--------|-------------|
| Endpoint | `POST https://openrouter.ai/api/v1/chat/completions` | 官方文件 | [API Overview](https://openrouter.ai/docs/api/reference/overview) | 高 | 否 |
| Auth | `Authorization: Bearer {api_key}` | 官方文件 | 同上 | 高 | 否 |
| Optional headers | `HTTP-Referer`，`X-OpenRouter-Title`（alias `X-Title`） | 官方文件 | 同上 | 高 | 否 |
| Request 格式 | OpenAI Chat Completions 相容 | 官方文件 | 同上 | 高 | 否 |
| **Reasoning effort** | `reasoning: {"effort": "none"\|"minimal"\|"low"\|"medium"\|"high"\|"xhigh"}` | 官方文件 | [Reasoning Tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)，effort levels 表格 | 高 | 否 |
| Reasoning max_tokens | `reasoning: {"max_tokens": N}`，最小 1024 | 官方文件 | 同上 | 高 | 依底層 provider |
| Reasoning exclude | `reasoning: {"exclude": true}` | 官方文件 | 同上 | 高 | 否 |
| Reasoning enabled | `reasoning: {"enabled": true}` — medium effort | 官方文件 | 同上 | 高 | 否 |
| Precedence | effort + max_tokens 互斥（"One of the following, not both"） | 官方文件 | 同上 | 高 | — |
| Provider routing | `provider: {"order": [...], "allow_fallbacks": bool}` | 官方文件 | [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection) | 高 | 依模型可用 endpoint |
| Tools | OpenAI function calling format | 官方文件 | [API Overview](https://openrouter.ai/docs/api/reference/overview) | 高 | 否 |
| Prompt caching | `cache_control: {"type": "ephemeral", "ttl": "1h"}` on content parts | 官方文件 | [Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching) | 高 | Claude 專用 TTL |
| Provider sticky routing | Cache hit 後自動路由到相同 provider endpoint | 官方文件 | 同上 | 高 | 否 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| effort / max_tokens 互斥 | config 層驗證，同時設定 → ValueError | `src/chat_agent/core/schema.py`（`OpenRouterConfig.validate_reasoning()`） | 符合官方 API 限制 |
| `enabled=False` -> `{"effort": "none"}` | 映射 | `src/chat_agent/llm/providers/openrouter.py` | 符合官方語意 |
| `provider_routing` payload | YAML `provider_routing` 映射到 request `provider` object；`null` 時不送 `provider`（走 OpenRouter 預設路由） | `src/chat_agent/core/schema.py` + `src/chat_agent/llm/providers/openrouter.py` + `src/chat_agent/llm/providers/openai_compat.py` | 允許各 profile 個別固定 endpoint 或回到預設 |
| Header 名稱 | 用 `X-Title` | `openrouter.py` | 官方 alias，兩者都接受 |
| 連線參數 self-contained | `api_key_env`/`base_url`/`site_url` 在每個 LLM YAML；`site_name` null 時 fallback 到 agent name | `src/chat_agent/core/config.py`（`load_config()`） | YAML 可獨立使用（validate_llm.py 等） |
| Cache breakpoint 注入 | `ContextBuilder` BP1 (system prompt) + BP2 (boot files)，`cache_control` passthrough via `_convert_content_parts()`；僅 OpenRouter provider 啟用 | `src/chat_agent/context/builder.py` + `openai_compat.py` + `cli/app.py` | 成本最佳化：1h TTL for heartbeat |

### 3. 逆向/實測資訊

無。

---

## Ollama

### 1. 官方 API 事實

| 項目 | 事實 | 來源類型 | 來源連結 | 可信度 | 模型/版本相關 |
|------|------|---------|---------|--------|-------------|
| Endpoint | `POST /v1/chat/completions`（OpenAI 相容） | 官方文件 | [OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility) | 高 | 否 |
| Auth | 無 | 官方文件 | 同上 | 高 | 否 |
| 支援功能 | chat completions, streaming, JSON mode, reproducible outputs, vision, tools | 官方文件 | 同上 | 高 | 否 |
| 不支援欄位 | `logit_bias`, `user`, `n`, `tool_choice` | 官方文件 | 同上 | 高 | 否 |
| `reasoning_effort` / `reasoning` | **未列出**。不在支援清單，不在不支援清單 | 官方文件 | 同上 | 高 | — |
| **Native thinking** | `think` 參數（boolean 或 level string），在 native Ollama API | 官方文件 | [Thinking](https://docs.ollama.com/capabilities/thinking) | 高 | 是 |
| `think` 值 | 大部分: `true`/`false`；GPT-OSS: `"low"`/`"medium"`/`"high"` | 官方文件 | 同上 | 高 | 是 |
| Thinking 預設 | 支援 thinking 的模型預設啟用 | 官方文件 | 同上 | 高 | 是 |
| Thinking response | `message.thinking`（reasoning）+ `message.content`（answer） | 官方文件 | 同上 | 高 | 否 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| 送 `reasoning_effort` | 透過 OpenAI-compat endpoint 送 | `src/chat_agent/llm/providers/ollama.py` | **無官方保證** |
| `enabled=True` 無 effort | 預設 `"medium"` | `src/chat_agent/llm/providers/ollama.py` | 本專案 fallback |
| `thinking` fallback | `OllamaClient.chat()` 讀 `message.thinking` | `ollama.py:41-42` | 處理 native thinking response |
| `provider_overrides.ollama_think` | bool 或 effort string | `src/chat_agent/llm/providers/ollama.py` | 本專案 escape hatch |

### 3. 逆向/實測資訊

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| `reasoning_effort` 經 OpenAI-compat | 部分模型實測可用 | 實測 | 低 | 無官方支撐 |

---

## 差異總結表

| 項目 | Copilot | OpenAI | Anthropic | Gemini | OpenRouter | Ollama |
|------|---------|--------|-----------|--------|------------|--------|
| Endpoint | OpenAI compat（歷史/實測） | Chat Completions | `/v1/messages` | `generateContent` | OpenAI compat | OpenAI compat |
| Reasoning 參數 | `reasoning_effort`（頂層，逆向/實測） | `reasoning_effort`（頂層） | `thinking.type` + `output_config.effort` | `thinkingConfig` | `reasoning: {"effort":...}` | `think`（native） |
| Effort 值 | low/medium/high（實測） | none/low/medium/high/xhigh | low/medium/high/max（output_config） | minimal/low/medium/high（依模型） | none/minimal/low/medium/high/xhigh | low/medium/high（GPT-OSS） |
| Token budget | 無 | 無 | `thinking.budget_tokens` | `thinkingBudget` | `reasoning.max_tokens` | 無 |
| Vision | `image_url`（實測） | `image_url` | `image` block（base64/url） | `inlineData`（base64） | `image_url` | 依模型 |
| Tools | OpenAI function（實測） | OpenAI function | Anthropic `input_schema` | Gemini `functionDeclarations` | OpenAI function | OpenAI function |
| Auth | proxy 處理（逆向） | Bearer token | Bearer/x-api-key + version | API key (header/query) | Bearer token | 無 |
| max_tokens | 不需要（實測） | 可選 | **必填** | 可選（maxOutputTokens） | 可選 | 可選 |

---

## Usage Token 回收（non-streaming）

本節描述本專案 runtime 對「回應 usage 欄位」的統一回收規則。

| Provider | API 是否可能回 usage | Adapter 是否回收 prompt/completion/total | Adapter 是否回收 cache read/write | 缺值策略 |
|---|---|---|---|---|
| OpenAI / OpenRouter / Ollama / Copilot（OpenAI-compatible） | 是（視 gateway/模型） | 是 | 是（若有 prompt_tokens_details） | `usage=None` 時標記 unavailable |
| Anthropic | 是 | 是（prompt = input + cache_read + cache_creation；completion = output） | 是（cache_read_input_tokens / cache_creation_input_tokens） | `usage` 缺失時標記 unavailable |
| Gemini | 是（usageMetadata） | 是（promptTokenCount / candidatesTokenCount / totalTokenCount） | 否 | `usageMetadata` 缺失時標記 unavailable |

補充：
- 本專案目前只看 non-streaming 回應，不使用 streaming usage。
- Copilot 在某些情況可能不回 usage；runtime 顯示 unavailable，不做估算。

---

## 修正清單（共 7 點有效 + 1 點撤回）

對照初版 A 表的修正紀錄。

| # | 原版敘述 | 修正 | 依據 | 狀態 |
|---|---------|------|------|------|
| 1 | Anthropic API 不認 effort | Anthropic 有 `output_config.effort`（low/medium/high/max），Opus 4.6 上 budget_tokens deprecated | [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) + [Adaptive Thinking](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking) | 有效 |
| 2 | Anthropic vision 只有 base64 | 支援 base64 和 url 兩種 source type | [Messages Examples](https://platform.claude.com/docs/en/api/messages-examples) Option 1 + Option 2 | 有效 |
| 3 | Gemini effort 只支援 low/high | 依模型：3 Pro 是 low/high；3 Flash 是 minimal/low/medium/high；3.1 Pro 是 low/medium/high | [Gemini Thinking](https://ai.google.dev/gemini-api/docs/thinking) ThinkingLevel 表格 | 有效 |
| 4 | Ollama 用 reasoning_effort | 無官方依據。Thinking 是 native `think` 參數 | [OpenAI Compatibility](https://docs.ollama.com/api/openai-compatibility) + [Thinking](https://docs.ollama.com/capabilities/thinking) | 有效 |
| 5 | OpenAI reasoning_effort 是頂層欄位（曾修正為「改成 reasoning object」） | **撤回修正**。Chat Completions API 仍用 `reasoning_effort` 頂層欄位。`reasoning` object 是 Responses API 格式。本專案用 Chat Completions，現行做法正確 | [GPT-5.2 Guide](https://developers.openai.com/api/docs/guides/latest-model/) 原文："Chat Completions API uses: `reasoning_effort`" | 撤回 |
| 6 | OpenAI enabled=false 需要 override | 非 API 事實，是本專案 `OpenAIConfig.validate_reasoning()` 規則 | `src/chat_agent/core/schema.py` | 有效 |
| 7 | Gemini auth 只有 URL parameter | 也支援 `x-goog-api-key` header | [Gemini Thinking](https://ai.google.dev/gemini-api/docs/thinking) 範例 | 有效 |
| 8 | OpenRouter effort + max_tokens 時 effort 優先 | 非官方保證，本專案自定 precedence | [Reasoning Tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens) | 有效 |
