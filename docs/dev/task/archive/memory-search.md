> **歸檔日期**：2026-02-18

# Memory 搜尋（取代 Pre-Reviewer）

實作 `memory_search` tool 取代 pre-reviewer，讓 brain agent 按需搜尋記憶。

**狀態**：進行中

## 背景

Pre-reviewer 每輪都跑獨立 LLM，預取檔案注入 system prompt，但 responder 不知道什麼已載入，又自己 `read_file` 同樣的檔案（已用 debug log 證實重複讀取）。

根本原因：架構錯配 — 「決定讀什麼」和「實際讀取」分在兩個不同 LLM 上。

## 設計決策

### Sub-LLM Agent + Two-stage Retrieval
- **選擇**：memory_search 採兩段式
  - Stage 1：query + `index.md` 選候選
  - Stage 2：query + 候選檔全文精排
- **原因**：只看 index 容易漏檔；兩段式可提升命中率與人物檔召回
- **退場策略**：Stage 2 失敗時回退 Stage 1，避免直接回空

### 回傳格式
- **選擇**：路徑 + 一句話相關性說明（不回傳檔案內容）
- **原因**：太少（只有路徑）→ responder 盲讀；太多（含內容）→ token 膨脹

### 上限策略（Config 化）
- `context_bytes_limit: null`（預設）= 不做程式端 context 截斷
- `max_results: null`（預設）= 不做程式端結果截斷
- 仍受模型 context window 限制

### Reminders 處理
- **選擇**：pre-reviewer reminders 寫入 brain system prompt 作為常駐規則
- **原因**：通用記憶使用規則，不需每次動態生成

### Pre-reviewer 處理
- **選擇**：直接刪除（非保留）
- **原因**：已被 memory_search 完全取代

## 檔案結構

### 新增
```
src/chat_agent/tools/builtin/memory_search.py
templates/kernel/agents/memory_searcher/prompts/system.md
templates/kernel/agents/memory_searcher/prompts/parse-retry.md
src/chat_agent/workspace/migrations/m0021_memory_searcher.py
tests/tools/test_memory_search.py
```

### 修改
```
tools/builtin/__init__.py, tools/__init__.py       # 匯出
cli/app.py                                         # 移除 pre-reviewer，加 memory_searcher
context/builder.py                                 # 移除 build_with_review
reviewer/__init__.py, reviewer/schema.py           # 清理 pre-reviewer 匯出
brain/prompts/system.md                            # 加 memory_search tool
cfgs/basic.yaml                                    # memory_searcher agent config
kernel/info.yaml                                   # 版本 0.6.0
```

### 刪除
```
reviewer/pre_reviewer.py
templates/kernel/agents/pre_reviewer/
tests 中 TestPreReviewer 相關
```

## 技術設計

### MemorySearchAgent

```python
class MemorySearchResult(BaseModel):
    path: str
    relevance: str

class MemorySearchAgent:
    def __init__(self, client, system_prompt, memory_dir, parse_retries=1, parse_retry_prompt=None)
    def search(self, query: str) -> list[MemorySearchResult]
    def _build_memory_context(self) -> str  # rglob index.md + listdir
```

### Tool

```python
MEMORY_SEARCH_DEFINITION = ToolDefinition(
    name="memory_search",
    description="Search memory for relevant files. Returns paths with descriptions.",
    parameters={"query": ToolParameter(type="string", ...)},
    required=["query"],
)

def create_memory_search(agent: MemorySearchAgent) -> Callable[..., str]
```

## 步驟

1. 建立 `memory_search.py` tool module
2. 建立 memory_searcher prompts
3. 更新 tool exports
4. 修改 `app.py`：移除 pre-reviewer，加 memory_searcher
5. 修改 `builder.py`：移除 `build_with_review`
6. 刪除 pre-reviewer 相關程式碼和 templates
7. 更新 brain system prompt
8. 更新 `basic.yaml`
9. 建立 migration m0021 + 版本升級
10. 更新測試
11. 全測試驗證

## 驗證

- `uv run pytest tests/` 全部通過
- 手動 debug mode 測試：無重複讀取、簡單對話不觸發 memory_search

## 完成條件

- [ ] `memory_search` tool 實作 + 測試
- [ ] memory_searcher sub-LLM prompt
- [ ] Brain system prompt 更新
- [ ] Pre-reviewer 完全移除
- [ ] Migration + 版本升級
- [ ] Debug log 驗證無重複讀取
