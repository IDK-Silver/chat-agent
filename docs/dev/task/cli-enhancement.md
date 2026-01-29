# CLI 介面美化

打造類似 Claude Code 的終端介面體驗。

**狀態**：已完成

## 背景

目前 CLI 使用簡單的 `input()` 和 `print()`，體驗差：
- 沒有顏色區分
- 看不到工具執行狀態
- 輸入體驗差（無多行、無歷史）
- 回覆沒有格式化

目標是達到 Claude Code 級別的終端體驗。

## Claude Code 介面分析

### 輸入區

- 提示符：`>`（閃爍游標）
- 多行輸入：支援貼上多行、Ctrl+J 換行
- 歷史記錄：上下鍵翻閱（~/.chat-agent/history）
- 快捷鍵：Ctrl+C 取消、Ctrl+D 退出
- 斜杠指令：`/help`, `/clear`, `/quit`
- 命令補全：輸入 `/` 時自動提示可用指令，Tab 補全

### 輸出區

- **工具調用**：灰色/藍色，顯示工具名和關鍵參數
- **執行中**：Spinner 動畫
- **結果摘要**：縮排顯示，截斷長輸出
- **Assistant 回覆**：Markdown 渲染、程式碼高亮
- **錯誤**：紅色顯示

### 範例輸出

```
> 讀取 CLAUDE.md 並告訴我專案結構

  Read: CLAUDE.md
    47 lines
  Read: docs/dev/index.md
    35 lines

專案採用以下結構：

- `src/chat_agent/` - 主程式碼
- `docs/dev/` - 開發文件
- `cfgs/` - 設定檔

```python
# 入口點
def main():
    ...
```
```

## 設計決策

### 輸出庫

- **選擇**：rich
- **原因**：
  - 功能完整（Markdown、Spinner、語法高亮、顏色）
  - API 簡潔
  - 活躍維護
  - 無需額外配置

### 輸入庫

- **選擇**：prompt_toolkit
- **原因**：
  - 多行輸入支援
  - 歷史記錄內建
  - 快捷鍵可自訂
  - 與 rich 相容

### 模組結構

- **選擇**：獨立 `cli/` 子套件
- **原因**：
  - 職責分離（UI vs 邏輯）
  - 方便測試和替換
  - 程式碼組織清晰

## 檔案結構

```
src/chat_agent/
├── cli/
│   ├── __init__.py ......... 匯出 main
│   ├── app.py .............. 主迴圈（對話邏輯）
│   ├── console.py .......... rich console 封裝
│   ├── formatter.py ........ 工具調用/結果格式化
│   ├── input.py ............ prompt_toolkit 輸入封裝
│   └── commands.py ......... 斜杠指令處理
├── __main__.py ............. 入口點（調用 cli.main）
└── cli.py .................. 刪除（邏輯移至 cli/app.py）
```

## 技術設計

### Console 封裝

```python
# cli/console.py
from rich.console import Console
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.live import Live

from .formatter import format_tool_call, format_tool_result
from ..llm.schema import ToolCall

class ChatConsole:
    """Rich-based console output for chat interface."""

    def __init__(self) -> None:
        self.console = Console()

    def print_tool_call(self, tool_call: ToolCall) -> None:
        """Print tool call in blue."""
        ...

    def print_tool_result(self, tool_call: ToolCall, result: str) -> None:
        """Print tool result in gray, indented. Errors in red."""
        ...

    def print_assistant(self, content: str) -> None:
        """Print assistant response with Markdown rendering."""
        ...

    def print_error(self, message: str) -> None:
        """Print error message in red."""
        ...

    def print_info(self, message: str) -> None:
        """Print info message."""
        ...

    def print_welcome(self) -> None:
        """Print welcome message."""
        ...

    def print_goodbye(self) -> None:
        """Print goodbye message."""
        ...

    @contextmanager
    def spinner(self, text: str = "Thinking...") -> Iterator[None]:
        """Show a spinner while processing."""
        ...
```

### 輸入封裝

```python
# cli/input.py
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

COMMANDS = {
    "/help": "Show available commands",
    "/clear": "Clear conversation history",
    "/quit": "Exit the chat",
}

class CommandCompleter(Completer):
    """Completer for slash commands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for cmd, desc in COMMANDS.items():
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display_meta=desc)

class ChatInput:
    """Prompt toolkit based input with history, multiline, and command completion."""

    def __init__(self) -> None:
        history_file = Path.home() / ".chat-agent" / "history"
        self._session = PromptSession(
            history=FileHistory(str(history_file)),
            completer=CommandCompleter(),
            complete_while_typing=True,
            multiline=True,
        )

    def get_input(self) -> str | None:
        """Get user input. Returns None on EOF/Ctrl+C."""
        ...
```

### Formatter

```python
# cli/formatter.py
from ..llm.schema import ToolCall

def format_tool_call(tool_call: ToolCall) -> str:
    """Format tool call for display.

    Examples:
        "Read: CLAUDE.md"
        "Shell: ls -la"
        "Time: UTC"
    """
    ...

def format_tool_result(tool_call: ToolCall, result: str) -> str:
    """Format tool result summary.

    Examples:
        "47 lines" (for read_file)
        "Successfully wrote 123 bytes..." (for write_file)
        "(empty)" or "3 lines" (for execute_shell)
    """
    ...
```

### 斜杠指令

```python
# cli/commands.py
from enum import Enum

class CommandResult(Enum):
    """Result of command execution."""
    CONTINUE = "continue"  # Continue chat loop
    QUIT = "quit"          # Exit chat
    CLEAR = "clear"        # Clear conversation history

class CommandHandler:
    """Handler for slash commands."""

    def __init__(self, console: ChatConsole) -> None:
        self._console = console
        self._commands = {
            "/help": (self._help, "Show available commands"),
            "/clear": (self._clear, "Clear conversation history"),
            "/quit": (self._quit, "Exit the chat"),
        }

    def is_command(self, text: str) -> bool:
        """Check if text is a slash command."""
        return text.startswith("/")

    def execute(self, text: str) -> CommandResult:
        """Execute a slash command."""
        ...

    def _help(self) -> CommandResult: ...
    def _clear(self) -> CommandResult: ...
    def _quit(self) -> CommandResult: ...
```

### App 主迴圈

```python
# cli/app.py

def main() -> None:
    config = load_config()
    client = create_client(config.agents["brain"].llm)
    registry = setup_tools(config.tools)

    console = ChatConsole()
    chat_input = ChatInput()
    conversation = Conversation()
    builder = ContextBuilder()
    commands = CommandHandler(console)

    console.print_welcome()

    while True:
        user_input = chat_input.get_input()
        if user_input is None:
            console.print_goodbye()
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Handle slash commands
        if commands.is_command(user_input):
            result = commands.execute(user_input)
            if result == CommandResult.QUIT:
                console.print_goodbye()
                break
            elif result == CommandResult.CLEAR:
                conversation = Conversation()
            continue

        conversation.add("user", user_input)
        messages = builder.build(conversation)

        try:
            tools = registry.get_definitions()

            with console.spinner():
                response = client.chat_with_tools(messages, tools)

            while response.has_tool_calls():
                conversation.add_assistant_with_tools(response.content, response.tool_calls)

                for tool_call in response.tool_calls:
                    console.print_tool_call(tool_call)
                    with console.spinner("Executing..."):
                        result = registry.execute(tool_call)
                    console.print_tool_result(tool_call, result)
                    conversation.add_tool_result(tool_call.id, tool_call.name, result)

                messages = builder.build(conversation)
                with console.spinner():
                    response = client.chat_with_tools(messages, tools)

            final_content = response.content or ""
            conversation.add("assistant", final_content)
            console.print_assistant(final_content)

        except Exception as e:
            console.print_error(str(e))
            conversation._messages.pop()
            continue
```

## 步驟

1. **新增依賴**
   - `pyproject.toml` 加入 `rich`, `prompt_toolkit`
   - `uv sync`

2. **建立 cli/ 結構**
   - 建立 `src/chat_agent/cli/` 目錄
   - 建立 `__init__.py`

3. **實作 console.py**
   - `ChatConsole` 類別
   - `print_tool_call()` - 藍色工具名
   - `print_tool_result()` - 灰色縮排結果
   - `print_assistant()` - Markdown 渲染
   - `print_error()` - 紅色錯誤
   - `spinner()` - 執行中動畫

4. **實作 formatter.py**
   - 從舊 `cli.py` 移植 `format_tool_call`, `format_tool_result`
   - 調整為回傳結構化資料

5. **實作 input.py**
   - `ChatInput` 類別
   - 多行輸入支援
   - 歷史記錄（~/.chat-agent/history）
   - 處理 Ctrl+C, Ctrl+D

6. **實作 commands.py**
   - `/help` - 顯示可用指令
   - `/clear` - 清除對話歷史
   - `/quit` - 退出程式

7. **實作 app.py**
   - 從舊 `cli.py` 移植主迴圈
   - 整合 ChatConsole, ChatInput
   - 加入 Spinner
   - 整合斜杠指令

8. **更新入口點**
   - `__main__.py` 改為調用 `cli.main()`
   - 刪除舊 `cli.py`

9. **測試與調整**
   - 手動測試各種場景
   - 調整顏色和格式

## 驗證

```bash
# 安裝依賴
uv sync

# 執行測試
uv run python -m chat_agent

# 驗證項目
# - 輸入提示符顯示正確
# - 上下鍵可翻閱歷史
# - 多行輸入正常
# - 工具調用顯示藍色
# - 執行時有 spinner
# - 結果摘要顯示正確
# - Markdown 正確渲染
# - 程式碼有語法高亮
# - 錯誤顯示紅色
# - Ctrl+C 可中斷
# - Ctrl+D 可退出
```

## 完成條件

- [x] 依賴加入（rich, prompt_toolkit）
- [x] cli/ 目錄結構建立
- [x] ChatConsole 實作
- [x] ChatInput 實作（多行、歷史）
- [x] formatter.py 實作
- [x] commands.py 實作（/help, /clear, /quit）
- [x] 命令補全（輸入 / 自動提示）
- [x] app.py 主迴圈整合
- [x] 入口點更新
- [x] 舊 cli.py 刪除
- [x] Spinner 執行動畫
- [x] Markdown 渲染
- [x] 語法高亮（via rich Markdown）
- [x] 錯誤紅色顯示
