# CLI UI 擴充指南（正規流程）

本指南的目標是避免未來在 `chat-cli` UI 上再次出現「直接寫 terminal」的旁路。

## 新增 UI 功能的正規流程

1. 定義或擴充 `UiEvent`（`src/chat_agent/tui/events.py`）
2. 在 runtime/controller 發送事件（透過 `UiSink.emit(...)`）
3. 在 `UiState` 增加狀態更新邏輯（必要時）
4. 在 Textual app / widget 消費狀態並渲染
5. 加測試（至少事件/狀態單元測試；互動行為加 pilot）

## 禁止事項

- 不要在 agent/runtime code 直接 `print()` 到 terminal
- 不要在 `tui/` 以外模組直接操作 Textual widget
- 不要在背景 thread 直接做終端輸出

## 何時新增事件、何時只更新狀態

- 若資訊跨層傳遞（runtime -> UI），優先用 `UiEvent`
- 若只是 UI 內部衍生顯示（例如格式化文字），可留在 Textual app/state

## 測試要求（最低）

- 新 `UiEvent`：補 `UiState` 套用測試
- 新 controller 行為：補 controller 單元測試
- 新快捷鍵 / 互動流程：補 Textual pilot 測試

## 註解規範

- 程式碼註解用英文
- 註解說明「為什麼」而不是重述程式在做什麼
