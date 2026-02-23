# 自主喚醒系統（Heartbeat + Scheduled Actions）

> v0.47.0 新增

## 概述

Agent 不再是純被動（等待訊息才動）。透過時間鎖機制，agent 可以：

1. **系統心跳**：隨機間隔自動喚醒，檢查記憶決定是否行動
2. **自主排程**：透過 `schedule_action` tool 安排未來的喚醒（如提醒吃藥）

## 核心機制：`not_before` 時間鎖

`InboundMessage` 新增 `not_before: datetime | None` 欄位。

- `not_before=None` → 立即可取（現有行為）
- `not_before` 在未來 → 訊息寫入 `pending/` 但被鎖住，時間到了才釋放

### Queue 兩池架構

```
put(msg)
  ├─ not_before 在未來 → _delayed pool（in-memory list）
  └─ 否則 → _mem queue（PriorityQueue，立即可取）

promotion thread（每 60 秒）
  └─ _delayed 中到期的 → 移到 _mem
```

所有訊息（不論是否 delayed）都持久化到 `pending/*.json`。Crash recovery 在重啟時根據 `not_before` 重新路由。

## 系統心跳

### 生命週期

```
啟動 → 清除舊心跳
     ├─ `enqueue_startup: true`  → 塞立即 [STARTUP] heartbeat
     │                           → agent 醒來，看記憶，決定要不要說話
     │                           → turn 完成後自動塞下一個 [HEARTBEAT]
     └─ `enqueue_startup: false` → 不建立 startup heartbeat（預設）
```

### 設定

```yaml
# agent.yaml
heartbeat:
  enabled: true
  enqueue_startup: false  # 預設 false；true 才會啟動時立刻塞 [STARTUP]
  interval: "2h-5h"    # 隨機間隔範圍
```

### SchedulerAdapter

- `channel_name = "system"`，`priority = 5`
- `start()` 清舊心跳；`enqueue_startup=true` 時才塞 startup heartbeat
- 其餘方法皆為 no-op
- 遞迴邏輯在 `AgentCore._process_inbound()` — 成功處理 recurring 訊息後自動建下一個

### 心跳 metadata

```python
metadata = {
    "system": True,       # Agent 不可刪除
    "recurring": True,    # 處理完後自動建下一個
    "recur_spec": "2h-5h" # 隨機間隔範圍
}
```

## schedule_action Tool

Agent 透過此 tool 排程未來的喚醒：

| action | 參數 | 說明 |
|--------|------|------|
| `add` | `reason`, `trigger_spec` | 建立排程（`trigger_spec` 為本地時間 ISO datetime） |
| `list` | - | 列出所有待處理的系統訊息 |
| `remove` | `pending_id` | 刪除排程（系統心跳不可刪） |

Agent 排程的訊息 `priority=0`（最高），系統心跳 `priority=5`。

## Brain Prompt

Agent 會收到三種 `[system]` 頻道訊息：

| 標籤 | 觸發 | 行為 |
|------|------|------|
| `[STARTUP]` | 系統啟動 | 檢查記憶，適當時打招呼 |
| `[HEARTBEAT]` | 隨機間隔 | 檢查記憶，有事做就做，沒事安靜 |
| `[SCHEDULED]` | agent 自排 | 按 reason 行動 |

## 靜默心跳清除（Silent Heartbeat Eviction）

> v0.51.0 新增

短間隔心跳（如 3m-5m）會快速佔滿 `preserve_turns` 的全部 turn 欄位，推走使用者對話歷史。

**機制**：心跳 turn 完成後，若 agent 沒有呼叫 `send_message`，整個 turn 從 in-memory 對話中移除。

- Session JSONL 保留完整記錄（歸檔用途）
- 記憶編輯、排程動作已持久化到磁碟，不受影響

**判定條件**（兩者同時滿足 → 清除）：
1. `msg.metadata["system"] == True`
2. `turn_context.sent_hashes` 為空

實作位於 `AgentCore._process_inbound()` 的 `finally` block。

## 排程 no-op 清除（Scheduled No-op Eviction）

`[SCHEDULED]` turn 不再一律保留在 in-memory conversation。若該 turn 完成後沒有任何可觀察且持久的主 turn 副作用，會從 ctx 清除，避免侵蝕 `preserve_turns`。

**保留條件**（任一成立就保留）：
1. 有 `send_message`（對外輸出）
2. `schedule_action add/remove` 成功（排程狀態變更）
3. `memory_edit` 結果中至少一個 `applied[].status == "applied"`（實際記憶寫入）

**視為 no-op（可清除）範例**：
- 只有 `schedule_action list`
- `schedule_action add/remove` 失敗
- `memory_edit` 全部為 `noop` / `already_applied`

**注意**：
- `memory sync` side-channel 不納入此判定（因為不寫入主 conversation turn）
- Session JSONL 仍保留完整 turn 記錄

## 檔案清單

| 檔案 | 說明 |
|------|------|
| `src/chat_agent/agent/schema.py` | `InboundMessage.not_before` 欄位 |
| `src/chat_agent/agent/queue.py` | 延遲投遞（兩池 + promotion thread + scan/remove） |
| `src/chat_agent/agent/adapters/scheduler.py` | SchedulerAdapter + heartbeat 建立 |
| `src/chat_agent/tools/builtin/schedule_action.py` | schedule_action tool |
| `src/chat_agent/agent/core.py` | `_schedule_next_heartbeat()` + promotion lifecycle |
| `src/chat_agent/agent/turn_effects.py` | scheduled turn no-op / side-effect 判定 |
| `src/chat_agent/cli/app.py` | 啟動整合 |
| `src/chat_agent/core/schema.py` | `HeartbeatConfig` |
