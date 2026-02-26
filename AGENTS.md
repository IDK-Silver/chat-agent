# AGENTS.md

本檔定義 LLM Agent 的行為規範與編碼準則。詳細設計與架構見 `docs/dev/` 下相關文件。

## 啟動流程

> **執行指令**：收到任何請求前，先讀取 `docs/dev/index.md` 並依其指示行動。

開發文件位於 `docs/dev/`，採用動態載入機制：
- 根據任務相關性載入對應文件
- 新增文件後更新對應的 `index.md`

**術語**：用戶提到「任務」「待辦」「進度」時，指專案待辦（由 `docs/dev/index.md` 定義），非 Claude Code 內建的 Task 工具。

## 環境

- 使用 uv 管理專案
- 所有 Python 命令使用 `uv run`，例如：`uv run python`, `uv run pytest`

## 核心原則

像 Linus Torvalds 一樣：直接、務實、不過度工程。最好的程式碼是能運作、容易理解、不試圖太聰明的程式碼。

要像正規專案一樣管理架構，要有遠見，不要硬編碼。

### LLM Provider 設計準則
- 修改 LLM provider/config/factory 前，先讀 `docs/dev/provider-api-spec.md` 與 `docs/dev/provider-architecture.md`
- 先查 API 事實，再設計抽象；不要從現有程式碼或舊 YAML 反推 provider 規格
- 統一的是 client 介面（如 `chat`/`chat_with_tools`），不是各 provider 的 config/payload 格式
- provider-specific 的驗證、payload 映射、能力差異應放在各 provider config/client，不放共用層
- `factory` 只做共通流程（timeout/retry/wrapping），不得加入 provider-specific 判斷
- runtime feature（如 Copilot `force_agent`）在組裝層路由，不放進 YAML config 欄位
- 不允許 silent ignore provider-specific kwargs；不支援就早停報錯
- 架構/API 行為變更時，同步更新 `docs/dev/provider-api-spec.md`（官方事實 / adapter 規則 / 實測逆向）

## 工作流程

- 變更前先對齊 `docs/dev/` 相關文件
- 修改聚焦且最小，避免牽動不相關部分
- 架構/API/流程變更時，更新 `docs/` 文件

## 程式碼標準

### 語言規範
- 註解：英文，解釋「為什麼」而非「做什麼」
- 文件（.md）：繁體中文
- 程式碼中不使用非 ASCII 字元或 emoji

### 品質原則
- 簡單可讀優於聰明
- 功能優先，優化其次（且只在需要時）
- 不添加未被要求的功能
- 不重構正常運作的程式碼，除非有明確理由
- 標準函式庫能做到就用標準函式庫
- 早停機制：錯誤在載入/啟動時發現，不要延遲到執行時
- 結構化資料使用 Pydantic 驗證

### 組織結構
- 模組化：依關注點拆分，避免單一檔案過大
- 單一職責：每個檔案專注一件事
- 清晰 API：只輸出必要的函式與型別，隱藏內部實作

## 回應格式
- 語氣：簡潔、直接
- 引用檔案給出路徑（如 `docs/dev/design.md:12`）
- 不複述 `docs/` 長篇內容，僅提供摘要與連結

## 應避免

- 添加使用者未要求的功能
- 為不可能發生的問題做防禦性程式設計
- 為一次性操作建立抽象
- 沒有理由的相容層或功能旗標
- 為瑣碎程式碼加冗長的型別提示和文件字串
