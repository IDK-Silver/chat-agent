# System Prompt 設計與維護

**實作狀態**：v0.30.0（2026-02-16）

## 概覽

System prompt 位於 `kernel/agents/brain/prompts/system.md`，是 Brain Agent 的核心指令。
以繁體中文撰寫（v0.8.0 起改為中文）。

**Template 路徑**：`src/chat_agent/workspace/templates/kernel/agents/brain/prompts/system.md`
**部署路徑**：`{working_dir}/kernel/agents/brain/prompts/system.md`

## 設計決策

### 結構：由重要到次要

Prompt 結構按重要性遞減排列：

1. **鐵則** — 語言、時間、路徑、索引、記憶管道、反幻覺、格式、Skills-first、工具即行
2. **啟動流程** — Boot Context 自動載入 + get_current_time
3. **觸發規則** — 3 類別（記憶與認知、回憶與查詢、情緒與反思）
4. **每輪檢查** — short-term、inner-state、long-term
5. **時間記憶防護** — 穩定 vs 易變、證據優先順序
6. **People / Skills 資料夾** — 結構、命名、門檻
7. **記憶結構** — 目錄樹 + 深層寫入目標
8. **可用工具** — 工具表 + memory_edit 契約
9. **行為準則** — 陪伴、自然措辭、成長可見性

### Boot 設計

系統自動載入核心身份檔案（persona、inner-state、short-term、用戶記憶、pending-thoughts、skills/interests 索引）於 [Boot Context] 區塊中。Agent 只需 `get_current_time` 即可開始。

### 觸發規則（v0.30.0 重組）

v0.8.0 用單一平面表格，v0.30.0 改為 3 類別：
- **A. 記憶與認知**：用戶認知、第三方人物、約定、身份更新
- **B. 回憶與查詢**：過去事件、時間線、個人任務、當前狀態
- **C. 情緒與反思**：情緒危機、用戶糾正

好處：降低認知負荷，關注點分離，更易定位相關規則。

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

### Brain Prompt v2 改版（v0.30.0）

全面改版，提升結構清晰度與功能完整性：

**鐵則精簡**：11 條合併為 9 條
- Rules 5+6 合併：記憶寫入管道與操作順序統一處理
- Rules 8+9 合併：內容格式與審查詞彙限制統一處理
- Rule 10 簡化：Skills-first 保持強制但更簡潔

**觸發規則重組**：從單一表格改為 A/B/C 三類別（見上方「觸發規則」段落）

**第三方人物支援**：
- 新增追蹤命名第三方人物（同事、朋友、家人等）
- 資料夾命名：拼音小寫 + 連字號（如 `zhang-san/`）
- 建檔門檻：需有姓名 + 至少一項持久屬性
- 不記錄：泛稱、單次提及無持續屬性

**Skills 資料夾結構**：
- 從平面 `.md` 檔改為 `{skill-name}/index.md` 子資料夾結構
- 更新相關路徑引用（鐵則第 8 條、工具學習協定、記憶結構樹）

**工具表補全**：新增 `read_image`、`screenshot`、`gui_task` 三個條件性工具

## 修改指南

1. 修改 Template（`src/.../templates/kernel/agents/brain/prompts/system.md`）
2. 建立新 migration 部署到已有的 workspace
3. 更新此文件的設計說明

### 注意事項

- 鐵則要維持在 prompt 最前段。
- 新增規則時考慮 token 限制，prompt 不宜過長。
- `{current_user}` 是 placeholder，由 `WorkspaceManager._resolve_placeholders()` 解析。

## 相關文件

- [bootstrap.md](bootstrap.md) — Bootloader 架構設計
- [maintenance.md](maintenance.md) — 記憶維護機制
- `src/chat_agent/workspace/manager.py` — Prompt 載入與 placeholder 解析
- `src/chat_agent/context/builder.py` — 注入當前時間到 system prompt
