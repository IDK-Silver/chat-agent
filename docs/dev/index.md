# 開發文件索引

本資料夾存放開發相關文件。

## 啟動指示

1. 若用戶有明確任務，根據任務載入對應文件
2. 若用戶無明確任務，遞歸讀取子資料夾的 index.md，尋找待處理事項

## Skills 機制

本專案的文件採用類似 Claude Skills 的動態載入機制：
- 每份 `.md` 文件可視為一個 skill
- 當任務需要建立新的 skill 時，應建立對應的 `.md` 文件
- 新增文件後須更新相關的 `index.md`

## 文件列表

| 文件 | 說明 |
|------|------|
| [goal.md](goal.md) | 專案目標與功能需求 |
| [subfolder-template.md](subfolder-template.md) | 子資料夾建立規範 |

## 子資料夾

| 資料夾 | 說明 |
|--------|------|
| [eval/](eval/index.md) | 技術評估與測試文件 |
| [project-setup/](project-setup/index.md) | 專案環境設置文件 |
| [task/](task/index.md) | 待辦任務 |
