# 移除 Reviewer + Shutdown 系統

移除 PostReviewer、ProgressReviewer、Shutdown Agent，簡化 AgentCore，為 Phase 2（Message Queue）做準備。

## 背景

現有 reviewer 系統（PostReviewer + ProgressReviewer）在每個 turn 增加一次額外 LLM call，檢查回覆品質並強制 memory 寫入。Shutdown Agent 在對話結束時再跑一輪 LLM call 做記憶保存。

移除理由：

1. **Memory sync side-channel 已覆蓋 shutdown 核心價值**：每個 turn 結束時已偵測缺失的 memory 寫入並補寫，shutdown 再做一次是冗餘
2. **Reviewer cost/benefit 不划算**：每 turn 額外 LLM call（延遲 + 費用），retry loop 引入大量邊界情況（fail-closed、重複 signature、persistence action 等），實際攔截到的問題有限
3. **對 Phase 2 影響巨大**：core.py 從 1580 行降到 ~900 行，`run_turn()` 從 ~440 行降到 ~100 行，大幅降低 queue-based 改造風險

## 設計決策

### 共用工具函式搬遷

- **`json_extract.py`**：搬到 `llm/json_extract.py`
- **原因**：被 `memory/search.py`、`memory/editor/planner.py`、`gui/worker.py` 使用，功能是解析 LLM 輸出中的 JSON，語義上屬於 LLM 工具
- **影響**：3 個檔案的 import path 更新

### Memory sync 相關函式搬遷

- **選擇**：從 `reviewer/enforcement.py` 提取需要的函式，搬到 `memory/tool_analysis.py`
- **原因**：這些函式分析 tool call 結果判斷 memory 寫入狀態，語義屬於 memory 系統
- **搬遷清單**（含內部依賴）：

| 函式 | 說明 | 被誰使用 |
|------|------|----------|
| `find_missing_memory_sync_targets()` | 偵測缺失的 sync targets | `agent/core.py` memory sync |
| `MEMORY_SYNC_TARGETS` | 常數 | 上述函式 |
| `extract_memory_edit_paths()` | 解析 memory_edit tool call 的路徑 | `agent/core.py` `_TurnMemorySnapshot` |
| `is_failed_memory_edit_result()` | 判斷 memory_edit 結果是否失敗 | `agent/core.py` `_run_responder` |
| `collect_turn_tool_calls()` | 收集 turn 中的 tool calls | 內部依賴 |
| `_collect_memory_write_paths()` | 收集成功寫入的 memory 路徑 | 內部依賴 |
| `_extract_applied_paths_from_result()` | 解析 memory_edit 結果中的 applied 路徑 | 內部依賴 |
| `_collect_failed_tool_call_ids()` | 收集失敗的 tool call IDs | 內部依賴 |
| `_is_failed_tool_result_message()` | 判斷 tool result 是否為失敗 | 內部依賴 |

- **Export**：`find_missing_memory_sync_targets`、`extract_memory_edit_paths`、`is_failed_memory_edit_result` 從 `memory/__init__.py` 匯出

### AgentConfig 欄位清理

- **移除**（僅 reviewer 使用）：`max_post_retries`、`allow_unresolved`、`history_turns`、`history_turn_max_chars`、`reply_max_chars`、`tool_preview_max_chars`
- **保留**：`pre_parse_retries`（memory_searcher）、`post_parse_retries`（memory_editor planner）、`warn_on_failure`（通用）

### `/shutdown` CLI command 移除

- **選擇**：移除 `/shutdown` 指令和 `CommandResult.SHUTDOWN`
- **原因**：`/shutdown` 的核心功能是觸發 shutdown agent 做最後一輪 LLM 記憶保存。移除 shutdown agent 後，`/shutdown` 跟 Ctrl+D（`graceful_exit()`）行為完全相同：session finalize + memory archive + goodbye，不再有 LLM 呼叫。保留兩個功能相同的退出方式沒有意義
- **退出方式**：
  - **Ctrl+D**：`graceful_exit()`（session finalize + archive + backup + goodbye）— 正常退出
  - **`/exit`**：quick exit（session finalize + goodbye，跳過 archive/backup）— 快速退出

### Migration 策略

- **新 migration m0071**：從 kernel 刪除 `agents/post_reviewer/`、`agents/progress_reviewer/`、`agents/shutdown_reviewer/` 目錄，以及 `agents/brain/prompts/shutdown.md`
- **舊 migrations 不動**：歷史記錄保持原樣
- **Templates**：同步刪除對應的 template 檔案

## 檔案結構

```
src/chat_agent/
├── agent/
│   ├── core.py               # 移除 reviewer imports/params/helpers/post-review loop
│   ├── shutdown.py            # 刪除
│   └── __init__.py            # 移除 shutdown export
├── cli/
│   ├── app.py                 # 移除 reviewer 初始化（~120 行）、SHUTDOWN command handling
│   └── commands.py            # 移除 /shutdown 指令、CommandResult.SHUTDOWN
├── core/
│   └── schema.py              # AgentConfig 移除 6 個 reviewer 欄位
├── llm/
│   └── json_extract.py        # 從 reviewer/ 搬來
├── memory/
│   ├── __init__.py            # 新增 3 個 export
│   └── tool_analysis.py       # 新建，從 enforcement.py 提取
├── reviewer/                  # 整個刪除（9 個檔案）
└── workspace/
    ├── templates/kernel/agents/
    │   ├── post_reviewer/     # 刪除
    │   ├── progress_reviewer/ # 刪除
    │   ├── shutdown_reviewer/ # 刪除
    │   └── brain/prompts/
    │       └── shutdown.md    # 刪除
    └── migrations/
        └── m0071_remove_reviewer_shutdown.py  # 新建
```

## 技術設計

### core.py 簡化後的 run_turn()

```python
def run_turn(self, user_input: str) -> None:
    pre_turn_anchor = len(self.conversation.get_messages())
    self.conversation.add("user", user_input)
    self.has_new_user_content = True
    messages = self.builder.build(self.conversation)

    # Truncation → new session
    if self.builder.last_was_truncated:
        self.session_mgr.finalize("truncated")
        self.session_mgr.create(self.user_id, self.display_name)
        self.conversation._on_message = self.session_mgr.append_message

    turn_memory_snapshot = _TurnMemorySnapshot(agent_os_dir=self.agent_os_dir)
    turn_anchor = len(self.conversation.get_messages())

    esc_monitor = EscInterruptMonitor()
    try:
        esc_monitor.start()
        tools = self.registry.get_definitions()

        # === Responder ===
        response = _run_responder(
            self.client, messages, tools,
            self.conversation, self.builder, self.registry, self.console,
            on_before_tool_call=turn_memory_snapshot.capture_from_tool_call,
            memory_edit_allow_failure=self.memory_edit_allow_failure,
        )
        final_content, used_fallback_content = _resolve_final_content(
            response.content,
            self.conversation.get_messages()[turn_anchor:],
        )

        # === Memory sync side-channel ===
        sync_turn_messages = self.conversation.get_messages()[turn_anchor:]
        missing_sync = find_missing_memory_sync_targets(sync_turn_messages)
        if missing_sync:
            # ... (same as current, ~20 lines)

        # === Finalize response ===
        if final_content and not used_fallback_content:
            self.conversation.add("assistant", final_content)
        elif not final_content:
            turn_msgs = self.conversation.get_messages()[turn_anchor:]
            intermediate = _latest_intermediate_text(turn_msgs)
            if intermediate:
                self.conversation.add("assistant", intermediate)
        if not used_fallback_content:
            self.console.print_assistant(final_content)

        # === Post-turn hooks ===
        _run_memory_archive(self.agent_os_dir, self.config, self.console)
        _run_memory_backup(self.memory_backup_mgr)

    except ContextLengthExceededError:
        # ... (same as current)
    except KeyboardInterrupt:
        # ... (same as current)
    except Exception as e:
        # ... (same as current)
    finally:
        esc_monitor.stop()
```

### core.py 簡化後的 graceful_exit()

```python
def graceful_exit(self) -> None:
    if self.session_mgr is not None:
        self.session_mgr.finalize("completed")

    if self.agent_os_dir and self.config:
        _run_memory_archive(self.agent_os_dir, self.config, self.console)
        if self.config.hooks.session_cleanup.enabled:
            # session cleanup (same as current)

    _run_memory_backup(self.memory_backup_mgr)
    self.console.print_goodbye()
```

### core.py 簡化後的 AgentCore.__init__

```python
class AgentCore:
    def __init__(
        self,
        *,
        client: LLMClient,
        conversation: Conversation,
        builder: ContextBuilder,
        registry: ToolRegistry,
        console: ChatConsole,
        workspace: WorkspaceManager,
        config: AppConfig,
        agent_os_dir: Path,
        user_id: str,
        session_mgr: SessionManager | None = None,
        display_name: str = "",
        # Memory
        memory_edit_allow_failure: bool = False,
        memory_backup_mgr: MemoryBackupManager | None = None,
    ):
```

### _run_responder 簡化

移除 `progress_reviewer` 和 `progress_review_warn_on_failure` 參數。tool loop 中的 progress review 區塊（~40 行）全部移除。

### memory/tool_analysis.py

```python
"""Analyze tool call messages for memory-related state."""

from ..llm.content import content_to_text
from ..llm.schema import Message, ToolCall

MEMORY_SYNC_TARGETS: tuple[str, ...] = (
    "memory/agent/short-term.md",
    "memory/agent/inner-state.md",
)

def find_missing_memory_sync_targets(
    turn_messages: list[Message],
    targets: tuple[str, ...] = MEMORY_SYNC_TARGETS,
) -> list[str]: ...

def extract_memory_edit_paths(tool_call: ToolCall) -> list[str]: ...

def is_failed_memory_edit_result(result: str) -> bool: ...

# Internal helpers (private):
# _collect_memory_write_paths, _extract_applied_paths_from_result,
# collect_turn_tool_calls, _collect_failed_tool_call_ids,
# _is_failed_tool_result_message
```

### m0071 migration

```python
"""Remove reviewer and shutdown agent templates from kernel."""

def upgrade(kernel_dir: Path) -> None:
    for name in ("post_reviewer", "progress_reviewer", "shutdown_reviewer"):
        agent_dir = kernel_dir / "agents" / name
        if agent_dir.exists():
            shutil.rmtree(agent_dir)

    shutdown_prompt = kernel_dir / "agents" / "brain" / "prompts" / "shutdown.md"
    if shutdown_prompt.exists():
        shutdown_prompt.unlink()
```

## 步驟

1. 建立 `llm/json_extract.py`（從 `reviewer/json_extract.py` 搬移），更新 3 個消費者的 import（`memory/search.py`、`memory/editor/planner.py`、`gui/worker.py`）
2. 建立 `memory/tool_analysis.py`（從 `reviewer/enforcement.py` 提取 9 個函式），更新 `memory/__init__.py` export
3. 更新 `agent/core.py`：
   - import 改為從 `memory` 取 `find_missing_memory_sync_targets`、`extract_memory_edit_paths`、`is_failed_memory_edit_result`；從 `llm.json_extract` 取 `extract_json_object`（如仍需要）
   - 移除所有 reviewer imports（`PostReviewer`、`ProgressReviewer`、`RequiredAction`、`ReviewPacketConfig`、`build_post_review_packet`、enforcement 函式、schema 型別）
   - 移除 reviewer 相關的 `__init__` 參數（12 個）及對應的 instance attributes
   - 移除 post-review helper functions（`_build_post_review_packet_messages` 到 `_format_anomaly_retry_instruction`，約 15 個函式）
   - 移除 `_has_memory_write`、`_is_memory_write_shell_command`、`_build_memory_shell_write_patterns`（僅 post-review 使用）
   - 移除 `_turn_has_visible_intermediate_text`（僅 post-review 使用）
   - 移除 `_format_debug_json`、`_build_reviewer_warning`（僅 reviewer debug 使用）
   - 簡化 `run_turn()`：移除整個 post-review while loop（1172-1433 行），保留 else branch 邏輯作為主路徑
   - 簡化 `graceful_exit()`：移除 `perform_shutdown` 呼叫及 shutdown 相關邏輯
   - 移除 `_run_responder` 的 `progress_reviewer` 參數和 progress review 區塊（~40 行）
   - 移除 shutdown import（`from .shutdown import perform_shutdown, _has_conversation_content`）
4. 刪除 `agent/shutdown.py`，更新 `agent/__init__.py`
5. 更新 `cli/app.py`：移除 post_reviewer、progress_reviewer、shutdown_reviewer 的初始化區塊（~120 行），移除 AgentCore 建構時的 reviewer 參數，移除 `CommandResult.SHUTDOWN` 分支
6. 更新 `cli/commands.py`：移除 `/shutdown` 指令、`CommandResult.SHUTDOWN` 枚舉值、`_shutdown` 方法
7. 更新 `core/schema.py`：從 `AgentConfig` 移除 6 個 reviewer-only 欄位
7. 更新 `cfgs/agent.yaml`：移除 `post_reviewer`、`progress_reviewer`、`shutdown_reviewer` 區塊
8. 刪除 `src/chat_agent/reviewer/` 整個模組（9 個檔案）
9. 刪除 template 檔案：`templates/kernel/agents/post_reviewer/`、`progress_reviewer/`、`shutdown_reviewer/`、`brain/prompts/shutdown.md`
10. 建立 migration `m0071_remove_reviewer_shutdown.py`
11. 更新測試：
    - 刪除 `tests/reviewer/`（整個目錄，6 個檔案）
    - 刪除 `tests/cli/test_app_retry.py`
    - 刪除 `tests/cli/test_shutdown.py`
    - 更新 `tests/cli/test_memory_searcher_wiring.py`：移除 reviewer config entries、`_DummyPostReviewer`、reviewer 相關測試
    - 更新 `tests/workspace/test_initializer.py`：移除對 reviewer template 檔案存在性的檢查
12. `uv run pytest` 全部通過

## 驗證

- `uv run pytest` 全部通過
- CLI 行為不變（對話、tool call、memory sync、session resume）
- 無循環依賴（`python -c "from chat_agent.memory.tool_analysis import find_missing_memory_sync_targets"` 成功）
- `import chat_agent.reviewer` 應失敗（模組已刪除）

## 完成條件

- [ ] `llm/json_extract.py` 建立，3 個消費者 import 更新
- [ ] `memory/tool_analysis.py` 建立，9 個函式從 enforcement.py 提取
- [ ] `agent/core.py` 移除所有 reviewer/shutdown 相關程式碼
- [ ] `agent/shutdown.py` 刪除
- [ ] `cli/app.py` 移除 reviewer 初始化、SHUTDOWN 分支
- [ ] `cli/commands.py` 移除 `/shutdown` 指令和 `CommandResult.SHUTDOWN`
- [ ] `core/schema.py` 移除 6 個 reviewer-only 欄位
- [ ] `cfgs/agent.yaml` 移除 3 個 reviewer agent 區塊
- [ ] `reviewer/` 模組刪除
- [ ] Templates 中 3 個 reviewer agent + brain/shutdown.md 刪除
- [ ] m0071 migration 建立
- [ ] 相關測試刪除/更新
- [ ] 現有測試全過
