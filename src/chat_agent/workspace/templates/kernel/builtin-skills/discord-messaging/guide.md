# Discord 訊息指南

## 用途

當當前頻道是 Discord，且你需要判斷：

- 要不要介入回覆
- 訊息要怎麼分段
- 哪些 Markdown 可以用
- 何時需要 `reply_to_message`
- 何時需要先查群組上下文

就先讀這份 skill，再決定 `send_message` 內容。

## 核心規則

### 1. 先判斷要不要介入

- Discord 訊息可能來自 DM（即時）或 guild channel 巡看（`sender` 可能是 `#channel @ guild`）
- 群組訊息不是每句都要回
- 只在以下情況介入：
  - 被 @tag
  - 被直接詢問
  - 需要澄清
  - 你判斷值得插話或提供幫助
- 若選擇保持沉默，可以是完全 `no-op`，也可以只做 `memory_edit`

### 2. 日常聊天優先短句單行

- 日常聊天優先用短句單行
- 不要在同一個 `body` 裡用多個換行硬塞成多段聊天
- 多主題時，拆成多次 `send_message`，每次一個重點
- 所有要送的 `send_message` 必須在同一輪一起呼叫

### 3. Discord 只支援部分 Markdown

- 可用：粗體、斜體、標題、清單、引用、inline code、code block、連結、spoiler
- 不要為了裝飾濫用標題或粗體
- 只有在清單、引用、code block、明確格式需求時，才用多行 Markdown

### 4. 不要用 Markdown table

- Discord 不會把 Markdown table 渲染成真正表格
- 使用者要求「表格」時：
  - 優先改用 code block 對齊輸出
  - 或改成條列
- 不要把 `| a | b | c |` 直接當成表格期待 Discord 幫你渲染

範例：

```text
114-2 學期課表
| 星期 | 課程           | 時間        |
|------|----------------|-------------|
| 週二 | 能源科技與生活 | 14:00-15:50 |
```

上面這種寫法在 Discord 只會顯示成普通文字，不會變成真正 table。

若真的要表格感，改成：

```text
114-2 學期課表

星期   課程             時間
週二   能源科技與生活   14:00-15:50
週三   平行計算         09:00-11:50
```

### 5. 回覆前看上下文

- 需要理解群組前文時，先用 `get_channel_history(channel="discord", ...)`
- Reply reference、link preview、embed 文字常常是重要上下文
- 若要明確回某一則，使用 `reply_to_message`
- 主動傳 guild channel 時，使用 `to="#channel @ guild"`

### 6. 圖片通常很重要

- Discord 圖片附件常是關鍵內容
- 若訊息或附件提示顯示圖片重要，先 `read_image_by_subagent`（或 `read_image`）再回覆

## 快速範例

好：

- `send_message(channel="discord", body="乖～藥吃了就好")`
- `send_message(channel="discord", body="比昨天好多了")`
- `send_message(channel="discord", body="快去吃午餐，想吃什麼？")`
- `send_message(channel="discord", body="重點我整理成下面這樣：\n- 先更新 A\n- 再處理 B")`

避免：

- 在同一次 `send_message` 的 `body` 裡塞三大段日常聊天
- 用 Markdown table 期待 Discord 會幫你排版成表格
