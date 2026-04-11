# Chat Agent

An AI assistant that feels alive.

## Platform

macOS only. 本專案依賴 macOS system API（如 CoreImage `CIDetector`、FSEvents），不支援 Linux/Windows。

## Quick Start

```bash
# Install dependencies
uv sync

# Copy env template and set CHAT_AGENT_USER
cp .env.example .env

# Initialize workspace (first time only)
uv run python -m chat_agent init

# Login once for Copilot proxy
uv run copilot-proxy login

# Start supervisor (this will start chat-cli and any auto-enabled proxy processes)
uv run chat-supervisor start
```

`chat-cli` 會從 `.env` 的 `CHAT_AGENT_USER` 讀取使用者，不需要在 `supervisor.yaml` 額外帶 `--user`。

如果只想單獨啟動互動介面，也可以直接執行：

```bash
uv run chat-cli
```

如果要使用 Claude Code provider，另外走獨立 proxy：

```bash
# Browser OAuth login (preferred)
uv run claude-code-proxy login

# Or import an existing Claude Code login state
uv run claude-code-proxy login --from-claude-code

# Start the Claude Code proxy on http://127.0.0.1:4142
uv run claude-code-proxy
```

`claude-code-proxy login` 預設走 browser OAuth，瀏覽器授權後把 Anthropic 顯示的 `code#state` 貼回 terminal。只有在你明確使用 `--from-claude-code`，或額外啟用 fallback 時，proxy 才會去讀 Claude Code credentials / macOS Keychain。

如果要使用 Codex provider，`codex-proxy` 現在和 `claude-code-proxy` 一樣，預設自己走 browser OAuth：

```bash
# Browser OAuth login (preferred)
uv run codex-proxy login

# Or import an existing official Codex login state
uv run codex-proxy login --from-codex

# Start the Codex proxy on http://127.0.0.1:4143
uv run codex-proxy
```

`codex-proxy login` 會開瀏覽器，並在本機 `http://localhost:1455/auth/callback` 等待 OAuth callback。只有在你明確使用 `--from-codex`，或額外啟用 fallback 時，proxy 才會去讀 `~/.codex/auth.json`。如果你只想手動單獨測 Codex，可以把 `cfgs/agent.yaml` 裡對應 agent 的 `llm` 路徑切到：

- `cfgs/llm/codex/gpt-5.2-codex/no-thinking.yaml`
- `cfgs/llm/codex/gpt-5.2-codex/thinking.yaml`

`cfgs/supervisor.yaml` 現在支援 `enabled: auto`。`copilot-proxy`、`codex-proxy`、`claude-code-proxy` 會依 `cfgs/agent.yaml` 裡實際使用的 provider 自動決定是否啟動。如果你想手動單獨測 Claude Code，也可以直接另外啟 `claude-code-proxy`，再把 `cfgs/agent.yaml` 裡對應 agent 的 `llm` 路徑切到：

- `cfgs/llm/claude_code/claude-sonnet-4.6/no-thinking.yaml`
- `cfgs/llm/claude_code/claude-sonnet-4.6/thinking.yaml`

這兩個 profile 目前只保留 Claude Sonnet 4.6，並且已把輸出上限拉到高值：
- `model: claude-sonnet-4-6`
- `max_tokens: 128000`
- `thinking.type: adaptive` + `output_config.effort: high`（thinking profile）
- `thinking.type: disabled` + `output_config.effort: low`（no-thinking profile）

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
