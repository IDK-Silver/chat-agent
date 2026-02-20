# Sender-Aware 訊息標籤 + 移除靜態 current_user

## 狀態：完成

## 目標

每則訊息一律帶 `[channel, from sender]` 標籤。LLM 從標籤識別 sender，用 `memory_search` 按需載入記憶。不再預載固定用戶記憶。

## 改動摘要

| 元件 | 說明 |
|------|------|
| `agent/core.py` `_tag_message()` | 一律加 `[channel, from sender]` 標籤 |
| `context/builder.py` | 移除 `current_user` 參數和相關邏輯 |
| `core/schema.py` | Boot files 預設移除 `{current_user}` 項目 |
| `cfgs/agent.yaml` | 移除 `{current_user}` boot file 和註解 |
| `cli/app.py` | ContextBuilder 呼叫移除 `current_user` |
| `system.md` | 移除 `get_current_time`、`{current_user}` → `{sender}`、Turn 0 改為 sender 識別 |
| `m0071` | Wire 進 migrations |
| `m0072` | 複製更新後的 system.md |

## 版本

- Kernel: 0.41.0
- Migration: m0071 (wire) + m0072 (sender-aware)

## 依賴

- mq-phase2（完成）
- message-timestamps（完成）
