# CLI UI（Textual）

本資料夾記錄 `chat-cli` 互動式 UI 的 Textual 重構設計與維護規範。

## 狀態

- 目前狀態：已切換 `chat-cli` 主互動路徑為 Textual（持續收尾）
- 已完成：
  - Textual 單一 renderer（主聊天介面）
  - typed UI event / sink / state / controller
  - `AgentCore` 對外建構介面收斂為 `UiSink`
  - `Esc` 走 turn-level cancel request（LLM/tool 邊界安全中止）
  - `Ctrl+R` Textual history modal（最近輸入選擇與回退預填）
  - `execute_shell` subprocess cancel hook（Esc 可主動終止 shell 工具）
  - `gui_task` cancel hook（GUI manager loop + wait tool 可中止）
  - 移除 `prompt_toolkit` 互動輸入路徑
- 尚未完成（後續優化）：
  - GUI worker / LLM in-flight request 的硬中止（目前仍主要依賴邊界中止）

## 文件列表

| 文件 | 說明 |
|------|------|
| [architecture.md](architecture.md) | 架構原則、事件管線、責任分層 |
| [extension-guide.md](extension-guide.md) | 新功能擴充的正規流程與限制 |

## 注意事項

- `chat-cli` 主介面已使用 `src/chat_agent/tui/`
- `ChatConsole` 仍保留給非 Textual 互動（如 `init` 流程與一般 Rich 輸出）
