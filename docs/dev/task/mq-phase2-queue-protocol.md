# Phase 2：Message Queue + Channel Protocol

加入 PriorityQueue、訊息 schema、ChannelAdapter protocol，CLI 改用 queue 對接 Agent Core。

## 背景

詳見設計文件 [message-queue.md](message-queue.md)。

### 前提

- **Phase 1 完成**：Agent Core 已從 `cli/app.py` 獨立到 `agent/core.py`
- **Reviewer + Shutdown 已移除**（[remove-reviewer-shutdown.md](remove-reviewer-shutdown.md)）：AgentCore 不再有 post-review retry loop 和 shutdown agent。`run_turn()` 流程簡化為 responder → memory sync → finalize response。`graceful_exit()` 不再呼叫 LLM。AgentCore `__init__` 參數大幅精簡（無 reviewer/shutdown 相關參數）

此階段加入 message queue 和 channel adapter 抽象，讓 CLI 成為第一個 adapter 實作。

## 設計決策

### 訊息 schema

- **選擇**：`InboundMessage` / `OutboundMessage` dataclass
- **原因**：簡單、無外部依賴、Pydantic 在此不需要驗證

### Queue 實作

- **選擇**：`queue.PriorityQueue`（stdlib）
- **原因**：標準函式庫，thread-safe，支援優先級。個人使用不需 async

### GUI Lock

- **選擇**：`threading.Lock`
- **原因**：Brain 的 gui_task 和未來 LINE adapter 共用 GUIManager，需互斥
- 此階段先建立 lock 機制，Brain 的 gui_task 經過 lock

## 檔案結構

```
src/chat_agent/
├── agent/
│   ├── __init__.py
│   ├── core.py                # 改為從 queue 拉訊息、route 回應
│   ├── schema.py              # InboundMessage, OutboundMessage, PendingOutbound
│   └── adapters/
│       ├── __init__.py
│       ├── protocol.py        # ChannelAdapter protocol
│       └── cli.py             # CLI adapter（從 cli/app.py 拆出）
├── cli/
│   ├── app.py                 # 進一步瘦身：啟動 adapters + AgentCore.run()
│   └── ...
└── ...
```

## 技術設計

### InboundMessage / OutboundMessage

```python
@dataclass
class InboundMessage:
    channel: str
    content: str
    priority: int
    sender: str
    metadata: dict
    timestamp: datetime
```

```python
@dataclass
class OutboundMessage:
    channel: str
    content: str
    metadata: dict
```

### ChannelAdapter Protocol

```python
class ChannelAdapter(Protocol):
    channel_name: str
    priority: int

    def start(self, agent: AgentCore) -> None: ...
    def send(self, message: OutboundMessage) -> None: ...
    def stop(self) -> None: ...
```

### AgentCore 改動

```python
def run(self):
    while True:
        msg = self.queue.get()           # PriorityQueue, blocking
        tagged = self._tag_message(msg)
        self.conversation.add("user", tagged)
        response = self._run_turn()
        self._route_response(msg, response)
```

### Channel 標記格式

| 情境 | 格式 |
|------|------|
| 主用戶，CLI | `[cli] 原始內容` |
| 主用戶，LINE | `[line] 原始內容` |
| 系統提醒 | `[系統提醒] 原始內容` |
| 其他人 | `[line，來自 小明] 原始內容` |

### GUI Lock

```python
gui_lock = threading.Lock()
```

Brain 的 gui_task tool 執行前取 lock。LINE adapter（Phase 3）的 GUI 操作也取同一把 lock。

## 步驟

1. 建立 `agent/schema.py`（InboundMessage、OutboundMessage）
2. 建立 `agent/adapters/protocol.py`（ChannelAdapter protocol）
3. 建立 `agent/adapters/cli.py`（從 cli/app.py 拆出 input/output 邏輯）
4. 改 `agent/core.py`：主迴圈從 queue 拉訊息、tag、run_turn、route
5. 改 `cli/app.py`：啟動 CLI adapter + AgentCore
6. 加入 GUI lock，gui_task tool 經過 lock
7. 補測試

## 驗證

- `uv run pytest` 全部通過
- CLI 行為不變
- GUI lock 不影響現有 gui_task 功能

## 完成條件

- [x] `agent/schema.py` 完成
- [x] `agent/queue.py` 完成（PersistentPriorityQueue 目錄式持久化）
- [x] `agent/adapters/protocol.py` 完成
- [x] `agent/adapters/cli.py` 完成，CLI 透過 queue 對接 Agent Core
- [x] Agent Core 主迴圈改為 queue-based
- [x] GUI lock 機制建立
- [x] 現有測試全過（587 passed）
