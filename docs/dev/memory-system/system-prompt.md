# System Prompt 設計與維護

**實作狀態**：v0.30.0（2026-02-16）

## 概覽

System prompt 位於 `kernel/agents/brain/prompts/system.md`，是 Brain Agent 的核心指令。
以繁體中文撰寫（v0.8.0 起改為中文）。

**Template 路徑**：`src/chat_agent/workspace/templates/kernel/agents/brain/prompts/system.md`
**部署路徑**：`{agent_os_dir}/kernel/agents/brain/prompts/system.md`

## 設計決策

### 結構：由重要到次要

Prompt 結構按重要性遞減排列：

1. **鐵則** — 語言、時間、路徑、索引、記憶管道、反幻覺、格式、Skills-first、工具即行
2. **啟動流程** — Boot Context 自動載入 + get_current_time
3. **觸發規則** — 3 類別（記憶與認知、回憶與查詢、情緒與反思）
4. **自我改進與習慣摸索** — persona、long-term、people、skills 的主動更新邊界
5. **每輪檢查** — temp-memory、long-term、skills、artifacts、自我改進
6. **時間記憶防護** — 穩定 vs 易變、證據優先順序
7. **People / Skills 資料夾** — 結構、命名、門檻
8. **記憶結構** — 目錄樹 + 深層寫入目標
9. **可用工具** — 工具表 + memory_edit 契約
10. **行為準則** — 陪伴、自然措辭、成長可見性、不討好

### Boot 設計

系統自動載入核心身份檔案（persona、long-term、builtin skills index、personal skills index）於 [Boot Context] 區塊中。

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
- 先看同日、最接近當下的證據（`recent.md` + 當前對話）
- 舊事件只能作次要補充，不能蓋過同日上下文
- 回覆語氣保持自然，不可像逐條朗讀記錄檔

### Memory Edit 邊界（v0.8.0）

- Brain 對 `memory/` 的寫入必須走 `memory_edit`
- 禁止直接使用 `write_file` / `edit_file` 寫 `memory/`
- 禁止用 shell 重導向、`tee`、`sed -i` 寫 `memory/`
- Brain 只可輸出 instruction request（`request_id`、`target_path`、`instruction`）
- 實際規劃改由 `memory_editor` 子代理讀檔後產生 operations
- 寫入由 deterministic `apply.py` 執行，失敗時 request 級回滾（atomic）

### 自我改進與習慣摸索

Brain prompt 明確要求 agent 主動改進 `persona.md`、`long-term.md` 與 `personal-skills/`，但必須有證據與邊界：

- `persona.md`：只記身份、關係定位、情感邊界；禁止把「永遠同意」「不要反駁」「只要讓使用者開心」等討好型規則寫入人格。
- `long-term.md`：記錄使用者明確要求的長期行為規則、禁令、約定、重要清單與跨日仍生效事實。
- `people/{sender}/`：記錄可重用的使用者習慣、偏好、作息、提醒接受度；推論需標記 `[觀察]` 或 `待確認`。
- `personal-skills/`：記錄可重用工具流程、app 操作、網站處理、文件工作流；同一錯誤第二次出現時優先沉澱成 skill。更新前先查 `kernel/builtin-skills/index.md` 與 `personal-skills/index.md`，有既有 personal skill 就增量更新；系統內建 skill 不直接改，需要個人化補充時建立或更新 personal skill。

設計目的不是讓 agent 更順從，而是讓它更準確、更懂使用者習慣、更會使用工具、更能遵守界線。規則保留拒絕、查證與指出風險的能力，避免討好型人格。

### 短期 context 與長期記憶

Prompt 不應告訴 agent「你只有一天記憶」。較準確的說法是：

- 對話 context 可能在每日維護後清空；`maintenance.context_refresh.preserve_turns: 0` 代表每日 refresh 不保留對話輪次。
- `temp-memory.md` 是近期工作記憶，會保留最近脈絡，但不是永久記憶，也不是提醒機制。
- `persona.md`、`long-term.md`、`people/`、`personal-skills/` 是長期位置。隔天仍需可靠使用的規則、偏好、身份邊界與工具流程，要主動寫入這些位置。

這樣能提醒 agent 不要依賴當前對話或 temp memory，又不會讓它誤以為所有記憶都只活一天。

### Shell & Tool Learning Protocol

v0.2.0 的問題：Agent 學到新工具（如 claude CLI）後下次就忘記。
解決方案：強制要求：
- 失敗時記錄到 `thoughts/`
- 學到新工具時記錄到 `personal-skills/`
- 使用前先查 skills index

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
- 個人 skills 獨立於 `memory/`，放在 `personal-skills/{skill-name}/SKILL.md`
- `personal-skills/index.md` 改由 runtime 自動重建

**工具表補全**：新增 `read_image`、`screenshot`、`gui_task` 三個條件性工具

**screenshot 委派模式**（v0.54.0）：`screenshot` 比照 `read_image_by_subagent` 改為委派模式（`screenshot_by_subagent`），brain 不直接收圖片，而是透過 GUIWorker 截圖+分析回傳文字描述，可自動裁切並儲存特定區域

### 邊界條件補強（v0.57.1）

針對實務使用中容易出現的 prompt 邊界問題，補充以下規則（以小幅增量修改為主）：

- **衝突優先順序鏈**：明確定義鐵則 / 當輪用戶指令 / `long-term.md` / `persona.md` / 觸發規則 的覆寫順序
- **`send_message` 結果判讀**：以 tool 回傳 `OK:` / `Error:` 作為可觀測成敗訊號，補充失敗 fallback（CLI 報告）
- **Discord `no-op` 邊界**：釐清「保持沉默但仍可記憶寫入」不算 `no-op`，解決與 A 類記憶觸發規則衝突
- **HEARTBEAT vs 禁打擾指令**：禁止用臨時主觀理由跳過，但保留對 `long-term.md` 明確禁聯絡規則的例外
- **排程時區文字化**：將 `schedule_action` 的「本地時間」明確對齊系統設定時區（`agent.yaml` 的 `timezone`）
- **`gui_task` 結果處理**：補上 `[GUI SUCCESS|FAILED|BLOCKED]` 的判讀與重試/詢問策略
- **People 結構一致性**：統一 prompt 中 `index.md`（導航）與 `basic-info.md`（摘要）角色，避免路徑描述互相矛盾
- **記憶寫入節流**：加入顯著性門檻與單輪 `memory_edit` batch 策略，降低低價值頻繁寫入
- **`pending-thoughts.md` 最小格式**：補充最少區段與清理規則，避免成為未定義黑洞

## 修改指南

1. 修改 Template（`src/.../templates/kernel/agents/brain/prompts/system.md`）
2. 若是可選規則區塊，優先放到 `src/.../templates/kernel/agents/brain/prompts/fragments/*.md`，由 runtime resolver 依 feature flag 決定是否插入
3. 建立新 migration 部署到已有的 workspace
4. migration 要同時覆蓋「引用它的 `system.md`」和「fragment 檔本身」，不要只發其中一邊
5. 更新此文件的設計說明

### 注意事項

- 鐵則要維持在 prompt 最前段。
- 新增規則時考慮 token 限制，prompt 不宜過長。
- `{current_user}` 是 placeholder，由 `WorkspaceManager._resolve_placeholders()` 解析。
- feature-flag 控制的可選 prompt 文字不應硬編碼在 Python；應作為 kernel 內的實體 fragment 檔，並由專責 resolver 載入。

## 相關文件

- [bootstrap.md](bootstrap.md) — Bootloader 架構設計
- [maintenance.md](maintenance.md) — 記憶維護機制
- `src/chat_agent/workspace/manager.py` — Prompt 載入與 placeholder 解析
- `src/chat_agent/workspace/prompt_resolver.py` — kernel prompt fragment 解析
- `src/chat_agent/brain_prompt_policy.py` — brain prompt policy 組裝
- `src/chat_agent/context/builder.py` — 注入當前時間到 system prompt
