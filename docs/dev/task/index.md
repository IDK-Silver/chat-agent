# 任務索引

本資料夾存放待辦任務。Agent 開始工作前應先讀取此檔了解目前任務狀態。

## 操作規範

| 文件 | 說明 |
|------|------|
| [template.md](template.md) | 任務文件格式模板 |
| [task-create.md](task-create.md) | 建立任務的流程 |
| [task-archive.md](task-archive.md) | 歸檔任務的流程 |

## 子資料夾

| 資料夾 | 說明 |
|--------|------|
| [archive/](archive/index.md) | 已完成並歸檔的任務 |

## 任務列表

| 文件 | 說明 | 狀態 |
|------|------|------|
| [llm-base.md](llm-base.md) | LLM 對話基礎架構 | 待處理 |

## 狀態說明

- **待處理**：已建立文件，尚未開始
- **進行中**：工作進行中
- **完成**：已完成

## Agent 行為

- 完成任務後，讀取 [task-archive.md](task-archive.md) 執行歸檔流程
- 建立新任務時，依 [task-create.md](task-create.md) 流程操作
