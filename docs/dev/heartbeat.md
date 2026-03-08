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
     └─ `enqueue_startup: false` → 不建立立即 startup heartbeat（預設）
                                 → 但會 seed 一個 delayed [HEARTBEAT] 以啟動鏈
```

若本次啟動前剛發生 kernel upgrade，upgrade 摘要是否獨立注入由 `enqueue_upgrade_notice` 控制，預設 `true`：

- `enqueue_startup: true`：upgrade 摘要會直接取代一般 `[STARTUP]` 內容，仍只產生一個 startup turn
- `enqueue_startup: false` 且 `enqueue_upgrade_notice: true`：額外 enqueue 一則 one-shot upgrade notice，並另外 seed delayed `[HEARTBEAT]`
- `enqueue_upgrade_notice: false`：忽略 upgrade 摘要，不送任何升級通知

### 設定

```yaml
# agent.yaml
heartbeat:
  enabled: true
  enqueue_startup: false  # 預設 false；true 才會啟動時立刻塞 [STARTUP]
                          # false 時仍會 seed 一個 delayed [HEARTBEAT]
  enqueue_upgrade_notice: true  # 預設 true；是否獨立投遞 kernel 升級摘要
  interval: "2h-5h"    # 隨機間隔範圍
  quiet_hours:         # 可選：本地時間靜默時段（HH:MM-HH:MM）
    - "01:00-06:00"
```

`quiet_hours` 行為：
- 若心跳排程時間落在靜默時段內，會自動延後到該時段結束後再觸發
- `enqueue_startup: true` 的 startup heartbeat 也會套用同樣規則（不會硬闖 quiet hours）

### Pre-Sleep Sync（睡前記憶同步）

當心跳被推遲到 quiet_hours 結束後，系統額外排一個 30 分鐘後的 sync-only 訊息：

- 在 prompt cache TTL（1h）到期前觸發，sync 成本低
- 只跑 `memory sync side-channel`，不跑 brain responder
- `_turns_since_memory_sync == 0`（無累積未同步內容）時跳過，不呼叫 LLM
- metadata 為 `{"system": True, "pre_sleep_sync": True}`（無 `recurring`，不被 defer 影響）
- 啟動時和普通心跳一樣被清除（`system: True`）

觸發點：`_schedule_next_heartbeat()` 和 `_defer_pending_heartbeat()` 偵測到心跳被推遲時呼叫 `_maybe_schedule_pre_sleep_sync()`。

### SchedulerAdapter

- `channel_name = "system"`，`priority = 5`
- `start()` 清舊心跳；`enqueue_startup=true` 塞 startup heartbeat，否則 seed delayed heartbeat
- kernel upgrade 摘要是否獨立送出由 `enqueue_upgrade_notice` 決定；開啟時，startup disabled 也會 enqueue 一則獨立通知
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
| `add` | `reason`, `trigger_spec` | 建立排程（`trigger_spec` 為本地時間 ISO datetime；本地時間 = `agent.yaml` 的 `timezone` 設定） |
| `list` | - | 列出所有待處理的系統訊息 |
| `remove` | `pending_id` | 刪除排程（系統心跳不可刪） |

Agent 排程的訊息 `priority=0`（最高），系統心跳 `priority=5`。

> Humanized Follow-up Policy V1（Prompt-first）目前只調整 prompt 與策略規則，**不修改** `schedule_action` API。
> 目前沒有工具層 jitter 參數；軟性追蹤時間自然度先由 prompt 規則與範例引導。

## Brain Prompt

Agent 會收到三種 `[system]` 頻道訊息：

| 標籤 | 觸發 | 行為 |
|------|------|------|
| `[STARTUP]` | 系統啟動 | 檢查記憶，適當時打招呼 |
| `[HEARTBEAT]` | 隨機間隔 | 檢查記憶，有事做就做，沒事安靜 |
| `[SCHEDULED]` | agent 自排 | 按 reason 行動 |

### 共用決策原則（`[HEARTBEAT]` / `[SCHEDULED]`）

收到 `[HEARTBEAT]` 或 `[SCHEDULED]` 時，先做跟進決策，而不是直接等同於送訊。合法結果只有三種：

1. `send_message`：現在提醒/關心有實際價值（有新資訊、可執行、時限逼近）
2. `schedule_action`：現在不適合送訊，但應重排到更合理時間
3. `silent wait`：本輪不送訊，且可合理確定該主題不會被遺忘（例如已有排程覆蓋、屬於 `long-term.md` 持續規則、或稍後 heartbeat 會自然再評估）

重點：

- 保留責任感：不可無理由逃避決策
- 避免機械式：責任感不等於每次都送訊

### Actionability 與 Blocked State

若 agent 已知使用者暫時無法完成某動作（例如藥在宿舍、人還在外面），視為 **blocked state**。

blocked state 下的原則：

1. 不要重複催同一個目前做不到的最終動作
2. 優先問 blocker 狀態、重排更合理時間，或在有保障不會遺忘時 `silent wait`
3. blocker 解除後，再回到最終動作追蹤

### Topic Cooldown（定性規則）

對同一聯絡人、同一主題的追問，短時間內避免重複。V1 採定性規則，不在 prompt 寫死分鐘數。

典型判斷：

1. 最近一兩輪才剛追問過同主題
2. 且沒有新資訊、沒有時限逼近、用戶也沒再主動提起
3. 優先不要再追問同主題（可換主題關心、重排、或 `silent wait`）

可突破 cooldown 的常見情況：

- 有新資訊進來
- 時限逼近
- 用戶再次主動提起該主題
- 上次問的是 blocker，現在 blocker 可能已解除

### Hard Reminder vs Soft Follow-up（策略層概念）

- **Hard Reminder（精準時間）**：固定時點提醒（如 12:00 吃藥、15:00 會議）。可用精準時間，不需刻意避免整齊分鐘。
- **Soft Follow-up（狀態追蹤）**：回報追問、進度跟進、blocker 跟進。優先考慮可執行性與自然時機，避免習慣性排成整齊倍數時間。

> V1 先用 prompt 規則與範例改善 soft follow-up 的時間選擇；若仍常出現整齊時間，再評估工具層 jitter。

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

## 非心跳 Turn 後延遲心跳（Heartbeat Deferral）

> v0.59.0 新增

當 agent 處理非心跳的 inbound 訊息（Discord、Gmail、scheduled action 等）成功完成後，
pending 的系統心跳會被自動推遲。

**原因**：agent 剛完成一次活躍的 turn，短時間內不需要自主喚醒。

**機制**：在 `_process_inbound()` 的 `finally` block 中，若 turn 成功且非 recurring：
1. 掃描 `pending/` 中的系統心跳（`metadata.system=True` + `metadata.recurring=True`）
2. 刪除舊心跳
3. 以相同 `recur_spec` 重新建立一個新心跳（`not_before = now + random_delay(recur_spec)`）

等同於心跳計時器從 turn 結束時重新開始。`recur_spec` 來源為心跳 metadata，最終源自 `HeartbeatConfig.interval`。

**不觸發延遲的情況**：
- Recurring 訊息（心跳本身）→ 走 `_schedule_next_heartbeat()` 路徑
- Turn 失敗（`completed=False`）→ 不做 heartbeat defer；若有開啟 failed inbound requeue，原訊息會依 queue delay 設定重新入列
- 無 pending 心跳 → no-op

實作位於 `AgentCore._defer_pending_heartbeat()`。

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
