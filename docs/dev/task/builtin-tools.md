# 內建工具擴展

新增核心工具：shell 執行、檔案讀寫，以及記憶專用寫入通道。

**狀態**：完成

## 背景

目前只有 `get_current_time` 工具。Agent 需要更多基礎能力來：
- 探索環境（ls、find、grep）
- 讀寫檔案（包含 memory）
- 執行各種任務

設計理念：給她基礎工具，讓 LLM 自己決定怎麼用，不需為每個功能寫專門程式碼。就像 Claude Code 一樣。

## 設計決策

### 工具清單

- **選擇**：`execute_shell`、`read_file`、`write_file`、`edit_file`、`memory_edit`
- **原因**：
  - Shell 提供最大靈活性，LLM 很熟悉
  - 分開 read/write/edit 語義更清晰
  - `memory_edit` 將記憶寫入收斂為「意圖輸入 + 程式化執行」，降低覆寫與匹配失敗風險

### 安全機制

- **選擇**：黑名單 + 路徑限制
- **原因**：
  - Agent 會在 VM 執行，可以相對自由
  - 黑名單擋危險指令（rm -rf、sudo 等）
  - 路徑限制防止存取敏感區域

### 設定方式

- **選擇**：`AppConfig` 頂層 `tools` 區塊
- **原因**：簡單直接，目前只有一個 agent

```yaml
# cfgs/basic.yaml
tools:
  agent_os_dir: ~/.chat-agent  # 執行路徑，相對路徑的基準
  allowed_paths:
    - ~/.chat-agent/memory
    - ~/workspace
  shell:
    blacklist:
      - "rm -rf /"
      - "sudo"
      - "chmod"
      - "chown"
      - "> /dev"
      - "mkfs"
      - "dd if="
    timeout: 30

agents:
  brain:
    llm: llm/ollama/gemini-3-flash-preview/think-medium.yaml
```

### 黑名單檢查

- **選擇**：字串包含（pattern in command）
- **原因**：簡單直接，如 `sudo` 會擋住任何包含 sudo 的指令

## 檔案結構

```
src/chat_agent/
├── core/
│   └── schema.py ............ 新增 ToolsConfig, ShellConfig
├── tools/
│   ├── __init__.py
│   ├── registry.py
│   ├── executor.py .......... ShellExecutor（追蹤 cwd、黑名單檢查）
│   ├── security.py .......... 路徑檢查模組
│   └── builtin/
│       ├── __init__.py
│       ├── time.py .......... get_current_time（已完成）
│       ├── shell.py ......... execute_shell
│       ├── file.py .......... read_file, write_file, edit_file
│       └── memory_edit.py ... memory_edit（記憶專用）
```

## 技術設計

### Config Schema

```python
# core/schema.py

class ShellConfig(BaseModel):
    """Shell tool configuration."""
    blacklist: list[str] = []
    timeout: int = 30

class ToolsConfig(BaseModel):
    """Tools security configuration."""
    agent_os_dir: str = "~/.chat-agent"
    allowed_paths: list[str] = []
    shell: ShellConfig = ShellConfig()

class AppConfig(BaseModel):
    """Application configuration."""
    tools: ToolsConfig = ToolsConfig()
    agents: dict[str, AgentConfig]
```

### ShellExecutor

追蹤 working directory，支援 `cd` 指令：

```python
class ShellExecutor:
    CWD_MARKER = "__CWD_MARKER__"

    def __init__(self, agent_os_dir: str, config: ShellConfig):
        self.current_dir = Path(agent_os_dir).expanduser().resolve()
        self.config = config

    def execute(self, command: str) -> str:
        # 1. 黑名單檢查
        if self._is_blacklisted(command):
            return "Error: Command contains blacklisted pattern"

        # 2. 執行指令，最後加 pwd 追蹤目錄變化
        full_command = f"{command}; echo '{self.CWD_MARKER}'; pwd"

        result = subprocess.run(
            full_command,
            cwd=self.current_dir,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.config.timeout
        )

        # 3. 解析輸出，更新 current_dir
        stdout = result.stdout
        parts = stdout.split(self.CWD_MARKER)
        output = parts[0].rstrip()

        if len(parts) > 1:
            new_dir = parts[1].strip()
            if Path(new_dir).is_dir():
                self.current_dir = Path(new_dir)

        # 4. 合併 stdout 和 stderr
        if result.stderr:
            output += f"\n{result.stderr}"

        return output

    def _is_blacklisted(self, command: str) -> bool:
        command_lower = command.lower()
        for pattern in self.config.blacklist:
            if pattern.lower() in command_lower:
                return True
        return False
```

### Edge Cases 處理

| Case | 處理方式 | 說明 |
|------|----------|------|
| `cd ~` | Shell 處理 | `pwd` 回傳展開後的路徑 |
| `cd ~/foo` | Shell 處理 | 同上 |
| `cd -` | Shell 處理 | `pwd` 回傳正確路徑 |
| `cd` (無參數) | Shell 處理 | 回到 home，`pwd` 追蹤 |
| `pushd` / `popd` | 部分支援 | `pwd` 追蹤當前目錄，但不追蹤 directory stack |
| `cd` 失敗 | 不變 | 目錄不變，`pwd` 回傳原路徑 |
| 指令失敗 | 仍追蹤 | 用 `;` 而非 `&&`，`pwd` 仍執行 |

### 路徑檢查

```python
def is_path_allowed(path: str, allowed_paths: list[str]) -> bool:
    """Check if path is within allowed directories."""
    resolved = Path(path).expanduser().resolve()
    for allowed in allowed_paths:
        allowed_resolved = Path(allowed).expanduser().resolve()
        if resolved == allowed_resolved or allowed_resolved in resolved.parents:
            return True
    return False
```

### execute_shell

```python
EXECUTE_SHELL_DEFINITION = ToolDefinition(
    name="execute_shell",
    description="Execute a shell command. Use for exploring files, searching, etc.",
    parameters={
        "command": ToolParameter(
            type="string",
            description="The shell command to execute",
        ),
        "timeout": ToolParameter(
            type="integer",
            description="Timeout in seconds (default: 30)",
        ),
    },
    required=["command"],
)
```

### read_file

類似 Claude Code 的 Read tool。

```python
READ_FILE_DEFINITION = ToolDefinition(
    name="read_file",
    description="Read file content. Default text output with line numbers; supports structured JSON output.",
    parameters={
        "path": ToolParameter(
            type="string",
            description="The file path to read",
        ),
        "offset": ToolParameter(
            type="integer",
            description="Line number to start reading from (1-indexed). Default: 1",
        ),
        "limit": ToolParameter(
            type="integer",
            description="Number of lines to read. Default: 2000",
        ),
        "output_format": ToolParameter(
            type="string",
            description="Output format: 'text' (default) or 'json'",
        ),
    },
    required=["path"],
)
```

功能：
- 預設讀取前 2000 行
- 預設輸出格式：行號 + tab + 內容（like cat -n）
- `output_format="json"` 時，回傳包含 `path`、`resolved_path`、`offset/limit`、`total_lines`、`returned_lines`、`lines` 的結構化資料
- 二進制檔案：回傳錯誤訊息
- 大檔案：截斷並警告

### write_file

```python
WRITE_FILE_DEFINITION = ToolDefinition(
    name="write_file",
    description="Create file content. Fails if the file already exists and is non-empty.",
    parameters={
        "path": ToolParameter(
            type="string",
            description="The file path to write",
        ),
        "content": ToolParameter(
            type="string",
            description="The content to write to the file",
        ),
    },
    required=["path", "content"],
)
```

功能：
- 允許建立新檔案
- 允許寫入已存在但為空的檔案
- 已存在且非空的檔案直接報錯（要求改用 `edit_file`）
- 自動建立父目錄
- 對 `memory/` 路徑由上層流程封鎖（必須改走 `memory_edit`）

### edit_file

類似 Claude Code 的 Edit tool。

```python
EDIT_FILE_DEFINITION = ToolDefinition(
    name="edit_file",
    description="Replace text in a file. old_string must be unique unless replace_all is true.",
    parameters={
        "path": ToolParameter(
            type="string",
            description="The file path to edit",
        ),
        "old_string": ToolParameter(
            type="string",
            description="The text to replace (must be unique in file)",
        ),
        "new_string": ToolParameter(
            type="string",
            description="The text to replace with",
        ),
        "replace_all": ToolParameter(
            type="boolean",
            description="Replace all occurrences instead of requiring uniqueness. Default: false",
        ),
    },
    required=["path", "old_string", "new_string"],
)
```

功能：
- old_string 必須唯一（除非 replace_all=True）
- 找不到或不唯一時失敗
- 找不到時回傳可操作提示（相似行、空白/換行正規化提示）
- 比 write_file 更高效處理小修改
- 對 `memory/` 路徑由上層流程封鎖（必須改走 `memory_edit`）

### memory_edit

記憶專用寫入工具，僅接受 `memory/` 路徑與 v2 request。

v2 對 Brain 的公開契約：
- 根層：`as_of`、`turn_id`、`requests`
- request item：`request_id`、`target_path`、`instruction`
- 不再接受 `kind`、`payload_text`、`old_block/new_block` 等舊欄位

v2 管線：
1. Brain 只輸出意圖（`target_path + instruction`）
2. `memory_editor` 子代理讀取目標檔案全文並規劃內部 operations
3. `apply.py` 以 deterministic 規則執行（含驗證與 rollback）

deterministic operations（內部 IR）：
- `create_if_missing`
- `append_entry`
- `replace_block`
- `toggle_checkbox`（支援 `apply_all_matches`）
- `ensure_index_link`
- `prune_checked_checkboxes`

設計重點：
- 不做舊 payload 相容，輸入不符直接 validation error
- 同一 `(turn_id, request_id, operations_hash)` 再送回 `already_applied`（冪等）
- `memory/` 直接 `write_file/edit_file` 與 shell 重導向一律拒絕

## 步驟

1. **Config 擴展**
   - `core/schema.py` 新增 `ToolsConfig`, `ShellConfig`
   - `cfgs/basic.yaml` 新增 `tools` 區塊

2. **安全模組**
   - `tools/executor.py`：ShellExecutor
   - `tools/security.py`：路徑檢查

3. **execute_shell**
   - 使用 ShellExecutor
   - 整合黑名單檢查

4. **read_file**
   - 分段讀取（offset, limit）
   - 輸出帶行號
   - 二進制檢測
   - 截斷保護
   - 整合路徑檢查

5. **write_file**
   - 僅允許新建檔案或寫入空檔（非 memory）
   - 非空檔案拒絕覆寫（提示改用 `edit_file`）
   - 自動建立目錄
   - 整合路徑檢查

6. **edit_file**
   - 文字替換（非 memory）
   - 唯一性檢查
   - replace_all 選項
   - 整合路徑檢查

7. **memory_edit**
   - 結構化記憶寫入與冪等保證
   - Writer 決策 + deterministic apply
   - 錯誤 fail-closed

8. **CLI 整合**
   - 註冊新工具到 registry
   - 傳遞 config 給工具

9. **測試**
   - ShellExecutor 測試（cwd 追蹤、黑名單）
   - 路徑檢查測試
   - 各工具功能測試

## 驗證

```bash
# 測試
uv run pytest tests/

# 手動測試
uv run python -m chat_agent
# "列出目前目錄的檔案" → agent 用 execute_shell("ls -la")
# "讀取 memory/persona.md" → agent 用 read_file
# "cd ~ && pwd" → 追蹤目錄變化
```

## 完成條件

- [x] Config 擴展（ToolsConfig, ShellConfig）
- [x] ShellExecutor 實作（cwd 追蹤、黑名單檢查）
- [x] 路徑檢查模組
- [x] execute_shell 實作
- [x] read_file 實作（分段、行號、二進制檢測）
- [x] write_file 實作
- [x] edit_file 實作（唯一性檢查、replace_all）
- [x] memory_edit 實作（結構化寫入、冪等）
- [x] CLI 整合
- [x] 測試覆蓋
