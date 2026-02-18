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

## 檔案結構

```
src/chat_agent/
├── agent/                    # 新增
│   ├── __init__.py
│   └── core.py               # AgentCore：conversation、builder、responder、reviewer、memory sync
├── cli/
│   ├── app.py                # 瘦身：初始化 + AgentCore + input/output loop
│   └── ...                   # 其餘不動
└── ...
```

## 步驟

1. 建立 `src/chat_agent/agent/` package
2. 將 `cli/app.py` 中 agent 邏輯（`_run_responder`、`_run_memory_sync_side_channel`、post-review loop、`setup_tools`、memory snapshot/rollback、helper functions）搬到 `agent/core.py`
3. `cli/app.py::main()` 改為：初始化 config → 建立 AgentCore → while-True 讀 input → 呼叫 AgentCore 處理 → 顯示 output
4. `cli/shutdown.py` 的 `perform_shutdown` 由 AgentCore 呼叫，不再由 CLI 直接呼叫
5. 確認所有 import 正確、無循環依賴

## 驗證

- `uv run pytest` 全部通過（現有 697+ 測試）
- CLI 行為完全不變（手動測試一輪對話 + shutdown + resume）
- 不新增任何功能

## 完成條件

- [ ] `src/chat_agent/agent/core.py` 建立，包含核心 agent 邏輯
- [ ] `cli/app.py` 瘦身為初始化 + input/output loop
- [ ] 現有測試全過
- [ ] CLI 功能不變（對話、tool call、reviewer、shutdown、session resume）
