---
name: skill-installer
description: "從生態系安裝、更新、移除 Agent Skills。當 owner 要求安裝外部 skill、更新已安裝的 skill、搜尋可用的 skill、或移除不需要的 skill 時使用。"
---

# Skill 安裝指南

## 用途

需要安裝、更新或移除生態系 skill 時使用。外部 skill 安裝到 `~/.agents/skills/`，安裝後 runtime 自動偵測。

## 指令

所有指令使用 `npx skills` CLI。**一律加 `-y` flag** 跳過互動式確認。

### 搜尋可用 skill

```bash
npx skills find <keyword>
```

### 安裝 skill

從 GitHub repo 安裝特定 skill：

```bash
npx skills add <owner>/<repo> --skill <skill-name> -g -y
```

安裝 repo 裡的所有 skill：

```bash
npx skills add <owner>/<repo> --all -g -y
```

常見範例：

```bash
npx skills add vercel-labs/agent-skills --skill agent-browser -g -y
```

### 列出已安裝

```bash
npx skills list -g
```

### 更新

```bash
npx skills update -y
```

### 移除

```bash
npx skills remove <skill-name> -g -y
```

## 安裝位置

- 外部 skill 安裝到 `~/.agents/skills/<skill-name>/SKILL.md`
- 安裝後 runtime 會自動偵測新 skill（hot reload）
- 不需要手動重啟

## 注意事項

- 安裝即信任 — 安裝指令本身就是 owner 的核准
- 外部 skill 的優先順序低於 builtin 和 personal skill（同名時 builtin 優先）
- 安裝失敗時檢查網路連線和 npm registry 可達性
- 若安裝的 skill 需要特定工具（如 browser automation），確認系統已具備

## 主動建議

當 heartbeat 回顧中發現重複的手動操作模式，且生態系有對應 skill，可以主動建議 owner 安裝：

> 「我注意到最近多次手動操作瀏覽器。生態系有 `agent-browser` skill 可以更好地處理這件事。要安裝嗎？」

建議即可，不要自行安裝。
