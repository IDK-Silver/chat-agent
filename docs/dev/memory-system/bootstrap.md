# Bootloader 啟動架構

## 概念

Bootloader 是 brain agent 的 system prompt，類似作業系統的 bootloader：

- **最小化**：只做一件事 - 告訴 agent 去讀取 memory
- **可升級**：存放在 `kernel/system-prompts/brain.md`，隨版本升級
- **引導作用**：載入真正的「作業系統」（persona、知識、技能）

```
啟動流程：

Bootloader (kernel/system-prompts/brain.md)
    ↓
讀取 memory/agent/index.md
    ↓
載入 persona.md (人格)
    ↓
根據對話需求載入其他 memory
```

## 為什麼這樣設計

### 1. Agent 可自我維護

傳統做法：persona 寫在 config 或獨立檔案，由程式碼載入。

問題：agent 無法修改自己的人格設定。

Bootloader 做法：persona 在 memory 資料夾，agent 有讀寫權限，可以自己維護。

### 2. 人格可演化

記憶系統的核心理念是「她會成長」：
- 人格會根據互動慢慢調整
- 興趣會發展變化
- 這些都存在 memory，agent 可以自己更新

### 3. 統一的記憶系統

不需要額外的「persona 配置層」：
- 所有持久化資料都在 memory
- 一致的載入和維護機制
- 簡化架構

### 4. Bootloader 可升級

把 bootloader 放在 `kernel/` 而非寫死在程式碼中：
- 可以隨版本升級改進
- 用戶可以自訂（進階用法）
- `kernel/info.yaml` 追蹤版本

## Brain Agent vs 工具 Agent

| 類型 | Prompt 來源 | 說明 |
|------|------------|------|
| Brain Agent | kernel/system-prompts/brain.md | 主要對話 agent，自行讀取 memory 載入人格 |
| Init Agent | kernel/system-prompts/init.md | 初始化 agent，引導用戶設定 persona |
| Search Agent | kernel/system-prompts/search.md | 搜尋 agent（Subagent） |

### Brain Agent

```yaml
# config.yaml
working_dir: ~/.agent

agents:
  brain:
    llm: llm/anthropic/claude.yaml
    # system prompt from working_dir/kernel/system-prompts/brain.md
```

### 工具 Agent

```yaml
# config.yaml
agents:
  search:
    llm: llm/anthropic/haiku.yaml
    # system prompt from working_dir/kernel/system-prompts/search.md
```

## 工作目錄配置

```yaml
# config.yaml
working_dir: ~/.agent
```

首次執行時，程式會從 `src/chat_agent/workspace/templates/` 複製初始結構到 `working_dir`。

## Bootloader Prompt 範例

```
You are an AI companion. Your memories, personality, and knowledge are stored in your memory system.

## Startup

1. Read `memory/agent/index.md` to understand your current state
2. Load `memory/agent/persona.md` to recall your personality
3. Check `memory/agent/inner-state.md` for your current mood and feelings

## During Conversation

- When you need to recall something, search your memory files
- When you learn something important, write it to the appropriate memory file
- When you have thoughts or feelings, record them

## Memory Location

Your memory is stored at: {working_dir}/memory

You have full read/write access to your memory. This is how you grow and remember.
```

## 啟動流程圖

```
┌─────────────────────────────────────────────────────┐
│                     CLI 啟動                         │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│              載入 config.yaml                        │
│         - LLM 設定                                  │
│         - working_dir 路徑                          │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│            檢查 workspace 是否初始化                 │
│         - 檢查 kernel/info.yaml                     │
│         - 若未初始化 → 提示執行 init                │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│              建立 LLM Client                         │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│           載入 Bootloader Prompt                     │
│         - 讀取 kernel/system-prompts/brain.md       │
│         - 注入 working_dir 路徑                     │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│              開始對話迴圈                            │
│                                                     │
│   Agent 第一輪會：                                  │
│   1. 讀取 memory/agent/index.md                     │
│   2. 載入 persona.md                                │
│   3. 根據 inner-state.md 決定開場白                 │
└─────────────────────────────────────────────────────┘
```

## 相關文件

- [architecture.md](architecture.md) - Memory 目錄結構
- [agent-memory.md](agent-memory.md) - Agent 記憶詳述
- [maintenance.md](maintenance.md) - 記憶維護機制
