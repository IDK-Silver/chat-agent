# Token-only 上下文策略

本文件定義目前 runtime 的 token 管理策略。  
目標是以 provider 回傳的 usage 作為唯一真實來源，避免字元估算造成誤差。

## 核心原則

1. 不做送出前 tokenizer 預估
2. 不做額外 token count API 呼叫
3. 只用 non-streaming 回應中的 usage 欄位
4. soft limit 是回合後處理，不阻擋當前回合完成

## 軟上限（soft limit）

- 設定欄位：`context.soft_max_prompt_tokens`
- 判定欄位：brain 回應中的 `prompt_tokens`
- 行為：
  - 若 `prompt_tokens <= soft_max_prompt_tokens`：不動作
  - 若超過：回合結束後才做 compact，影響下一輪
- compact 策略：保留最新 `context.preserve_turns` 輪 user-turn
- **Pre-compaction sync**：compact 前檢查 `_turns_since_memory_sync > 0`，若有累積未同步內容且該輪沒有 memory_edit，先執行一次 memory sync side-channel 再 compact（best-effort，sync 失敗仍會 compact）

## Copilot usage 缺值

Copilot 若沒有回傳 usage，不做估算，也不報錯中止。  
狀態列固定顯示：

`tok unavailable/<soft_limit> (copilot no usage)`

## 硬超限 fallback

若 provider 回傳 `ContextLengthExceededError`：

1. 回滾本輪變更
2. 執行 memory archive + reload boot files
3. 以 `context.overflow_retry_keep_turns` 先 compact
4. 單次 retry
5. retry 仍超限則本輪失敗

## 顯示口徑

狀態列只顯示 brain prompt token，不加總 memory/vision/gui 子代理。
