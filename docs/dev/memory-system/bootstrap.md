# Bootloader 啟動架構

## 概念

Bootloader 是 brain agent 的固定 system prompt，類似作業系統的 bootloader：

- **最小化**：只做一件事 - 告訴 agent 去讀取 memory
- **固定不變**：寫死在程式碼中，不從外部配置
- **引導作用**：載入真正的「作業系統」（persona、知識、技能）

```
啟動流程：

Bootloader (固定 prompt)
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

## Brain Agent vs 工具 Agent

| 類型 | Prompt 來源 | 說明 |
|------|------------|------|
| Brain Agent | Bootloader + Memory | 主要對話 agent，從 memory 載入人格 |
| 工具 Agent | Config 直接設定 | 單一用途，例如「搜尋工具」，不需要人格 |

### Brain Agent

```yaml
# config.yaml
agents:
  brain:
    llm: llm/anthropic/claude.yaml
    # 沒有 prompt 設定，使用 bootloader
```

### 工具 Agent

```yaml
# config.yaml
agents:
  search:
    llm: llm/openai/gpt4.yaml
    prompt: "You are a search assistant. Given a query, return relevant results."
```

## Memory 資料夾配置

Memory 是 runtime 資料，不在專案目錄：

```yaml
# config.yaml
memory_path: ~/.chat-agent/memory
```

首次執行時，agent 會根據 `architecture.md` 定義的結構初始化 memory 資料夾。

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

Your memory is stored at: {memory_path}

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
│         - memory_path 路徑                          │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│              建立 LLM Client                         │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│           組裝 Bootloader Prompt                     │
│         - 固定模板                                  │
│         - 注入 memory_path                          │
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
