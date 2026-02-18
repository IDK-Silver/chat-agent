# Phase 4：System Adapter

實作 system adapter，處理排程提醒與程式碼自動產生的訊息。

## 背景

詳見設計文件 [message-queue.md](message-queue.md)。

System adapter 處理非人類來源的訊息：排程提醒、cron 任務、程式事件等。優先級最低（P2），回應透過 `metadata.notify_via` 轉發到指定 channel（預設 LINE）。

## 設計決策

### 排程機制

待定。候選方案：
- **方案 A**：簡單的 polling loop + 到期檢查（memory 裡的提醒事項）
- **方案 B**：cron-like scheduler（APScheduler 等）
- **方案 C**：結合 memory 的 pending-thoughts 機制

### 回應路由

- **選擇**：`metadata.notify_via` 指定目標 channel
- **原因**：system 訊息本身不是從用戶 channel 來的，需要明確指定回應送往哪裡
- **預設**：LINE（用戶最可能不在電腦前時收到提醒）

## 檔案結構

```
src/chat_agent/
├── agent/
│   └── adapters/
│       ├── system.py          # System adapter（scheduler + event handler）
│       └── ...
└── ...
```

## 技術設計

### System Adapter

```python
class SystemAdapter:
    channel_name = "system"
    priority = 2

    def _scheduler_loop(self):
        while True:
            reminders = self._check_due_reminders()
            for r in reminders:
                self.agent.queue.put(InboundMessage(
                    channel="system",
                    content=r.message,
                    priority=2,
                    sender="system",
                    metadata={"notify_via": r.preferred_channel or "line"},
                ))
            sleep(60)

    def send(self, msg: OutboundMessage):
        target = msg.metadata.get("notify_via", "line")
        self.agent.adapters[target].send(msg)
```

## 步驟

1. 選型排程機制
2. 實作 scheduler loop（到期檢查）
3. 實作 notify_via 轉發邏輯
4. 整合到 Agent Core
5. 補測試

## 驗證

- 到期提醒能自動推入 queue
- Agent Core 處理後回應轉發到正確 channel
- 不影響其他 adapter 的正常運作

## 完成條件

- [ ] 排程機制選型完成
- [ ] Scheduler loop 能偵測到期提醒
- [ ] notify_via 轉發邏輯正確
- [ ] 整合測試通過
