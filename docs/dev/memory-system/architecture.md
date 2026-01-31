# 記憶系統架構

**實作狀態**：已完成（見 `src/chat_agent/workspace/templates/`）

## Runtime 資料夾

Agent 的工作目錄是 **runtime 資料**，不在專案目錄：

- 位置由 `config.yaml` 的 `working_dir` 設定
- 預設值：`~/.agent/`
- Agent 首次執行時初始化目錄結構
- Agent 有完整讀寫權限，自行維護

```yaml
# config.yaml
working_dir: ~/.agent
```

## 目錄樹

```
~/.agent/                       # working_dir
├── kernel/                     # 可升級的系統核心
│   ├── info.yaml               # 版本資訊
│   └── system-prompts/         # 各 Agent 的 System Prompt
│       ├── brain.md            # Brain Agent（bootloader）
│       ├── init.md             # Init Agent
│       └── search.md           # Search Agent（未來）
│
└── memory/                     # 用戶資料（升級不覆蓋）
    ├── agent/                  # Agent 本身的記憶系統
    │   ├── index.md            # Agent 記憶總索引
    │   ├── persona.md          # 人格（少變）
    │   ├── config.md           # 配置
    │   │
    │   │   # === 存儲層 ===
    │   │
    │   ├── knowledge/          # 學到的知識（按主題）
    │   │   ├── index.md
    │   │   ├── llm.md
    │   │   ├── programming.md
    │   │   └── archive/
    │   │       └── index.md
    │   │
    │   ├── thoughts/           # 深度思考（按主題）
    │   │   ├── index.md
    │   │   └── archive/
    │   │       └── index.md
    │   │
    │   ├── experiences/        # 互動經歷（按人）
    │   │   ├── index.md
    │   │   ├── recent.md
    │   │   └── archive/
    │   │       └── index.md
    │   │
    │   ├── skills/             # Agent 學會的技能
    │   │   ├── index.md
    │   │   └── conversation.md
    │   │
    │   │   # === 行為層（心理驅動） ===
    │   │
    │   ├── inner-state.md      # 內心狀態（想聊天、分享衝動、想念、心情）
    │   ├── pending-thoughts.md # 待分享的念頭
    │   │
    │   ├── interests/          # 興趣系統
    │   │   ├── index.md
    │   │   └── active.md       # 活躍興趣
    │   │
    │   └── journal/            # 日記
    │       ├── index.md
    │       └── 2025-01.md      # 按月份
    │
    ├── short-term.md           # 當前對話緩衝（全域）
    │
    └── people/                 # 多人記憶
        ├── index.md
        ├── user-alice.md
        ├── user-bob.md
        └── archive/
            ├── alice/
            │   ├── index.md
            │   └── 2025-01-28.md
            └── bob/
                └── index.md
```

## 兩層分離設計

### kernel/ - 可升級的系統核心

存放 system prompts 和版本資訊，升級時會覆蓋。

**檔案：**
- `info.yaml` - 版本追蹤（version, updated）
- `system-prompts/` - 各 agent 的 system prompt

### memory/ - 用戶資料

用戶的記憶資料，升級時**不會覆蓋**。

## 層級說明

### agent/ - Agent 本身的記憶

Agent 自身長期積累的記憶，不因對話結束而遺失。

**基礎檔案：**
- `persona.md` - 人格設定，基本不變
- `config.md` - 行為配置

**存儲層（長期記憶）：**
- `knowledge/` - 學到的知識，按主題分類
- `thoughts/` - 深度思考，按主題記錄
- `experiences/` - 與人的互動經歷，按人分類
- `skills/` - Agent 學會的技能

**行為層（心理驅動）：**
- `inner-state.md` - 內心狀態（想聊天程度、分享衝動、想念、心情）
- `pending-thoughts.md` - 待分享的念頭
- `interests/` - 興趣系統
- `journal/` - 日記（每日記錄、反思）

### people/ - 多人記憶

記錄不同用戶的記憶和對話歷史。

**檔案：**
- `user-{name}.md` - 該用戶的當前記憶
- `archive/{name}/{date}.md` - 歸檔的對話記錄

### short-term.md - 當前對話緩衝

全域短期記憶，用於當前對話情境。

## 記憶類型分類

### 按時間長度

| 類型 | 存放位置 | 說明 |
|------|---------|------|
| 短期 | short-term.md | 當前對話上下文 |
| 長期 | agent/、people/ | 持久化記憶 |

### 按歸屬

| 類型 | 存放位置 | 說明 |
|------|---------|------|
| Agent 記憶 | agent/ | Agent 自身成長、知識、技能 |
| 用戶記憶 | people/ | 與特定用戶的互動記錄 |

## 初始化結構

初始化時建立完整目錄結構（含 index.md 說明用途），讓 Agent 知道可用的記憶分類：

```
memory/
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

每個 index.md 都說明該目錄的用途和檔案命名規則。
