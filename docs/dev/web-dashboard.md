# Web Dashboard（chat_web_api + chat_web_ui）

監控 dashboard，即時顯示 token 用量、成本、read cache rate，並提供本機 Web Chat 介面。

## 架構

```
Browser → uvicorn (:9002) → FastAPI (chat_web_api)
                             ├── /health
                             ├── /api/*        REST endpoints
                             ├── /ws           WebSocket 即時推送
                             └── /*            Vue dist/ 靜態檔 + SPA fallback
```

資料流：JSONL append → watchfiles 偵測 → incremental read → cache 更新 → WebSocket push → Vue reactive 更新

Web Chat 資料流：

```
Browser → chat_web_api /api/chat/messages → chat-cli control API
        → WebAdapter → AgentCore queue → send_message(channel="web")
        → WebAdapter → state/web_chat/events.jsonl → chat_web_api /ws
```

## 後端 (`src/chat_web_api/`)

| 檔案 | 職責 |
|------|------|
| `settings.py` | 從 `cfgs/agent.yaml` 讀取 `agent_os_dir`、`soft_max_prompt_tokens` |
| `pricing.py` | 從 LiteLLM GitHub JSON 抓取 model pricing，本地 cache 24h |
| `session_reader.py` | 增量 JSONL 讀取器（byte offset seek，只讀新行） |
| `cache.py` | In-memory metrics cache：sessions、turns、responses 聚合 |
| `watcher.py` | `watchfiles.awatch()` 監控 session 目錄變動 |
| `app.py` | FastAPI factory：REST + WebSocket + 靜態檔 serving |

Web Chat 事件模型與 JSONL store 位於 `src/lincy/agent/web_chat.py`，adapter 位於 `src/lincy/agent/adapters/web.py`。事件檔固定在 `agent_os_dir/state/web_chat/events.jsonl`。

### API Endpoints

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/dashboard?from=&to=` | 總覽：cost、turns、read cache rate、daily 聚合 |
| GET | `/api/sessions?from=&to=&limit=&offset=` | Session 列表 |
| GET | `/api/sessions/{id}` | Session 細節：turns + per-request breakdown |
| GET | `/api/requests?from=&to=&limit=&offset=` | 跨 session 的全域 request log |

### 日期篩選語意

`from` / `to` 以 **turn 開始時間** 與 **response 時間戳** 判斷是否落在區間內，**不是** session `created_at`：

- 跨午夜仍在跑的 session（例如 7/10 建立、7/11 還在用 Grok）在「今天 / 7 天」會出現
- `/api/requests` 依 response `ts` 過濾，並 **最新優先**（前端首頁 limit 500 才看得到當前 model）
- dashboard 的 daily cost / token 也依 response / turn 當日聚合
| GET | `/api/live` | 當前 active session 的 token 位置 |
| GET | `/api/claude-accounts` | 轉發 claude-code-proxy `/usage`：帳號、5h/週用量、model list；proxy 不可用時回 `available: false` |
| POST | `/api/claude-accounts/login` | 轉發 proxy `POST /login`：開始 browser OAuth，回 `login_id` + `authorization_url` |
| POST | `/api/claude-accounts/login/{login_id}/complete` | 轉發 proxy 完成登入：body `{"code": "code#state"}`，token 寫入 proxy store |
| POST | `/api/claude-accounts/{token_id}/promote` | 轉發 proxy `POST /tokens/{id}/promote`：設為最高優先 |
| DELETE | `/api/claude-accounts/{token_id}` | 轉發 proxy `DELETE /tokens/{id}`：移除 token |
| GET | `/api/codex-accounts` | 轉發 codex-proxy `/usage`：帳號、usage windows；proxy 不可用時回 `available: false` |
| POST | `/api/codex-accounts/login` | 轉發 proxy 開始 browser OAuth，回 `login_id` + `authorization_url`（可能附 `listener_error`） |
| GET | `/api/codex-accounts/login/{login_id}` | 輪詢登入狀態，回 `status`：`pending` / `completed` / `expired`（前端每 2 秒輪詢一次） |
| POST | `/api/codex-accounts/login/{login_id}/complete` | 完成登入：body `{"value": "<callback URL 或 code#state>"}`，token 寫入 proxy store |
| POST | `/api/codex-accounts/{token_id}/promote` | 設為最高優先 |
| DELETE | `/api/codex-accounts/{token_id}` | 移除 token |
| GET | `/api/chat/events?limit=` | Web Chat 最近事件 |
| POST | `/api/chat/messages` | 轉送本機 Web Chat 訊息到 chat-cli control API |
| WS | `/ws` | 即時推送：`session_updated`、`live_token_update`、`session_created` |

WebSocket 另會推送 `chat_event`：

```json
{"type": "chat_event", "event": {"id": "...", "kind": "message", "role": "assistant"}}
```

### Token 計費邏輯

Anthropic provider 的 `prompt_tokens` 已包含 cache tokens（見 `src/lincy/llm/providers/anthropic.py:241`）：

```
prompt_tokens = base_input + cache_read + cache_write
base_input = prompt_tokens - cache_read_tokens - cache_write_tokens
cost = base_input × input_rate + cache_read × cr_rate + cache_write × cw_rate + completion × output_rate
```

Pricing 來源：`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`

本專案可在 `src/chat_web_api/pricing.py` 維護本地 override，處理 LiteLLM 尚未更新或價格不符合本專案口徑的模型。DeepSeek V4 目前使用官方原價計算，不使用 DeepSeek 官網列出的 75% 折扣價；override 會帶 `pricing_source=local_override`、`pricing_source_url` 與 stale 狀態，前端會在 Total Cost 與 request breakdown 顯示。

### 增量讀取

JSONL 是 append-only，每個檔案追蹤 `byte_offset`：
- `seek(offset)` → 讀到 EOF → 更新 offset
- 不重讀舊資料，新 session 出現時建立新 entry

## 前端 (`src/chat_web_ui/`)

Tech stack：Vue 3 + Vite + Bun + shadcn-vue + Tailwind CSS + Chart.js

### 頁面結構

| 路由 | 頁面 | 說明 |
|------|------|------|
| `/monitor` | MonitorDashboard | 總覽：summary cards + 圖表 + sessions 表格 |
| `/monitor/requests` | MonitorRequests | 跨 session request log，按 session 分組 |
| `/monitor/:id` | MonitorSession | 單一 session：turn timeline + expandable responses |
| `/proxy` | ProxyPage | Proxy usage 獨立區塊：Claude / Codex 帳號用量 + 帳號管理（add/promote/remove） |
| `/chat` | ChatPage | 本機 Web Chat：訊息列表 + composer |
| `/settings` | SettingsPlaceholder | 預留 |

Overview 和 Requests 之間用 tab bar 切換（`MonitorTabs.vue`）。

### Proxy 頁（Claude Accounts 卡片）

`components/proxy/ClaudeAccountsCard.vue`（獨立 `/proxy` 頁，sidebar「Proxy」）顯示並管理 claude-code-proxy token pool：

- 每帳號一列：狀態點（active 綠 / standby 灰 / benched 琥珀 / unusable 紅）、email、plan 標籤。狀態文字改為 dot 的 `title` tooltip，不再另外顯示 ACTIVE/STANDBY 等文字；plan 標籤縮短（`rate_limit_tier`/`plan_type` 去掉 `default_`、`claude_` 前綴並 title-case，例：`default_claude_max_5x` → `Max 5x`、`claude_pro` → `Pro`、`default_claude_ai` → `AI`）
- 用量改為對齊的單欄 meter rows（每帳號一個 grid，欄位對齊）：一列一個時間窗，依序 label、bar（<70% 黑、70-90% 琥珀、≥90% 紅）、% 與重置時間，涵蓋 5h、Week，以及 model-scoped weekly（如 Fable）；model-scoped 列來源是 proxy 解析 OAuth usage `limits[]` 中 `kind=weekly_scoped` 的項目（以 `scope.model.display_name` 當 label），在 `/api/claude-accounts` 回應以 `usage.seven_day_scoped` 欄位輸出
- 底部列出 active 帳號可用的 model id（來源 proxy `/v1/models` passthrough），呈現為 mono 文字列表（`·` 分隔）；預設收合，點「Models (N)」disclosure 展開
- 資料來源 `/api/claude-accounts`，3 分鐘輪詢；卡片右上有手動 Refresh 按鈕，帶 `?refresh=true` 繞過 proxy 端 60s snapshot 快取強制重抓
- 帳號用量抓取失敗（如 OAuth endpoint 429）時顯示上次成功資料，錯誤降為灰字 `stale — ...` 註記；完全沒有資料才顯示紅字錯誤

編輯操作（等價於 `proxy claude-code login` / `tokens promote` / `tokens remove`）：

- **Add account**：`POST /api/claude-accounts/login` 取得授權連結，使用者在新分頁授權後把 `code#state` 貼回卡片，`POST .../login/{login_id}/complete` 完成；pending login 狀態存在 proxy 記憶體，15 分鐘過期
- **Promote**：非最高優先帳號顯示 icon 按鈕（`ArrowUp`），設為最高優先
- **Remove**：icon 按鈕（`X`），`window.confirm` 確認後移除 token
- 任何編輯成功後 proxy 會失效 usage snapshot 快取，卡片跟著 `?refresh=true` 重抓
- proxy 端管理端點與 `/usage` 同一道 inbound gate：loopback 直接信任，遠端需 `CLAUDE_CODE_PROXY_API_KEY`

單帳號列的呈現（狀態點、email/id、plan 標籤、promote/remove 按鈕、meter grid、error/stale 行）抽成共用元件 `components/proxy/ProxyAccountRow.vue`，Claude 與 Codex 卡片都用它渲染，只餵不同的 props（rows 陣列、plan 文字、canPromote/canRemove 等），視覺上完全一致。

### Proxy 頁（Codex Accounts 卡片）

`components/proxy/CodexAccountsCard.vue`（`/proxy` 頁，Claude 卡片下方）顯示並管理 codex-proxy token pool，版面與互動邏輯比照 Claude 卡片，皆透過 `ProxyAccountRow` 渲染：

- 用量列直接對應 `/api/codex-accounts` 回應的 `usage.windows[]`：每列 `label`（例：`5h`、`Week`，由 proxy 端 `limit_window_seconds` 推導）、`utilization`、`resets_at`；label 不是 `\d+h` 形式時重置時間帶日期（`MM/DD HH:MM`），否則只顯示 `HH:MM`
- plan 標籤：`account.plan_type` title-case（例：`plus` → `Plus`）；`source === "codex_auth"` 的帳號（讀自官方 Codex CLI 的 `~/.codex/auth.json`，不在本專案 token store 裡）在標籤後綴 ` · codex cli`，並隱藏 promote/remove 按鈕，因為這類帳號無法透過 proxy store API 操作
- `models` 目前固定回空陣列，沿用 Claude 卡片同一顆 `v-if`，故不顯示 Models disclosure
- 資料來源 `/api/codex-accounts`，3 分鐘輪詢；同 Claude 卡片提供手動 Refresh（`?refresh=true`）

登入流程與 Claude 卡片的手動貼 `code#state` 不同，改成「自動完成為主、手動貼網址為備援」：

- **Add account**：`POST /api/codex-accounts/login` 取得 `authorization_url` 後在新分頁開啟；ChatGPT 授權完成後會導回 proxy 監聽的 `http://localhost:1455/auth/callback`，proxy 端 listener 自動完成登入（僅當瀏覽器與 proxy 在同一台機器時才連得到 localhost）
- 面板開啟後卡片以 2 秒間隔輪詢 `GET /api/codex-accounts/login/{login_id}`：`completed` 時關閉面板並 `?refresh=true` 重抓；`expired` 時顯示錯誤並清空面板，需重新點 Add account
- 手動 fallback：遠端瀏覽器打不開 callback 頁面時，把網址列上失敗的 `localhost:1455/...` 網址複製貼到卡片輸入框，按 Complete 呼叫 `POST .../login/{login_id}/complete`（body `{"value": "..."}`）
- `beginCodexLogin()` 回應若帶 `listener_error`，卡片顯示提示：自動完成不可用，需改用手動貼網址
- Cancel 按鈕清除輪詢 timer；元件 unmount 時一併清掉，避免背景持續打 API

### 視覺風格

- 背景 `#FFFFFF`，卡片邊框 `1px #E5E7EB`，陰影 `0 1px 2px rgba(0,0,0,0.04)`
- 主色 `#111827`（近黑），次色 `#6B7280`（灰），禁用 `#D1D5DB`
- 數字一律 `tabular-nums`
- **零漸層、零彩色背景**
- 唯一彩色：Live 綠點 `#22C55E`、token bar 警告色（amber `#F59E0B`、red `#EF4444`）

### Token Bar

位於 top bar 右上，顯示當前 active session 的 context 用量：
- 單一 bar：0 → hard limit（model max_input_tokens），soft limit 位置有細線標記
- 顏色：`<70% soft` 灰、`70-85%` 黑、`85-100%` 琥珀、`>100%` 紅
- Hover tooltip 顯示完整數字
- 只在有 active session 時顯示

### 圖表

- **Daily Cost**（bar chart）：每日花費，黑色柱狀
- **Read Cache Rate**（line chart）：
  - Today → per-request 粒度，x 軸 `HH:MM`，標題 "Request Read Cache Rate"
  - 7D/30D/Month → per-day 粒度，x 軸 `MM/DD`，標題 "Daily Read Cache Rate"

### 時間範圍

`TimeRangeSelector.vue` 提供：
- Today / 7D / 30D 快速按鈕
- Month 按鈕：彈出下拉面板，年份左右箭頭 + 3×4 月份網格，未來月份禁用

### 即時更新

- `stores/websocket.ts`：singleton WebSocket，3 秒自動重連
- `stores/live.ts`：active session token 位置，WebSocket `live_token_update` 更新
- `stores/dashboard.ts`：收到 `session_updated` / `session_created` 時自動 refresh
- `stores/chat.ts`：載入 `/api/chat/events`，送出 `/api/chat/messages`，收到 `chat_event` 時 dedupe 後追加

## Supervisor 整合

```yaml
# cfgs/supervisor.yaml
chat-web-ui-build:       # oneshot：bun run build → dist/
  auto_restart: false
  
chat-web-api:            # daemon：uvicorn :9002
  depends_on: [chat-web-ui-build]
  
chat-cli:
  depends_on: [..., chat-web-api]
```

啟動順序：build frontend → start API（health check）→ start chat-cli

### 環境檢查

```bash
chat-supervisor check    # 檢查 bun/uv 是否在 PATH、sessions 目錄是否存在、dist/ 是否已 build
```

Supervisor 在子 process 環境自動補充 `~/.local/bin`、`~/.bun/bin`、`/opt/homebrew/bin` 等路徑。

## 開發模式

```bash
# Terminal 1：後端
uv run chat-web-api serve          # :9002

# Terminal 2：前端（HMR）
cd src/chat_web_ui && bun run dev  # :5173，proxy /api → :9002
```

Production 模式由 `chat-web-api` 直接 serve `dist/` 靜態檔。

## 注意事項

- Web Chat v1 是本機單使用者介面，信任 loopback service，不做登入、附件與 token streaming。
- Web Chat 可見回覆仍必須由模型透過 `send_message(channel="web")` 送出；一般 assistant text 只視為內部思考/console 顯示。
- `channels.web.enabled` 控制 chat-cli 是否註冊 WebAdapter；`channels.web.history_limit` 控制前端預設讀取筆數。
- `requests.jsonl` 含完整 message payload，**不要全部載入 cache**，日後做 lazy load
- `read_cache_rate = cache_read_tokens / prompt_tokens`
- `write_cache` 和 `read_cache_rate` 分開顯示
- provider 不支援 write cache 度量時，前端直接顯示「無法測量」
- `watchfiles` 使用 OS 原生通知（macOS FSEvents），不是 polling
- 前端 `node_modules/` 和 `dist/` 已加入 `.gitignore`
- 新機器部署需先 `cd src/chat_web_ui && bun install` 安裝 node 依賴
