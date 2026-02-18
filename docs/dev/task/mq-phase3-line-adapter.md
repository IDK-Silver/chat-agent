# Phase 3：LINE Adapter

實作 LINE adapter，透過 GUI 自動化收發 LINE 訊息。

## 背景

詳見設計文件 [message-queue.md](message-queue.md)。

LINE 不走 API，透過 `gui_task`（GUIManager / GUIWorker）操作 LINE app。LINE adapter 是獨立的 adapter module，不是 brain 的 sub-agent — 它直接呼叫 GUIManager，不經過 brain LLM。

## 設計決策

### Reader 偵測機制

待選型，候選方案：
- **方案 A**：macOS notification watcher（監聽通知欄）
- **方案 B**：定期截圖 LINE dock icon 看 badge
- **方案 C**：accessibility API 讀 LINE 視窗狀態

共同原則：先用便宜的方式偵測，確認有新訊息才動用 gui_task 讀取內容。

### 失敗重試

- **發送失敗**：放回 retry queue，帶 retry_count，間隔重試
- **超過 max_retries**：存到 pending 檔案（下次啟動時補發）+ CLI 通知
- **讀取失敗**：不進 queue，下次 polling cycle 自然重試

## 檔案結構

```
src/chat_agent/
├── agent/
│   └── adapters/
│       ├── line.py            # LINE adapter（reader + writer）
│       └── ...
└── ...
```

## 技術設計

### LINE Adapter

```python
class LINEAdapter:
    def __init__(self, gui_manager: GUIManager, gui_lock: Lock, ...):
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

### PendingOutbound

```python
@dataclass
class PendingOutbound:
    message: OutboundMessage
    retry_count: int = 0
    max_retries: int = 3
    next_retry: datetime | None = None
```

## 步驟

1. 選型偵測機制（notification watcher / badge / accessibility）
2. 實作 reader（polling loop + 偵測 + gui_task 讀取）
3. 實作 writer（gui_task 發送 + retry queue）
4. 實作 pending 持久化（發送失敗超限時存檔）
5. 整合到 Agent Core（config 驅動，可開關）
6. 補測試

## 驗證

- LINE adapter 能偵測到新訊息並推入 queue
- Agent Core 處理後能透過 gui_task 回覆 LINE
- 發送失敗時 retry 機制正常運作
- GUI lock 與 brain 的 gui_task 不衝突

## 完成條件

- [ ] 偵測機制選型完成
- [ ] Reader 能讀取 LINE 未讀訊息
- [ ] Writer 能發送 LINE 訊息
- [ ] 發送失敗 retry + pending 機制
- [ ] 與 brain gui_task 透過 GUI lock 互斥
- [ ] 整合測試通過
