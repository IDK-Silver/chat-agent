# 基礎對話迴圈（含記憶系統）

實作 Workspace 初始化 + Bootloader 整合，讓 agent 能存取 memory。

**狀態**：完成

## 背景

目前 `cli.py` 有基本對話，但：
- 沒有 system prompt（人格）
- 沒有記憶系統

目標：啟動後能對話，agent 根據 bootloader 引導自行讀取 memory（persona 等），對話中能讀寫記憶。

見 [bootstrap.md](../memory-system/bootstrap.md) 的設計。

## 設計決策

### 工作目錄

- **選擇**：統一用 `agent_os_dir`，提升到 `AppConfig` 層級
- **預設值**：`~/.agent`
- **原因**：歷史遺留的 `memory_path` 概念合併

### 目錄結構

- **選擇**：`agent_os_dir/kernel/` + `agent_os_dir/memory/`
- **原因**：kernel 可升級（system prompts），memory 是用戶資料（升級不覆蓋）

### Memory 讀寫

- **選擇**：用 builtin file tools（`file_read`/`file_write`）
- **原因**：已有工具足夠，不重複造輪子
- **替代方案**：專用 `memory_read`/`memory_write`（多餘的包裝）

### Memory 搜尋

- **選擇**：Subagent 形式，獨立任務
- **原因**：避免污染 Brain context window
- **任務**：見 [memory-search.md](memory-search.md)

### 初始化 Agent

- **選擇**：獨立 Agent（`agents.init`）
- **原因**：特化任務需要特化 prompt，可用不同 LLM

### 模板存放

- **選擇**：`src/chat_agent/workspace/templates/`
- **原因**：程式碼一部分，初始化時複製到 agent_os_dir

## 檔案結構

### 原始碼（模板來源）

```
src/chat_agent/
├── workspace/                  # 新模組（管理工作目錄）
│   ├── __init__.py
│   ├── manager.py              # WorkspaceManager（路徑管理、狀態檢查）
│   ├── initializer.py          # 初始化邏輯（目錄建立、模板複製）
│   └── templates/              # 模板檔案（打包資源）
│       ├── kernel/             # 可升級的系統核心
│       │   ├── info.yaml       # 版本資訊
│       │   └── system-prompts/
│       │       ├── brain.md    # Brain Agent（bootloader）
│       │       └── init.md     # Init Agent
│       └── memory/             # 用戶資料（升級不覆蓋）
│           ├── agent/
│           │   ├── index.md            # 說明各目錄用途
│           │   ├── persona.md
│           │   ├── config.md
│           │   ├── inner-state.md
│           │   ├── pending-thoughts.md
│           │   ├── knowledge/
│           │   │   └── index.md
│           │   ├── thoughts/
│           │   │   └── index.md
│           │   ├── experiences/
│           │   │   └── index.md
│           │   ├── skills/
│           │   │   └── index.md
│           │   ├── interests/
│           │   │   └── index.md
│           │   └── journal/
│           │       └── index.md
│           ├── short-term.md
│           └── people/
│               └── index.md
│
├── tools/
│   └── builtin/
│       └── file.py             # Agent 用 file_read/write 存取 memory
│
├── core/
│   └── schema.py               # agent_os_dir 在 AppConfig 層級
│
└── cli/
    └── app.py                  # 整合初始化流程
```

### 執行時（agent_os_dir）

```
~/.agent/                       # agent_os_dir（預設值）
├── kernel/                     # 可升級的系統核心
│   ├── info.yaml               # 版本資訊
│   └── system-prompts/
│       ├── brain.md            # Brain Agent（bootloader）
│       └── init.md             # Init Agent
│
└── memory/                     # 用戶資料（升級不覆蓋）
    ├── agent/
    │   ├── index.md            # 說明各目錄用途
    │   ├── persona.md
    │   ├── config.md
    │   ├── inner-state.md
    │   ├── pending-thoughts.md
    │   ├── knowledge/
    │   │   └── index.md
    │   ├── thoughts/
    │   │   └── index.md
    │   ├── experiences/
    │   │   └── index.md
    │   ├── skills/
    │   │   └── index.md
    │   ├── interests/
    │   │   └── index.md
    │   └── journal/
    │       └── index.md
    ├── short-term.md
    └── people/
        └── index.md
```

## 技術設計

### WorkspaceManager

管理整個工作目錄（kernel + memory）。

```python
class WorkspaceManager:
    def __init__(self, agent_os_dir: Path):
        self.agent_os_dir = agent_os_dir
        self.kernel_dir = agent_os_dir / "kernel"
        self.memory_dir = agent_os_dir / "memory"
        self.system_prompts_dir = self.kernel_dir / "system-prompts"

    def is_initialized(self) -> bool:
        """Check if kernel/info.yaml exists"""

    def get_kernel_version(self) -> str:
        """Read version from kernel/info.yaml"""

    def get_system_prompt(self, agent_name: str) -> str:
        """Load system prompt for specified agent"""
        return (self.system_prompts_dir / f"{agent_name}.md").read_text()

    def resolve_memory_path(self, relative_path: str) -> Path:
        """Resolve memory path, ensure within memory_dir"""
```

### WorkspaceInitializer

```python
class WorkspaceInitializer:
    def __init__(self, manager: WorkspaceManager):
        self.manager = manager

    def create_structure(self) -> None:
        """Copy templates/ to agent_os_dir (kernel + memory)"""

    def needs_upgrade(self, target_version: str) -> bool:
        """Check if kernel upgrade needed"""

    def upgrade_kernel(self) -> None:
        """Upgrade kernel/ (preserve memory/)"""

    async def run_init_agent(self, llm_client: LLMClient) -> None:
        """Run init agent conversation"""
```

### Config 結構

```yaml
# config.yaml
agent_os_dir: ~/.agent           # AppConfig level

agents:
  brain:
    llm: llm/anthropic/claude.yaml
    # system prompt from agent_os_dir/kernel/system-prompts/brain.md

  init:
    llm: llm/openai/gpt4.yaml    # can use different LLM
    # system prompt from agent_os_dir/kernel/system-prompts/init.md
```

### 模板範例

#### kernel/info.yaml

```yaml
version: "0.1.0"
updated: "2025-01-30"
```

#### memory/agent/persona.md（極簡核心）

```markdown
# Persona

<!-- Agent's core identity. Filled during init, can be modified later. -->

## Identity

<!-- Who are you? Your name? How do you see yourself? -->
```

#### memory/people/user-xxx.md

```markdown
# User: {name}

<!-- Interaction records and relationship with this user -->

## Relationship

<!-- What is your relationship with this user? -->

## Memories

<!-- Important memories about this user -->
```

## 步驟

### Phase 1：基礎架構

1. **Config 調整**
   - `schema.py`: `AppConfig` 新增 `agent_os_dir: str = "~/.agent"`
   - 路徑展開邏輯（`~` -> 完整路徑）
   - 調整 `ToolsConfig` 參照 `AppConfig.agent_os_dir`

2. **Workspace 模組**
   - 建立 `src/chat_agent/workspace/` 目錄
   - 實作 `WorkspaceManager`
   - 實作 `WorkspaceInitializer.create_structure()`

3. **模板檔案**
   - 建立 `src/chat_agent/workspace/templates/` 完整結構
   - 包含 `kernel/` 和 `memory/` 子目錄
   - 建立引導式模板（persona.md, inner-state.md 等）
   - 建立 brain.md（bootloader）和 init.md

### Phase 2：CLI 整合

4. **init 子命令**
   - 新增 `uv run python -m chat_agent init`
   - 複製模板到 agent_os_dir
   - 啟動初始化 Agent 對話

5. **主命令整合**
   - 檢查 workspace 是否初始化
   - 載入 bootloader prompt
   - 確保 file tools 可存取 memory/

### Phase 3：測試

6. **測試**
   - WorkspaceManager 單元測試（`tests/workspace/`）
   - 初始化流程測試
   - Memory 存取測試（透過 file tools）

## 驗證

```bash
# Init
uv run python -m chat_agent init
# Expected: create ~/.agent/ (kernel/ + memory/), start init agent conversation

# Check structure
ls ~/.agent/
# Expected: kernel/, memory/

ls ~/.agent/kernel/
# Expected: info.yaml, system-prompts/

ls ~/.agent/kernel/system-prompts/
# Expected: brain.md, init.md

ls ~/.agent/memory/agent/
# Expected: index.md, persona.md, config.md, inner-state.md, pending-thoughts.md,
#           knowledge/, thoughts/, experiences/, skills/, interests/, journal/

# Start conversation
uv run python -m chat_agent
# Expected: Program loads brain.md as system prompt, Agent reads persona via file_read, responds with that personality

# Test memory access (via file tools)
# Ask agent to remember something (uses file_write to memory/)
# Restart and ask, confirm memory persists (uses file_read)
```

## 完成條件

- [x] Config 支援 agent_os_dir（AppConfig 層級）
- [x] Workspace 模組實作
- [x] 模板檔案建立（kernel + memory）
- [x] init 子命令
- [x] 主命令整合 bootloader
- [x] 測試覆蓋
