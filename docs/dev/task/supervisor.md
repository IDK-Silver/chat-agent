# Supervisor + Control API

**狀態**：完成

## 一句話目標

新增進程管理 supervisor 與 chat-cli Control API，支援 VM 部署場景。

## 背景

部署到 VM 時需要：
- 進程守護（crash 自動重啟）
- 定時重啟 copilot-api（token 過期）
- 遠端升級（git pull + 重啟）
- Graceful shutdown chat-cli

## 設計決策

| 決策 | 選擇 | 原因 |
|------|------|------|
| 架構 | 單一 Python supervisor | 跨平台（Windows/Linux），不依賴 systemd |
| HTTP framework | FastAPI + uvicorn | 一致性，可擴展 |
| chat-cli 停止方式 | HTTP Control API | 比 OS signal 更跨平台可靠 |
| 設定檔 | `agent.yaml` + `supervisor.yaml` | 兩個程式各自獨立配置 |
| 啟動順序 | `depends_on` + topological sort | 通用 DAG 排序 |
| 定時重啟 | 全局 `interval_hours` + `join_restart_cycle` | 可控制哪些進程參與 |

## 檔案結構

```
src/chat_supervisor/
├── __init__.py
├── __main__.py      # entry: uv run chat-supervisor
├── schema.py        # Pydantic models
├── config.py        # load_supervisor_config()
├── process.py       # ManagedProcess + topological_sort()
├── server.py        # FastAPI: /upgrade, /restart/{name}, /status, /shutdown
└── scheduler.py     # 定時重啟 + crash recovery

src/chat_agent/
├── control.py       # ControlServer: /health, /shutdown
└── core/schema.py   # ControlConfig

cfgs/
├── agent.yaml       # 原 config.yaml，新增 control 區塊
└── supervisor.yaml  # supervisor 配置
```

## API

### chat-cli Control API（port 9001）

| Method | Path | 說明 |
|--------|------|------|
| GET | `/health` | 存活檢查 |
| POST | `/shutdown` | Graceful shutdown |

### Supervisor API（port 9000）

| Method | Path | 說明 |
|--------|------|------|
| GET | `/status` | 各進程狀態 + pid |
| POST | `/restart/{name}` | 重啟指定進程 |
| POST | `/upgrade` | git pull + post_pull + restart cycle |
| POST | `/shutdown` | 停止所有進程 + 退出 |

## 完成條件

- [x] `config.yaml` 更名為 `agent.yaml`
- [x] `ControlConfig` schema + `ControlServer` 實作
- [x] `cli/app.py` 接入 ControlServer
- [x] `chat_supervisor` 套件（schema、config、process、scheduler、server、entry point）
- [x] `cfgs/supervisor.yaml` 範例配置
- [x] `pyproject.toml` 新增依賴 + entry point
- [x] 測試 38 項通過
- [x] 既有測試 796 項無迴歸
