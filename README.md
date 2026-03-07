# Chat Agent

An AI companion that feels alive.

## Quick Start

```bash
# Initialize (first time only)
uv run python -m chat_agent init

# Start chatting
uv run python -m chat_agent --user alice

# You can also pass a display name (it will be resolved to a stable user_id)
uv run python -m chat_agent --user "Alice Chen"
```

## Configuration

See `cfgs/basic.yaml`

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
