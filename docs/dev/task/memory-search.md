# Memory 搜尋（Subagent）

實作 `memory_search` 工具，以 Subagent 形式搜尋記憶系統。

**狀態**：草稿

## 背景

Brain Agent 需要搜尋記憶時，直接在主對話中執行 grep/讀檔會污染 context window。

使用 Subagent 形式：
- Brain 只需下達搜尋指令
- Search Subagent 獨立執行（多輪 grep + 讀檔 + 判斷相關性）
- 返回精簡的搜尋結果

## 設計決策

### Subagent 架構

- **選擇**：獨立 Agent（`agents.search`）
- **原因**：避免污染 Brain context window
- **LLM**：可用便宜模型（如 haiku）

### System Prompt 位置

- **選擇**：`kernel/system-prompts/search.md`
- **原因**：與其他 agent prompts 一致

## 步驟

<!-- TODO: 待規劃 -->

1. Search Agent 配置
2. system-prompts/search.md
3. memory_search tool 實作
4. 整合到 Brain Agent

## 驗證

<!-- TODO: 待規劃 -->

## 完成條件

- [ ] Search Agent 配置
- [ ] search.md prompt
- [ ] memory_search tool
- [ ] 測試覆蓋
