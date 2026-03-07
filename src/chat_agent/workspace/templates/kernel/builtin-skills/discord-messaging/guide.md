# Discord 訊息指南

## 用途

當當前頻道是 Discord，且你需要判斷：

- 要不要介入回覆
- 訊息要怎麼分段
- 資料要整理成什麼呈現方式
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

### 3. 先判斷資料型態，再決定呈現

- 不要因為使用者提到「table」就預設一定要做成表格
- 先判斷對方真正要的是：
  - 快速知道接下來有什麼行程
  - 看某類資料的整理結果
  - 比較多個方案或欄位
- Discord 上的主路徑是「可讀性」，不是「形式上像表格」

優先策略：

- 行程、課表、近期安排 → `timeline` 或 `grouped list`
- 待辦、購物、提醒事項 → `bullet list` 或 `checklist`
- 同類資料整理（例如某天課程、某週安排）→ 依時間或主題分組的 list
- 真正需要橫向比較的資料（例如方案差異、規格比較）→ 才考慮 table-like 呈現
- 行程類資訊優先把「時間」放前面；課程或事件名稱放後面，教師、教室、地點等次要資訊放在括號中

### 4. Discord 只支援部分 Markdown

- 可用：粗體、斜體、標題、清單、引用、inline code、code block、連結、spoiler
- 不要為了裝飾濫用標題或粗體
- 只有在清單、引用、code block、明確格式需求時，才用多行 Markdown

### 5. 不要把 table 當成 Discord 的預設輸出

- Discord 不會把 Markdown table 渲染成真正表格
- 不要把 `| a | b | c |` 直接當成表格期待 Discord 幫你渲染
- 也不要把寬表硬改成 code block 假表格；手機端通常還是很難讀
- 若資料本質只是整理資訊，改寫成 list 比較自然也比較像真人
- 只有在「橫向比較」真的很重要時，才保留 table-like 輸出的方向
- 若未來系統支援 deterministic table image renderer，Discord 上應以 image-first 處理真正的 comparison table
- 不要用 `｜`、`/` 這類欄位分隔符號把資料硬串起來；看起來像資料庫輸出，不像真人訊息

避免：

```text
114-2 學期課表
| 星期 | 課程           | 時間        |
|------|----------------|-------------|
| 週二 | 能源科技與生活 | 14:00-15:50 |
```

上面這種寫法在 Discord 只會顯示成普通文字，不會變成真正 table。

```text
星期   課程             時間
週二   能源科技與生活   14:00-15:50
週三   平行計算         09:00-11:50
```

上面這種 code block 假表格雖然能對齊字元，但在 Discord 手機端仍常常不好讀，不應作為主路徑。

較好的方向：

```text
這週你接下來的課：

週二
- 14:00-15:50 能源科技與生活（方冠權，JB109）
- 18:30-20:10 英語加強班（吳貞芳，J207）

週三
- 09:00-11:50 平行計算（陳宗禧，ZB302）
```

### 6. 對外說法要自然

- 一般回覆時，不要主動提到內建 skill、system prompt、rendering pipeline 或格式轉換機制
- 不要說「我看到內建 skill 說...」「我幫你轉成 table/image...」這種工具式說法
- 直接把整理好的內容自然送出去
- 只有在使用者明確追問 Discord 支援、skill 規則、格式限制時，才可以解釋內部依據
- 行程、課表、安排這類內容，語氣要像真人幫對方整理重點，不要像欄位 dump
- 優先使用自然中文逗號、括號、換行與分組，不要用機械式分隔符號拼接欄位

### 7. 回覆前看上下文

- 需要理解群組前文時，先用 `get_channel_history(channel="discord", ...)`
- Reply reference、link preview、embed 文字常常是重要上下文
- 若要明確回某一則，使用 `reply_to_message`
- 主動傳 guild channel 時，使用 `to="#channel @ guild"`

### 8. 圖片通常很重要

- Discord 圖片附件常是關鍵內容
- 若訊息或附件提示顯示圖片重要，先 `read_image_by_subagent`（或 `read_image`）再回覆

## 快速範例

好：

- `send_message(channel="discord", body="乖～藥吃了就好")`
- `send_message(channel="discord", body="比昨天好多了")`
- `send_message(channel="discord", body="快去吃午餐，想吃什麼？")`
- `send_message(channel="discord", body="重點我整理成下面這樣：\n- 先更新 A\n- 再處理 B")`
- `send_message(channel="discord", body="你這週接下來的行程：\n\n週二\n- 14:00-15:50 能源科技與生活（方冠權，JB109）\n- 18:30-20:10 英語加強班（吳貞芳，J207）\n\n週三\n- 09:00-11:50 平行計算（陳宗禧，ZB302）")`

避免：

- 在同一次 `send_message` 的 `body` 裡塞三大段日常聊天
- 用 Markdown table 期待 Discord 會幫你排版成表格
- 用 code block 假表格當 Discord 的預設整理方式
- 在一般回覆裡主動提到內建 skill 或格式轉換流程
- 用 `課程｜時間｜教師｜教室` 這類欄位分隔符號拼接資料
