# Phase 5：自主探索

Agent 自己上網查感興趣的資料，決定要不要跟用戶分享。

## 背景

詳見設計文件 [message-queue.md](message-queue.md)。

目標是做出接近真人的 agent。真人會自己看文章、查資料，看到有趣的東西會想跟朋友分享。Agent 也應該有這個能力。

核心約束：**一次只做一件事**。Agent 跟真人一樣，在上網查資料時就是專心在查，不會同時跟你聊天。這由 Agent Core 的序列處理天然保證 — queue 一次只處理一個 turn。

## 設計決策

### 觸發方式

- **選擇**：System Adapter 的 Interest Scheduler 定期觸發
- **原因**：不需要新機制，探索就是一種 system 訊息
- **替代方案**：獨立 explorer agent（會失去對話上下文，判斷不了用戶會不會有興趣）

### 探索者

- **選擇**：brain LLM 自己探索
- **原因**：brain 有完整對話脈絡，知道什麼跟用戶相關。用 sub-agent 會失去上下文
- brain 已經有所有需要的工具（execute_shell、gui_task、memory_edit）

### 分享判斷

- **選擇**：brain 自己判斷要不要傳訊息給用戶
- **原因**：接近真人行為 — 不是每次看到東西都分享，而是覺得「對方會有興趣」才說
- 判斷依據寫在 system prompt 裡

## 技術設計

### Interest Scheduler

System Adapter（Phase 4）裡新增的一種 trigger type：

```python
class InterestScheduler:
    def __init__(self, memory_dir: Path, config: ExplorationConfig):
        self.memory_dir = memory_dir
        self.config = config
        self.last_exploration: datetime | None = None

    def check(self, queue: PriorityQueue, idle_minutes: int) -> InboundMessage | None:
        # 不符合條件就不觸發
        if not self._should_trigger(queue, idle_minutes):
            return None

        topic = self._pick_topic()
        if topic is None:
            return None

        self.last_exploration = datetime.now()
        return InboundMessage(
            channel="system",
            content=f"[自主探索] 你對「{topic}」有興趣，去查查有什麼新消息",
            priority=2,
            sender="system",
            metadata={"notify_via": "line", "type": "exploration"},
        )
```

### 觸發條件

```
每 30 分鐘檢查一次，全部滿足才觸發：
1. 距離上次探索 > explore_interval_hours（避免太頻繁）
2. queue 沒有 P0/P1 訊息等待（不搶用戶的時間）
3. 用戶閒置超過 idle_threshold_minutes（不打擾活躍對話）
```

### 主題選取

從 `memory/agent/interests/` 讀取興趣主題，策略：
- 輪流或加權隨機（最近沒探索過的優先）
- 跳過最近 N 小時內已探索的主題
- 可讀 `memory/agent/short-term.md` 參考最近對話相關性

### 探索 Turn 的流程

```
Agent Core 從 queue 拿到探索 InboundMessage
  │
  ▼
brain LLM 收到: [系統提醒] [自主探索] 你對「LLM 架構」有興趣，去查查...
  │
  ├── 用工具查資料（execute_shell curl / gui_task 開瀏覽器）
  ├── memory_edit 記錄學到的東西
  │
  ├── 判斷：值得分享嗎？
  │   ├── 跟用戶最近聊的話題高度相關 → 分享
  │   ├── 用戶明確說過想知道的事 → 分享
  │   ├── 很久沒互動，想找話題 → 分享
  │   └── 只是自己覺得有趣但跟用戶無關 → 不分享
  │
  ├── 要分享 → 回應：「我剛看到一篇關於 XXX 的文章...」
  │             → route → LINE（metadata.notify_via）
  │
  └── 不分享 → 回應為空或僅更新 memory
               → route → silent
```

### System Prompt 規則（探索行為）

需在 brain system prompt 加入探索指引：

```
收到 [自主探索] 訊息時：
- 你可以自由使用工具查資料
- 查到的重要內容寫進 memory
- 只有在以下情況才主動傳訊息給用戶：
  - 內容跟用戶最近的對話或興趣高度相關
  - 用戶之前明確提過想知道的事
  - 你覺得用戶會開心收到這個訊息
- 不要為了傳而傳，真正有趣才分享
- 分享時語氣自然，像朋友看到好東西想跟你說
```

### Config

```yaml
exploration:
  enabled: true
  check_interval_minutes: 30
  explore_interval_hours: 4
  idle_threshold_minutes: 15
  notify_via: line
```

## 步驟

1. 在 System Adapter 裡加入 InterestScheduler
2. 實作觸發條件檢查
3. 實作主題選取邏輯（讀 interests/）
4. 更新 brain system prompt 加入探索行為規則
5. 加入 exploration config
6. 整合測試

## 驗證

- 閒置時 scheduler 能觸發探索
- brain 能用工具查資料並記錄到 memory
- brain 能正確判斷是否分享
- 分享的訊息能正確路由到 LINE
- 活躍對話中不會被探索打斷
- 探索 turn 結束後能正常處理下一個 queue 訊息

## 完成條件

- [ ] InterestScheduler 實作完成
- [ ] 觸發條件正確（interval、idle、queue 狀態）
- [ ] 主題選取邏輯正確
- [ ] Brain system prompt 包含探索行為規則
- [ ] Config 可控制開關和參數
- [ ] 整合測試通過
