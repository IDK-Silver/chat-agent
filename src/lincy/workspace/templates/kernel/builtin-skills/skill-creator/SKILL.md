---
name: skill-creator
description: "建立、修改、刪除個人技能的流程規範與格式指引。當學會新工具或技巧、想建立可重複使用的 skill、或需要修改現有 skill 時使用。"
---

# Skill 建立指南

## 用途

學會新工具或技巧，想建立可重複使用的 skill 時使用。

## 判斷標準

### 值得建 skill

- 同樣的任務模式出現 3 次以上
- 跨不同天或不同 channel 出現
- 工作流程穩定（相同工具序列、低變異）
- 沒有既存 skill 已覆蓋 80% 以上

### 不要建 skill

- 一次性操作（如修一個 typo）
- 高度依賴外部 API 且 API 易變的流程
- 已有既存 skill 可更新而非新建
- 內容含敏感個資不適合固化

### 建立前必做

- 確認不與現有 skill 重疊（比對 name、description、涵蓋範圍）
- 若重疊度高，優先更新既有 skill 而非建新的

## 格式規範

### SKILL.md 是唯一入口

每個 skill 是一個獨立目錄，包含必要的 `SKILL.md`：

```
skill-name/
├── SKILL.md              # 必要：入口檔
├── scripts/              # 可選：可執行腳本
├── references/           # 可選：按需載入的參考文件
└── assets/               # 可選：模板、檔案
```

### Frontmatter

`SKILL.md` 開頭必須有 YAML frontmatter：

```yaml
---
name: skill-name
description: "一句話描述這個 skill 做什麼、何時使用。"
---
```

欄位規則：

- `name`：最長 64 字元，只用小寫字母、數字、連字號
- `description`：最長 1024 字元，用第三人稱描述
- description 是觸發機制的核心 — brain 靠它判斷要不要載入 skill

### Description 品質指引

description 同時說明 **做什麼** 和 **何時用**，不要只寫其中一個。

重要關鍵字放前面（超過 250 字元可能被截斷）。

Agent 傾向 under-trigger skill，所以 description 要稍微「pushy」—— 明確列出觸發情境。

好：

```yaml
description: "影片/音訊格式轉換與常用 ffmpeg 指令。當需要轉檔、擷取音訊、調整解析度、或處理任何影音格式問題時使用。"
```

壞：

```yaml
description: "幫忙處理影片"
```

### Progressive Disclosure

- `SKILL.md` body 控制在 500 行以內
- 大量內容拆到 `references/` 子目錄，在 `SKILL.md` 裡引用
- 引用保持一層深度（SKILL.md → references/xxx.md），不要巢狀引用

### 腳本依賴

Python 腳本使用 PEP 723 inline metadata 宣告依賴：

```python
# scripts/extract.py
# /// script
# dependencies = ["pdfplumber>=0.10"]
# requires-python = ">=3.11"
# ///

import pdfplumber
...
```

TS 腳本簡單場景用 versioned import，複雜場景用 `package.json`。

`SKILL.md` 不寫執行器名稱（不寫 `uv run`、`bun`），只描述要執行的腳本路徑。

## 建立流程

### 1. 建立目錄

```
personal-skills/{skill-name}/
```

### 2. 寫 SKILL.md

包含 frontmatter（`name` + `description`）與 body 內容。

至少包含：用途、核心操作方式、注意事項。

### 3. Index 自動維護

- 不要手動編輯 `personal-skills/index.md`
- runtime 會根據每個 skill 的 `SKILL.md` frontmatter 自動重建索引
- skill package 不屬於 `memory/`，建立/修改時使用一般檔案工具，不使用 `memory_edit`

## 修改與刪除

- 修改 skill：直接編輯 `SKILL.md` 或 `references/` 下的檔案
- 刪除 skill：刪掉整個 `skill-name/` 目錄
- 當目錄空了或只剩 `index.md`，runtime 會自動清掉

## 命名規則

- 使用 kebab-case（如 `ffmpeg-convert`、`git-rebase`）
- 以工具名或動作為主（如 `image-resize`，不是 `how-to-resize-images`）
- 不要用太模糊的名稱（如 `helper`、`utils`）

## 注意事項

- Skill 檔案用繁體中文撰寫
- 指令區塊用 code block，確保可直接複製執行
- 不要把整份 man page 塞進去，只記關鍵用法和踩過的坑
- Agent 已經很聰明 — 只加它還不知道的 context
- 刪除 skill 時刪整個 `personal-skills/{skill-name}/` 目錄；不要留下空殼資料夾
