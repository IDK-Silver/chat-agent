# GitHub Copilot Premium Request 與 copilot-agent-hint 機制

## 背景：GitHub Copilot 的計費模型

GitHub Copilot 對 API 請求分兩類計費：
- **Premium request**：用戶主動發起的請求，消耗付費額度
- **Non-premium request**：AI agent 自動發起的後續請求（如工具呼叫後的第二輪推理），不消耗額度

這個分類的意義在於：一次用戶提問可能觸發多輪 LLM 呼叫（例如 agent 先呼叫工具，再根據結果回答），GitHub 認為只有第一次應該算用戶請求，後續是 agent 行為。

## forked `copilot-api`：本地 proxy 的角色

本專案目前使用外部 fork 的 Node.js `copilot-api`（由 `cfgs/supervisor.yaml` 啟動）作為本地 proxy。

判定 `X-Initiator` 的邏輯由 forked `copilot-api` 負責（檢查 messages 中是否有 `assistant` 或 `tool` role），以維持 Copilot premium request 省費機制。

proxy 在轉發請求到 GitHub 時，會設定 `X-Initiator` header 告知 GitHub 這個請求的性質：
- `X-Initiator: user` → GitHub 計為 premium request
- `X-Initiator: agent` → GitHub 不計費

**判定邏輯：** proxy 檢查請求中的 messages 陣列，如果包含 `assistant` 或 `tool` role 的 message，就判定為 `agent`（代表這是一個多輪對話中的後續請求）；如果只有 `system` + `user`，則判定為 `user`。

## 問題：sub-agent 每次都被當成新對話

在我們的架構中，除了主要的 brain agent（負責跟用戶對話）之外，還有多個 sub-agent 各自負責不同功能（記憶搜尋、圖片辨識、GUI 自動化、內容審查等）。

Brain agent 的對話是持續的：第一輪是 `[system, user]`，第二輪起帶有歷史訊息 `[system, user, assistant, user, ...]`，所以只有第一輪被判 `user`，後續自然變成 `agent`。

但 sub-agent 每次都是獨立的一次性對話，messages 永遠是 `[system, user]`，所以**每次呼叫都被判定為 `user`，每次都扣 premium**。一輪用戶對話可能觸發 3-5 個 sub-agent 呼叫，造成額度快速消耗。

## 解法：`features.copilot_agent_hint`

開啟這個 feature flag 後，sub-agent 使用的 `CopilotClient` 會在送出 messages 前，自動在 system message 後插入一條 `{"role": "assistant", "content": "."}`。

proxy 看到 messages 裡有 `assistant` role，就會設 `X-Initiator: agent`，sub-agent 的請求不再消耗 premium 額度。

Brain agent 不受影響，維持自然行為（第一輪仍為 `user` initiator）。`copilot_agent_hint` 僅用於 sub-agent 的一次性 LLM 呼叫。

## 相關程式碼

| 檔案 | 說明 |
|------|------|
| `src/chat_agent/core/schema.py` | `FeaturesConfig.copilot_agent_hint` 定義 |
| `src/chat_agent/llm/providers/copilot.py` | `CopilotClient._convert_messages()` 注入邏輯 |
| `src/chat_agent/cli/app.py` | 讀取 flag，於組裝層以 provider-aware routing 只對 Copilot sub-agent 傳 `force_agent=True` |
| `src/chat_agent/llm/factory.py` | provider-agnostic passthrough（不做 Copilot 特判） |
| `../copilot-api`（外部 fork） | `X-Initiator` 判定與 Copilot headers 組裝 |
| `cfgs/agent.yaml` | `features.copilot_agent_hint: true` |
| `cfgs/supervisor.yaml` | 啟動本地 `copilot-api` process（port `4141`） |
