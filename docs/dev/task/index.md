# 任務索引

本資料夾存放待辦任務。Agent 開始工作前應先讀取此檔了解目前任務狀態。

## 操作規範

| 文件 | 說明 |
|------|------|
| [template.md](skills/template.md) | 任務文件格式模板 |
| [task-create.md](skills/task-create.md) | 建立任務的流程 |
| [task-archive.md](skills/task-archive.md) | 歸檔任務的流程 |

## 子資料夾

| 資料夾 | 說明 |
|--------|------|
| [archive/](archive/index.md) | 已完成並歸檔的任務 |

## 任務列表

| 文件 | 說明 | 狀態 | 依賴 |
|------|------|------|------|
| [tool-use.md](tool-use.md) | Tool Use 系統 | 已完成 | - |
| [builtin-tools.md](builtin-tools.md) | 內建工具擴展（shell、檔案讀寫） | 完成 | tool-use |
| [conversation-with-memory.md](conversation-with-memory.md) | 基礎對話迴圈（含記憶系統） | 完成 | tool-use, builtin-tools |
| [memory-search.md](memory-search.md) | Memory 搜尋（Subagent） | 草稿 | conversation-with-memory |
| [memory-import.md](memory-import.md) | Memory 匯入（ChatGPT/Claude/Line） | 草稿 | conversation-with-memory |
| [cli-enhancement.md](cli-enhancement.md) | CLI 介面美化（類 Claude Code） | 完成 | builtin-tools |
| [llm-base.md](llm-base.md) | LLM 對話基礎架構 | 完成 | - |
| [config-pydantic.md](config-pydantic.md) | Config 模組 Pydantic 重構 | 完成 | - |

## 狀態說明

```
草稿 → 完成規劃 → 進行中 → 完成 → (歸檔)
```

| 狀態 | 說明 | 可執行 |
|------|------|--------|
| 草稿 | 快速記錄想法，部分區塊可留空 | ❌ |
| 完成規劃 | 所有區塊填寫完整，可開始執行 | ✅ |
| 進行中 | 工作進行中 | - |
| 完成 | 已完成，等待歸檔 | - |

## Agent 行為

- **草稿任務不可執行**：遇到草稿狀態的任務，需先完成規劃才能執行
- 完成任務後，讀取 [task-archive.md](skills/task-archive.md) 執行歸檔流程
- 建立新任務時，依 [task-create.md](skills/task-create.md) 流程操作
