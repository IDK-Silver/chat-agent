# 多人記憶系統

## 結構總覽

```
people/
├── index.md           # 總索引
├── user-alice.md      # Alice 的當前記憶
├── user-bob.md        # Bob 的當前記憶
└── archive/
    ├── alice/
    │   ├── index.md
    │   └── 2025-01-28.md
    └── bob/
        └── index.md
```

## 記憶策略（所有人都有檔；重要的人才寫詳細）

`people/` 的目的，是讓 Agent 在「多人」情境下能正確區分人、且維持關係連續性；但不代表必須對每個 user 都建立完整長期記憶。

- `user-{id}.md` 是「該人的長期記憶摘要」，**每個人都應該有檔案**：
  - 重要的人：維護較完整摘要（偏好、關係、關鍵事件）
  - 不重要或一次性互動：檔案仍存在，但只保留極簡基本資訊
- `id` 應視為穩定識別字（例如 CLI `--user` 傳入的 `user_id`），不是顯示名稱
- 若 `user-{id}.md` 不存在：視為新的人；啟動或首次互動時建立

## 總索引 (people/index.md)

記錄所有用戶的概況和快速索引。

```markdown
# 多人記憶索引

## 用戶列表

| user_id | 顯示名稱 | 檔案 | 狀態 | 最後接觸 |
|---------|----------|------|------|---------|
| alice | Alice | user-alice.md | 活躍 | 2025-01-28 |
| bob | Bob | user-bob.md | 歸檔 | 2025-01-15 |

## 狀態說明

- **活躍**: 近期有互動，記憶在 user-{user_id}.md
- **歸檔**: 長期無互動，記憶移至 archive/{user_id}/

## 對話歸檔

詳見各用戶的 archive/ 資料夾。
```

## 用戶記憶檔案 (user-{user_id}.md)

存放該用戶的當前記憶摘要（重要的人才需要維護得完整）。

```markdown
# Alice 記憶

## 基本

- 名稱: Alice
- 身份: 軟體工程師
- 首次接觸: 2025-01-20

## 特徵

- 偏好簡潔回答
- 關注架構設計
- 熟悉 Python

## 對話主題

1. 記憶系統設計 (2025-01-28)
2. LLM 應用 (2025-01-25)

## 關鍵決定

- 選用 .md 檔案系統而非資料庫
- 使用 Grep 檢索而非 RAG

## 詳細記錄

見 [archive/alice/](archive/alice/)
```

## 對話歸檔

### archive/{user_id}/ 結構

按日期歸檔的完整對話記錄。

```
archive/
└── alice/
    ├── index.md
    └── 2025-01-28.md
```

### 索引檔案 (archive/{user_id}/index.md)

```markdown
# Alice 對話歸檔

## 歸檔記錄

| 日期 | 檔案 | 主題 |
|------|------|------|
| 2025-01-28 | 2025-01-28.md | 記憶系統設計 |
| 2025-01-25 | 2025-01-25.md | LLM 應用 |
```

### 對話記錄格式 (archive/{user_id}/{date}.md)

```markdown
# 2025-01-28 對話記錄

## 參與者

- Alice
- Chat Agent

## 主題

記憶系統架構設計

## 對話內容

### Alice

請幫我設計記憶系統的架構。

### Chat Agent

（回應內容...）

...

## 關鍵結論

1. 採用 .md 檔案系統
2. 按主題/時間/人拆分檔案
3. 使用 Grep 檢索

## 相關記憶更新

- agent/knowledge/memory-system.md
- people/user-alice.md
```

## 歸檔觸發條件

| 條件 | 操作 |
|------|------|
| 對話結束 | 記錄至 archive/{user_id}/{date}.md |
| 長期無互動 | user-{user_id}.md 移至 archive/{user_id}/ |
| 用戶重新活躍 | 從 archive 恢復至 user-{user_id}.md |
