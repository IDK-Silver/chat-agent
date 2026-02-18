# Phase 1：抽出 Agent Core

從 `cli/app.py::main()` 抽出 agent 核心邏輯，CLI 瘦身為 adapter。

## 背景

詳見設計文件 [message-queue.md](message-queue.md)。

目前所有 agent 邏輯（conversation、builder、responder、reviewer、memory sync、shutdown）都在 `cli/app.py::main()` 裡，約 2000 行。要接入第三方訊息，必須先把核心邏輯獨立出來。

## 設計決策

### 模組位置

- **選擇**：`src/chat_agent/agent/core.py`
- **原因**：agent 是獨立於 CLI 的核心概念，不應放在 `cli/` 下
- **替代方案**：放在 `cli/` 下重構（但這會讓非 CLI adapter 依賴 cli package）

### 重構範圍

- **選擇**：純搬移，不改功能
- **原因**：Phase 1 目標是結構分離，功能變更留給 Phase 2
- 現有 `_run_responder`、post-review loop、memory sync side-channel 等函式原封搬到 agent core

### Console 依賴策略

- **選擇**：Phase 1 保留 `ChatConsole` 作為 AgentCore 參數傳入
- **原因**：純搬移原則，不引入新抽象層。AgentCore 內部照常呼叫 `console.print_*`、`console.spinner()` 等
- **替代方案**：抽出 output protocol（Phase 2 再做，配合 Channel Adapter 引入）
- **依賴方向**：`agent/` → `cli.console`（僅 output layer），`cli.console` 不 import `agent/` 或 `cli.app`，無循環依賴

### 初始化責任

- **選擇**：AgentCore 接收預建好的依賴（client、builder、registry 等），不負責建立
- **原因**：初始化涉及 config 解析、LLM client 建立、reviewer 配置等，屬於 app 啟動邏輯。AgentCore 專注 turn-level 邏輯
- **替代方案**：AgentCore.from_config() 工廠方法（可在 Phase 2 視需要加入）

### shutdown.py 搬移

- **選擇**：`cli/shutdown.py` 一起搬到 `agent/shutdown.py`
- **原因**：shutdown 是 agent 邏輯（LLM 記憶存檔），若留在 `cli/` 會造成 `agent/` → `cli/` 反向依賴
- **影響**：`cli/app.py` 不再直接 import shutdown，改由 AgentCore 內部呼叫

### 檔案拆分

- **選擇**：所有 helper functions + AgentCore class 放同一個 `agent/core.py`（約 1400 行）
- **原因**：「純搬移」最小風險，所有函式維持原有的 module-level private 語義
- **替代方案**：按 concern 拆分（`agent/responder.py`、`agent/review.py` 等）。可後續再拆，但 Phase 1 不增加變數

## 檔案結構

```
src/chat_agent/
├── agent/                    # 新增
│   ├── __init__.py           # export AgentCore, setup_tools
│   ├── core.py               # AgentCore class + helpers + responder + memory sync + post-review
│   └── shutdown.py           # 從 cli/shutdown.py 搬移，原封不動
├── cli/
│   ├── __init__.py           # 不動（lazy proxy to app.main）
│   ├── app.py                # 瘦身：初始化 + AgentCore + input/output loop（~500 行）
│   ├── console.py            # 不動（output layer）
│   ├── commands.py           # 不動
│   ├── input.py              # 不動
│   ├── interrupt.py          # 不動
│   ├── picker.py             # 不動
│   └── formatter.py          # 不動
└── ...                       # 其餘 package 不動
```

## 技術設計

### AgentCore class

封裝完整的 turn 生命週期，取代 `cli/app.py` main loop body。

```python
class AgentCore:
    """Core agent logic: responder + memory sync + post-review + shutdown."""

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
        # Post-review
        post_reviewer: PostReviewer | None = None,
        post_max_retries: int = 2,
        post_allow_unresolved: bool = False,
        post_warn_on_failure: bool = True,
        post_review_packet_config: ReviewPacketConfig | None = None,
        # Progress review
        progress_reviewer: ProgressReviewer | None = None,
        progress_warn_on_failure: bool = True,
        # Shutdown review
        shutdown_reviewer: PostReviewer | None = None,
        shutdown_reviewer_max_retries: int = 0,
        shutdown_allow_unresolved: bool = False,
        shutdown_reviewer_warn_on_failure: bool = True,
        # Memory
        memory_edit_allow_failure: bool = False,
        memory_backup_mgr: MemoryBackupManager | None = None,
    ):
        # Store all as instance attributes
        ...
        self.has_new_user_content: bool = False

    def run_turn(self, user_input: str) -> None:
        """Process one user turn.

        Full lifecycle:
        1. Add user message to conversation
        2. Responder (LLM + tool loop)
        3. Memory sync side-channel
        4. Post-review retry loop
        5. Memory archive + backup hooks

        Handles ContextLengthExceededError (reduce preserve_turns + retry),
        KeyboardInterrupt (patch incomplete tool calls), and general exceptions
        (rollback memory + restore conversation).

        Output goes through self.console.
        """

    def graceful_exit(self) -> None:
        """Handle graceful exit.

        1. Finalize session
        2. perform_shutdown() if has_new_user_content
        3. Memory archive + backup
        4. Print goodbye
        """
```

### 搬移清單

#### `cli/app.py` → `agent/core.py`（約 1400 行）

**Constants & regex patterns**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_MEMORY_EDIT_RETRY_LIMIT` | 95 | memory_edit 連續失敗上限 |
| `_DEBUG_RESPONSE_PREVIEW_CHARS` | 96 | debug 預覽字數 |
| `_SENSITIVE_URL_PARAM_RE` | 97 | URL 中的敏感參數 regex |
| `_GOOGLE_API_KEY_RE` | 98 | Google API key regex |

**Classes**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_MemoryFileSnapshot` | 217-223 | 單一 memory 檔案快照 |
| `_TurnMemorySnapshot` | 226-297 | Turn 層級 memory 快照管理 |

**Content resolution helpers**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_latest_nonempty_assistant_content` | 102-110 | 取最後非 tool 的 assistant 內容 |
| `_latest_intermediate_text` | 113-121 | 取最後 tool-call 中的 assistant 文字 |
| `_resolve_final_content` | 124-136 | 解析最終回覆（含 fallback） |
| `_turn_has_visible_intermediate_text` | 139-147 | 檢查是否有已顯示的中間文字 |

**Debug & output helpers**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_debug_print_responder_output` | 150-187 | Debug 印出 responder 結果摘要 |
| `_format_debug_json` | 665-670 | Debug JSON 格式化 |
| `_build_reviewer_warning` | 673-688 | Reviewer 失敗警告訊息 |
| `_sanitize_error_message` | 691-694 | 遮蔽 API key |

**Memory path validation**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_normalize_memory_path` | 190-192 | 正規化路徑格式 |
| `_is_memory_path` | 195-214 | 判斷是否為 memory 路徑 |
| `_build_memory_shell_write_patterns` | 300-311 | Shell memory 寫入偵測 patterns |
| `_is_memory_write_shell_command` | 314-319 | 偵測 shell 寫 memory |
| `_has_memory_write` | 322-335 | 檢查 turn 是否有 memory 寫入 |

**Post-review helpers**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_build_post_review_packet_messages` | 338-360 | 限定 review 範圍 |
| `_filter_retry_violations` | 363-375 | 過濾已解決的 violations |
| `_collect_required_actions_for_retry` | 378-392 | 收集需重試的 actions |
| `_build_turn_persistence_action` | 395-405 | 建立 fallback persistence action |
| `_ensure_turn_persistence_action` | 408-421 | 確保有 persistence action |
| `_build_memory_edit_retry_hints` | 424-479 | 建立 memory_edit 重試提示 |
| `_build_memory_sync_reminder` | 482-489 | 建立 memory-sync 提醒 |
| `_build_retry_directive` | 492-571 | 建立完整重試指令 |
| `_build_missing_visible_reply_directive` | 574-598 | 建立缺失回覆重試指令 |

**Review state helpers**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_resolve_effective_target_signals` | 601-614 | 合併 target signals |
| `_promote_anomaly_targets_to_sticky` | 617-630 | 升級 anomaly targets |
| `_action_signature` | 633-650 | 建立 retry loop guard 簽名 |
| `_format_anomaly_retry_instruction` | 653-662 | 格式化 anomaly 指令 |

**Memory snapshot helper**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `_rollback_turn_memory_changes` | 697-712 | 執行 memory rollback |

**Core functions**：

| Symbol | 行數 | 說明 |
|--------|------|------|
| `setup_tools` | 715-849 | 建立 tool registry |
| `_run_responder` | 877-989 | LLM + tool call loop |
| `_run_memory_sync_side_channel` | 992-1031 | Memory sync 側通道 |
| `_run_memory_archive` | 1033-1040 | Memory archive hook |
| `_run_memory_backup` | 1043-1050 | Memory backup hook |
| `_patch_interrupted_tool_calls` | 852-874 | 修補中斷的 tool calls |

**原 `_graceful_exit` (1053-1111)**：邏輯搬入 `AgentCore.graceful_exit()`。

**原 main loop body (1640-2063)**：邏輯搬入 `AgentCore.run_turn()`。

#### `cli/shutdown.py` → `agent/shutdown.py`（276 行，原封搬移）

| Symbol | 說明 |
|--------|------|
| `_has_conversation_content` | 檢查 conversation 是否有 user messages |
| `_get_last_user_timestamp` | 取得最後 user message 時間戳 |
| `_build_shutdown_retry_prompt` | 建立 shutdown retry prompt |
| `_run_shutdown_tool_loop` | Shutdown 的 tool call loop |
| `perform_shutdown` | Shutdown 主流程 |
| `_MAX_TOOL_ITERATIONS` | Constant |
| `_MEMORY_EDIT_RETRY_LIMIT` | Constant |

Import 更新：`from .console import ChatConsole` → `from ..cli.console import ChatConsole`

#### 留在 `cli/app.py`（約 500 行）

| Symbol | 說明 |
|--------|------|
| `_DebugConsoleHandler` | CLI-specific logging handler |
| `main()` | 初始化 + 建立 AgentCore + input loop + command dispatch |

`main()` 重構後結構：

```python
def main(user: str, resume: str | None = None) -> None:
    # === 初始化（同現有，約 400 行） ===
    # config, workspace, user, system_prompt
    # LLM clients (brain, memory_editor, reviewers)
    # conversation, builder, session_mgr
    # registry = setup_tools(...)  # 從 agent.core import
    # reviewers (post, progress, shutdown)

    # === 建立 AgentCore ===
    agent = AgentCore(
        client=client, conversation=conversation, builder=builder,
        registry=registry, console=console, workspace=workspace,
        config=config, agent_os_dir=agent_os_dir, user_id=user_id,
        session_mgr=session_mgr, display_name=display_name,
        post_reviewer=post_reviewer, post_max_retries=post_max_retries,
        # ... 其餘 reviewer 參數
        memory_edit_allow_failure=memory_edit_allow_failure,
        memory_backup_mgr=memory_backup_mgr,
    )

    # === Main loop（約 100 行） ===
    while True:
        user_input = chat_input.get_input()
        if user_input is None:
            agent.graceful_exit()
            break

        # CLI-specific: double-ESC history rollback
        if chat_input.wants_history_select:
            ...
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        # CLI-specific: slash commands
        if commands.is_command(user_input):
            result = commands.execute(user_input)
            if result == CommandResult.SHUTDOWN:
                agent.graceful_exit()
                break
            elif result == CommandResult.EXIT:
                session_mgr.finalize("exited")
                console.print_goodbye()
                break
            elif result == CommandResult.CLEAR:
                conversation.clear()
            elif result == CommandResult.COMPACT:
                ...
            elif result == CommandResult.RELOAD_SYSTEM_PROMPT:
                ...
            continue

        agent.run_turn(user_input)
```

### 依賴方向

```
cli/app.py ──→ agent/core.py ──→ cli/console.py (output layer only)
             → agent/shutdown.py → cli/console.py
```

`agent/` 唯一的 `cli/` 依賴是 `cli.console.ChatConsole`（輸出層）。`cli.console` 不 import `agent/` 或 `cli.app`，無循環依賴。Phase 2 可將 ChatConsole 抽為 Protocol 放到 `agent/output.py`。

### 測試遷移

4 個測試檔需更新 import path：

| 測試檔 | 原 import | 新 import |
|--------|----------|----------|
| `tests/cli/test_app_retry.py` | `chat_agent.cli.app` | `chat_agent.agent.core`（18 個 symbols） |
| `tests/cli/test_app_interrupt.py` | `chat_agent.cli.app` | `chat_agent.agent.core` |
| `tests/cli/test_vision_wiring.py` | `chat_agent.cli.app` | `chat_agent.agent.core` |
| `tests/cli/test_shutdown.py` | `chat_agent.cli.shutdown` | `chat_agent.agent.shutdown` |

## 步驟

1. 建立 `src/chat_agent/agent/` package，寫 `__init__.py`
2. 搬移 `cli/shutdown.py` → `agent/shutdown.py`，更新內部 import（`from .console` → `from ..cli.console`）
3. 建立 `agent/core.py`，搬移 constants + regex patterns
4. 搬移 helper functions（content resolution、memory path、memory snapshot、post-review、review state、debug）和 classes（`_MemoryFileSnapshot`、`_TurnMemorySnapshot`）
5. 搬移 core functions（`setup_tools`、`_run_responder`、`_run_memory_sync_side_channel`、`_run_memory_archive`、`_run_memory_backup`、`_patch_interrupted_tool_calls`、`_rollback_turn_memory_changes`）
6. 建立 `AgentCore` class，將 `main()` loop body（1640-2063 行）封裝為 `run_turn()`，將 `_graceful_exit`（1053-1111 行）封裝為 `graceful_exit()`
7. 瘦身 `cli/app.py::main()`：保留初始化邏輯 → 建立 AgentCore → input loop → command dispatch。從 `agent.core` import `AgentCore` 和 `setup_tools`
8. 更新 4 個測試檔的 import paths
9. 更新 `agent/__init__.py` export `AgentCore` 和 `setup_tools`

## 驗證

- `uv run pytest` 全部通過（現有 697+ 測試）
- CLI 行為完全不變（手動測試一輪對話 + shutdown + resume）
- 無循環依賴（`python -c "from chat_agent.agent import AgentCore"` 成功）
- 不新增任何功能

## 完成條件

- [x] `src/chat_agent/agent/core.py` 建立，包含 AgentCore class + 所有 helper functions（1580 行）
- [x] `src/chat_agent/agent/shutdown.py` 建立，從 `cli/shutdown.py` 搬移
- [x] `cli/app.py` 瘦身為初始化 + input/output loop（575 行）
- [x] `cli/shutdown.py` 刪除
- [x] 4 個測試檔 import path 更新 + 1 個測試檔 monkeypatch target 更新
- [x] 現有測試全過（697 passed）
- [ ] CLI 功能不變（對話、tool call、reviewer、shutdown、session resume）—— 待手動驗證
