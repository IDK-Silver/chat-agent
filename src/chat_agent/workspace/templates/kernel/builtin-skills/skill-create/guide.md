# Skill 建立指南

## 用途

學會新工具或技巧，想建立可重複使用的 skill 時使用。

## 建立流程

### 1. 確認是否值得建檔

- 一次性操作（如修一個 typo）→ 不建檔
- 未來會重複使用的指令、流程、工具用法 → 建檔

### 2. 建立 skill 資料夾

用 `memory_edit` 建立 `memory/agent/skills/{skill-name}/index.md`：

```
target_path: memory/agent/skills/{skill-name}/index.md
action: create
instruction: |
  建立 skill：{skill-name}
  內容如下：
  # {Skill 名稱}

  ## 用途
  {何時使用這個 skill}

  ## 指令
  {具體的命令格式、flag、參數}

  ## 注意事項
  {陷阱、環境差異、已知限制}
```

### 3. 更新索引

用 `memory_edit` 在 `memory/agent/skills/index.md` 的 `## 技能` 下新增一行：

```
- [{skill-name}](./{skill-name}/index.md) — 一句話摘要
```

## 命名規則

- 使用 kebab-case（如 `ffmpeg-convert`、`git-rebase`）
- 以工具名或動作為主（如 `image-resize`，不是 `how-to-resize-images`）

## 注意事項

- Skill 檔案用繁體中文撰寫
- 指令區塊用 code block，確保可直接複製執行
- 複雜 skill 可拆分子檔案，但 `index.md` 必須自足
- 不要把整份 man page 塞進去，只記關鍵用法和踩過的坑
