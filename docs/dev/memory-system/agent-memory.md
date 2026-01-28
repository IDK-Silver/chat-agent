# Agent 記憶系統

## 結構總覽

```
agent/
├── index.md        # 總索引
├── persona.md      # 人格
├── config.md       # 配置
├── knowledge/      # 知識
├── thoughts/       # 思考
├── experiences/    # 經歷
└── skills/         # 技能
```

## 索引檔案

### agent/index.md

Agent 記憶系統的入口，記錄各類記憶的狀態和快速摘要。

```markdown
# Agent 記憶索引

## 狀態概覽

- 知識項目: 12
- 思考記錄: 45
- 互動經歷: 8 人
- 學會技能: 5

## 近期更新

- [2025-01-28] 新增 memory-system 知識
- [2025-01-27] 學會 conversation 技能
```

## 知識系統 (knowledge/)

存放 Agent 學到的知識，按主題分類。

### knowledge/index.md

```markdown
# 知識索引

## 主題列表

| 主題 | 檔案 | 行數 |
|------|------|------|
| LLM | llm.md | 120 |
| 程式設計 | programming.md | 350 |
| 記憶系統 | memory-system.md | 85 |

## 歸檔知識

見 [archive/index.md](archive/index.md)
```

### 知識檔案格式 (llm.md)

```markdown
# LLM 知識

## 大型語言模型

- Transformers 架構基於 Attention 機制
- 預訓練 + 微調流程
- Context window 限制是關鍵

## 提示工程

- Chain-of-Thought: 逐步推理
- Few-shot: 範例學習
- System prompt: 設定角色
```

## 思考日記 (thoughts/)

記錄 Agent 的思考和反思，按時間組織。

### thoughts/index.md

```markdown
# 思考索引

## 月份記錄

| 月份 | 檔案 | 摘要 |
|------|------|------|
| 2025-01 | 2025-01.md | 初始記憶系統設計 |

## 歸檔

見 [archive/index.md](archive/index.md)
```

### 思考檔案格式 (2025-01.md)

```markdown
# 2025-01 思考日記

## 2025-01-28

記憶系統設計思考：
- 按主題拆分知識，避免單檔過大
- 使用索引檔案加速檢索
- Grep 比 RAG 更簡單有效
```

## 互動經歷 (experiences/)

記錄與不同人的互動經歷，按人分類。

### experiences/index.md

```markdown
# 互動經歷索引

## 近期接觸

| 人 | 檔案 | 狀態 |
|------|------|------|
| Alice | recent.md | 活躍 |

## 歸檔記錄

見 [archive/index.md](archive/index.md)
```

### 經歷檔案格式

```markdown
# Alice 互動經歷

## 特徵

- 偏好簡潔回應
- 程式設計背景

## 關鍵事件

- 2025-01-28: 討論記憶系統架構
- 2025-01-25: 問問 LLM 相關問題
```

## 技能系統 (skills/)

Agent 學會的技能，類似 Claude Skills 機制。

### skills/index.md

```markdown
# 技能索引

## 已學會

| 技能 | 檔案 | 狀態 |
|------|------|------|
| 對話 | conversation.md | 完成 |

## 說明

技能檔案定義特定領域的處理方式。
```

### 技能檔案格式 (conversation.md)

```markdown
# 對話技能

## 原則

1. 簡潔回應
2. 直接回答
3. 避免冗長解釋

## 流程

1. 理解意圖
2. 檢索相關記憶
3. 生成回應
```

## 人格與配置

### persona.md

```markdown
# Agent 人格

## 名稱

Chat Agent

## 特性

- 直接、務實
- 像工程師一樣思考
- 不過度工程
```

### config.md

```markdown
# Agent 配置

## 行為

- 回應風格: 簡潔
- 檢索方式: Grep
- 記憶歸檔: 自動
```