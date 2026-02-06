# Kernel 目錄重構與 Shutdown Prompt

重構 kernel 目錄結構，將 prompts 按 agent 組織，並新增 shutdown prompt。

## 背景

現有結構 `system-prompts/{agent}.md` 不夠靈活。需要支持每個 agent 有多個 prompts（如 system、shutdown 等事件），改為 `agents/{agent}/prompts/` 結構。

## 設計決策

### 目錄結構

- **選擇**：`agents/{agent}/prompts/{prompt}.md`
- **原因**：每個 agent 可有多個 prompts，未來可擴展（如 config.yaml）
- **替代方案**：扁平結構（不利於擴展）

### Shutdown Prompt 內容

- **選擇**：使用繁體中文，指示必要與可選任務
- **原因**：符合專案語言規範，Agent 記憶系統使用繁體中文
- **替代方案**：英文（與記憶系統語言不一致）

## 檔案結構

### 現有結構
```
templates/kernel/
├── info.yaml
└── system-prompts/
    ├── brain.md
    └── init.md
```

### 新結構
```
templates/kernel/
├── info.yaml
└── agents/
    ├── brain/
    │   └── prompts/
    │       ├── system.md      # 原 brain.md
    │       └── shutdown.md    # 新增
    └── init/
        └── prompts/
            └── system.md      # 原 init.md
```

## 技術設計

### WorkspaceManager 新方法

```python
def get_agent_prompt(
    self,
    agent_name: str,
    prompt_name: str,
    current_user: str | None = None,
) -> str:
    """Load prompt from agents/{agent}/prompts/{prompt}.md"""

def get_system_prompt(self, agent_name: str, current_user: str | None = None) -> str:
    """Backward compatible wrapper."""
    return self.get_agent_prompt(agent_name, "system", current_user)
```

### M0002AgentsStructure Migration

```python
class M0002AgentsStructure(Migration):
    version = "0.2.0"

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # 1. Remove old system-prompts/
        # 2. Copy new agents/ structure
        # 3. Update info.yaml version
```

### Shutdown Prompt 佔位符

- `{current_user}` - 當前用戶 ID
- `{date}` - 今日日期（ISO 格式）

## 步驟

1. 建立 `templates/kernel/agents/brain/prompts/` 目錄
2. 移動 `brain.md` 至 `agents/brain/prompts/system.md`
3. 建立 `agents/brain/prompts/shutdown.md`
4. 建立 `templates/kernel/agents/init/prompts/` 目錄
5. 移動 `init.md` 至 `agents/init/prompts/system.md`
6. 刪除 `templates/kernel/system-prompts/` 目錄
7. 更新 `templates/kernel/info.yaml` 版本為 0.2.0
8. 修改 `WorkspaceManager` 新增 `get_agent_prompt()`
9. 建立 `migrations/m0002_agents_structure.py`
10. 在 `migrations/__init__.py` 註冊 M0002

## 驗證

- `uv run python -m chat_agent init`（新 workspace）使用新結構
- 現有 workspace 執行 `--user` 時自動升級到新結構
- `workspace.get_system_prompt("brain")` 可正常載入
- `workspace.get_agent_prompt("brain", "shutdown")` 可正常載入

## 完成條件

- [ ] templates 目錄重構完成
- [ ] shutdown.md 建立
- [ ] WorkspaceManager.get_agent_prompt() 可用
- [ ] M0002 migration 建立並註冊
- [ ] info.yaml 版本更新為 0.2.0

## 依賴

- migration-system.md（需要 Migration 系統）
