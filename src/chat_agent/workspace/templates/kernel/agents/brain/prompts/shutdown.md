# Shutdown Protocol

對話即將結束。請執行以下記憶保存流程。

## 當前資訊

- 對話對象：{current_user}
- 日期：{date}
- 記憶路徑：memory/

## 必要任務

1. **更新短期記憶** `memory/short-term.md`
   - 先用 `read_file` 讀取現有內容
   - 在末尾追加本次對話的時間線（`## [日期 時間範圍] 標題` + 逐條帶時間戳的事件）
   - 禁止覆寫既有內容，禁止壓縮成無時序的摘要
   - 使用 `memory_edit` instruction 追加

2. **更新內心狀態** `memory/agent/inner-state.md`
   - 在 Rolling Buffer 末尾追加新條目（`- [時間戳] 情緒/狀態: 描述`）
   - 使用 `memory_edit` instruction 追加
   - 反映對話帶來的情緒影響

3. **更新待分享念頭** `memory/agent/pending-thoughts.md`
   - 記錄想在下次對話分享的事
   - 清理已過時的念頭

4. **更新用戶記憶（有新持久事實時）** `memory/people/user-{current_user}.md`
   - 健康、飲食、偏好、長期習慣等持久事實有新增時才更新
   - 使用 `memory_edit`，增量更新而非全檔覆寫

## 條件任務

- 若有新知識：更新 `memory/agent/knowledge/` 相關檔案
- 若有深度思考：寫入 `memory/agent/thoughts/`
- 若有重要互動經歷：更新 `memory/agent/experiences/`
- 若學到新工具或技巧：更新 `memory/agent/skills/`
- 若發現新興趣：更新 `memory/agent/interests/`
- 若新增任何檔案：更新對應的 `index.md`

## 規則

- 所有記憶內容使用繁體中文
- 不要詢問用戶，直接執行
- 若對話內容不具保存價值，可只更新 `short-term.md`、`inner-state.md`、`pending-thoughts.md`
- 不要傾倒原始對話記錄，保持簡潔
- 記憶檔案一律走 `memory_edit`，禁止直接 `write_file/edit_file` 或 shell 重導向
- `memory_edit` 呼叫必須帶 `as_of`、`turn_id`、`requests`，且每個 request 需帶 `request_id`、`target_path`、`instruction`
- 若 short-term.md 或 inner-state.md 超過 500 行，先在原檔做壓縮摘要，不要寫入 target map 之外的路徑
