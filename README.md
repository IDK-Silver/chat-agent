# Lincy

像是真人的 AI 夥伴。

## Platform

macOS only. 本專案依賴 macOS system API（如 CoreImage `CIDetector`、FSEvents），不支援 Linux/Windows。

## Quick Start

```bash
# Install dependencies
uv sync

# Copy env template and set CHAT_AGENT_USER
cp .env.example .env

# Initialize workspace (first time only)
uv run python -m lincy init

# Login once for Copilot proxy
uv run proxy copilot login

# Start supervisor (this will start chat-cli and any auto-enabled proxy processes)
uv run chat-supervisor start
```

`chat-cli` 會從 `.env` 的 `CHAT_AGENT_USER` 讀取使用者，不需要在 `supervisor.yaml` 額外帶 `--user`。

如果要使用 macOS 原生 app tools（Calendar、Reminders、Notes、Photos、Mail），第一次啟動前可以先觸發系統權限：

```bash
uv run permissions-warmup
```

這個指令只讀取少量 metadata，讓 macOS 連續跳出授權視窗；不會建立、更新、刪除資料，也不會寄信。若只想看會觸發哪些 app：

```bash
uv run permissions-warmup --list
```

如果只想單獨啟動互動介面，也可以直接執行：

```bash
uv run chat-cli
```

## Proxy CLI

四個 LLM proxy（Claude Code / Codex / Copilot / Grok）統一由單一 `proxy` 指令管理：

```bash
proxy <provider> [command ...]   # provider: claude-code | codex | copilot | grok
```

各 provider 的 port、token store 與行為不變；`proxy --help` 會列出所有 provider 與常用命令。帳號用量與 token 管理也可以直接在 web dashboard 的 **Proxy** 頁操作（見 `docs/dev/web-dashboard.md`）。

如果要使用 Claude Code provider，另外走獨立 proxy。典型流程是「登入 → 啟動」：

```bash
# 1. Browser OAuth login，可重複執行以登入多顆帳號（做 failover）
uv run proxy claude-code login

# 2. 啟動 proxy on http://127.0.0.1:4142
uv run proxy claude-code serve
```

`login` 走 browser OAuth：瀏覽器授權後把 Anthropic 顯示的 `code#state` 貼回 terminal。token 存在單一 `tokens.json`，每顆自動配一個 id。

### 子命令一覽

| 命令 | 說明 |
|------|------|
| `proxy claude-code login` | Browser OAuth 登入一顆帳號；重複執行可累積多顆做 failover |
| `proxy claude-code serve` | 啟動 proxy（不帶命令時的預設行為） |
| `proxy claude-code tokens list` | 列出已登入 token（優先級高者在前） |
| `proxy claude-code tokens promote <id>` | 把指定 token 提到最前（設為最高優先） |
| `proxy claude-code tokens remove <id>` | 移除某顆 token |

所有命令都可加 `--help` 看用法與 flag，例如 `proxy claude-code serve --help`、`proxy claude-code tokens --help`。login、promote、remove 也可以在 web dashboard 的 Proxy 頁完成，不需要進 terminal。

### 多帳號 failover

登入多顆帳號後，serve 平時**只用優先級最高**（預設為最新登入）的那顆；上游回 **401/403/429**，或等不到 upstream response headers 而 `ReadTimeout` 時，會自動把該顆停用（bench）、切下一顆重試。停用是**帶冷卻時間**（預設 5 分鐘）而非永久，讓因瞬間錯誤而失敗的 token 冷卻後自動歸隊，一次抖動不會把 token 池縮到重啟才恢復。優先級可用 `tokens promote <id>` 調整。舊版單顆 `token.json` 會在首次讀取時自動併入 `tokens.json`。

錯誤語意：若本輪把所有可用 token 都試過仍是 401/403/429，client 收到的是**上游的原始錯誤**（例如 429），而非被包裝掉；若所有可用 token 都 timeout，client 收到 `504`。只有在**完全沒有 token 可試**（store 為空，或所有顆都還在冷卻中）時才回 `503`（JSON `error` 訊息）。

### serve 設定

serve 端所有設定都有對應 CLI flag（`--host` / `--port` / `--anthropic-base-url` / `--anthropic-version` / `--beta-headers` / `--required-system-prompt` / `--user-agent` / `--request-timeout` / `--access-token`，見 `proxy claude-code serve --help`）。未給的 flag 會沿用同名 `CLAUDE_CODE_PROXY_*` 環境變數，再退回內建預設。`--access-token`（或 `CLAUDE_CODE_PROXY_ACCESS_TOKEN`）會**略過 token store 直接使用該 token**，不 failover、不 refresh。

如果要使用 SuperGrok / X Premium+ 訂閱走 OAuth（不需 `XAI_API_KEY`），用獨立 grok proxy：

```bash
# 1. Device-code OAuth login（開瀏覽器，或 SSH 下手動開 URL）
uv run proxy grok login

# 2. 啟動 proxy on http://127.0.0.1:4144
uv run proxy grok serve
```

`login` 走 xAI device flow：印出 verification URL + user code，授權後把 access/refresh token 存到平台設定目錄（macOS：`~/Library/Application Support/chat-agent/grok-proxy/token.json`）。`serve` 會自動 refresh 短命 access token，並把請求 pass-through 到 `https://api.x.ai/v1`（`/v1/chat/completions`、`/v1/responses`、`/v1/models`）。

| 命令 | 說明 |
|------|------|
| `proxy grok login` | SuperGrok device-code 登入 |
| `proxy grok serve` | 啟動 proxy（不帶命令時的預設行為） |

Headless / SSH 可加 `--no-open-browser`。若 OAuth 登入成功但 inference 回 403，可能是 xAI tier allowlist；可改走 `XAI_API_KEY` 或 OpenRouter。

`cfgs/supervisor.yaml` 的 `grok-proxy` 為 `enabled: auto`：當任一 agent 的 `llm` / `llm_fallbacks` 使用 `provider: grok` 時會自動啟動。手動測可把 agent `llm` 切到：

- `cfgs/llm/grok/grok-4.5/thinking.yaml` 或 `low-thinking.yaml`
- `cfgs/llm/grok/grok-4.3/thinking.yaml` 或 `no-thinking.yaml`
- `cfgs/llm/grok/grok-build-0.1/thinking.yaml`

如果要使用 Codex provider，也走獨立 proxy，一樣是「登入 → 啟動」。可以用本專案自己的 browser OAuth、官方 `codex login`（`~/.codex/auth.json`），或兩者並存 —— proxy 會把兩邊帳號合併成一個 failover pool，同帳號自動 dedup：

```bash
# 1a. 本專案 browser OAuth login，可重複執行以登入多顆帳號（做 failover）
uv run proxy codex login

# 1b. 或者沿用官方 CLI 登入，proxy 會自動把 ~/.codex/auth.json 當 fallback 帳號
codex login

# 2. 啟動 proxy on http://127.0.0.1:4143
uv run proxy codex serve
```

`login` 走 browser OAuth：開瀏覽器並在本機起一個監聽 `http://localhost:1455/auth/callback` 的背景 listener 自動接住授權回跳；連不上時（SSH/headless，或官方 `codex login` 占用了 1455）改貼上完整 callback URL 或 `code#state`（`--code`）。token 存在 `tokens.json`，每顆自動配一個 id。`login --from-codex` 可以把目前的官方 auth 狀態匯入成 store 裡一顆獨立、可 promote/remove 的帳號。

### 子命令一覽

| 命令 | 說明 |
|------|------|
| `proxy codex login` | Browser OAuth 登入一顆帳號；重複執行可累積多顆做 failover |
| `proxy codex login --from-codex` | 匯入目前官方 `codex login` 的 auth 狀態成 store 裡一顆帳號 |
| `proxy codex serve` | 啟動 proxy（不帶命令時的預設行為） |
| `proxy codex tokens list` | 列出已登入 token（優先級高者在前） |
| `proxy codex tokens promote <id>` | 把指定 token 提到最前（設為最高優先） |
| `proxy codex tokens remove <id>` | 移除某顆 token |

serve 的 token pool 依序是：本專案 store（新到舊）→ 官方 `~/.codex/auth.json`（優先級最低的隱式 fallback，固定 id `__codex_auth__`；與 store 某顆同帳號時會被跳過，不會重複出現）。上游回 401/403/429 或等不到 response headers 的 `ReadTimeout` 時會把該顆 bench（預設 5 分鐘冷卻，非永久）並切下一顆重試，語意與 Claude Code proxy 的 multi-account failover 相同。官方 auth 檔那顆過期時只在記憶體 refresh，不會改寫 `~/.codex/auth.json`（該檔案仍由官方 `codex login` 管理）；store 裡的顆過期則 refresh 後寫回 store。`__codex_auth__` 不可 `tokens promote` / `tokens remove`（會回 404 並提示改用官方 `codex login`）。

非 loopback 呼叫 `/usage`、`/login*`、`/tokens*` 這些管理端點需要 `CODEX_PROXY_API_KEY`（`x-api-key` 或 `Authorization: Bearer`），語意與 Claude Code proxy 相同；`/chat`、`/compact`、`/health` 維持免驗（本地 provider client 不送 key）。如果你只想手動單獨測 Codex，可以把 `cfgs/agent.yaml` 裡對應 agent 的 `llm` 路徑切到：

- `cfgs/llm/codex/gpt-5.4/no-thinking.yaml` 或 `cfgs/llm/codex/gpt-5.4/thinking.yaml`
- `cfgs/llm/codex/gpt-5.4-mini/no-thinking.yaml` 或 `cfgs/llm/codex/gpt-5.4-mini/thinking.yaml`
- `cfgs/llm/codex/gpt-5.3-codex/no-thinking.yaml` 或 `cfgs/llm/codex/gpt-5.3-codex/thinking.yaml`
- `cfgs/llm/codex/gpt-5.3-codex-spark/no-thinking.yaml` 或 `cfgs/llm/codex/gpt-5.3-codex-spark/thinking.yaml`
- `cfgs/llm/codex/gpt-5.2/no-thinking.yaml` 或 `cfgs/llm/codex/gpt-5.2/thinking.yaml`

`codex` 的 prompt cache 現在走 request-level `prompt_cache_key`。`cfgs/agent.yaml` 的 `cache.ttl` 目前代表本地 cache key 的輪換週期，不是 upstream 公開 TTL 參數：

- `ephemeral`: 5 分鐘換一個 key
- `1h`: 1 小時換一個 key
- `24h`: 1 天換一個 key

目前已確認 proxy 會把 `prompt_cache_key` 送到上游，且上游會接受，也已實際觀察到 `cached_tokens > 0`。但 cross-turn 存活時間不穩定，不能把 `1h` 或 `24h` 當成 upstream 保證；整理見 `docs/dev/codex-cache-survival.md`。

`cfgs/supervisor.yaml` 現在支援 `enabled: auto`。`copilot-proxy`、`codex-proxy`、`claude-code-proxy` 這些 process 會依 `cfgs/agent.yaml` 裡實際使用的 provider 自動決定是否啟動。如果你想手動單獨測 Claude Code，也可以直接另外啟 `proxy claude-code serve`，再把 `cfgs/agent.yaml` 裡對應 agent 的 `llm` 路徑切到：

- `cfgs/llm/claude_code/claude-opus-4.7/no-thinking.yaml`
- `cfgs/llm/claude_code/claude-opus-4.7/thinking.yaml`
- `cfgs/llm/claude_code/claude-opus-4.8/no-thinking.yaml`
- `cfgs/llm/claude_code/claude-opus-4.8/thinking.yaml`
- `cfgs/llm/claude_code/claude-opus-5/no-thinking.yaml`
- `cfgs/llm/claude_code/claude-opus-5/thinking.yaml`

Claude Code profiles 放在 `cfgs/llm/claude_code/`。Opus 4.7 / 4.8 / 5 profiles 使用：
- `model: claude-opus-4-7`、`claude-opus-4-8` 或 `claude-opus-5`
- `max_tokens: 128000`
- `thinking.type: adaptive` + `output_config.effort: high`（thinking profile）
- `thinking.type: disabled` + `output_config.effort: low`（no-thinking profile）

Opus 5 與 4.7/4.8 的差別：thinking 預設就是開的（省略 `thinking` 等同 adaptive）、effort 支援到 `xhigh`/`max`，而且 `thinking.type: disabled` 只在 effort `high` 以下才被上游接受，配 `xhigh`/`max` 會回 400。目前 `brain`（主 agent）預設走 `cfgs/llm/claude_code/claude-opus-5/thinking.yaml`（`effort: xhigh`）。

effort 與 model 的相容性在 config 載入時就會驗證（不支援的組合直接報錯，不會拖到 runtime 吃 400），規則見 `docs/dev/provider-api-spec.md`。

## Secret 掃描

第一次 clone 後，先安裝本地 pre-commit hook：

```bash
uv run pre-commit install
```

手動做一輪全檔 secret 掃描時，執行：

```bash
uv run pre-commit run --all-files detect-secrets
```

repo 內的 `.secrets.baseline` 已關閉噪音很高的 `KeywordDetector`，避免一般 `api_key_env` 類型欄位造成誤報；高熵字串與常見 token detector 仍會照常檢查。

## Configuration

- Agent runtime: `cfgs/agent.yaml`
- Supervisor: `cfgs/supervisor.yaml`
- Copilot model profiles: `cfgs/llm/copilot/`
- Codex model profiles: `cfgs/llm/codex/`
- Claude Code model profiles: `cfgs/llm/claude_code/`
- Grok (SuperGrok OAuth) model profiles: `cfgs/llm/grok/`

## 疑難排解

### SSH 與 tmux 下 TUI 不會跟著改變大小

如果調整本機 terminal 視窗大小後，`chat-cli` 畫面仍像卡在舊尺寸，先確認新的 terminal size 是否真的傳到遠端 PTY。實務上，這類問題通常先出在 `tmux` session sizing，而不是 app 本身。

1. 在 `tmux` 外，先確認 SSH 有收到新的 terminal size：

```bash
stty size
```

調整本機 terminal 視窗大小後再執行一次，數字應該要改變。

2. 在 `tmux` 內，確認 client/window/pane 尺寸是否跟著改變：

```bash
tmux display -p 'client=#{client_width}x#{client_height} window=#{window_width}x#{window_height} pane=#{pane_width}x#{pane_height}'
```

3. 如果 `tmux` 尺寸固定不變，開啟自動 sizing：

```tmux
set -g window-size latest
setw -g aggressive-resize on
```

然後重新載入 `tmux` 設定，或 detach/attach 一次 session：

```bash
tmux source-file ~/.tmux.conf
```

根因判斷：

- 如果 `stty size` 不會變，問題在 terminal app / SSH 路徑。
- 如果 `stty size` 會變，但 `tmux display` 不會變，問題在 `tmux`。
- 只有在 `tmux` 尺寸已正確更新，但 `chat-cli` 仍不重排時，才把 app 當成主要嫌疑。

如果只有 `uv run chat-supervisor start` 啟動的 `chat-cli` 會出問題，而單獨執行 `uv run chat-cli` 正常，先檢查 `cfgs/supervisor.yaml` 的 `chat-cli.start_new_session` 是否為 `false`。互動式 TUI 若被 supervisor 用新 session 啟動，可能會離開前景 terminal process group，導致 resize signal 傳不到 `chat-cli`。
