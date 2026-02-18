> **歸檔日期**：2026-02-18

# CLI 退出時自動存檔

CLI 退出時自動觸發 LLM 執行記憶保存。

## 背景

目前 CLI 退出時只打印 "Bye!"，不會保存對話記錄。根據記憶系統設計（`docs/dev/memory-system/maintenance.md`），對話結束後應執行歸檔和更新。

## 設計決策

### 保存方式

- **選擇**：發送 shutdown prompt 給 LLM，讓 LLM 使用 file tools 保存
- **原因**：符合「Agent 自我維護」架構，不硬編碼保存邏輯
- **替代方案**：Python 直接寫檔（失去 LLM 判斷能力）

### 中斷處理

- **選擇**：二次 Ctrl+C 強制退出
- **原因**：允許用戶在保存過程中放棄
- **替代方案**：無法中斷（可能卡住）

### 空對話處理

- **選擇**：跳過保存
- **原因**：沒有對話內容就不需要歸檔
- **替代方案**：總是調用 LLM（浪費資源）

## 檔案結構

```
src/chat_agent/cli/
├── app.py       # 修改退出邏輯
├── shutdown.py  # 新增
├── console.py   # （現有）
└── ...
```

## 技術設計

### perform_shutdown 函數

```python
def perform_shutdown(
    client: LLMClient,
    conversation: Conversation,
    builder: ContextBuilder,
    registry: ToolRegistry,
    console: ChatConsole,
    workspace: WorkspaceManager,
    user_id: str,
) -> bool:
    """
    1. 從 workspace 載入 shutdown prompt
    2. 發送給 LLM
    3. 執行工具調用循環（最多 20 次）
    4. 返回成功與否
    """
```

### _graceful_exit helper

```python
def _graceful_exit(client, conversation, ...) -> None:
    """
    1. 檢查是否有對話內容
    2. 有則調用 perform_shutdown
    3. 打印 goodbye
    """
```

### 退出觸發點

- `/quit` 命令
- EOF (Ctrl+D)
- 主循環中的 KeyboardInterrupt

## 步驟

1. 建立 `src/chat_agent/cli/shutdown.py`
2. 實作 `perform_shutdown()` 函數
3. 在 `app.py` 導入 `perform_shutdown`
4. 建立 `_graceful_exit()` helper
5. 修改 EOF 處理調用 `_graceful_exit()`
6. 修改 `/quit` 處理調用 `_graceful_exit()`
7. 修改升級提示顯示 migration 版本列表

## 驗證

- `uv run python -m chat_agent --user test`
- 進行簡短對話
- 輸入 `/quit`
- 確認看到 "Saving memories..." spinner
- 確認有工具調用（write_file 等）
- 確認 `~/.agent/memory/people/archive/` 產生歸檔
- 測試 Ctrl+C 中斷保存

## 完成條件

- [ ] shutdown.py 建立
- [ ] perform_shutdown() 可正常執行
- [ ] /quit 觸發保存
- [ ] EOF 觸發保存
- [ ] 空對話時跳過保存
- [ ] 二次 Ctrl+C 可強制退出

## 依賴

- kernel-restructure.md（需要 shutdown.md prompt）
