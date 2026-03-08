# Token-only 上下文策略

本文件定義目前 runtime 的 token 管理策略。  
目標是以 provider 回傳的 usage 作為唯一真實來源，避免字元估算造成誤差。

## 核心原則

1. 不做送出前 tokenizer 預估
2. 不做額外 token count API 呼叫
3. 只用 non-streaming 回應中的 usage 欄位
4. soft limit 是回合後處理，不阻擋當前回合完成

## Prompt Cache 不可破壞的前綴

- `ContextBuilder` 的 BP1 / BP2 屬於 system-tier cache prefix：system prompt + boot files
- 任何 per-turn reminder / hint / overlay 都**不可**改寫 BP1 / BP2，也**不可**額外插入新的 system message 來做近端提醒
- 原因：
  - 會破壞快取前綴穩定性，降低 cache hit
  - Anthropic / Gemini adapter 都只保留最後一個 system 欄位，多個 system message 可能互相覆蓋
- 若需要本輪近端提醒，只能放在 conversation tier；目前建議做法是附加在**最新 user message**

## 軟上限（soft limit）

- 設定欄位：`context.soft_max_prompt_tokens`
- 設計定位：把它當成**保守安全值**，不是逼近 provider context window 的調參目標
- 配置原則：數值應保守到足以容納一個完整正常 turn，包含 prompt 成長、tool loop、必要的 synthetic context（如 common ground / skill guide）與最終回覆
- 文件口徑：可把它視為操作上的「絕對安全預算」；也就是說，一旦配置正確，正常 turn 應在此預算內完成，而不是把它拿來賭最後一點窗口
- 判定欄位：brain 回應中的 `prompt_tokens`
- 行為：
  - 若 `prompt_tokens <= soft_max_prompt_tokens`：不動作
  - 若超過：回合結束後才做 compact，影響下一輪
- 注意：runtime 目前仍是**回合後**依 usage 做 compact，不是送出前 hard stop；真正溢出時仍由 `ContextLengthExceededError` fallback 接手
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
3. 以 `context.preserve_turns` compact
4. 單次 retry
5. retry 仍超限則本輪失敗

## 顯示口徑

狀態列只顯示 brain prompt token，不加總 memory/vision/gui 子代理。
