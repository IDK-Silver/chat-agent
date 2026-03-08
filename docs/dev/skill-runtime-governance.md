# Skill Runtime Governance

本文件定義 runtime 如何把 skill 從「提示建議」提升為「工具執行前置條件」。

## 目標

- 讓 skill 是否必讀由 machine-readable metadata 宣告，不再只靠 prompt 自覺
- 保證受管工具真正執行前，模型在**當前回合**已看過對應版本的 `guide.md`
- 同時覆蓋 `staged_planning` 開啟與關閉兩條路徑

## 核心設計

### 1. `meta.yaml`

每個可治理工具的 skill，可在 skill 目錄旁放 `meta.yaml`。

目前格式：

```yaml
id: discord-messaging
guide: guide.md
governs:
  - tool: send_message
    when:
      channel: discord
    enforcement: require_context
```

欄位語意：

- `id`：skill 穩定識別
- `guide`：runtime 需要載入的入口檔
- `governs`：此 skill 會治理哪些 tool call
- `when`：工具參數精確比對條件
- `enforcement=require_context`：真正執行前，模型本輪必須已在上下文看到 guide

### 2. `SkillGovernanceRegistry`

runtime 啟動時掃描：

- `kernel/builtin-skills/**/meta.yaml`
- `memory/agent/skills/**/meta.yaml`

並建立：

- skill id -> guide path
- governed tool rule -> prerequisite lookup
- guide path -> skill id 反查（供 `read_file` 成功後標記已載入）

### 3. 共用 responder preflight

治理掛點在 `src/chat_agent/agent/responder.py` 的 tool loop，而不是某個 channel 或 `staged_planning` 專屬分支。

因此：

- `staged_planning=false`：legacy responder 直接受保護
- `staged_planning=true`：Stage 3 execute 仍走同一個 responder loop，也受保護

### 4. 缺 prerequisite 時的行為

若某輪模型要求受管工具，但本輪尚未載入必要 guide：

1. 先不執行該輪任何 tool side effect
2. 對該輪 tool calls 回傳 deferral tool result
3. runtime 將 guide 內容以 synthetic assistant/tool pair 注入 conversation
4. 讓既有 while-loop 自然再呼叫模型一次

如此可保證第二次決策時，模型已在當前上下文看到 guide。

## 為何不直接假造 `read_file`

runtime 補的是 synthetic skill-load 訊息，不是假裝模型自己呼叫了 `read_file`。

原因：

- 保持 session / debug log 誠實
- 避免讓審計紀錄看起來像模型自己完成了前置讀取

## 當前回合 loaded state

`TurnContext.loaded_skill_guides` 記錄本輪已載入的 skill id。

來源有兩種：

- runtime synthetic 注入
- 模型在正常 responder loop 中自行 `read_file` 讀到對應 guide

作用：

- 避免同一輪重複注入同一份 guide
- 允許「先自己讀 guide，再呼叫受管工具」直接放行

## 邊界

- 這套機制保證的是「執行前，本輪上下文已看過 guide」
- 不保證每輪都真的重新呼叫 `read_file`
- `Stage 1` gather 不直接承擔 prerequisite enforcement；真正 gate 在 execute loop
