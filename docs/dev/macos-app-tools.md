# macOS 原生個人資料工具

這批工具直接操作 macOS 原生 app 的真實資料：

- `calendar_tool`
- `reminders_tool`
- `notes_tool`
- `photos_tool`

目標不是做最小 CRUD，而是提供足夠強的搜尋、分類探索、讀寫能力，讓 agent 可以先理解使用者目前的分類結構，再決定資料應該放哪裡。

## 原則

1. 這些能力放在 `tools.apple_apps`，不是 `features.*`
2. 預設啟用，但只在 `sys.platform == "darwin"` 註冊
3. 每個 app 採「單一強工具 + 多個 action」設計，不拆成大量零碎 tool
4. 寫入前，若目標分類不明確，先跑 `catalog` 或 `search`
5. `notes_tool` 建立新筆記時，必須指定 `folder_id` 或 `folder_path`，避免直接丟進預設資料夾
6. `photos_tool` 先負責搜尋、分範圍、匯出；後續 OCR、摘要、整理流程由其他既有工具處理
7. `notes_tool` 對 LLM 預設回傳可讀文字，不直接暴露巨大 `body_html`

## 設定

`cfgs/agent.yaml`

```yaml
tools:
  apple_apps:
    enabled: true
    timeout_seconds: 30
    max_search_results: 25
    photos_export_dir: "tmp/photos-exports"
```

- `timeout_seconds`：單次 JXA / AppleScript 呼叫 timeout
- `max_search_results`：`search` 預設上限
- `photos_export_dir`：`photos_tool(action="export")` 未指定 `destination_dir` 時的預設匯出根目錄，會建立在 `agent_os_dir` 下

這批工具現在只在 LLM 明確判斷需要時才呼叫，不會自動同步到 `agent_note`，也不會自動建立排程或背景摘要。

Runtime cache：
- `cache/apple_notes`：Apple Notes 衍生快取，保存 `content_markdown` 與搜尋摘要
- `cache/vision`：所有 vision request 共用的圖片快取；只要圖片內容、prompt、vision 模型指紋相同，就直接回舊結果

## Tool 設計

### `calendar_tool`

用途：
- 列出 calendars
- 依 calendar / 日期區間 / 關鍵字搜尋 events
- 檢查候選時段是否撞期
- 讀取單一 event
- 建立 event
- 更新 event

actions：
- `catalog`
- `search`
- `conflicts`
- `get`
- `create`
- `update`

重要參數：
- `calendar`
- `calendars`
- `event_uid`
- `exclude_event_uid`
- `query`
- `start`
- `end`
- `title`
- `notes`
- `location`
- `url`
- `all_day`
- `sort_by`

備註：
- `create` 必須指定 `calendar`
- `update` 用 `event_uid`
- `start` / `end` 使用本地時間 ISO 格式，例如 `2026-04-20T14:00`
- Calendar 讀回的 `start` / `end` 會以 app timezone 輸出並帶 offset，例如 `2026-04-20T14:00:00+08:00`
- `conflicts` 適合先檢查某個時段是否已有重疊事件，更新既有 event 時可帶 `exclude_event_uid`
- `catalog` 會回傳 `writable`、`description`、`color`，讓 agent 不只知道名字，也知道哪個 calendar 可寫

### `reminders_tool`

用途：
- 列出 reminder lists
- 搜尋 reminders
- 讀取單一 reminder
- 建立 reminder
- 更新 reminder
- 標記完成 / 取消完成

actions：
- `catalog`
- `search`
- `get`
- `create`
- `update`
- `complete`

重要參數：
- `list_id`
- `list_name`
- `list_path`
- `reminder_id`
- `query`
- `title`
- `notes`
- `due`
- `due_start`
- `due_end`
- `priority`
- `priority_min`
- `priority_max`
- `flagged`
- `completed`
- `sort_by`

備註：
- 沒有 `list_id` 時可用 `list_name` 或 `list_path`
- 若 agent 不知道要放哪個 list，應先 `catalog`
- `search` 可直接篩完成狀態、旗標、priority 範圍、到期區間

### `notes_tool`

用途：
- 列出 Notes account / folder tree
- 搜尋筆記
- 讀取單一筆記
- 在指定 folder 建筆記
- 更新或 append 既有筆記
- 將筆記搬到另一個 folder

actions：
- `catalog`
- `search`
- `get`
- `create`
- `update`
- `move`

重要參數：
- `account`
- `folder_id`
- `folder_path`
- `target_folder_id`
- `target_folder_path`
- `note_id`
- `query`
- `created_after`
- `created_before`
- `modified_after`
- `modified_before`
- `title`
- `body`
- `template_markdown`
- `variables`
- `images`
- `append`
- `sort_by`
- `offset`

備註：
- `create` 必須指定 `folder_id` 或 `folder_path`
- `folder_path` 來自 `catalog`，格式如 `iCloud/待讀`
- `title` 是 Notes 真正的筆記名稱；如果同時用了 `template_markdown`，tool 會確保 `title` 成為第一個可見區塊
- `title` 與正文是兩層不同概念：`title` 管 Notes 真正的筆記名稱，`template_markdown` 管正文排版
- `body` 會轉成簡單 HTML，保留換行
- `template_markdown` 走受控 Markdown 子集，適合讓 agent 控制 note 區塊順序與格式
- `variables` 與 `images` 的 key 可以自由新增，不限固定欄位；模板裡寫 `{paper_title}`、`{quote_1}`、`{image_cover}` 都可以
- 圖片 placeholder 支援兩種：
  - `{image_cover}`
  - `![封面](image_cover)`
- `images.image_cover` 這種值必須是可讀的本機圖片路徑
- `template_markdown` 目前支援：
  - `#` / `##` / `###`
  - 段落與空行
  - `**粗體**` / `*斜體*` / `` `inline code` ``
  - 無序清單 / 編號清單
  - 連結 `[文字](url)` 與裸 `https://...` URL
  - 簡單表格 `|...|`
  - 圖片 placeholder
- Notes 寫回時可能把 HTML `<a href>` 正規化成底線文字；最穩的是直接保留完整 URL，不要假設自訂連結文字一定還能點
- `template_markdown` 不支援完整自由排版；重點是控制區塊順序，不是做 Word 等級排版
- Notes 友善映射目前固定為：
  - `#` -> `h1` + `15.0pt bold`
  - `##` -> `h2` + `13.5pt bold`
  - `###` -> `h3` + `12pt bold`
  - 無序清單 / 編號清單 -> 純文字行，不強制轉成 HTML list
- 若已傳 `title`，正文預設不要再用同樣文字寫一個 `# 主標題`；否則會變成「筆記名稱一份，正文大標又一份」
- 正文若已傳 `title`，建議直接從 `##` 開始
- `#` / `##` / `###` 後面建議空一行，版面比較自然
- `search` 預設回傳摘要結果，不回 `body_html` 或全文；欄位包含 `summary`、`content_kind`、`has_images`、`source_url`、`content_chars`
- `get` 預設回傳單一 `content_markdown`
- `notes_tool` 讀取筆記時，會先把 HTML 轉成 Markdown；若遇到 `data:image/...` 內嵌圖片，會先走 vision，再把圖片替換成文字摘要
- `search` 的摘要建立在 `get` 產出的 `content_markdown` 上，並快取到 `cache/apple_notes`
- `move` 讓 agent 可以先暫存到某個 folder，之後再整理到正確分類
- `search` 支援 `offset` 分頁；`limit` 預設 5，最大受 `tools.apple_apps.max_search_results` 限制

### `photos_tool`

用途：
- 列出 Photos albums / folders
- 依 album / album path / folder / folder path / 日期 / 關鍵字 / favorite 搜尋照片
- 讀取單一 album
- 讀取指定 media item metadata
- 匯出 media items 成檔案
- 建立 album
- 把照片加入 album

actions：
- `catalog`
- `search`
- `get_album`
- `get_media`
- `export`
- `create_album`
- `add_to_album`

重要參數：
- `album_id`
- `album_name`
- `album_path`
- `folder_id`
- `folder_path`
- `parent_folder_id`
- `parent_folder_path`
- `query`
- `start`
- `end`
- `favorite`
- `sort_by`
- `media_ids`
- `destination_dir`
- `use_originals`

備註：
- `search` 回傳 `media item id`
- `search` 也會回傳 `description`、`width`、`height`、`size`、`location`
- `get_album` 可用 `album_id`、`album_name`、`album_path`
- `create_album` 可用 `parent_folder_id` 或 `parent_folder_path`
- `export` 要帶 `media_ids`
- `destination_dir` 必須落在 `allowed_paths` 內；未指定時會自動匯出到 `tools.apple_apps.photos_export_dir`
- 若要排序整個圖庫，必須先縮小範圍；否則容易掃太久

## 使用規則

### 1. 先查分類，再寫入

錯的做法：
- 直接把文章存進 Notes 預設資料夾
- 直接把會議建立到預設 calendar

對的做法：
1. `notes_tool(action="catalog")`
2. 看有沒有 `待讀`、`工作`、`講座` 等 folder
3. 再 `notes_tool(action="create", folder_path="iCloud/待讀", ...)`

### `template_markdown` 例子

```json
{
  "action": "create",
  "folder_path": "iCloud/待讀",
  "title": "多目標追蹤模型",
  "template_markdown": "來源：{url}\n\n## 原圖\n{image_cover}\n\n## 簡介\n{summary}\n\n## 重點\n- {point_1}\n- {point_2}",
  "variables": {
    "url": "https://x.com/...",
    "summary": "Roboflow 把多目標追蹤完整開源。",
    "point_1": "支援任意檢測器即插即用",
    "point_2": "CLI 一行命令可追蹤影片"
  },
  "images": {
    "image_cover": "/absolute/path/to/clip.png"
  }
}
```

說明：
- agent 可以自己新增變數名，不必侷限在 `title`、`summary`
- 如果要控制 Notes 實際顯示的筆記名稱，應另外傳 `title`，不要只把標題塞在模板變數裡
- 模板出現順序，就是最後 note 的區塊順序
- 圖片要在上面，就把 `{image_cover}` 放在上面

乾淨版型：

```json
{
  "action": "create",
  "folder_path": "iCloud/待讀",
  "title": "多目標追蹤模型",
  "template_markdown": "來源：{url}\n\n## 簡介\n\n{summary}\n\n## 原文\n\n- {point_1}\n- {point_2}"
}
```

不要這樣：

```json
{
  "action": "create",
  "folder_path": "iCloud/待讀",
  "title": "多目標追蹤模型",
  "template_markdown": "# 多目標追蹤模型\n來源：{url}\n\n## 簡介\n\n{summary}"
}
```

原因：
- `title` 已經會控制 Notes 真正的筆記名稱
- 正文再放同樣的 `# 多目標追蹤模型`，畫面就會重複一份大標

### 2. 複合流程先拿資料，再處理

例子：整理今天的講座照片成文字

1. `photos_tool(action="search", query="講座", start="2026-04-11T00:00", end="2026-04-11T23:59")`
2. `photos_tool(action="export", media_ids=[...])`
3. 對匯出的圖片跑 `read_image`
4. 把整理結果寫進 `notes_tool`

例子：先檢查會議是否撞期，再建立事件

1. `calendar_tool(action="conflicts", calendars=["工作", "居家"], start="2026-04-15T14:00", end="2026-04-15T15:00")`
2. 若 `count=0`，再 `calendar_tool(action="create", calendar="工作", ...)`

例子：把已整理過的筆記從 `待讀` 搬到 `已讀`

1. `notes_tool(action="search", folder_path="iCloud/待讀", query="某篇文章")`
2. `notes_tool(action="move", note_id="...", target_folder_path="iCloud/已讀")`

### 3. 危險操作要保守

目前這批工具刻意不提供大量刪除能力。

原因：
- 這些都是使用者真實資料
- 第一版先把探索、搜尋、建立、更新做穩
- 刪除或搬移大量資料時，後續要另外設計保護機制

## 實作細節

- 讀取與搜尋主要走 `osascript -l JavaScript`（JXA）
- 建立與更新主要走 AppleScript，因為對 Calendar / Reminders / Notes / Photos 的寫入比較直接
- AppleScript 寫入的文字欄位不直接用 `system attribute` 傳內容，會先寫成 UTF-8 暫存檔再讀回
- 這是為了避開 `osascript` 在非 ASCII 文字上的亂碼問題，像中文標題、備忘錄內容、相簿名稱都會受影響
- 若 JXA / AppleScript 呼叫過慢或超時，log 會記下 `operation`、`elapsed` 與隱私安全的參數摘要，方便追查是哪個 app action 卡住
- `photos_tool(action="export")` 會先驗證 `destination_dir` 是否在 `allowed_paths` 內

## 測試範圍

這批工具至少要有：

- registry wiring 測試：macOS 時會註冊，非 macOS 不註冊
- action 參數驗證測試：缺必要參數時要回 `Error: ...`
- export 路徑限制測試：不允許匯出到未授權路徑

真正讀寫 Calendar / Reminders / Notes / Photos 的整合測試目前不放進自動化測試，因為會碰到本機資料與系統權限。
