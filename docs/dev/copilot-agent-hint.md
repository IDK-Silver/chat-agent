# GitHub Copilot Initiator Routing 與 Native Proxy

## 背景

GitHub Copilot 的請求會區分兩種 initiator：

- `user`: 視為使用者主動發起，會消耗 premium request
- `agent`: 視為 agent 在同一個工作流中的後續行為，不應重複計費

真正需要控制的不是 message shape，而是「這一發請求在目前 inbound turn 中扮演什麼角色」。

## 舊機制為何移除

舊的 `features.copilot_agent_hint` 會對 sub-agent 注入假的 `assistant` message，讓外部 `copilot-api` proxy 以 message history 猜出 `X-Initiator: agent`。

這個做法有兩個問題：

- 它依賴 message history 猜測，對 one-shot sub-agent 與各種 side-channel 不夠穩
- 它把計費路由偽裝成 prompt/message hack，不是正式契約

因此新版移除 `copilot_agent_hint`，改成明確的 initiator routing。

## 新架構

### 1. chat-agent 端負責分類 inbound

`AgentCore._process_inbound()` 進入一個 turn-scoped `CopilotRuntime` scope。

每個 inbound 先被分類成：

- `human_entry`: 此 inbound 允許一次 `initiator=user`
- `agent_entry`: 此 inbound 從頭到尾都必須是 `initiator=agent`

brain agent 在 `human_entry` inbound 中：

- 第一次 Copilot 請求送 `user`
- 同一個 inbound 之後所有請求都送 `agent`

sub-agent / one-shot client（memory search、memory editor、vision、GUI worker 等）固定使用 `always_agent` dispatch mode。

### 2. Native proxy 接受明確欄位

本專案不再對外暴露 OpenAI-compatible route；內部只打自家的 native proxy API：

```json
POST /chat
{
  "model": "gpt-5",
  "messages": [...],
  "tools": [...],
  "response_schema": {...},
  "max_tokens": 4096,
  "temperature": 0.2,
  "reasoning_effort": "medium",
  "initiator": "user",
  "interaction_id": "turn-uuid",
  "interaction_type": "conversation-agent",
  "request_id": "req-uuid"
}
```

proxy 再把這些欄位轉成 GitHub Copilot 上游需要的 headers，例如：

- `x-initiator`
- `x-interaction-id`
- `x-interaction-type`
- `x-request-id`

### 3. proxy 是獨立 executable

本地 proxy 由本專案自己的 `copilot-proxy` 執行檔提供，掛在 `cfgs/supervisor.yaml`，不再依賴外部 fork 的 Node.js `copilot-api`。

proxy 支援：

- `uv run copilot-proxy` 或 `uv run copilot-proxy serve`
- `uv run copilot-proxy login`

`login` 會走 GitHub device flow，拿到 GitHub access token 後存到使用者自己的設定目錄，而不是 repo：

- macOS: `~/Library/Application Support/chat-agent/copilot-proxy/github-token.json`
- Linux: `~/.config/chat-agent/copilot-proxy/github-token.json`
- Windows: `%APPDATA%/chat-agent/copilot-proxy/github-token.json`

可用 `COPILOT_PROXY_TOKEN_PATH` 覆蓋路徑。runtime 讀 token 的優先序是：

1. `COPILOT_PROXY_GITHUB_TOKEN`
2. `GH_TOKEN`
3. `GITHUB_TOKEN`
4. token store 檔案

## Inbound policy

runtime 有內建的 agent-only 安全規則，以下 inbound 永遠走 `agent`：

- `channel in {"system", "gui", "shell_task"}`
- `metadata.system == true`
- `pre_sleep_sync`
- `scheduled_reason`
- `turn_failure_requeue_count`
- `yield_reschedule_count`
- Discord review 類來源：`guild_review`、`guild_mention_review`

此外，`InboundMessage.metadata["copilot_entry"]` 可顯式指定：

- `"human"` -> `human_entry`
- `"agent"` -> `agent_entry`

`cfgs/agent.yaml` 的 `features.copilot.initiator_policy` 只負責 human-entry allowlist，不負責 provider payload：

```yaml
features:
  copilot:
    initiator_policy:
      use_default_human_entry_rules: false
      human_entry_rules:
        - channel: cli
        - channel: gmail
        - channel: line
        - channel: discord
          metadata_equals:
            source: dm_immediate
```

重點是 allowlist。沒有被允許的 inbound，預設都走 `agent_entry`。

## 相關程式碼

| 檔案 | 說明 |
|------|------|
| `src/chat_agent/llm/providers/copilot_runtime.py` | inbound 分類、turn-scoped request counter、initiator 決策 |
| `src/chat_agent/agent/core.py` | 以 inbound scope 包住整個 turn |
| `src/chat_agent/cli/app.py` | 在組裝層把 brain/sub-agent 對應到不同 dispatch mode |
| `src/chat_agent/llm/providers/copilot.py` | native proxy client，直接送 `/chat` |
| `src/chat_agent/copilot_proxy/service.py` | native request -> GitHub Copilot upstream payload / headers |
| `src/chat_agent/copilot_proxy/__main__.py` | `copilot-proxy` executable |
| `src/chat_agent/core/schema.py` | Copilot proxy config 與 initiator policy schema |
| `cfgs/agent.yaml` | app-level initiator policy |
| `cfgs/llm/copilot/*` | Copilot model profiles，`base_url` 指向 proxy root |
| `cfgs/supervisor.yaml` | 啟動 `copilot-proxy` process |
