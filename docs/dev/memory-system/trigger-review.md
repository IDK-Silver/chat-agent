# Trigger Review 系統

本文件描述目前 reviewer 架構：
- `responder` 負責生成與工具執行
- `progress_reviewer` 只監控中間可見文字（advisory）
- `post_reviewer` 只做工具義務完成審核（completion gate）
- `shutdown_reviewer` 與 post 採同一 completion-only 策略

## 責任切分

## Responder

- 在單一 turn 內可多次 `tool_calls`。
- 只要是中間回合 `content + tool_calls` 且 `content` 非空，會即時顯示給用戶。
- conversation 內永遠保留原始 assistant(tool_calls) 與 tool result 配對。

## Progress Reviewer（中間文字監控）

- 只審中間段落 `candidate_assistant_reply` 的可見文字風險。
- 角色是監控器（advisory only），不攔截輸出。
- `passed=false` 或 parse 失敗都不會阻擋顯示；僅輸出 warning/debug。

## Post Reviewer（Completion Gate）

- 只審「required actions 是否完成」。
- 不審文字品質（不處理 `empty_reply`、`repetitive_content`、`simulated_user_turn`、`gender_confusion`）。
- 只在 required actions 缺漏時觸發 retry。

## Shutdown Reviewer

- 只審 shutdown 流程中的 required actions 完成度。
- 不再做 target/anomaly runtime enforcement。

## Chat 流程重點

1. `_run_responder` 中間段落即時顯示。  
2. 若 `post_reviewer.enabled=true`：
- 若 final 為空但本輪已有中間可見文字：直接接受，不做「空回覆重試」。
- 若本輪完全無可見文字：觸發補最終回覆 retry。
- 其他情況：呼叫 post reviewer，僅依 `required_actions` 缺漏判定是否 retry。
3. reviewer parse/呼叫失敗採 fail-open（警告後放行）。
4. 保留程式端粗網：若本輪完全沒有 memory 寫入，仍可補 `persist_turn_memory` action。

## Shutdown 流程重點

- 只依 `required_actions` + `find_missing_actions(...)` 決定是否 retry。
- reviewer 失敗仍維持現有 shutdown 策略（由 `perform_shutdown` 控制）。

## Post-review Schema

```python
class PostReviewResult(BaseModel):
    passed: bool
    required_actions: list[RequiredAction]
    retry_instruction: str
    guidance: str | None
```

`required_actions` 的欄位維持：
- `code`
- `description`
- `tool`
- `target_path`（optional）
- `target_path_glob`（optional）
- `command_must_contain`（optional）
- `index_path`（optional）

## Config（摘要）

```yaml
warn_on_failure: true

agents:
  progress_reviewer:
    enabled: false
    post_parse_retries: 2
    warn_on_failure: true

  post_reviewer:
    enabled: true
    post_parse_retries: 2
    max_post_retries: 5
    allow_unresolved: true
    warn_on_failure: true

  shutdown_reviewer:
    enabled: false
    post_parse_retries: 2
    max_post_retries: 5
    allow_unresolved: true
    warn_on_failure: true
```

## 既知行為

- 不會再因「final 空字串」而強制重講一次，只要前面已有中間可見文字。
- post-review retry 觸發點收斂為工具義務缺漏。
- progress reviewer 關閉時，中間文字預設直接顯示。
