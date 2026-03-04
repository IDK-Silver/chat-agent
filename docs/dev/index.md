# 開發文件索引

本資料夾存放開發相關文件。

## 啟動指示

1. 若用戶有明確任務，根據任務載入對應文件
2. 若用戶無明確任務，遞歸讀取子資料夾的 index.md，尋找待處理事項
3. 建立或修改 skill 時，須先讀取 [skills-guide.md](skills-guide.md)

## Skills 機制

本專案的文件採用類似 Claude Skills 的動態載入機制：
- 每份 `.md` 文件可視為一個 skill
- 當任務需要建立新的 skill 時，應建立對應的 `.md` 文件
- 新增文件後須更新相關的 `index.md`

## 文件列表

| 文件 | 說明 |
|------|------|
| [skills-guide.md](skills-guide.md) | Skills 建立與修改指南（必讀） |
| [copilot-agent-hint.md](copilot-agent-hint.md) | Copilot premium request 省費機制 |
| [copilot-staged-planning.md](copilot-staged-planning.md) | Copilot brain 三階段（看/想/做）流程與上下文邊界 |
| [heartbeat.md](heartbeat.md) | 自主喚醒系統（Heartbeat + Scheduled Actions） |
| [provider-api-spec.md](provider-api-spec.md) | LLM Provider API 規格盤點（官方事實 / adapter 規則 / 實測逆向） |
| [provider-architecture.md](provider-architecture.md) | LLM Provider 自治架構準則（config/client/factory 邊界） |
| [token-only-context-policy.md](token-only-context-policy.md) | Token-only 上下文策略（soft limit、Copilot usage 缺值、overflow fallback） |

## 子資料夾

| 資料夾 | 說明 |
|--------|------|
| [goals/](goals/index.md) | 專案目標與願景 |
| [eval/](eval/index.md) | 技術評估與測試文件 |
| [project-setup/](project-setup/index.md) | 專案環境設置文件 |
| [task/](task/index.md) | 待辦任務 |
| [memory-system/](memory-system/index.md) | 記憶系統設計 |
| [cli-ui/](cli-ui/index.md) | CLI UI（Textual）架構與擴充指南 |
