# 修復 Response Section 缺失問題

修正中間 responder 輪次產出的文字回覆無法出現在 response section 及 adapter 路由的問題。

## 背景

當 LLM 在中間輪次產出文字（伴隨 tool_calls 如 memory_edit），而最終輪次回傳空內容時：
- 文字只出現在 processing section（透過 `print_assistant()`）
- response section 完全不顯示
- adapter 路由（LINE、System）永遠收不到內容

根本原因：
1. `_resolve_final_content()` 的 fallback 跳過有 tool_calls 的 assistant message
2. `used_fallback_content=True` 導致 `_output()` 被跳過，破壞 adapter 路由

## 步驟

1. `_resolve_final_content` 加入 `_latest_intermediate_text()` 作為第三層 fallback
2. 主路徑 finalization 移除 `used_fallback_content` 對 `_output()` 的守衛，永遠呼叫
3. ContextLengthExceededError retry 路徑套用同樣修正
4. 新增 18 個單元測試覆蓋三個 helper function

## 驗證

- `uv run pytest tests/agent/test_resolve_content.py` — 18 tests pass
- `uv run pytest tests/agent/test_empty_response.py` — 7 tests pass（無 regression）
- `uv run pytest tests/` — 全部 621 tests pass

## 完成條件

- [x] `_resolve_final_content` 能找到 intermediate text
- [x] `_output()` 永遠被呼叫（adapter 路由不被跳過）
- [x] retry 路徑同步修正
- [x] 單元測試覆蓋
- [x] 全部測試通過
