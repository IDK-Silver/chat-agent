# Common Ground 時間錨點（V1）

修復「時間旅行解讀」：Agent 在處理較早到達、較晚處理的訊息時，不可用後來才共享的內容重解讀使用者當時的話。

## 背景

在多 channel（例如 Gmail + Discord）共用同一個 conversation 的架構下，Agent 可能在處理某則 Discord 訊息前先得知新的 Gmail 資訊，導致使用者較早說出的模糊指代（例如「我全部都要」）被錯綁到後來才出現或後來才共享的方案。

這不是單純的 queue 優先級問題，而是「使用者訊息的解讀時間點」缺少共同認知錨點。

## 設計決策

### 共同認知版本（shared_rev）+ 訊息錨點（anchor_shared_rev）

- **選擇**：每個對話 scope 維護 `shared_rev`，入站訊息 enqueue 時寫入 `anchor_shared_rev`
- **原因**：鎖定「這則訊息送出當下，對方已被告知到哪裡」
- **替代方案**：只看全域最新上下文（會發生時間旅行解讀）

### Common Ground 注入方式

- **選擇**：synthetic assistant + tool pair（假的 tool 呼叫與 tool result）
- **原因**：保持與現有 boot context 一致，且避免 Anthropic provider 的多 system message 覆蓋問題
- **替代方案**：追加 system message（Anthropic 只保留最後一個 system，風險高）

### 觸發條件

- **選擇**：`anchor_shared_rev != turn_start_current_shared_rev` 才注入
- **原因**：僅在這則訊息等待期間該 scope 共同認知變動時才需要補充
- **替代方案**：每回合都注入（浪費 context window）

### 不做 regex / 關鍵字偵測

- **選擇**：不硬編碼「全部 / 那個 / 剛剛那個」
- **原因**：讓 LLM 依 common-ground 區塊自行解釋模糊指代，場景通用
- **替代方案**：regex 偵測（脆弱且語言綁定）

### shared_state 定位

- **選擇**：`shared_state.json` 視為 cache（可刪除、可 best-effort 重建）
- **原因**：避免更動 `messages.jsonl` schema，降低 migration 成本
- **替代方案**：直接改 session schema（風險較高）

## 檔案結構

```text
src/chat_agent/agent/
├── scope.py                    # scope_id 計算規則（inbound/outbound/replay）
├── shared_state.py             # shared_state cache + common-ground synthetic pair builder
├── shared_state_replay.py      # cache miss 時由 session replay 重建（best effort）
└── core.py                     # enqueue 蓋 anchor + run_turn synthetic common-ground 注入

src/chat_agent/tools/builtin/
└── send_message.py             # send 成功後更新 shared_state（shared_rev +1）

src/chat_agent/context/
└── conversation.py             # Conversation.add(metadata=...) 向後相容擴充

src/chat_agent/core/
└── schema.py                   # context.common_ground config schema

src/chat_agent/cli/
└── app.py                      # 啟動載入/重建 shared_state cache 並注入 AgentCore / send_message

src/chat_agent/workspace/templates/kernel/agents/brain/prompts/
└── system.md                   # common-ground 解讀規則（repo 模板）
```

## 技術設計

### Scope 與 Shared State

- `scope_id` 以「對話作用域」為單位（例如 `discord:dm:<user_id>`、`gmail:thread:<thread_id>`）
- `shared_rev` 只在 `send_message` 成功後遞增
- `anchor_shared_rev` 在 `AgentCore.enqueue(...)` 蓋章（寫入 `InboundMessage.metadata`）

### run_turn 的 common-ground 決策

1. `builder.build(conversation)` 建出本回合 messages
2. 從 inbound metadata 讀 `scope_id` 與 `anchor_shared_rev`
3. 讀 `turn_start_current_shared_rev`
4. 若 `anchor != turn_start_current`：
   - 從 `shared_state` 取 `rev <= anchor` 的共享訊息
   - 生成 synthetic assistant+tool pair（假的 `_load_common_ground_at_message_time`）
   - 插在「最新 user message 之前」注入本回合 local messages（不寫回 `Conversation`）
   - 原因：這份 common-ground 描述的是使用者發訊當下已存在的共同認知，不應在 request 中出現在最新 user 之後形成 post-user synthetic tool history
5. `_run_responder` 每輪重建 messages 時都重複注入同一份 synthetic pair（anchor 不變）

### Resume / Cache 重建

- state 檔：`{agent_os_dir}/state/shared_state.json`
- 啟動時：
  1. 嘗試載入 cache
  2. cache 缺失或損壞時（若 config 允許）從 `session/brain/*/messages.jsonl` replay 重建
- replay 只回放成功 `send_message`（tool result 以 `OK: sent` 開頭）

## 步驟

1. 新增 `scope.py` 與 `shared_state.py`
2. 新增 `shared_state_replay.py`（cache miss fallback）
3. `AgentCore.enqueue` 蓋 `scope_id / anchor_shared_rev`
4. `send_message` 成功後更新 `shared_state`
5. `run_turn` / `_run_responder` 注入 synthetic common-ground pair
6. 新增 `context.common_ground` config 與 `cfgs/agent.yaml`
7. 更新 brain prompt 模板規則（repo template）
8. 補測試與回歸案例

## 驗證

- `scope` 單元測試：Discord/Gmail scope 判定
- `shared_state` 單元測試：rev 遞增、common-ground 篩選與 synthetic pair 格式
- `send_message` 測試：成功更新 shared_state，dedup 不重複更新
- `AgentCore.enqueue` 測試：會蓋 `scope_id / anchor_shared_rev`
- responder overlay 測試：重建 messages 後仍重複注入 synthetic pair
- replay 測試：只回放成功 `send_message`

## 完成條件

- [x] 入站訊息 enqueue 時會蓋 `scope_id` 與 `anchor_shared_rev`
- [x] `send_message` 成功後會更新 shared_state cache（`shared_rev` 前進）
- [x] `anchor != turn_start_current` 時自動注入 common-ground synthetic tool pair
- [x] common-ground 注入不寫回 `Conversation`
- [x] repo brain prompt 模板加入 common-ground 行為規則
- [x] `cfgs/agent.yaml` 新增 `context.common_ground` 設定
- [x] cache 缺失時可 best-effort replay sessions 重建 shared_state
