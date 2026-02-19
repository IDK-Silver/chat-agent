# Message Queue + Channel Adapter

**類型**：設計文件（實作任務見各 Phase 文件）

## 動機

現在 CLI 是同步一問一答，所有邏輯綁死在 `cli/app.py::main()` 的 while-True 迴圈：

```
ChatInput.get_input() → conversation.add("user") → builder.build()
→ _run_responder(LLM + tool loop) → console.print_assistant()
```

問題：
- 無法接入第三方訊息（LINE、排程提醒等）
- CLI 同時扮演輸入來源、輸出目的地、主控迴圈，職責混雜
- 加新 channel 必須重寫整個迴圈

目標：將訊息來源與 agent 核心解耦，讓 CLI 成為眾多 channel 之一。

## 被否決的方案：LLM Tool 收發

曾考慮讓 LLM 透過 `receive_message` / `send_message` tool 來跟外界互動。否決原因：

**LLM 不是 daemon**。目前的模型是 turn-based — 給 context，產 output，結束。要讓 LLM 用 `receive_message` tool「等」新訊息，就得讓 tool 阻塞，LLM 處於永遠不結束的 tool-call loop。

具體問題：
- **Token 成本爆炸**：每次 receive → process → send 都要重傳完整 context
- **Context 管理困難**：一直跑不停，context 只會越來越大
- **錯誤恢復差**：LLM 跑一半掛了，整個 session 狀態都要重建

結論：訊息路由由 Agent Core 處理，LLM 不需要知道訊息從哪來。它只看到 conversation 裡帶 channel 標記的 user message，正常回應就好。

## 設計約束

- **個人使用**：單一用戶，不考慮高併發
- **Agent 環境**：Agent 擁有自己的桌面 VM 環境和帳號（Gmail、LINE 等），其他人透過這些帳號聯繫 agent
- **訊息來源**：CLI（互動）、Gmail（agent 自己的信箱）、LINE（手機，透過 GUI 自動化）、程式碼自動產生（排程提醒）、偶爾其他人的訊息
- **不同 channel 有不同優先級**
- **Gmail 走 REST API**（httpx + OAuth2），不需要 GUI
- **LINE 不走 API，走 gui_task**（GUI 自動化操作 agent VM 上的 LINE app）
- Agent 一次處理一個 turn，序列化，不需並行

## 架構總覽

```
 ┌──────────┐  ┌──────────────────┐  ┌────────────┐
 │   CLI    │  │   LINE Adapter   │  │  Scheduler │
 │ Adapter  │  │  (GUI-based)     │  │  (cron)    │
 └────┬─────┘  └────────┬─────────┘  └─────┬──────┘
      │                 │                   │
      ▼                 ▼                   ▼
 ┌──────────────────────────────────────────────────┐
 │         Priority Queue (inbound)                  │
 │  按 priority 排序，同 priority 按 FIFO            │
 └────────────────────┬─────────────────────────────┘
                      │
                      ▼
 ┌──────────────────────────────────────────────────┐
 │                Agent Core                         │
 │                                                   │
 │  Conversation（共用，帶 channel 標記）             │
 │  ContextBuilder                                   │
 │  LLM + Tool Loop（_run_responder）                │
 │  Memory Sync（side-channel）                      │
 │  Memory（read/write）                             │
 └────────────────────┬─────────────────────────────┘
                      │
                      ▼
 ┌──────────────────────────────────────────────────┐
 │               Response Router                     │
 │  依 inbound 的 channel 回對應 adapter              │
 └────┬──────────────┬──────────────┬───────────────┘
      ▼              ▼              ▼
    CLI            LINE          (silent)
   print()      gui_task 發送    system 不需回覆
```

## 訊息模型

### InboundMessage

```python
@dataclass
class InboundMessage:
    channel: str            # "cli", "line", "system"
    content: str
    priority: int           # 0 = 最高
    sender: str             # "yufeng", "system", "friend_name"
    metadata: dict          # channel-specific（reply_token 等）
    timestamp: datetime
```

### OutboundMessage

```python
@dataclass
class OutboundMessage:
    channel: str
    content: str
    metadata: dict          # 從 inbound 透傳
```

### 優先級定義

| Priority | Channel | 理由 |
|----------|---------|------|
| 0 | cli | 用戶坐在螢幕前等，必須最快回應 |
| 1 | gmail | email channel，用戶對延遲有容忍度 |
| 1 | line | 手機端，用戶對延遲有容忍度 |
| 2 | system | 程式自動產生的提醒，不急 |

優先級決定的是 **queue 中下一個處理誰**，不會中斷當前正在處理的 turn。

### 核心約束：一次只做一件事

Agent 像真人一樣，一次只做一件事。在上網查資料時就是專心在查，不會同時跟你聊天。這由 Agent Core 的序列處理天然保證 — queue 一次只出一個 turn，處理完才拿下一個。

優先級的作用是：當有多個訊息排隊時，先處理重要的。例如用戶在 CLI 打字（P0）比自主探索（P2）優先。

## 共用 Conversation

### 決策：一個用戶 = 一個 conversation

所有 channel 的訊息進同一個 conversation，帶 channel 標記：

```
14:05  User [cli]:    幫我查今天行程
14:06  Assistant:     今天下午 3 點有會議
14:30  User [line]:   提醒我晚上買牛奶
14:31  Assistant:     好的，已記下
15:00  User [cli]:    剛剛 LINE 說了什麼？
15:00  Assistant:     你請我提醒晚上買牛奶
```

### 共用 vs Per-channel 比較

```
                          共用 Conversation      Per-channel
─────────────────────────────────────────────────────────────
跨 channel 資訊          直接在 context 裡       依賴 memory sync
memory sync 失敗影響     無                      資訊斷層
架構複雜度               簡單                    多 session 管理
並發處理                 必須序列化              可平行（各自 LLM call）
context 效率             被其他 channel 稀釋     各自獨立
system prompt 靈活度     一套打天下              per-channel prompt
回應延遲（多 channel）   排隊等待                獨立處理
```

並發處理的劣勢在本專案不成立：個人使用，不會同時在兩個 channel 密集對話。

### 選擇共用的原因

**1. 不依賴 memory sync 的可靠性**

Per-channel 的跨 channel 資訊 100% 依賴 memory sync。現有的 memory sync 有多個失敗點：
- side-channel LLM call 可能失敗
- memory_edit 有 3 次 fail streak 限制
- `ContextLengthExceededError` 時 side-channel 直接 skip

共用 conversation 不需要 memory 當橋樑，LLM 直接在 context 裡看到完整脈絡。

**2. 架構更簡單**

一個 conversation、一個 session、一個 context builder。不需要 session routing table。

**3. LLM 理解更完整**

看到完整互動時間線，不會出現 CLI 和 LINE 給出矛盾回答的情況。

**4. 日後可拆**

從共用拆成 per-channel 比反過來容易。InboundMessage 本來就帶 channel 標記，只要改 routing 邏輯。

### Memory 是跨 channel 的共享層

Conversation 共用，Memory 自然也共用：

```
              CLI turn                     LINE turn
              (同一 Conversation)           (同一 Conversation)
                   │                            │
                   ▼                            ▼
             ┌─────────┐                  ┌─────────┐
             │ Builder  │                  │ Builder  │
             │ + LLM    │                  │ + LLM    │
             └────┬─────┘                  └────┬─────┘
                  │                             │
                  ▼                             ▼
            ┌──────────────────────────────────────────┐
            │       Shared Memory (.agent/memory/)      │
            │  - agent/short-term.md                    │
            │  - agent/long-term.md                     │
            │  - people/yufeng.md                       │
            └──────────────────────────────────────────┘
```

LLM 在 context 裡看到跨 channel 對話歷史，同時透過 memory 保留長期資訊。兩層互補：context 提供近期完整脈絡，memory 提供長期記憶。

### Context 稀釋的緩解

不同 channel 的訊息共用 context window，會加速 context 消耗。緩解策略：

- `ContextBuilder` 既有的 truncation（`preserve_turns`、`max_chars`）已可處理
- `/compact` 命令已存在
- Memory archive hook 自動清理
- 可選：加入 channel-aware truncation — 優先保留當前 channel 的近期 turns，較早截斷其他 channel

### Channel 標記格式

`AgentCore._tag_message()` 在 user content 前加標記：

| 情境 | 格式 |
|------|------|
| 主用戶，CLI | `[cli] 原始內容` |
| 主用戶，LINE | `[line] 原始內容` |
| 系統提醒 | `[系統提醒] 原始內容` |
| 其他人，LINE | `[line，來自 小明] 原始內容` |

## Agent Core

從 `cli/app.py::main()` 抽出的核心邏輯。

```python
class AgentCore:
    def __init__(self, config, workspace, ...):
        self.queue = PriorityQueue()
        self.conversation = Conversation()
        self.builder = ContextBuilder(...)
        self.adapters: dict[str, ChannelAdapter] = {}

    def run(self):
        while True:
            msg = self.queue.get()           # blocking, priority-ordered
            tagged = self._tag_message(msg)
            self.conversation.add("user", tagged)
            response = self._run_turn()      # responder + memory sync
            self._route_response(msg, response)
```

`_run_turn()` 內部邏輯：responder（LLM + tool loop）+ memory sync side-channel。差別在於不直接呼叫 `console.print_assistant()`，而是回傳 response 由 router 分發。

## Channel Adapter

### Protocol

```python
class ChannelAdapter(Protocol):
    channel_name: str
    priority: int

    def start(self, agent: AgentCore) -> None: ...
    def send(self, message: OutboundMessage) -> None: ...
    def stop(self) -> None: ...
```

### CLI Adapter

最薄的一層，取代現在 `cli/app.py` 中的 input/output 邏輯：

- `start()`：啟動 input thread，讀 stdin → 包成 InboundMessage → 推入 queue
- `send()`：`console.print_assistant(msg.content)`
- slash commands（`/help`、`/exit` 等）、double-ESC 歷史回退等仍由 CLI adapter 內部處理，不進 queue

### LINE Adapter（GUI-based）

LINE 不走 API，透過 `gui_task`（GUIManager / GUIWorker）操作 LINE app。

**收訊息（reader）**：

```
polling loop:
  輕量偵測有無新訊息
    方案 A：macOS notification watcher（監聽通知欄）
    方案 B：定期截圖 LINE dock icon 看 badge
    方案 C：accessibility API 讀 LINE 視窗
  ↓ 偵測到新訊息
  gui_task("讀取 LINE 未讀訊息")  ← 拿 GUI lock
  ↓
  解析結果 → InboundMessage → queue
```

先用便宜的方式偵測，確認有新訊息才動用 gui_task。

**發訊息（writer）**：

```
收到 OutboundMessage
  → gui_task("打開跟 X 的對話，輸入 Y，送出")  ← 拿 GUI lock
  → 成功 → done
  → 失敗 → retry queue
```

### System Adapter（排程提醒）

```
scheduler loop:
  檢查到期的提醒
  → InboundMessage(channel="system", priority=2)
  → queue
```

System 訊息的回應透過 `metadata.notify_via` 指定目標 channel 轉發（預設 LINE）。

### 主動發訊息（`send_message` tool）

現有架構是 **request-response**：收到 inbound → 處理 → outbound 回同一 channel。Agent 無法主動對外發訊息。

#### 場景

System adapter 或 idle 偵測觸發 brain：「你有什麼想做的嗎？」，brain 決定主動寄信、傳 LINE 等。

#### 設計：通用 `send_message` tool

```python
send_message(channel="gmail", to="yufeng", subject="明天的約", body="...")
send_message(channel="line", to="小明", body="週末見！")
```

- Brain 用**內部人名**（不是 email / LINE ID）
- Tool 透過 ContactMap 反查實際地址：`yufeng → a288235403@gmail.com`
- 找不到 → 回 error，提示 brain 先用 `update_contact_mapping` 建立對應
- Tool 內部建 `OutboundMessage` → 呼叫對應 adapter 的 `send()`

#### 架構

```
Brain 呼叫 send_message(channel, to, ...)
  │
  ▼
Tool 從 AgentCore.adapters 找對應 adapter
  │
  ▼
ContactMap.resolve_reverse(channel, name) → identifier
  │
  ├─ 找到 → OutboundMessage → adapter.send()
  └─ 找不到 → 回 error
```

#### ContactMap 擴充

現有 ContactMap 是 `identifier → name` 單向。加一個反向查詢：

```python
def resolve_reverse(self, channel: str, name: str) -> str | None:
    """Reverse lookup: name → identifier."""
    for key, val in self._data.get(channel, {}).items():
        if val == name:
            return key
    return None
```

#### 參數設計

| 參數 | 必要 | 說明 |
|------|------|------|
| `channel` | 是 | 目標 channel（`gmail`、`line` 等） |
| `to` | 是 | 內部人名（ContactMap 反查地址） |
| `body` | 是 | 訊息內容 |
| `subject` | 否 | Gmail 專用，LINE 不需要 |

#### Brain Prompt 指引

- 只在有明確動機時主動發訊息（想分享、提醒、關心）
- 不可重複發送同樣內容
- 發送前先確認 ContactMap 有該人的 channel 地址

#### 依賴

- System Adapter / idle 偵測機制（Phase 4-5）觸發 brain 的主動行為
- ContactMap 反向查詢

#### 不需要改的

- GmailAdapter.send() 已支援非回覆模式（`thread_id`、`in_reply_to` 都是 optional）
- Queue 流程不受影響 — tool 直接呼叫 adapter.send()，不經過 queue

### LINE Adapter 不是 Sub-agent

LINE adapter 是獨立的 adapter module，不是 brain 的 sub-agent。

| | Sub-agent（如 GUIWorker） | LINE Adapter |
|---|---|---|
| 控制者 | brain LLM 呼叫 | 自己獨立運行 |
| 觸發時機 | brain 決定什麼時候做 | 自己 polling、自己決定 |
| 結果去向 | 回到 brain 的 tool loop | 推入 inbound queue |
| 發送 | N/A | 從 outbound 拿訊息獨立發送 |

但 LINE adapter **內部會用 GUIManager / GUIWorker** 來操作 GUI — 它是 GUIManager 的消費者，不是 brain 的子任務。

```python
class LINEAdapter:
    def __init__(self, gui_manager: GUIManager, gui_lock: Lock):
        self.gui = gui_manager
        self.lock = gui_lock

    def _read_new_messages(self) -> list[InboundMessage]:
        with self.lock:
            result = self.gui.execute(
                "打開 LINE，讀取所有未讀訊息，回報每則訊息的發送者和內容"
            )
        return self._parse_gui_result(result)

    def _send_message(self, msg: OutboundMessage) -> bool:
        with self.lock:
            result = self.gui.execute(
                f"打開跟 {msg.metadata['recipient']} 的 LINE 對話，"
                f"輸入以下內容並送出：{msg.content}"
            )
        return result.success
```

## GUI 資源管理

螢幕只有一個。Brain 的 gui_task tool 和 LINE adapter 的 GUI 操作必須互斥。

### GUI Lock

```python
gui_lock = threading.Lock()
```

使用場景：
- LINE adapter reader/writer 操作前取 lock
- Brain 的 gui_task tool 執行前取 lock
- 同一時間只有一方能操作 GUI

衝突場景範例：
1. LINE adapter 正在讀訊息 → brain 的 gui_task 排隊等
2. Brain 正在操作瀏覽器 → LINE adapter 的發送排隊等
3. 等到 lock 釋放後正常執行，不會撞車

GUIManager 實例可以共用，但呼叫必須經過 lock。

### GUI 衝突場景範例

```
10:30  LINE notification 偵測到新訊息
       → LINE adapter 拿 GUI lock
       → gui_task 讀 LINE → 成功，讀到小明：「中午吃飯嗎」
       → 釋放 GUI lock
       → InboundMessage 推入 queue

10:31  Agent Core 處理，回應：「好啊，12 點老地方」
       → OutboundMessage → LINE adapter writer
       → LINE adapter 拿 GUI lock
       → gui_task 發送 → 失敗（LINE app 被蓋住了）
       → 放回 retry queue，30 秒後重試
       → 釋放 GUI lock

10:31  用戶在 CLI：「幫我開 Chrome 查個東西」
       → Agent Core 處理，brain 呼叫 gui_task
       → gui_task 要拿 GUI lock → lock 已釋放，直接拿到
       → brain 操作 Chrome

10:32  brain gui_task 完成，釋放 lock
       → LINE adapter retry 拿到 lock
       → gui_task 重新發送 → 成功
       → CLI 不受影響，用戶甚至不知道 LINE 發送有延遲
```

## 失敗處理

GUI 自動化本質上不可靠，失敗是常態。

### 讀取失敗

```
gui_task 讀 LINE 失敗
  → 不推 InboundMessage（訊息還在 LINE 裡）
  → 下次 polling cycle 自然重試
  → 連續失敗 N 次 → CLI 顯示警告
```

### 發送失敗

```
gui_task 發送失敗
  → 放回 retry queue（帶 retry_count）
  → 等一段時間後重試
  → 超過 max_retries →
     ① 存到 pending 檔案（下次啟動時補發）
     ② CLI 通知用戶：「LINE 訊息發送失敗：[摘要]」
```

```python
@dataclass
class PendingOutbound:
    message: OutboundMessage
    retry_count: int = 0
    max_retries: int = 3
    next_retry: datetime | None = None
```

### Brain 的 gui_task 失敗

與現行邏輯相同，由 GUIManager 的 max_steps 機制處理，不受此設計影響。

## 完整場景走讀：一天的使用

```
09:00  用戶開電腦，啟動 CLI

09:05  [CLI, P0] 用戶：「早安，今天有什麼事？」
       → queue.get() 取出
       → conversation: [cli] 早安，今天有什麼事？
       → LLM 呼叫 memory_search 查行程
       → 回應：「早安！今天 10 點有 design review，下午 3 點 1:1」
       → route → CLI Adapter → terminal 顯示

09:30  用戶離開電腦去開會，CLI 還開著

10:30  [LINE, P1] 同事小明傳 LINE：「中午要一起吃飯嗎」
       → LINE adapter 偵測到 → gui_task 讀取 → InboundMessage
       → queue.get() 取出
       → conversation:
           [cli] 早安，今天有什麼事？
           Assistant: 早安！今天 10 點有 design review...
           [line，來自 小明] 中午要一起吃飯嗎
       → LLM 看到是別人的訊息，依 system prompt 規則回應
       → 回應：「小明找你吃飯，我先回他說你在開會晚點回覆」
       → route → LINE Adapter → gui_task 發送

11:00  [System, P2] 排程提醒：「design review 筆記還沒寫」
       → InboundMessage(channel="system", metadata={notify_via: "line"})
       → conversation:
           ...（之前的訊息）
           [系統提醒] design review 筆記還沒寫
       → LLM：「好的，我透過 LINE 提醒你」
       → route → System Adapter → 轉發 LINE Adapter → LINE 推播

12:00  [LINE, P1] 用戶自己回 LINE：「好，review 筆記等下寫」
       → conversation:
           ...
           [line] 好，review 筆記等下寫
       → LLM：「收到，你回電腦時我提醒你」
       → memory_edit 更新 short-term.md
       → route → LINE

13:00  用戶回到電腦
       [CLI, P0] 用戶：「剛剛有什麼事？」
       → conversation 裡完整記錄了上午所有互動
       → LLM 不需要查 memory，直接從 context 看到：
         - 小明找你吃飯（已回覆）
         - 系統提醒你寫 review 筆記（你說等下寫）
       → 回應摘要
       → route → CLI
```

13:00 的 turn 是共用 conversation 的關鍵優勢：若用 per-channel，CLI conversation 裡只有 09:05 那輪，中間的 LINE 和 system 互動全部看不到，必須完全依靠 memory search。

## 實施階段

各 phase 為獨立任務文件，依序執行：

| 任務 | 說明 | 依賴 |
|------|------|------|
| [mq-phase1-agent-core.md](mq-phase1-agent-core.md) | 抽出 Agent Core，CLI 瘦身 | - |
| [remove-reviewer-shutdown.md](remove-reviewer-shutdown.md) | 移除 Reviewer + Shutdown 系統 | - |
| [mq-phase2-queue-protocol.md](mq-phase2-queue-protocol.md) | Message Queue + Channel Protocol | Phase 1, remove-reviewer-shutdown |
| [mq-phase-gmail-adapter.md](mq-phase-gmail-adapter.md) | Gmail Adapter + Contact Map | Phase 2 |
| [mq-phase3-line-adapter.md](mq-phase3-line-adapter.md) | LINE Adapter（GUI-based） | Phase 2 |
| [mq-phase4-system-adapter.md](mq-phase4-system-adapter.md) | System Adapter（排程提醒） | Phase 2 |
| [mq-phase5-autonomous-exploration.md](mq-phase5-autonomous-exploration.md) | 自主探索（上網查資料、主動分享） | Phase 4 |

Phase 1 已完成。remove-reviewer-shutdown 為 Phase 2 的前置任務 — 移除 reviewer 和 shutdown agent 後 AgentCore 大幅簡化（`run_turn()` 從 ~440 行降到 ~100 行），降低 queue-based 改造的複雜度和風險。

Phase 3 和 Phase 4 互不依賴，可平行。Phase 5 依賴 Phase 4（System Adapter 的 scheduler 機制）。

## 影響範圍

### 已移除（remove-reviewer-shutdown 任務）

| 模組 | 說明 |
|------|------|
| Reviewer（post / progress） | 已移除，不再存在 |
| Shutdown Agent | 已移除，graceful_exit 不再呼叫 LLM |

### 不需要改

| 模組 | 理由 |
|------|------|
| LLM client / provider | 完全不動 |
| Tool registry / tools | 完全不動 |
| Memory（editor / search） | 完全不動 |
| Workspace / migration | 完全不動 |

### 需要改

| 模組 | 變更 |
|------|------|
| `cli/app.py::main()` | 瘦身為 CLI adapter（Phase 1 已拆出 agent core，reviewer 移除後進一步簡化） |
| `context/conversation.py` | Message 可能加 channel field |
| `context/builder.py` | 可選：channel-aware truncation |
| 新增 `agent/core.py` | Agent Core 主迴圈 |
| 新增 `agent/schema.py` | InboundMessage / OutboundMessage |
| 新增 `agent/adapters/` | CLI / LINE / System adapter |
