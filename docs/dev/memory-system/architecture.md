# 記憶系統架構

## 目錄樹

```
memory/
├── agent/                      # Agent 本身的記憶系統
│   ├── index.md                # Agent 記憶總索引
│   ├── persona.md              # 人格（少變）
│   ├── config.md               # 配置
│   │
│   │   # === 存儲層 ===
│   │
│   ├── knowledge/              # 學到的知識（按主題）
│   │   ├── index.md
│   │   ├── llm.md
│   │   ├── programming.md
│   │   └── archive/
│   │       └── index.md
│   │
│   ├── thoughts/               # 深度思考（按主題）
│   │   ├── index.md
│   │   └── archive/
│   │       └── index.md
│   │
│   ├── experiences/            # 互動經歷（按人）
│   │   ├── index.md
│   │   ├── recent.md
│   │   └── archive/
│   │       └── index.md
│   │
│   ├── skills/                 # Agent 學會的技能
│   │   ├── index.md
│   │   └── conversation.md
│   │
│   │   # === 行為層（心理驅動） ===
│   │
│   ├── inner-state.md          # 內心狀態（想聊天、分享衝動、想念、心情）
│   ├── pending-thoughts.md     # 待分享的念頭
│   │
│   ├── interests/              # 興趣系統
│   │   ├── index.md
│   │   └── active.md           # 活躍興趣
│   │
│   └── journal/                # 日記
│       ├── index.md
│       └── 2025-01.md          # 按月份
│
├── short-term.md               # 當前對話緩衝（全域）
│
└── people/                     # 多人記憶
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