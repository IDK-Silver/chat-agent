# Skill 建立指南

## 用途

學會新工具或技巧，想建立可重複使用的 skill 時使用。

## 建立流程

### 1. 確認是否值得建檔

- 一次性操作（如修一個 typo）→ 不建檔
- 未來會重複使用的指令、流程、工具用法 → 建檔

### 2. 決定單檔或資料夾模式

- 內容短、沒有子主題 → 單檔 `memory/agent/skills/{skill-name}.md`
- 內容較長、要拆子主題或附帶資源 → 資料夾 `memory/agent/skills/{skill-name}/`

### 3. 建立可搜尋的主檔

單檔模式直接建立 `{skill-name}.md`。

資料夾模式建立 `guide.md`，不要把實際內容只放在 `index.md`。

```
target_path: memory/agent/skills/{skill-name}/guide.md
instruction: 建立此 skill 主檔，至少包含標題、用途、核心操作方式、注意事項。
```

### 4. 讓 index 自動維護

- 不要手動新增或刪除 `index.md` 連結
- 建立或刪除主檔時，系統會自動更新父層 `index.md`
- 若只是摘要描述要改，才對現有 `index.md` 連結使用 `replace_block`

### 5. 刪除 skill

- 刪除單檔 skill：刪掉該 `.md` 主檔
- 刪除資料夾 skill：先刪 `guide.md` 與其他實際內容檔
- 當資料夾真的只剩 `index.md` 或已經空了，runtime 會自動清掉空目錄

## 命名規則

- 使用 kebab-case（如 `ffmpeg-convert`、`git-rebase`）
- 以工具名或動作為主（如 `image-resize`，不是 `how-to-resize-images`）

## 注意事項

- Skill 檔案用繁體中文撰寫
- 指令區塊用 code block，確保可直接複製執行
- 資料夾模式下，`guide.md` 才是主要內容入口；`index.md` 只做導覽
- `memory_search` 不會把 `index.md` 當主要內容來源，別把核心內容只塞進 `index.md`
- 不要把整份 man page 塞進去，只記關鍵用法和踩過的坑
