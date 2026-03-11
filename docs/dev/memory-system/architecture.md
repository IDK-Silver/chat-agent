# 記憶系統架構

**實作狀態**：已完成（見 `src/chat_agent/workspace/templates/`）

## Runtime 資料夾

Agent 的工作目錄是 **runtime 資料**，不在專案目錄：

- 位置由 `agent.yaml` 的 `agent_os_dir` 設定
- 預設值：`~/.agent/`
- Agent 首次執行時初始化目錄結構
- Agent 有完整讀寫權限，自行維護

```yaml
# agent.yaml
agent_os_dir: ~/.agent
```

## 目錄樹

```
~/.agent/                       # agent_os_dir
├── kernel/                     # 可升級的系統核心
│   ├── info.yaml               # 版本資訊
│   └── agents/                 # 各 Agent 的 Prompt
│       ├── brain/
│       │   └── prompts/
│       │       ├── system.md   # Brain Agent（bootloader）
│       │       └── shutdown.md # 關機記憶保存
│       └── init/
│           └── prompts/
│               └── system.md   # Init Agent
│       ├── memory_searcher/
│       │   └── prompts/
│       │       ├── system.md      # 記憶搜尋 sub-LLM
│       │       └── parse-retry.md # 搜尋 JSON 解析失敗重試提示
│       ├── post_reviewer/
│       │   └── prompts/
│       │       ├── system.md      # 回答後合規檢查
│       │       └── parse-retry.md # Post-review JSON 解析失敗重試提示
│       └── shutdown_reviewer/
│           └── prompts/
│               ├── system.md      # 關機後記憶保存檢查
│               └── parse-retry.md # Shutdown review JSON 解析失敗重試提示
│
├── artifacts/                  # 實體附件、創作、匯出成果（非 memory）
│   ├── files/
│   └── creations/
│
├── state/                      # runtime operational state（可重建，不屬於 memory）
│   ├── shared_state.json       # Common-ground shared state
│   ├── contact_map.json        # sender → name 快取
│   ├── thread_registry.json    # 主動延續 thread 的 runtime state
│   └── discord/
│       ├── channel_registry.json
│       ├── cursors.json
│       ├── history/
│       ├── media/
│       └── image_summaries/
│
└── memory/                     # 可回憶/可檢索的記憶資料（升級不覆蓋）
    ├── agent/                  # Agent 本身的記憶系統
    │   ├── index.md            # Agent 記憶總索引
    │   ├── persona.md          # 人格（少變）
    │   ├── temp-memory.md      # 暫存工作記憶（近期上下文，不是提醒機制）
    │   ├── long-term.md        # 長期重要事項（仍生效的規則、約定、重要記錄）
    │   ├── artifacts.md        # artifacts/ 的可搜尋索引入口
    │   │
    │   │   # === live memory ===
    │   │
    │   ├── skills/             # Agent 學會的技能
    │   │   ├── index.md
    │   │   └── conversation.md
    │   │
    │   └── archive/            # 歷史回憶與退役記憶類別
    │       ├── index.md
    │       ├── deprecated/
    │       └── temp-memory/
    │
    └── people/                 # 多人記憶
        ├── index.md
        ├── alice/
        │   ├── index.md       # 用戶摘要（Boot Context 載入）
        │   └── {topic}.md     # 詳細主題資料
        └── bob/
            └── index.md
```

## 兩層分離設計

### kernel/ - 可升級的系統核心

存放 system prompts 和版本資訊，升級時會覆蓋。

**managed prompt 清理**：
- `kernel/agents/*/prompts/*.md` 視為受系統管理的 canonical prompt 檔案
- workspace 初始化與 migration 結束後，會自動清掉 `system 2.md` 這類 Finder / iCloud 衝突副本，以及 prompt 目錄中的 `.DS_Store`
- 目的不是保留多版本，而是避免這些副本在 runtime 被誤讀或持續累積

**檔案：**
- `info.yaml` - 版本追蹤（version, updated）
- `agents/` - 各 agent 的 prompt（按 agent 分目錄）

### state/ - Runtime operational state

系統運作所需、可重建但不屬於「可回憶記憶」的資料。

- `shared_state.json` - Common-ground shared state
- `contact_map.json` - sender/name 對應快取
- `thread_registry.json` - 主動延續 thread 的狀態
- `discord/` - Discord channel cursor、history、media 等 runtime state

這些資料不應與 `memory/` 混放，因為它們不是 Agent 要拿來「回憶」的內容。

### artifacts/ - Durable file storage

系統需要保存「檔案本體」，例如：

- Gmail/Discord 附件
- PDF、匯出結果、下載文件
- 長篇創作、故事草稿

這些資料不適合直接放進 `memory/`，因為 `memory/` 的角色是可檢索的文字記憶，不是 binary/document store。

規則：
- 檔案本體放 `artifacts/`
- 搜尋入口放 `memory/agent/artifacts.md`
- 若檔案會影響未來行為，另外同步更新 `long-term.md` 或 `people/...`
- 若之後還要跟進，另外使用 `schedule_action`

### memory/ - 用戶資料

用戶的記憶資料，升級時**不會覆蓋**。

## 層級說明

### agent/ - Agent 本身的記憶

Agent 自身長期積累的記憶，不因對話結束而遺失。

**基礎檔案：**
- `persona.md` - 人格設定，基本不變
- `temp-memory.md` - 暫存工作記憶（近期上下文，不是提醒機制）
- `long-term.md` - 長期重要事項（仍生效的規則、承諾、待辦、不可遺忘的事實）

**live memory：**
- `temp-memory.md` - 近期上下文與當前情緒
- `long-term.md` - 仍生效的規則與長期事實
- `skills/` - Agent 學會的技能
- `archive/` - 歷史回憶與退役記憶類別

### people/ - 多人記憶

每位用戶擁有獨立資料夾，包含摘要與詳細主題檔案。

**結構：**
- `index.md` - 所有用戶列表
- `{user_id}/index.md` - 用戶摘要 + 子檔案連結（Boot Context 載入）
- `{user_id}/{topic}.md` - 詳細主題資料（健康、通勤、飲食等）

### agent/recent.md - 近期記憶

合併原 `short-term.md`（短期工作記憶）與 `inner-state.md`（內心狀態）為單一檔案，維持「像真人一樣的一條時間線」：把最近狀態與近期對話做**壓縮摘要**，讓下次啟動時能快速回到相近狀態（避免每次都像重開機）。

它是 **Agent 自己的 working memory + 內心狀態**，可以包含：
- 最近一次互動是跟誰（`user_id`）
- 近期對話的壓縮摘要（包含明確的 `user_id` 與日期）
- 當前焦點、未完事項、想分享的念頭（摘要即可）
- 內心狀態（想聊天程度、分享衝動、想念、心情）

規則：
- **人的長期資訊不要放這裡**（偏好、背景、關係里程碑等），要寫到 `people/{user_id}/index.md`
- 內容要短（例如 < 200 行）；過長就再次壓縮成更短摘要
- 即使近期記憶提到其他人，也必須帶 `user_id`，避免被誤認成「當前正在對話的人」

## 記憶類型分類

### 按時間長度

| 類型 | 存放位置 | 說明 |
|------|---------|------|
| 短期 | agent/temp-memory.md | 暫存工作記憶（近期上下文） |
| 長期 | agent/、people/ | 持久化記憶 |

### 按歸屬

| 類型 | 存放位置 | 說明 |
|------|---------|------|
| Agent 記憶 | agent/ | Agent 自身成長、知識、技能 |
| 用戶記憶 | people/ | 與特定用戶的互動記錄 |

## index.md 類型（語義分流，不改檔名）

為避免所有 `index.md` 被視為同一種格式，記憶系統將 `index.md` 分成兩類語義：

### 1. nav index（導覽索引）

用途：列出同層檔案/子目錄連結，供人類閱讀與搜尋索引。

- 常見格式：Markdown list
- 範例路徑：`memory/agent/knowledge/index.md`、`memory/people/{user_id}/index.md`
- 維護者：`memory_edit` 的通用 index 自動維護（新增/刪除檔案時更新連結）

### 2. registry index（名錄索引）

用途：保存有欄位語義的結構化名錄（不只是連結）。

- 常見格式：Markdown table
- 目前路徑：`memory/people/index.md`
- 維護者：對應 domain 模組（目前為 `workspace.people`），不是 `memory_edit` 的通用 link 維護

### Runtime 相容策略（個人使用版）

- **不改 runtime 檔名**：仍然使用 `index.md`
- 以「語義類型」區分行為，而不是改成 `registry.md`
- 讀取端保留基本容錯；寫入端輸出目前規範格式

## 初始化結構

初始化時建立完整目錄結構（含 index.md 說明用途），讓 Agent 知道可用的記憶分類：

```
memory/
├── agent/
│   ├── index.md            # 說明各目錄用途
│   ├── persona.md
│   ├── temp-memory.md
│   ├── long-term.md
│   ├── artifacts.md
│   ├── skills/
│   │   └── index.md
│   └── identity/
│   │   └── index.md
├── archive/
│   ├── index.md
│   ├── deprecated/
│   │   └── index.md
│   └── temp-memory/
│       └── index.md
└── people/
    └── index.md
```

每個 index.md 都說明該目錄的用途和檔案命名規則。
