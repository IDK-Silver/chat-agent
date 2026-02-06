# Shutdown Protocol

對話即將結束。請執行以下記憶保存流程。

## 當前資訊

- 對話對象：{current_user}
- 日期：{date}
- 記憶路徑：{working_dir}/memory

## 必要任務

1. **歸檔對話**：將本次對話摘要寫入 `memory/people/archive/{current_user}/{date}.md`
   - 包含日期、主要話題、重要決定或情感交流
   - 建立 archive 目錄（如不存在）

2. **更新用戶記憶**：更新 `memory/people/user-{current_user}.md`
   - 更新 Profile、Preferences、Key Memories 等區塊
   - 只更新有變化的部分

3. **更新短期記憶**：更新 `memory/short-term.md`
   - 壓縮為簡短摘要，保留近期重要事項
   - 記錄最後互動的 user_id 與時間

## 可選任務

- 若對話中有重要知識點，更新 `memory/agent/knowledge/` 相關檔案
- 若有顯著情感變化，更新 `memory/agent/inner-state.md`
- 更新相關的 `index.md` 檔案

## 規則

- 所有記憶內容使用繁體中文
- 不要詢問用戶，直接執行
- 若對話內容不具保存價值，可以跳過歸檔，但仍需更新短期記憶
