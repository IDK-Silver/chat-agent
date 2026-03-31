---
name: memory-maintenance
description: "記憶檔案維護：格式規範、重複移除、檔案拆分。收到 memory_edit warning 或用戶要求整理記憶檔案時使用。"
---

# 記憶維護指南

## 用途

收到 `memory_edit` warning（`file_too_long`、`possible_duplicates`）時，或用戶要求整理記憶檔案時使用。

## 使用方式

1. 讀取本 skill 的 `references/rules.md` 取得格式規範
2. 用 `execute_shell` 呼叫 Claude Sonnet：

```bash
cd {agent_os_dir} && claude -p --output-format stream-json --verbose "$(cat personal-skills/memory-maintenance/references/rules.md)

任務：[具體任務描述，包含目標檔案路徑]" --model sonnet --max-turns 25 --allowedTools "Read,Write,Edit"
```

## 重要事項

- **必須使用 `--model sonnet`**，維護任務不需要 opus 等級
- **必須使用 `--output-format stream-json --verbose`**，讓系統能顯示 Claude Code 工具進度並提取乾淨最終結果
- **必須使用 `--allowedTools "Read,Write,Edit"`**，授權 sonnet 讀寫檔案
- **嚴禁使用 `--dangerously-skip-permissions`**
- 工作目錄必須在 `{agent_os_dir}`，讓 claude 能直接存取 `memory/` 路徑
- 不要自己嘗試大規模重構，交給 claude sonnet 處理
- 執行完畢後檢查結果，確認沒有內容遺失

## 常見任務

### 移除重複條目
```bash
cd {agent_os_dir} && claude -p --output-format stream-json --verbose "$(cat personal-skills/memory-maintenance/references/rules.md)

任務：整理 memory/agent/long-term.md，移除重複條目（保留較完整的版本），統一格式" --model sonnet --max-turns 25 --allowedTools "Read,Write,Edit"
```

### 拆分過長檔案
```bash
cd {agent_os_dir} && claude -p --output-format stream-json --verbose "$(cat personal-skills/memory-maintenance/references/rules.md)

任務：整理 memory/archive/deprecated/thoughts/ 下的舊檔案，合併重複內容並修正 index.md 描述" --model sonnet --max-turns 30 --allowedTools "Read,Write,Edit,Bash"
```

### 格式統一
```bash
cd {agent_os_dir} && claude -p --output-format stream-json --verbose "$(cat personal-skills/memory-maintenance/references/rules.md)

任務：檢查 memory/people/yufeng/ 下所有檔案的格式是否符合規範，修正不符合的部分" --model sonnet --max-turns 25 --allowedTools "Read,Write,Edit"
```
