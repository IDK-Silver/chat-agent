# Web Dashboard（chat_web_api + chat_web_ui）

監控 dashboard，即時顯示 token 用量、成本、read cache rate。未來承載線上 chat 介面。

## 架構

```
Browser → uvicorn (:9002) → FastAPI (chat_web_api)
                             ├── /health
                             ├── /api/*        REST endpoints
                             ├── /ws           WebSocket 即時推送
                             └── /*            Vue dist/ 靜態檔 + SPA fallback
```

資料流：JSONL append → watchfiles 偵測 → incremental read → cache 更新 → WebSocket push → Vue reactive 更新

## 後端 (`src/chat_web_api/`)

| 檔案 | 職責 |
|------|------|
| `settings.py` | 從 `cfgs/agent.yaml` 讀取 `agent_os_dir`、`soft_max_prompt_tokens` |
| `pricing.py` | 從 LiteLLM GitHub JSON 抓取 model pricing，本地 cache 24h |
| `session_reader.py` | 增量 JSONL 讀取器（byte offset seek，只讀新行） |
| `cache.py` | In-memory metrics cache：sessions、turns、responses 聚合 |
| `watcher.py` | `watchfiles.awatch()` 監控 session 目錄變動 |
| `app.py` | FastAPI factory：REST + WebSocket + 靜態檔 serving |

### API Endpoints

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/dashboard?from=&to=` | 總覽：cost、turns、read cache rate、daily 聚合 |
| GET | `/api/sessions?from=&to=&limit=&offset=` | Session 列表 |
| GET | `/api/sessions/{id}` | Session 細節：turns + per-request breakdown |
| GET | `/api/requests?from=&to=&limit=&offset=` | 跨 session 的全域 request log |
| GET | `/api/live` | 當前 active session 的 token 位置 |
| WS | `/ws` | 即時推送：`session_updated`、`live_token_update`、`session_created` |

### Token 計費邏輯

Anthropic provider 的 `prompt_tokens` 已包含 cache tokens（見 `src/chat_agent/llm/providers/anthropic.py:241`）：

```
prompt_tokens = base_input + cache_read + cache_write
base_input = prompt_tokens - cache_read_tokens - cache_write_tokens
cost = base_input × input_rate + cache_read × cr_rate + cache_write × cw_rate + completion × output_rate
```

Pricing 來源：`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`

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
| `/chat` | ChatPlaceholder | 預留 |
| `/settings` | SettingsPlaceholder | 預留 |

Overview 和 Requests 之間用 tab bar 切換（`MonitorTabs.vue`）。

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

- `requests.jsonl` 含完整 message payload，**不要全部載入 cache**，日後做 lazy load
- `read_cache_rate = cache_read_tokens / prompt_tokens`
- `write_cache` 和 `read_cache_rate` 分開顯示
- provider 不支援 write cache 度量時，前端直接顯示「無法測量」
- `watchfiles` 使用 OS 原生通知（macOS FSEvents），不是 polling
- 前端 `node_modules/` 和 `dist/` 已加入 `.gitignore`
- 新機器部署需先 `cd src/chat_web_ui && bun install` 安裝 node 依賴
