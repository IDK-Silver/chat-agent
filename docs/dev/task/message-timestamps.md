# 訊息時間戳完整注入 + 移除 get_current_time tool

所有 user/assistant message 帶時間前綴，最後一則 user message 標記 `now`，移除 get_current_time tool。

## 背景

Queue-based 架構下，InboundMessage.timestamp（實際發送時間）與 agent 處理時間可能不同（LINE 訊息排隊）。改前只有 user message 有時間前綴，assistant 沒有，LLM 必須呼叫 get_current_time tool 才能知道現在幾點（多一輪 LLM call）。

## 步驟

1. `builder.py` — 擴展時間前綴：所有 user/assistant message 加 `[YYYY-MM-DD HH:MM]`，最後 user message 加 `[ts, now HH:MM]`
2. `core.py` — `run_turn()` 新增 `timestamp` 參數，傳給 `conversation.add()`
3. `core.py` — `_process_inbound()` 傳入 `msg.timestamp`
4. `core.py` — 移除 `get_current_time` tool 註冊（保留 `tools/builtin/time.py`）
5. 新增 4 個 builder 時間前綴測試
6. 建立此任務文件

## 驗證

- `uv run pytest tests/test_context_builder.py` — 31 passed
- `uv run pytest tests/` — 625 passed

## 完成條件

- [x] 歷史 user/assistant message 都有 `[YYYY-MM-DD HH:MM]` 前綴
- [x] 最後 user message 有 `[..., now HH:MM]`
- [x] tool/system message 沒有前綴
- [x] get_current_time 不在 tool 註冊列表中
- [x] 所有測試通過
