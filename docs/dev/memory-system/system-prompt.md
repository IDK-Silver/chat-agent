# System Prompt 設計與維護

**實作狀態**：v0.8.0（2026-02-10）

## 概覽

System prompt 位於 `kernel/agents/brain/prompts/system.md`，是 Brain Agent 的核心指令。
以英文撰寫，因 Gemini Flash 對英文指令的遵循度最高。

**Template 路徑**：`src/chat_agent/workspace/templates/kernel/agents/brain/prompts/system.md`
**部署路徑**：`{working_dir}/kernel/agents/brain/prompts/system.md`

## 設計決策

### 為什麼用英文

目標模型 gemini-3-flash-preview 對英文指令的遵循度顯著高於中文。
記憶內容仍為繁體中文（由 prompt 的 IRON RULES 第 1 條控制）。

### 結構：由重要到次要

Prompt 結構按重要性遞減排列，因 Flash 模型對前段指令的遵循度最高：

1. **IRON RULES** — 語言、時間、路徑、索引、反幻覺
2. **BOOT SEQUENCE** — 兩階段啟動
3. **DURING CONVERSATION** — 觸發規則 + 工具學習
4. **MEMORY STRUCTURE** — 目錄說明
5. **AVAILABLE TOOLS** — 工具表
6. **BEHAVIORAL NOTES** — 行為指引

### Boot 兩階段設計

- **Phase 1**（`read_file`）：讀核心身份文件（persona、inner-state、user profile 等）
- **Phase 2**（`execute_shell` + `cat`）：一次掃描全部 index.md

分兩階段的原因：
- `read_file` 更可靠，路徑由系統解析，不會搞錯
- `cat` 一次讀多個 index 比多次 `read_file` 更省 token

### During-Conversation 觸發規則

用 IF-THEN 表格格式，讓模型能機械式遵循，不依賴「自覺」。
解決了 v0.2.0 只在 Boot 和 Shutdown 有協定、對話中無規則的問題。

### Temporal Memory Guardrails（v0.3.1）

針對對話 Agent 常見的「把歷史記憶當成當下事實」問題，新增時間新鮮度規則：
- 記憶內容預設視為歷史快照，不是當下真相
- 區分 `stable`（可直接引用）與 `volatile`（需 freshness check）資訊
- 回答 `volatile` 的現在狀態前，要求：
  1. 先取當前時間
  2. 檢查最近證據時間
  3. 若證據偏舊，先向使用者做簡短確認
- 對外回覆保持自然語氣；只有在必要情境（用戶要求、時間敏感、衝突釐清）才露出精確時間戳

### 近時記憶優先（v0.5.8）

針對「有提到關鍵字但抓到太舊事件」的問題，新增近時優先規則：
- 若用戶語句含「今天／剛才／剛剛／到現在／從...到現在／剛回來」等線索
- 先看同日、最接近當下的證據（`short-term.md` + 當前對話）
- 舊事件只能作次要補充，不能蓋過同日上下文
- 回覆語氣保持自然，不可像逐條朗讀記錄檔

### Memory Edit 邊界（v0.8.0）

- Brain 對 `memory/` 的寫入必須走 `memory_edit`
- 禁止直接使用 `write_file` / `edit_file` 寫 `memory/`
- 禁止用 shell 重導向、`tee`、`sed -i` 寫 `memory/`
- Brain 只可輸出 instruction request（`request_id`、`target_path`、`instruction`）
- 實際規劃改由 `memory_editor` 子代理讀檔後產生 operations
- 寫入由 deterministic `apply.py` 執行，失敗時 request 級回滾（atomic）

### Shell & Tool Learning Protocol

v0.2.0 的問題：Agent 學到新工具（如 claude CLI）後下次就忘記。
解決方案：強制要求：
- 失敗時記錄到 `thoughts/`
- 學到新工具時記錄到 `skills/`
- 使用前先查 `skills/`

## 修改指南

1. 修改 Template（`src/.../templates/kernel/agents/brain/prompts/system.md`）
2. 建立新 migration 部署到已有的 workspace
3. 更新 `templates/kernel/info.yaml` 版本號
4. 更新此文件的設計說明

### 注意事項

- 保持英文。不要改成中文。
- IRON RULES 要維持在 prompt 最前段。
- 新增規則時考慮 Gemini Flash 的 token 限制，prompt 不宜過長。
- `{current_user}` 是 placeholder，由 `WorkspaceManager._resolve_placeholders()` 解析。

## 相關文件

- [bootstrap.md](bootstrap.md) — Bootloader 架構設計
- [maintenance.md](maintenance.md) — 記憶維護機制
- `src/chat_agent/workspace/manager.py` — Prompt 載入與 placeholder 解析
- `src/chat_agent/context/builder.py` — 注入當前時間到 system prompt
