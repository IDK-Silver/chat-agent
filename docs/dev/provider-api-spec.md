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

### 1. 上游歷史/逆向資訊

> **重要標示**：GitHub 目前沒有穩定、完整公開的「原生 Copilot 聊天 API」文件可直接對應本專案的使用方式。以下內容以歷史頁面、實測、以及 VS Code/Copilot proxy 逆向為主，不可視為官方穩定契約。

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| 上游 endpoint | `POST {endpoint}/chat/completions` | 歷史官方文件 + 實測 | 中 | 本專案 proxy 封裝此格式，不直接對外暴露 |
| 上游 request 形狀 | 接近 OpenAI Chat Completions（`messages`, `model`, `tools`） | 歷史官方文件 + 實測 | 中 | 僅代表 proxy 對 GitHub 上游的轉換目標 |
| Token 交換 | `GET https://api.github.com/copilot_internal/v2/token` with `Authorization: token {github_token}` | 逆向（VS Code/Copilot proxy 行為） | 中 | 非穩定契約 |
| Device Flow | `POST /login/device/code` + 輪詢 `POST /login/oauth/access_token` | GitHub 官方 OAuth device flow 文件 | 高 | GitHub OAuth app 需啟用 device flow |
| Copilot login client_id | `Iv1.b507a08c87ecfe98`, scope `read:user` | 逆向 | 中 | 本專案目前作為預設值；隨時可能變更 |
| VS Code / Chat 版本節奏 | GitHub Copilot Chat extension release 與最新 stable / 最新版 VS Code lockstep | Visual Studio Marketplace（官方） | 高 | `editor-version` / `editor-plugin-version` 不宜長期固定舊值 |
| Telemetry | Copilot Chat extension 會蒐集 usage / error 資料，並遵守 VS Code telemetry 設定 | Visual Studio Marketplace（官方） | 高 | 本專案 proxy **未**複製這層行為 |
| IDE Headers | `copilot-integration-id: vscode-chat`, `editor-version`, `editor-plugin-version`, `x-vscode-user-agent-library-version` | 逆向 | 低 | 偽裝 VS Code，版本號需維護 |
| 過舊版本風險 | 社群回報 gateway 可能因 `editor-version` / `editor-plugin-version` 過舊回 `410 Gone` | GitHub Community 討論 | 中 | 非官方契約，但代表版本漂移有實際風險 |
| `x-initiator` | `"user"` 視為 premium request；`"agent"` 視為 agent request | 逆向 | 中 | 計費語意非官方穩定文件 |
| `x-interaction-id` / `x-interaction-type` | 上游接受互動追蹤欄位 | 逆向 | 中 | 本專案直接顯式送出 |
| `reasoning_effort` | `/chat/completions` 使用頂層 `reasoning_effort` | 逆向 + 實測 | 中 | GitHub 未文件化此欄位 |
| Vision header | 需 `copilot-vision-request: true` | 逆向 | 中 | — |
| 非串流 max output | 16K tokens | 實測 | 中 | 可能隨 API 更新變動 |
| 串流 max output | 64K tokens | 實測 | 中 | 同上 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 |
|------|------|-----------|
| 對內 API | `CopilotClient` 只打本專案 native proxy `POST /chat` | `src/chat_agent/llm/providers/copilot.py` |
| 對內 request 格式 | `CopilotNativeRequest` 顯式攜帶 `initiator`, `interaction_id`, `interaction_type`, `request_id` | `src/chat_agent/llm/schema.py` |
| initiator 路由 | 由 `CopilotRuntime` 依 inbound 分類與同-turn request 次數決定；不再靠 message history 猜測 | `src/chat_agent/llm/providers/copilot_runtime.py` + `src/chat_agent/agent/core.py` |
| 本地登入 | `copilot-proxy login` 走 device flow，保存 GitHub token 到使用者設定目錄 | `src/copilot_proxy/auth.py` + `src/copilot_proxy/__main__.py` |
| serve token 來源 | 先讀 env，再 fallback 到 token store | `src/copilot_proxy/settings.py` |
| `reasoning` payload | proxy 轉上游時送頂層 `reasoning_effort` string（非 `reasoning` object） | `src/copilot_proxy/service.py` |
| Vision header 自動偵測 | request 含 image parts 時 proxy 自動加 `copilot-vision-request: true` | `src/copilot_proxy/service.py` |
| Proxy 不注入 reasoning 預設值 | 未提供 `reasoning_effort` 時不補值 | `src/copilot_proxy/service.py` |
| tools + reasoning 表現差異（觀測） | Copilot gateway 上 `reasoning_effort + tools` 可能有模型別差異；**本專案不做 adapter 自動特判** | `src/copilot_proxy/service.py`（無特判） |

---

## Claude Code

### 1. 官方 API 事實 / 逆向資訊

> **重要標示**：Claude Code 並沒有公開、穩定文件化的「訂閱憑證直接打 Anthropic API」契約。以下需區分：
> - `POST /v1/messages` 等 payload 形狀：以 **Anthropic Messages API 官方文件** 為準
> - Claude Code bearer token、必要 system prompt、beta headers：屬於 **逆向 / 實測資訊**

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| Upstream endpoint | `POST /v1/messages` | Anthropic 官方文件 | 高 | payload 形狀與一般 Anthropic Messages API 相同 |
| Tools format | `{name, description, input_schema}` | Anthropic 官方文件 | 高 | 與 Anthropic provider 相同 |
| Vision format | `image` block + `source.base64` | Anthropic 官方文件 | 高 | 與 Anthropic provider 相同 |
| Prompt caching | `cache_control` 標記 block，provider 依 prefix 命中 | Anthropic 官方文件 | 高 | breakpoint 位置必須穩定 |
| Auth header | `Authorization: Bearer <oauth access token>` | 逆向 / 實測 | 中 | 非 Anthropic API key，通常來自 Claude Code OAuth 憑證 |
| Required system prompt | 第一個 system text block 需為 `You are Claude Code, Anthropic's official CLI for Claude.` | 逆向 / 實測 | 中 | 社群 proxy 與實測都依賴此行為 |
| Beta headers | `claude-code-*`, `oauth-*`, `interleaved-thinking-*`, `fine-grained-tool-streaming-*` 等 beta header | 逆向 / 實測 | 低 | 非穩定契約，可能隨上游變動 |
| OAuth refresh endpoint | `POST https://console.anthropic.com/v1/oauth/token` | 逆向 / 實測 | 中 | 用 refresh token 換新 access token |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 |
|------|------|-----------|
| 對內 API | `ClaudeCodeClient` 只打本專案 native proxy `POST /v1/messages` | `src/chat_agent/llm/providers/claude_code.py` |
| Payload 保真 | client 保留 `system` block array、message block array、`cache_control`；**不壓平成單一 system string** | `src/chat_agent/llm/providers/claude_code.py` |
| Required prompt 注入 | proxy 在 upstream request 的第一個 system block 固定注入 Claude Code 必要 prompt；若已存在同內容首 block，則不重複注入 | `src/claude_code_proxy/service.py` |
| Browser OAuth login | `uv run claude-code-proxy login` 預設走 browser OAuth + 手動貼 `code#state`；成功後 token 存到本專案 token store | `src/claude_code_proxy/__main__.py` + `src/claude_code_proxy/auth.py` |
| Claude Code import login | `uv run claude-code-proxy login --from-claude-code` 明確匯入官方 Claude Code 現有登入狀態 | `src/claude_code_proxy/__main__.py` + `src/claude_code_proxy/auth.py` |
| Token 來源 | 先讀 env access token，再讀本專案 token store；只有明確啟用 fallback 時才讀 Claude Code credentials / Keychain；refresh 後只寫回本專案 token store | `src/claude_code_proxy/service.py` + `src/claude_code_proxy/auth.py` |
| Credentials 保護 | proxy **不修改** `~/.claude/.credentials.json`；只匯入 / refresh 後寫自己的 token store | `src/claude_code_proxy/auth.py` |
| Thinking payload | YAML 直接用 Claude Code `thinking` 物件：`type=adaptive|enabled|disabled`；`enabled` 時可選 `budget_tokens` | `src/chat_agent/core/schema.py` + `src/chat_agent/llm/providers/claude_code.py` |
| Effort payload | YAML 直接用 `output_config.effort`；client passthrough 成 upstream `output_config` | `src/chat_agent/core/schema.py` + `src/chat_agent/llm/providers/claude_code.py` |
| Effort beta header | proxy 依 request model / `output_config.effort` 動態補 `effort-2025-11-24`，不再只靠固定 header 清單 | `src/claude_code_proxy/service.py` |
| Prompt caching 開關 | app 層將 `claude_code` 列入 cache provider 白名單，讓 `ContextBuilder` 可下 BP1/BP2/BP3 | `src/chat_agent/cli/app.py` + `src/chat_agent/context/builder.py` |
| Availability 錯誤處理 | `HTTP 429` 與 `HTTP 529 overloaded` 都視為 availability/transient failure，走 retry / failover；不歸類成 request-format | `src/chat_agent/llm/retry.py` + `src/chat_agent/llm/failover.py` + `src/chat_agent/agent/core.py` |
| Structured outputs | `response_schema` 目前不支援；client 早停報錯，不做 silent ignore | `src/chat_agent/llm/providers/claude_code.py` |

### 3. 實測 / 社群維護風險

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| 社群 proxy bug：quota hit 卡住 | `horselock/claude-code-proxy` open issue #7 回報 hit limit 後 client 可能卡住 | GitHub issue | 中 | 代表上游錯誤型態要保守 passthrough |
| 社群 proxy schema bug | open PR #12 修 `system` 為 string 時包裝成無效 array 的問題 | GitHub PR | 中 | 代表不能依賴社群 main branch 當規格 |
| 社群 proxy client-specific patch 持續增加 | open PR #10/#11 持續新增 Haiku / TypingMind 特判 | GitHub PR | 中 | 本專案目前不跟進 client-specific 功能旗標 |

---

## Codex

### 1. 官方 API 事實 / 逆向資訊

> **重要標示**：這裡說的 `codex` provider，不是 OpenAI Platform API key 直接打 `api.openai.com`。本專案走的是 **ChatGPT / Codex CLI OAuth**，再由本地 proxy 轉送到 ChatGPT Codex backend。`chatgpt.com/backend-api/codex/responses` 與必要 headers 屬於逆向 / 實測資訊，不是官方穩定契約。

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| 官方登入入口 | `codex login` 支援 ChatGPT 帳號登入 | OpenAI 官方文件 | 高 | 本專案另外補自己的 browser OAuth flow |
| 官方 CLI auth 檔 | `~/.codex/auth.json` 會保存 ChatGPT OAuth 狀態 | 官方 CLI 行為 + 本機實測 | 高 | 本專案只讀，不直接改這個檔 |
| ChatGPT OAuth token 不能直打 Platform model API | `GET https://api.openai.com/v1/models/gpt-5.2-codex` 會回 `403` 缺 `api.model.read` | 本機實測 | 高 | 代表這不是 Platform API token |
| ChatGPT Codex backend endpoint | `POST https://chatgpt.com/backend-api/codex/responses` | 逆向 + 本機實測 | 中 | 非官方穩定契約 |
| 必要 headers | `Authorization`, `chatgpt-account-id`, `OpenAI-Beta: responses=experimental`, `originator: codex_cli_rs` | 逆向 + 本機實測 | 中 | 缺少時可能驗證失敗 |
| OAuth backend 已驗證可用模型 | `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark`, `gpt-5.2` 都能透過本專案 OAuth proxy 正常回應 | 本機實測 | 中 | 2026-04-11 測試；是否放行仍可能依帳號變動 |
| prompt cache key | 官方開源 CLI 會送 `prompt_cache_key`，值預設是 conversation id | 官方開源程式碼 | 高 | `openai/codex` `codex-rs/core/src/client.rs` |
| account id 來源 | access token JWT claim `https://api.openai.com/auth.chatgpt_account_id` | 本機實測 | 高 | 本專案從 JWT 解出來 |
| refresh endpoint | `POST https://auth.openai.com/oauth/token` with `grant_type=refresh_token` | 社群 proxy + 本機實測 | 中 | 目前可用，但非正式文件化 |
| tool calling 事件 | SSE 會發 `response.output_item.done`、`response.function_call_arguments.delta/done` | 本機實測 | 中 | `response.completed.response.output` 可能是空陣列 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 |
|------|------|-----------|
| 對內 API | `CodexClient` 只打本專案 native proxy `POST /chat` | `src/chat_agent/llm/providers/codex.py` |
| 對內 request 格式 | `CodexNativeRequest` 顯式攜帶 `messages`, `tools`, `response_schema`, `reasoning_effort` | `src/chat_agent/llm/schema.py` |
| 本地登入 | `uv run codex-proxy login` 預設走 browser OAuth；`uv run codex-proxy login --from-codex` 才匯入官方 `~/.codex/auth.json` | `src/codex_proxy/__main__.py` + `src/codex_proxy/auth.py` |
| Browser OAuth callback | 本地 callback server 固定等 `http://localhost:1455/auth/callback` | `src/codex_proxy/auth.py` |
| Token 來源順序 | 先讀 `CODEX_PROXY_ACCESS_TOKEN`，再讀本專案 token store；只有顯式啟用 fallback 時才讀 `~/.codex/auth.json`；refresh 後只寫本專案 token store | `src/codex_proxy/service.py` + `src/codex_proxy/auth.py` |
| 上游 endpoint | proxy 固定轉送到 `/codex/responses` | `src/codex_proxy/service.py` |
| `max_output_tokens` 處理 | native request 保留欄位，但 proxy 不會轉送到 ChatGPT backend，因為實測會回 `400 Unsupported parameter: max_output_tokens` | `src/codex_proxy/service.py` |
| Prompt cache key | app 組裝層對 `codex` 產生 request-level `prompt_cache_key`；key 基底跟官方 CLI 一樣用 session / conversation 概念，再額外加 agent namespace 與本地 TTL bucket，讓 `agent.yaml` 的 `cache.ttl` 不會被 silent ignore | `src/chat_agent/cli/app.py` + `src/chat_agent/llm/providers/codex.py` + `src/codex_proxy/service.py` |
| `cache.ttl` 語意 | 對 `codex` 而言，`cache.ttl` 目前代表**本地 prompt cache key 旋轉週期**，不是 upstream 明文 TTL 參數：`ephemeral`=5 分鐘、`1h`=1 小時、`24h`=1 天 | `src/chat_agent/cli/app.py` | upstream request 目前只看到 `prompt_cache_key`，沒看到公開 TTL 欄位 |
| System prompt 處理 | 所有 `system` message 先合併成 `instructions`，不送進 `input[]` | `src/codex_proxy/service.py` |
| Tool history 映射 | assistant `tool_calls[]` -> `function_call`；tool result -> `function_call_output` | `src/codex_proxy/service.py` |
| 圖片 tool result 映射 | tool result 裡的 image parts 會改成緊接著的 user `input_image` message | `src/codex_proxy/service.py` |
| Reasoning payload | proxy 送 Responses-style `reasoning: {"effort": "...", "summary": "auto"}` | `src/codex_proxy/service.py` |
| Structured outputs | `response_schema` 映射到 `text.format = {type: "json_schema", ...}` | `src/codex_proxy/service.py` |
| 回應解析 | 以 SSE `response.output_item.done` 為主還原 content / tool_calls；不依賴 `response.completed.response.output` 一定有值 | `src/codex_proxy/service.py` |

### 3. 社群參考來源

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| OAuth / backend headers 參考 | `insightflo/chatgpt-codex-proxy` | GitHub repo | 中 | `src/auth.ts`, `src/codex/client.ts` |
| OpenAI / Anthropic / Codex 格式轉換參考 | `icebear0828/codex-proxy` | GitHub repo | 中 | `src/translation/*` |
| 官方 prompt cache 行為參考 | `openai/codex` | GitHub repo | 高 | `codex-rs/core/src/client.rs`, `codex-rs/core/tests/suite/prompt_caching.rs` |
| 本專案程式註解 | reverse-engineered 常數與 header 附上原 repo 註解 | 本專案規則 | 高 | 方便日後追查來源 |

### 4. 目前實測狀態

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| `prompt_cache_key` transport | 本專案已實際送出 `prompt_cache_key`，上游正常接受，不報 request-format error | 本機實測 | 高 | 2026-04-11 |
| cache hit 觀測 | 以固定 `prompt_cache_key` 重送同 prompt，目前仍未觀測到 `cached_tokens > 0` | 本機實測 | 中 | 代表接線已通，但 upstream 命中條件還沒摸清楚 |

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
| max_tokens | 可選；GPT-5+ 需改用 `max_completion_tokens` | 官方文件 | 同上 | 高 | 是（GPT-5+ 拒絕 `max_tokens`） |
| **max_completion_tokens** | GPT-5+ 必用（替代 `max_tokens`） | 官方文件 | 同上 | 高 | 是（GPT-5+） |
| temperature | 可選 | 官方文件 | 同上 | 高 | 否 |
| **Prompt caching** | 自動 prefix-based，≥1024 tokens，128-token 遞增 | 官方文件 | [Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching) | 高 | 否 |
| **prompt_cache_retention** | `"in_memory"`（預設，≤1h）或 `"24h"`（extended） | 官方文件 | 同上 | 高 | 是（24h 僅 GPT-5+, GPT-4.1） |
| Cache 計費 | write 免費；read 50% off input（LiteLLM: 10% input rate） | 官方文件 | [Pricing](https://openai.com/api/pricing/) | 高 | 否 |
| Cache usage 回傳 | `prompt_tokens_details.cached_tokens`；`cache_write_tokens` 不回傳（永遠 0） | 官方文件 + 實測 | 同上 | 高 | 否 |
| `cache_control` 容忍 | content block 上的 Anthropic 式 `cache_control` 被 silent ignore，不報錯 | 實測 | `scripts/verify_openai_cache.py` | 中 | 否 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| 送 `reasoning_effort` 頂層欄位 | `OpenAICompatibleClient` 送 `reasoning_effort` | `src/chat_agent/llm/providers/openai_compat.py` | 符合 Chat Completions API 官方格式 |
| `enabled=false` 需要 override | 驗證要求有 `provider_overrides.openai_reasoning_effort` | `src/chat_agent/core/schema.py`（`OpenAIConfig.validate_reasoning()`） | 本專案規則，非 API 限制 |
| `max_tokens` 在 reasoning 裡擋掉 | OpenAI provider schema 不提供 reasoning.max_tokens 欄位 | `src/chat_agent/core/schema.py`（`OpenAIReasoningConfig`） | 本專案規則 |
| `max_completion_tokens` 切換 | `OpenAIConfig.use_max_completion_tokens=true` 時，client 送 `max_completion_tokens` 並 null 掉 `max_tokens` | `src/chat_agent/llm/providers/openai.py` + `src/chat_agent/core/schema.py` | GPT-5+ 必要 |
| `prompt_cache_retention` passthrough | agent cache config `ttl: "24h"` 時，組裝層傳入 `prompt_cache_retention="24h"` 給 `OpenAIClient` | `src/chat_agent/cli/app.py` + `src/chat_agent/llm/providers/openai.py` | 不走 breakpoint path |
| Cache TTL clamp | 組裝層依 provider 最大支援 TTL 做 clamp：OpenRouter `1h`、Anthropic/ClaudeCode `ephemeral`、OpenAI `24h` | `src/chat_agent/cli/app.py` | 避免 provider 切換時 silent misconfiguration |

### 3. 逆向/實測資訊

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| GPT-5.1 cache hit rate | 同 prefix 第二次呼叫 99.2% hit；跨 turn prefix 穩定 98.5%+ | 實測 | 高 | `scripts/verify_openai_cache.py` |
| `cache_write_tokens` 永遠 0 | OpenAI 不回傳 write tokens | 實測 | 高 | 成本公式 `base = prompt - read - write` 對 OpenAI 成立（write=0） |

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
| **Verbosity** | `verbosity: "low"\|"medium"\|"high"\|"max"`；Anthropic 路由會映射到 `output_config.effort` | 官方文件 | [API Parameters](https://openrouter.ai/docs/api/reference/parameters) | 高 | `max` 支援度依模型而異 |
| Precedence | effort + max_tokens 互斥（"One of the following, not both"） | 官方文件 | 同上 | 高 | — |
| Provider routing | `provider: {"order": [...], "allow_fallbacks": bool}` | 官方文件 | [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection) | 高 | 依模型可用 endpoint |
| Tools | OpenAI function calling format | 官方文件 | [API Overview](https://openrouter.ai/docs/api/reference/overview) | 高 | 否 |
| Prompt caching | `cache_control: {"type": "ephemeral", "ttl": "1h"}` on content parts | 官方文件 | [Prompt Caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching) | 高 | Claude 專用 TTL |
| Provider sticky routing | Cache hit 後自動路由到相同 provider endpoint | 官方文件 | 同上 | 高 | 否 |
| Claude 4.6 adaptive thinking | `reasoning: {"enabled": true}` 會走 adaptive thinking；`reasoning.max_tokens` 才切回 budget-based thinking | 官方文件 | [Claude 4.6 Migration Guide](https://openrouter.ai/docs/guides/guides/model-migrations/claude-4-6) | 高 | 僅 Claude Opus 4.6 / Sonnet 4.6 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| effort / max_tokens 互斥 | config 層驗證，同時設定 → ValueError | `src/chat_agent/core/schema.py`（`OpenRouterConfig.validate_reasoning()`） | 符合官方 API 限制 |
| `enabled=False` -> `{"effort": "none"}` | 映射 | `src/chat_agent/llm/providers/openrouter.py` | 符合官方語意 |
| `enabled=True` 單獨保留 | 只設 `enabled=true` 時送 `{"enabled": true}` | `src/chat_agent/llm/providers/openrouter.py` | 讓 Claude 4.6 可顯式走 adaptive thinking |
| `verbosity` passthrough | YAML `verbosity` 由 `OpenRouterClient` 在 provider 層補到 OpenRouter 頂層 `verbosity` | `src/chat_agent/core/schema.py` + `src/chat_agent/llm/providers/openrouter.py` | Anthropic 路由會再映射到 `output_config.effort` |
| `provider_routing` payload | YAML `provider_routing` 映射到 request `provider` object；`null` 時不送 `provider`（走 OpenRouter 預設路由） | `src/chat_agent/core/schema.py` + `src/chat_agent/llm/providers/openrouter.py` + `src/chat_agent/llm/providers/openai_compat.py` | 允許各 profile 個別固定 endpoint 或回到預設 |
| Header 名稱 | 同時送 `X-OpenRouter-Title` + `X-Title` | `openrouter.py` | 官方 header + alias 相容 |
| 連線參數 self-contained | `api_key_env`/`base_url`/`site_url` 在每個 LLM YAML；`site_name` null 時 fallback 到 agent name；`site_url` 在 `load_config()` 自動附加 `/{agent_name}`（可用 `agents.*.openrouter.site_url` 覆蓋） | `src/chat_agent/core/config.py`（`load_config()`） | YAML 可獨立使用（validate_llm.py 等） |
| Cache breakpoint 注入 | `ContextBuilder` BP1 (system prompt) + BP2 (boot files)，`cache_control` passthrough via `_convert_content_parts()`；所有 per-turn dynamic note（`current_local_time` / `[Timing Notice]` / message-time common ground）必須留在 latest turn，不得新增 system-tier message；僅 OpenRouter provider 啟用 | `src/chat_agent/context/builder.py` + `src/chat_agent/agent/responder.py` + `openai_compat.py` + `cli/app.py` | 成本最佳化：1h TTL for heartbeat；重建同一輪長 prompt 時，cache hit 應維持 >90% |

### 3. 逆向/實測資訊

無。

---

## Ollama

### 1. 官方 API 事實

| 項目 | 事實 | 來源類型 | 來源連結 | 可信度 | 模型/版本相關 |
|------|------|---------|---------|--------|-------------|
| Endpoint | `POST /api/chat`（native API） | 官方文件 | [Chat API](https://docs.ollama.com/api/chat) | 高 | 否 |
| Auth（本機 daemon） | 無 | 官方文件 | [Authentication](https://docs.ollama.com/api/authentication) | 高 | 否 |
| Cloud 模型經本機 daemon | 同一個本機 API 可直接 offload 到 Ollama cloud；官方範例使用 `:cloud` model tags | 官方文件 | [Cloud](https://docs.ollama.com/cloud) | 高 | 是 |
| Native tools | native `/api/chat` 支援 `tools` | 官方文件 | [Tool Calling](https://docs.ollama.com/capabilities/tool-calling) | 高 | 否 |
| Native structured outputs | native `/api/chat` 支援 `format`（JSON schema） | 官方文件 | [Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs) | 高 | 否 |
| Native vision | native `/api/chat` 的 message 支援 `images` | 官方文件 | [Vision](https://docs.ollama.com/capabilities/vision) | 高 | 是（vision models） |
| Native runtime options | native `/api/chat` 支援 `options`；`temperature`、`num_predict` 為官方 runtime parameters | 官方文件 | [Chat API](https://docs.ollama.com/api/chat) + [Modelfile](https://docs.ollama.com/modelfile) | 高 | 否 |
| **Native thinking** | `think` 參數（boolean 或 level string），在 native Ollama API | 官方文件 | [Thinking](https://docs.ollama.com/capabilities/thinking) | 高 | 是 |
| `think` 值 | 大部分: `true`/`false`；GPT-OSS: `"low"`/`"medium"`/`"high"` | 官方文件 | 同上 | 高 | 是 |
| Thinking 預設 | 支援 thinking 的模型預設啟用 | 官方文件 | 同上 | 高 | 是 |
| Thinking response | `message.thinking`（reasoning）+ `message.content`（answer） | 官方文件 | 同上 | 高 | 否 |
| Native usage 欄位 | non-streaming response 會回 `prompt_eval_count` / `eval_count` | 官方文件 | [Chat API](https://docs.ollama.com/api/chat) | 高 | 否 |

### 2. 本專案 adapter 規則

| 項目 | 規則 | 程式碼位置 | 備註 |
|------|------|-----------|------|
| 單一路徑 | 本專案 `ollama` provider 只走 native `/api/chat`，不混用 OpenAI-compat | `src/chat_agent/llm/providers/ollama_native.py` | 單一 concrete client 對應單一 API format |
| thinking YAML | 使用 `thinking.mode=toggle|effort`；toggle 映射到 `think: true/false`，effort 映射到 `think: "low"|"medium"|"high"` | `src/chat_agent/core/schema.py` + `src/chat_agent/llm/providers/ollama_native.py` | provider-specific config，不做假統一 |
| GPT-OSS 驗證 | `gpt-oss:*` 只允許 `thinking.mode=effort`；其他 Ollama profile 只允許 `toggle` | `src/chat_agent/core/schema.py` | 依官方目前 thinking 契約 |
| `max_tokens` 映射 | YAML `max_tokens` -> native `options.num_predict` | `src/chat_agent/llm/providers/ollama_native.py` | 本專案統一輸出 token cap 口徑 |
| `temperature` 映射 | YAML `temperature` -> native `options.temperature` | `src/chat_agent/llm/providers/ollama_native.py` | native 欄位名與 OpenAI compat 不同 |
| `response_schema` 映射 | `chat(..., response_schema=...)` -> native `format` JSON schema | `src/chat_agent/llm/providers/ollama_native.py` | 對齊 Ollama structured outputs |
| real tool-loop metadata round-trip | **真實 provider 回傳**的 assistant tool history 必須保留原始 `tool_calls[]` metadata；本專案在 unified `ToolCall` 上保存 `provider_roundtrip`，並 round-trip 回送至少 `id`、`function.index`、`thoughtSignature` 與其他未知欄位 | `src/chat_agent/llm/providers/ollama_native.py` + `src/chat_agent/llm/schema.py` | 維持 native `/api/chat` round-trip fidelity；不要再手動挑少數欄位 |
| synthetic tool history textification | `read_startup_context`、`_stage1_gather`、`_load_skill_prerequisite`、`_load_common_ground_at_message_time` 等 runtime synthetic tool pair 在 Ollama adapter 出口改寫成普通 `system` 文字訊息，不再以 native function-call history 送出 | `src/chat_agent/llm/providers/ollama_native.py` | 這些訊息是內部上下文注入，不是真實 provider tool loop；Gemini-backed Ollama 會對其做更嚴格驗證而回 `400` |
| real tool-loop thinking replay guard | 若**真實** assistant tool history 含 `thinking`，但對應 `tool_calls[]` 缺 `thoughtSignature`，adapter 會在 replay 時省略 `thinking`、只保留 tool_calls；否則 Gemini-backed Ollama 會回 `400` `"Function call is missing a thought_signature..."` | `src/chat_agent/llm/providers/ollama_native.py` | 實測確認上游可能回傳無 `thoughtSignature` 的真 tool call；此 guard 僅在 metadata 已壞掉時啟用 |
| token usage 回收 | `prompt_eval_count` / `eval_count` -> `LLMResponse.prompt_tokens` / `completion_tokens` / `total_tokens` | `src/chat_agent/llm/providers/ollama_native.py` | 供 soft limit / status bar 使用 |
| cloud profile 命名 | repo 內 curated profiles 一律使用 cloud-only 目錄命名與 cloud model ids | `cfgs/llm/ollama/` | 減少本地模型與 cloud 模型語意混淆 |
| API key 支援（cloud direct） | `api_key` / `api_key_env` 設定時，adapter 送 `Authorization: Bearer <key>`；未設定時不送 auth header（本機 daemon 預設） | `src/chat_agent/core/schema.py` + `src/chat_agent/core/config.py` + `src/chat_agent/llm/providers/ollama_native.py` | 讓同一個 provider 可接本機 daemon 或 `https://ollama.com` |

### 3. 逆向/實測資訊

| 項目 | 事實 | 來源類型 | 可信度 | 備註 |
|------|------|---------|--------|------|
| `ollama show` cloud capabilities（本專案當前 profile 集） | `kimi-k2.5:cloud`、`gemini-3-flash-preview` 具 vision；`glm-4.7:cloud`、`glm-5:cloud`、`gpt-oss:20b-cloud`、`minimax-m2.5:cloud` 不具 vision | 本機 `ollama show` 實測 | 中 | 用於 curated YAML 註解，不是通用 API 保證 |

---

## 差異總結表

| 項目 | Copilot | Claude Code | OpenAI | Anthropic | Gemini | OpenRouter | Ollama |
|------|---------|-------------|--------|-----------|--------|------------|--------|
| Endpoint | OpenAI compat（歷史/實測） | `/v1/messages`（Anthropic schema） | Chat Completions | `/v1/messages` | `generateContent` | OpenAI compat | native `/api/chat` |
| Reasoning 參數 | `reasoning_effort`（頂層，逆向/實測） | `thinking.type` + `output_config.effort` | `reasoning_effort`（頂層） | `thinking.type` + `output_config.effort` | `thinkingConfig` | `reasoning: {"effort":...}` | `think`（native） |
| Effort 值 | low/medium/high（實測） | low/medium/high/max（`output_config.effort`） | none/low/medium/high/xhigh | low/medium/high/max（output_config） | minimal/low/medium/high（依模型） | none/minimal/low/medium/high/xhigh | low/medium/high（GPT-OSS） |
| Token budget | 無 | `thinking.budget_tokens` | 無 | `thinking.budget_tokens` | `thinkingBudget` | `reasoning.max_tokens` | 無 |
| Vision | `image_url`（實測） | `image` block（base64） | `image_url` | `image` block（base64/url） | `inlineData`（base64） | `image_url` | 依模型 |
| Tools | OpenAI function（實測） | Anthropic `input_schema` | OpenAI function | Anthropic `input_schema` | Gemini `functionDeclarations` | OpenAI function | native `tools` |
| Auth | proxy 處理（逆向） | proxy 處理（Claude Code OAuth bearer，逆向） | Bearer token | Bearer/x-api-key + version | API key (header/query) | Bearer token | 本機 daemon 無 |
| max_tokens | 不需要（實測） | **必填** | 可選（GPT-5+ 用 `max_completion_tokens`） | **必填** | 可選（maxOutputTokens） | 可選 | `options.num_predict` |
| Prompt cache | 無 | Anthropic breakpoint | 自動 prefix（`prompt_cache_retention: "24h"`） | Anthropic breakpoint | 無 | `cache_control` breakpoint | 無 |

---

## Usage Token 回收（non-streaming）

本節描述本專案 runtime 對「回應 usage 欄位」的統一回收規則。

| Provider | API 是否可能回 usage | Adapter 是否回收 prompt/completion/total | Adapter 是否回收 cache read/write | 缺值策略 |
|---|---|---|---|---|
| OpenAI / OpenRouter / Copilot（OpenAI-compatible） | 是（視 gateway/模型） | 是 | 是（若有 prompt_tokens_details） | `usage=None` 時標記 unavailable |
| Ollama（native `/api/chat`） | 是（`prompt_eval_count` / `eval_count`） | 是 | 否 | 欄位缺失時標記 unavailable |
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
