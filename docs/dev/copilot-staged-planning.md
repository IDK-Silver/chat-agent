# Copilot Brain 三階段（看/想/做）流程

本文件說明在 **Copilot provider** 下，brain agent 的三階段流程與上下文邊界。

相關文件：
- `docs/dev/provider-api-spec.md`（Copilot adapter 規則與 reasoning/tool 相容性）
- `docs/dev/provider-architecture.md`（provider vs orchestration 邊界）

## 背景

在 Copilot gateway（`/chat/completions` 相容路徑）上，`tools + reasoning_effort` 的行為有**模型別差異**（非通用限制）：
- 部分模型（目前以 GPT-5 family 為已知問題族群）在帶 `tools` + `reasoning_effort` 時，可能降低/破壞 tool calling 行為
- 其他模型（例如部分 Claude）可能可正常同時使用 reasoning 與 tools

本專案目前不在 adapter 端做自動模型特判 workaround；由使用者自行選擇：
- 使用表現較穩定的模型（例如 Copilot 下可正常 `reasoning + tools` 的模型）
- 或改用 no-thinking / `reasoning.effort: null` 配置（避免在 tool loop 帶 reasoning）

因此本專案將問題拆成兩層：

1. **Brain orchestration（三階段流程）**
   - 作為品質/策略優化功能，讓 brain 在 Copilot 下可做到「先看資訊、再想、再做」

## Feature Flag

使用 `agent.yaml`：

```yaml
features:
  copilot_brain_staged_planning: true
```

規則：
- **僅 brain agent 生效**
- 只有 brain provider 是 `copilot` 時啟用
- 其他 provider 開啟此 flag 時為 no-op（退回舊流程）

## 三階段流程

### Stage 1: 看（資訊收集）

- 使用 `chat_with_tools(...)`
- 不額外處理 `reasoning_effort`；若使用已知表現不佳模型，建議 brain 使用 no-thinking 配置
- 僅允許 read-only 工具白名單（例如 `memory_search`, `read_file`, `get_channel_history`, `schedule_action(list)`）
- 禁止 `send_message` / `memory_edit` / 任何寫入或對外行動工具

此階段結果只存在本回合暫態，不寫入主對話 history。

### Stage 2: 想（規劃）

- 使用 `chat(...)`（不帶 tools）
- 可使用 `reasoning_effort`
- 讀取 Stage 1 收集結果，輸出結構化 JSON 計畫

此階段計畫：
- 會顯示在 TUI（供觀察與除錯）
- **不寫入 `Conversation`**
- **不寫入 session `messages.jsonl`**

### Stage 3: 做（執行）

- 使用既有 brain responder tool loop（`chat_with_tools(...)`）
- 將 Stage 2 計畫以 synthetic message overlay 注入上下文
- 不額外處理 `reasoning_effort`；若使用已知表現不佳模型，建議 brain 使用 no-thinking 配置
- host 端追蹤是否偏離計畫（guided strict），偏離時在 TUI 顯示 warning

## 上下文邊界（重要）

### 會進主對話 history

- 使用者輸入
- responder loop 的 assistant/tool 訊息（Stage 3）
- 最終 assistant 文字（若有）

### 不會進主對話 history

- Stage 1 暫態收集過程
- Stage 2 規劃內容（JSON plan）
- TUI 顯示用的 stage 記錄

這樣做是為了避免：
- 汙染後續 turn 上下文
- 增加 token 成本
- 讓過期計畫影響下一輪決策

## 失敗策略（第一版）

任一階段失敗（特別是 Stage 2 parse/schema 失敗）：
- 顯示 warning
- 退回舊的單段 brain responder tool loop（fail-open）

目標是先確保服務不中斷，再逐步提升規劃穩定度。
