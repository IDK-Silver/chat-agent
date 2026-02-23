# CLI UI 架構（Textual）

## 背景

原本 `chat-cli` 互動 UI 混用：

- `prompt_toolkit`（輸入 prompt / toolbar）
- `Rich` / `Rich Live`（spinner 與輸出）

這在背景通道（例如 Gmail）插入訊息時，容易出現 terminal redraw race，造成：

- `ctx` 顯示跑版
- 殘影
- 需要手動按 Enter 才恢復畫面

## 核心原則

### 1. 單一 Renderer

- 互動式 `chat-cli` UI 由 Textual 作為唯一 terminal renderer
- 其他執行緒不可直接操作 terminal（包含直接 `print()` 或 `Rich Console.print()`）

### 2. 型別化 UI Event

- Runtime 僅透過 `UiSink.emit(UiEvent)` 發送事件
- UI 僅消費事件並更新 `UiState`

目前已建立：

- `src/chat_agent/tui/events.py`
- `src/chat_agent/tui/sink.py`
- `src/chat_agent/tui/state.py`
- `src/chat_agent/tui/controller.py`
- `src/chat_agent/tui/app.py`

## 分層責任

### Runtime / Agent

- 產生 typed UI event
- 不直接觸碰 Textual widget

### Controller

- 接收 UI action（submit / interrupt / history / exit）
- 管理取消狀態（`TurnCancelController`）
- 推送狀態事件（例如 `CtxStatusEvent` / `InterruptStateEvent`）

### Textual App

- 唯一畫面 owner
- 消費 event 更新 log/status/input
- 綁定快捷鍵與輸入體驗

## 中斷語意（目前階段）

- `Esc` 走 `TurnCancelController` 狀態機
- 目前先完成 UI 層可觀察狀態（`requested/pending/...`）
- Agent / tool 邊界的取消接線仍需後續 phase 完成

## 非 TTY 行為

- `chat-cli` 主模式在 `__main__` 已加入 fail-fast 檢查
- 若 `stdin/stdout` 非 TTY，直接以錯誤訊息退出

## 現況限制（請先理解）

- `AgentCore` 對外建構介面已收斂為 `UiSink`，內部仍使用 event-emitter UI port 封裝常用顯示方法（避免在 core 散落 event 組裝細節）
- `Esc` 中斷已改為 turn-level cancel request，但屬於「邊界安全中止」：
  - LLM 呼叫中無法瞬間硬中止 HTTP request
  - `execute_shell` 已支援 subprocess kill hook
  - `gui_task` manager loop 與 `wait` tool 已支援 cancel hook
  - GUI worker (`ask_worker`) 與其內部 LLM 呼叫仍未支援 in-flight 硬中止
- `Ctrl+R` 已實作 Textual modal（最近 user turn 選擇與回退預填）

## 後續待完成

- GUI worker / provider in-flight 硬中止（更深層取消）
